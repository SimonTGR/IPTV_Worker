from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sources.base import Candidate, SourceResult
from sources.file_inbox import FileInboxAdapter
from sources.http_playlist import HttpPlaylistAdapter
from sources.worker_discovery import WorkerDiscoveryAdapter, result_state

ADAPTERS = {
    "file_inbox": FileInboxAdapter,
    "http_playlist": HttpPlaylistAdapter,
    "worker_discovery": WorkerDiscoveryAdapter,
}

_REFRESH_GUARD = set()
_REFRESH_GUARD_LOCK = threading.Lock()


@dataclass
class SourceCollection:
    started_at: str
    finished_at: str
    results: list[SourceResult]

    @property
    def candidates(self) -> list[Candidate]:
        return [candidate for result in self.results for candidate in result.candidates]

    def as_channel_map(self) -> dict[str, list[dict[str, Any]]]:
        channels: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()
        for candidate in self.candidates:
            key = (candidate.canonical_name, candidate.url)
            if key in seen:
                continue
            seen.add(key)
            channels[candidate.canonical_name].append(candidate.to_channel_data())
        return dict(channels)

    def to_report(self) -> dict[str, Any]:
        channels: dict[str, dict[str, Any]] = {}
        for candidate in self.candidates:
            item = channels.setdefault(candidate.canonical_name, {
                "candidate_count": 0,
                "original_names": set(),
                "source_ids": set(),
                "alias_conversions": set(),
            })
            item["candidate_count"] += 1
            item["original_names"].add(candidate.channel_name)
            item["source_ids"].add(candidate.source_id)
            if candidate.channel_name != candidate.canonical_name:
                item["alias_conversions"].add(f"{candidate.channel_name} -> {candidate.canonical_name}")
        channel_report = {
            name: {
                "candidate_count": item["candidate_count"],
                "original_names": sorted(item["original_names"]),
                "source_ids": sorted(item["source_ids"]),
                "alias_conversions": sorted(item["alias_conversions"]),
            }
            for name, item in sorted(channels.items())
        }
        return {
            "schema_version": 1,
            "run_started_at": self.started_at,
            "run_finished_at": self.finished_at,
            "sources": [result.to_report() for result in self.results],
            "candidate_count": len(self.candidates),
            "channels": channel_report,
        }


