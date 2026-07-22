from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from sources.http_client import redact_text, sanitize_report_value
from workers.cloudflare import CloudflareWorkersClient
from workers.github_upstream import GitHubUpstreamClient
from workers.validate import validate_worker_source


class SyncError(RuntimeError):
    pass


def load_manifest(path: str | os.PathLike[str] = "workers/manifest.json") -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise SyncError("unsupported worker manifest schema")
    upstream = manifest.get("upstream") or {}
    if not all(isinstance(upstream.get(key), str) and upstream[key].strip()
               for key in ("owner", "repository", "ref")):
        raise SyncError("worker manifest has invalid upstream")
    if manifest.get("mode") not in {"check-only", "manual", "auto"}:
        raise SyncError("worker manifest has invalid mode")
    targets = manifest.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise SyncError("worker manifest has no targets")
    seen_paths = set()
    seen_workers = set()
    for name, target in targets.items():
        path_value = target.get("path")
        worker_name = target.get("worker_name")
        alias = target.get("preview_alias")
        if not isinstance(path_value, str) or path_value.startswith(("/", "\\")) or ".." in path_value.split("/"):
            raise SyncError(f"target {name} has invalid path")
        if path_value in seen_paths:
            raise SyncError(f"duplicate upstream path: {path_value}")
        seen_paths.add(path_value)
        if not isinstance(worker_name, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", worker_name):
            raise SyncError(f"target {name} has invalid Worker name")
        if worker_name in seen_workers:
            raise SyncError(f"duplicate Worker name: {worker_name}")
        seen_workers.add(worker_name)
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", alias):
            raise SyncError(f"target {name} has invalid preview alias")
        if not isinstance(target.get("preview_url"), str) or not target["preview_url"].startswith("https://"):
            raise SyncError(f"target {name} has invalid preview URL")
        production = urlsplit(str(target.get("production_url") or ""))
        preview = urlsplit(target["preview_url"])
        production_prefix = f"{worker_name}."
        if production.scheme != "https" or not production.hostname or not production.hostname.startswith(production_prefix):
            raise SyncError(f"target {name} has invalid production URL")
        account_host = production.hostname[len(production_prefix):]
        expected_preview_host = f"{alias}-{worker_name}.{account_host}"
        if preview.hostname != expected_preview_host or preview.username or preview.password:
            raise SyncError(f"target {name} preview URL does not belong to its Worker")
        if not isinstance(target.get("smoke"), dict):
            raise SyncError(f"target {name} has no smoke rules")
    return manifest


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                                         prefix=path.name + ".tmp.") as file:
            json.dump(sanitize_report_value(value), file, ensure_ascii=False, indent=2)
            file.write("\n")
            temp_path = file.name
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


