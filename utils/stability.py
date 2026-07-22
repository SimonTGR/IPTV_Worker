from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _atomic_json(path: str | os.PathLike[str], data: dict) -> None:
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


def _load(path: str | os.PathLike[str]) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _probe_succeeded(item: dict) -> bool:
    if "playable" in item:
        return bool(item.get("playable"))
    delay = item.get("delay_ms", item.get("delay"))
    throughput = item.get("download_speed_mbps", item.get("speed")) or 0
    return delay not in (None, -1) and throughput > 0


def apply_stability_history(
    grouped_results: dict,
    path: str = "state/probe_history.json",
    *,
    history_size: int = 10,
) -> dict:
    """Update rolling success history without persisting credential-bearing URLs."""
    state = _load(path)
    records = state.setdefault("records", {})
    now = datetime.now(timezone.utc).isoformat()

    for channel_obj in grouped_results.values():
        for items in channel_obj.values():
            for item in items:
                url = item.get("url") or ""
                if not url:
                    continue
                key = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()
                record = records.setdefault(key, {"history": [], "consecutive_failures": 0})
                succeeded = _probe_succeeded(item)
                history = [bool(value) for value in record.get("history", [])][-max(0, history_size - 1):]
                history.append(succeeded)
                record["history"] = history
                record["success_ratio"] = sum(history) / len(history)
                if succeeded:
                    record["consecutive_failures"] = 0
                    record["last_success_at"] = now
                else:
                    record["consecutive_failures"] = int(record.get("consecutive_failures", 0)) + 1
                record["last_probe_at"] = now
                record["source_id"] = item.get("source_id")
                item["success_ratio"] = record["success_ratio"]
                item["stability"] = record["success_ratio"]
                item["consecutive_failures"] = record["consecutive_failures"]

    state["schema_version"] = 1
    state["updated_at"] = now
    _atomic_json(path, state)
    return state
