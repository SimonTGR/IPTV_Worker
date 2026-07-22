from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from cloud.r2 import R2Client, R2Error


INPUT_PREFIX = "input/pending_sources/"
STATE_KEYS = (
    "state/source_state.json",
    "state/worker_sync_state.json",
    "state/last_good_result.m3u",
)
OUTPUTS = (
    ("output/user_result.m3u", "output/user_result.m3u", "application/vnd.apple.mpegurl"),
    ("output/report.json", "output/report.json", "application/json; charset=utf-8"),
    ("state/source_state.json", "state/source_state.json", "application/json; charset=utf-8"),
    ("state/worker_sync_state.json", "state/worker_sync_state.json", "application/json; charset=utf-8"),
    ("state/last_good_result.m3u", "state/last_good_result.m3u", "application/vnd.apple.mpegurl"),
)


class WorkflowError(RuntimeError):
    pass


def _project_path(root: Path, key: str) -> Path:
    relative = PurePosixPath(key)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkflowError("invalid project object key")
    return root.joinpath(*relative.parts)


def download_inputs(client: R2Client, root: Path) -> int:
    destination = root / "config" / "pending_sources"
    destination.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for key in client.list_keys(INPUT_PREFIX):
        if not key.startswith(INPUT_PREFIX):
            continue
        relative = PurePosixPath(key).relative_to(INPUT_PREFIX)
        if not relative.name or relative.suffix.lower() not in {".m3u", ".m3u8", ".txt"}:
            continue
        local = destination.joinpath(*relative.parts)
        if client.download_file(key, local):
            downloaded += 1
    if not downloaded:
        raise WorkflowError("R2 has no usable input playlist files")
    for key in STATE_KEYS:
        client.download_file(key, _project_path(root, key))
    return downloaded


def _read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkflowError("playlist run did not create a valid report") from exc
    if not isinstance(value, dict):
        raise WorkflowError("playlist report has an invalid structure")
    return value


def upload_published_outputs(client: R2Client, root: Path) -> None:
    report = _read_report(root / "output" / "report.json")
    if report.get("published") is not True:
        raise WorkflowError("playlist was not published locally; R2 output remains unchanged")
    for object_key, local_name, content_type in OUTPUTS:
        local = _project_path(root, local_name)
        if local.is_file():
            client.upload_file(object_key, local, content_type=content_type)


def run_update(client: R2Client, root: Path) -> None:
    download_inputs(client, root)
    result = subprocess.run([sys.executable, "main.py"], cwd=root, check=False)
    if result.returncode:
        raise WorkflowError(f"playlist process failed with exit code {result.returncode}")
    upload_published_outputs(client, root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the R2-backed IPTV update workflow")
    parser.add_argument("action", choices=("download-inputs", "upload-published", "run"))
    parser.add_argument("--root", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.root).resolve()
        client = R2Client.from_env()
        if args.action == "download-inputs":
            print(f"downloaded_input_files={download_inputs(client, root)}")
        elif args.action == "upload-published":
            upload_published_outputs(client, root)
            print("published_outputs_uploaded=true")
        else:
            run_update(client, root)
            print("workflow_completed=true")
        return 0
    except (R2Error, WorkflowError) as exc:
        print(f"cloud workflow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