class WorkerSynchronizer:
    def __init__(self, manifest: dict, *, state_path: str = "state/worker_sync_state.json",
                 report_path: str = "output/worker_sync_report.json",
                 github_client=None, cloudflare_client=None,
                 trigger_update: Callable[[], bool] | None = None):
        self.manifest = manifest
        self.state_path = Path(state_path)
        self.report_path = Path(report_path)
        self.state = _load_json(self.state_path)
        self.state.setdefault("schema_version", 1)
        self.state.setdefault("targets", {})
        upstream = manifest["upstream"]
        self.github = github_client or GitHubUpstreamClient(
            upstream["owner"], upstream["repository"], upstream["ref"],
            token=os.getenv("UPSTREAM_GITHUB_TOKEN") or None,
        )
        self.cloudflare = cloudflare_client
        self.trigger_update = trigger_update

    def _cloudflare(self):
        if self.cloudflare is None:
            account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
            api_token = os.getenv("CLOUDFLARE_API_TOKEN")
            missing = [name for name, value in (
                ("CLOUDFLARE_ACCOUNT_ID", account_id), ("CLOUDFLARE_API_TOKEN", api_token)
            ) if not value]
            if missing:
                raise SyncError(f"missing required secrets: {', '.join(missing)}")
            self.cloudflare = CloudflareWorkersClient(account_id, api_token)
        return self.cloudflare

    def _target(self, name: str) -> dict:
        try:
            return self.manifest["targets"][name]
        except KeyError as exc:
            raise SyncError(f"unknown worker target: {name}") from exc

    def _process_target(self, name: str, *, deploy: bool) -> dict:
        target = self._target(name)
        upstream_file = self.github.fetch_file(target["path"])
        validation = validate_worker_source(upstream_file.content, max_size=int(target["max_size"]))
        previous = self.state["targets"].get(name, {})
        changed = upstream_file.blob_sha != previous.get("upstream_blob_sha")
        result = {
            "target": name,
            "worker_name": target["worker_name"],
            "path": target["path"],
            "changed": changed,
            "upstream_blob_sha": upstream_file.blob_sha,
            "upstream_commit_sha": upstream_file.commit_sha,
            "content_sha256": upstream_file.sha256,
            "size": upstream_file.size,
            "validation": validation,
            "status": "changed" if changed else "unchanged",
            "deployed": False,
        }
        if not deploy or not changed:
            return result

        cloudflare = self._cloudflare()
        old_version = cloudflare.get_active_version(target["worker_name"])
        message = f"Sync {target['path']} at {upstream_file.commit_sha[:12]}"
        uploaded = None
        try:
            uploaded = cloudflare.upload_version(
                target["worker_name"], upstream_file.content,
                preview_alias=target["preview_alias"], preview_url=target["preview_url"],
                message=message, tag=upstream_file.blob_sha[:40],
            )
            smoke = cloudflare.smoke_test(uploaded.preview_url, target["smoke"])
            deployment_id = cloudflare.deploy_version(
                target["worker_name"], uploaded.version_id, message=message,
            )
        except Exception as exc:
            result.update({
                "status": "failed",
                "error": redact_text(str(exc)),
                "previous_active_version": old_version,
                "attempted_version": uploaded.version_id if uploaded else None,
            })
            self.state["targets"].setdefault(name, {})["last_failed_attempt"] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "upstream_blob_sha": upstream_file.blob_sha,
                "attempted_version": uploaded.version_id if uploaded else None,
                "error": redact_text(str(exc)),
            }
            return result

        deployed_at = datetime.now(timezone.utc).isoformat()
        result.update({
            "status": "deployed",
            "deployed": True,
            "previous_active_version": old_version,
            "active_version": uploaded.version_id,
            "deployment_id": deployment_id,
            "smoke": smoke,
            "deployed_at": deployed_at,
        })
        self.state["targets"][name] = {
            "upstream_blob_sha": upstream_file.blob_sha,
            "upstream_commit_sha": upstream_file.commit_sha,
            "content_sha256": upstream_file.sha256,
            "active_version": uploaded.version_id,
            "previous_active_version": old_version,
            "deployment_id": deployment_id,
            "deployed_at": deployed_at,
            "smoke": smoke,
        }
        return result

    def run(self, names: list[str], *, deploy: bool, mode: str) -> dict:
        if mode not in {"check-only", "manual", "auto"}:
            raise SyncError("invalid sync mode")
        if deploy and mode == "check-only":
            raise SyncError("check-only mode cannot deploy")
        if mode == "auto" and self.manifest.get("mode") != "auto":
            raise SyncError("auto mode is disabled in workers/manifest.json")
        started_at = datetime.now(timezone.utc).isoformat()
        results = []
        for name in names:
            try:
                results.append(self._process_target(name, deploy=deploy))
            except Exception as exc:
                results.append({"target": name, "status": "failed", "deployed": False,
                                "error": redact_text(str(exc))})
        deployed_any = any(item.get("deployed") for item in results)
        trigger_status = "not_needed"
        if deployed_any:
            if self.trigger_update:
                try:
                    trigger_status = "triggered" if self.trigger_update() else "not_triggered"
                except Exception as exc:
                    trigger_status = f"failed:{redact_text(str(exc))}"
            else:
                trigger_status = "not_configured"
        report = {
            "schema_version": 1,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "deploy_requested": deploy,
            "success": all(item.get("status") != "failed" for item in results),
            "playlist_update_trigger": trigger_status,
            "targets": results,
        }
        self.state["updated_at"] = report["finished_at"]
        _atomic_json(self.state_path, self.state)
        _atomic_json(self.report_path, report)
        return report


def github_workflow_trigger() -> bool:
    repository = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    ref = os.getenv("GITHUB_REF_NAME") or "main"
    if not repository or not token:
        return False
    url = f"https://api.github.com/repos/{repository}/actions/workflows/update-playlist.yml/dispatches"
    response = requests.post(
        url, json={"ref": ref}, timeout=20,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "iptv-worker-sync/1",
        },
    )
    if response.status_code != 204:
        raise SyncError(f"playlist workflow trigger returned HTTP {response.status_code}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely synchronize allowlisted upstream Worker scripts")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="check all allowlisted upstream files")
    action.add_argument("--deploy", choices=("chaozhou", "guangdong"), help="deploy one target")
    action.add_argument("--deploy-all", action="store_true", help="deploy all changed targets")
    parser.add_argument("--mode", choices=("check-only", "manual", "auto"), default=None)
    parser.add_argument("--manifest", default="workers/manifest.json")
    parser.add_argument("--state", default="state/worker_sync_state.json")
    parser.add_argument("--report", default="output/worker_sync_report.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.check:
            names = list(manifest["targets"])
            deploy = False
            mode = args.mode or "check-only"
        elif args.deploy:
            names = [args.deploy]
            deploy = True
            mode = args.mode or "manual"
        else:
            names = list(manifest["targets"])
            deploy = True
            mode = args.mode or "manual"
        synchronizer = WorkerSynchronizer(
            manifest, state_path=args.state, report_path=args.report,
            trigger_update=github_workflow_trigger,
        )
        report = synchronizer.run(names, deploy=deploy, mode=mode)
        print(json.dumps(sanitize_report_value(report), ensure_ascii=False, indent=2))
        return 0 if report["success"] else 1
    except Exception as exc:
        print(f"worker sync failed: {redact_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