class SourceRegistry:
    def __init__(
        self,
        path: str | os.PathLike[str],
        base_dir: str | os.PathLike[str] | None = None,
        state: dict[str, Any] | None = None,
    ):
        self.path = Path(path)
        self.base_dir = str(Path(base_dir or os.getcwd()).resolve())
        self.state = state or {}
        self.adapters = {}
        self.config = self._load_and_validate()

    def _load_and_validate(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if data.get("schema_version") != 1:
            raise ValueError("sources.json schema_version must be 1")
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise ValueError("sources.json sources must be a list")
        ids: set[str] = set()
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise ValueError(f"source at index {index} must be an object")
            source_id = source.get("id")
            source_type = source.get("type")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError(f"source at index {index} has invalid id")
            if source_id in ids:
                raise ValueError(f"duplicate source id: {source_id}")
            ids.add(source_id)
            if source.get("enabled", True) and source_type not in ADAPTERS:
                raise ValueError(f"unsupported enabled source type: {source_type}")
            if source_type == "file_inbox" and not isinstance(source.get("paths"), list):
                raise ValueError(f"file_inbox source {source_id} must define paths")
            if "required" in source and not isinstance(source.get("required"), bool):
                raise ValueError(f"source {source_id} required must be a boolean")
            if source_type in {"http_playlist", "worker_discovery"} and not isinstance(source.get("url"), str):
                raise ValueError(f"{source_type} source {source_id} must define url")
            if source_type == "worker_discovery" and source.get("channel_paths") is not None and not isinstance(
                source.get("channel_paths"), dict
            ):
                raise ValueError(f"worker_discovery source {source_id} channel_paths must be an object")
        return data

    def collect(self) -> SourceCollection:
        started_at = datetime.now(timezone.utc).isoformat()
        results: list[SourceResult] = []
        for source in self.config["sources"]:
            if not source.get("enabled", True):
                continue
            adapter_type = ADAPTERS[source["type"]]
            try:
                source_state = (self.state.get("sources") or {}).get(source["id"], {})
                adapter = adapter_type(source, self.base_dir, source_state)
                self.adapters[source["id"]] = adapter
                results.append(adapter.collect())
            except Exception as exc:
                results.append(
                    SourceResult(
                        source_id=source["id"],
                        source_type=source["type"],
                        success=False,
                        status="failed",
                        errors=[str(exc)],
                    )
                )
        return SourceCollection(started_at, datetime.now(timezone.utc).isoformat(), results)

    def refresh_source_once(self, source_id: str) -> SourceResult:
        adapter = self.adapters.get(source_id)
        if adapter is None:
            raise KeyError(f"source was not collected: {source_id}")
        return adapter.collect(force_refresh=True)


def _write_json_atomic(path: str | os.PathLike[str], data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            temp_path = file.name
        os.replace(temp_path, target)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def collect_configured_sources(
    registry_path: str = "config/sources.json",
    *,
    base_dir: str | None = None,
    report_path: str | None = "output/source_report.candidate.json",
    state_path: str | None = "state/source_state.json",
) -> SourceCollection:
    with _REFRESH_GUARD_LOCK:
        _REFRESH_GUARD.clear()
    root = os.path.abspath(base_dir or os.getcwd())
    path = registry_path if os.path.isabs(registry_path) else os.path.join(root, registry_path)
    state_target = state_path if state_path and os.path.isabs(state_path) else (
        os.path.join(root, state_path) if state_path else None
    )
    previous_state = {}
    if state_target and os.path.exists(state_target):
        try:
            with open(state_target, "r", encoding="utf-8") as file:
                previous_state = json.load(file)
        except (OSError, ValueError):
            previous_state = {}
    collection = SourceRegistry(path, base_dir=root, state=previous_state).collect()
    if report_path:
        target = report_path if os.path.isabs(report_path) else os.path.join(root, report_path)
        _write_json_atomic(target, collection.to_report())
    if state_path:
        source_state = {
            "schema_version": 1,
            "updated_at": collection.finished_at,
            "sources": {
                result.source_id: result_state(result)
                for result in collection.results
            },
        }
        _write_json_atomic(state_target, source_state)
    return collection


def refresh_configured_source_once(
    source_id: str,
    registry_path: str = "config/sources.json",
    *,
    base_dir: str | None = None,
    state_path: str = "state/source_state.json",
) -> SourceResult:
    """Refresh one dynamic source at most once until the next collection run."""
    with _REFRESH_GUARD_LOCK:
        if source_id in _REFRESH_GUARD:
            return SourceResult(
                source_id=source_id,
                source_type="worker_discovery",
                success=False,
                status="failed",
                errors=["forced refresh already used for this run"],
            )
        _REFRESH_GUARD.add(source_id)

    root = os.path.abspath(base_dir or os.getcwd())
    registry_target = registry_path if os.path.isabs(registry_path) else os.path.join(root, registry_path)
    state_target = state_path if os.path.isabs(state_path) else os.path.join(root, state_path)
    previous_state = {}
    if os.path.exists(state_target):
        try:
            with open(state_target, "r", encoding="utf-8") as file:
                previous_state = json.load(file)
        except (OSError, ValueError):
            previous_state = {}

    registry = SourceRegistry(registry_target, base_dir=root, state=previous_state)
    source = next((item for item in registry.config["sources"] if item.get("id") == source_id), None)
    if not source or not source.get("enabled", True):
        return SourceResult(source_id, "worker_discovery", success=False, status="failed", errors=["source is missing or disabled"])
    if source.get("type") != "worker_discovery":
        return SourceResult(source_id, str(source.get("type")), success=False, status="failed", errors=["source is not dynamic"])

    source_state = (previous_state.get("sources") or {}).get(source_id, {})
    result = WorkerDiscoveryAdapter(source, root, source_state).collect(force_refresh=True)
    previous_state.setdefault("schema_version", 1)
    previous_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    previous_state.setdefault("sources", {})[source_id] = result_state(result)
    _write_json_atomic(state_target, previous_state)
    return result
