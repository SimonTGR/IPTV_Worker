from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sources.http_client import redact_url, sanitize_report_value


SUPPORTED_PLAYLIST_SCHEMES = {"http", "https", "rtmp", "rtsp", "rtp", "udp"}


@dataclass(frozen=True)
class PlaylistEntry:
    name: str
    group: str
    url: str


@dataclass
class PublishResult:
    published: bool
    reasons: list[str] = field(default_factory=list)
    coverage: float = 0.0
    channel_count: int = 0
    previous_channel_count: int = 0
    report: dict[str, Any] = field(default_factory=dict)


def candidate_path_for(final_path: str | os.PathLike[str]) -> str:
    target = Path(final_path)
    return str(target.with_name(f"{target.stem}.candidate{target.suffix or '.m3u'}"))


def parse_template(path: str | os.PathLike[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current_group = ""
    with Path(path).open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "#genre#" in line:
                current_group = re.split(r"[，,]", line, maxsplit=1)[0].strip()
                groups.setdefault(current_group, [])
                continue
            name = re.split(r"[，,]", line, maxsplit=1)[0].strip()
            if name and current_group:
                groups[current_group].append(name)
    return groups


def parse_m3u(path: str | os.PathLike[str]) -> list[PlaylistEntry]:
    target = Path(path)
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError("empty_output")
    content = target.read_text(encoding="utf-8-sig")
    lines = content.splitlines()
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        raise ValueError("invalid_m3u_header")

    entries: list[PlaylistEntry] = []
    pending_name = ""
    pending_group = ""
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            match = re.search(r'group-title="([^"]*)"', line, re.IGNORECASE)
            pending_group = match.group(1).strip() if match else ""
            pending_name = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            continue
        if line.startswith("#"):
            continue
        if not pending_name:
            continue
        parsed = urlsplit(line)
        if parsed.scheme.lower() not in SUPPORTED_PLAYLIST_SCHEMES:
            raise ValueError(f"invalid_playlist_url_scheme:{parsed.scheme or 'missing'}")
        entries.append(PlaylistEntry(pending_name, pending_group, line))
        pending_name = ""
        pending_group = ""
    if not entries:
        raise ValueError("no_playlist_entries")
    return entries


def _atomic_write_json(path: str | os.PathLike[str], data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False,
                                         prefix=target.name + ".tmp.") as file:
            json.dump(sanitize_report_value(data), file, ensure_ascii=False, indent=2)
            file.write("\n")
            temp_path = file.name
        os.replace(temp_path, target)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False,
                                         prefix=target.name + ".tmp.") as file:
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, file)
            temp_path = file.name
        os.replace(temp_path, target)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _load_json(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_strictly_verified(item: dict[str, Any]) -> bool:
    """Return True only for the exact URL that passed this run's media probes."""
    if item.get("playable") is not True:
        return False
    if item.get("content_verified") is not True or item.get("failure_reason"):
        return False
    delay = item.get("delay_ms", item.get("delay"))
    speed = item.get("download_speed_mbps", item.get("speed"))
    try:
        if delay in (None, -1) or speed is None or float(speed) <= 0 or math.isinf(float(speed)):
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    stability = item.get("success_ratio", item.get("stability"))
    if stability is not None:
        try:
            if float(stability) <= 0:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    return True


def _verified_entry_keys(
    channel_data: dict[str, dict[str, list[dict[str, Any]]]],
) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for group, channel_obj in channel_data.items():
        for name, items in channel_obj.items():
            for item in items:
                url = item.get("url")
                if url and _is_strictly_verified(item):
                    keys.add((group, name, url))
    return keys


def _filter_m3u_to_verified_entries(
    path: Path,
    allowed: set[tuple[str, str, str]],
) -> None:
    """Preserve M3U metadata while removing blocks not in the exact probe allowlist."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    prefix: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.strip().upper().startswith("#EXTINF"):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            prefix.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)

    kept: list[list[str]] = []
    for block in blocks:
        info = block[0].strip()
        match = re.search(r'group-title="([^"]*)"', info, re.IGNORECASE)
        group = match.group(1).strip() if match else ""
        name = info.rsplit(",", 1)[-1].strip() if "," in info else ""
        url = next((line.strip() for line in block[1:] if line.strip() and not line.strip().startswith("#")), "")
        if (group, name, url) in allowed:
            kept.append(block)

    content = "\n".join(prefix + [line for block in kept for line in block]) + "\n"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, prefix=path.name + ".tmp."
        ) as file:
            file.write(content)
            temp_path = file.name
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _metric_view(item: dict[str, Any], *, rejected: bool = False) -> dict[str, Any]:
    return {
        "url": redact_url(item.get("url")),
        "source_id": item.get("source_id"),
        "source_type": item.get("source_type"),
        "playable": item.get("playable"),
        "content_verified": item.get("content_verified"),
        "download_speed_mbps": item.get("download_speed_mbps", item.get("speed")),
        "bitrate_kbps": item.get("bitrate_kbps"),
        "bitrate_estimated": item.get("bitrate_estimated"),
        "resolution": item.get("resolution"),
        "delay_ms": item.get("delay_ms", item.get("delay")),
        "stability": item.get("stability", item.get("success_ratio")),
        "failure_reason": item.get("failure_reason") or ("not_selected_by_rank" if rejected else None),
    }


def _build_channel_report(
    template: dict[str, list[str]],
    channel_data: dict[str, dict[str, list[dict[str, Any]]]],
    selected_entries: list[PlaylistEntry],
) -> dict[str, Any]:
    selected_urls: dict[str, set[str]] = {}
    for entry in selected_entries:
        selected_urls.setdefault(entry.name, set()).add(entry.url)
    report: dict[str, Any] = {}
    for group, names in template.items():
        group_data = channel_data.get(group, {})
        for name in names:
            candidates = list(group_data.get(name, []))
            chosen_urls = selected_urls.get(name, set())
            selected = [item for item in candidates if item.get("url") in chosen_urls]
            rejected = [item for item in candidates if item.get("url") not in chosen_urls]
            report[name] = {
                "group": group,
                "candidate_count": len(candidates),
                "valid_count": sum(1 for item in candidates if _is_strictly_verified(item)),
                "selected": [_metric_view(item) for item in selected],
                "rejected": [_metric_view(item, rejected=True) for item in rejected],
            }
    return report


def _main_urls(entries: list[PlaylistEntry]) -> dict[str, str]:
    result = {}
    for entry in entries:
        result.setdefault(entry.name, entry.url)
    return result


def _build_diff(previous: list[PlaylistEntry], current: list[PlaylistEntry]) -> dict[str, Any]:
    old_main = _main_urls(previous)
    new_main = _main_urls(current)
    old_names = set(old_main)
    new_names = set(new_main)
    changed = []
    for name in sorted(old_names & new_names):
        if old_main[name] != new_main[name]:
            changed.append({
                "channel": name,
                "before": redact_url(old_main[name]),
                "after": redact_url(new_main[name]),
            })
    return {
        "added_channels": sorted(new_names - old_names),
        "removed_channels": sorted(old_names - new_names),
        "main_source_changes": changed,
    }


def publish_candidate(
    candidate_path: str | os.PathLike[str],
    *,
    final_path: str | os.PathLike[str],
    report_path: str | os.PathLike[str],
    last_good_path: str | os.PathLike[str],
    template_path: str | os.PathLike[str],
    channel_data: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    source_report_path: str | os.PathLike[str] | None = None,
    worker_state_path: str | os.PathLike[str] | None = "state/worker_sync_state.json",
    min_coverage: float = 0.70,
    max_drop_ratio: float = 0.30,
    critical_groups: list[str] | tuple[str, ...] = ("央视频道", "广东频道", "卫视频道"),
    unhandled_errors: list[str] | None = None,
) -> PublishResult:
    candidate = Path(candidate_path)
    final = Path(final_path)
    last_good = Path(last_good_path)
    template = parse_template(template_path)
    expected_names = {name for names in template.values() for name in names}
    reasons = list(unhandled_errors or [])
    candidate_entries: list[PlaylistEntry] = []
    current_entries: list[PlaylistEntry] = []
    previous_entries: list[PlaylistEntry] = []

    try:
        candidate_entries = parse_m3u(candidate)
    except (OSError, UnicodeError, ValueError) as exc:
        reasons.append(str(exc))

    if candidate_entries:
        verified_keys = _verified_entry_keys(channel_data or {})
        current_entries = [
            entry for entry in candidate_entries
            if (entry.group, entry.name, entry.url) in verified_keys
        ]
        if current_entries:
            _filter_m3u_to_verified_entries(candidate, verified_keys)
        else:
            reasons.append("no_strictly_verified_entries")

    if final.is_file():
        try:
            previous_entries = parse_m3u(final)
        except (OSError, UnicodeError, ValueError):
            previous_entries = []

    current_names = {entry.name for entry in current_entries} & expected_names
    previous_names = {entry.name for entry in previous_entries} & expected_names
    coverage = len(current_names) / len(expected_names) if expected_names else 0.0
    if coverage < max(0.0, min(1.0, float(min_coverage))):
        reasons.append(f"coverage_below_threshold:{coverage:.4f}")

    for marker in critical_groups:
        matching_groups = [group for group in template if marker and marker in group]
        if not matching_groups or not any(
            name in current_names for group in matching_groups for name in template[group]
        ):
            reasons.append(f"critical_group_empty:{marker}")

    drop_ratio = 0.0
    if previous_names:
        drop_ratio = max(0.0, (len(previous_names) - len(current_names)) / len(previous_names))
        if drop_ratio > max(0.0, min(1.0, float(max_drop_ratio))):
            reasons.append(f"channel_drop_exceeded:{drop_ratio:.4f}")

    source_report = _load_json(source_report_path)
    worker_state = _load_json(worker_state_path)
    now = datetime.now(timezone.utc).isoformat()
    approved = not reasons
    report = {
        "schema_version": 3,
        "generated_at": now,
        "published": approved,
        "publish_reasons": reasons,
        "validation": {
            "m3u_reparsed": bool(candidate_entries),
            "candidate_entry_count": len(candidate_entries),
            "verified_entry_count": len(current_entries),
            "filtered_unverified_entry_count": len(candidate_entries) - len(current_entries),
            "channel_count": len(current_names),
            "expected_channel_count": len(expected_names),
            "coverage": coverage,
            "minimum_coverage": min_coverage,
            "previous_channel_count": len(previous_names),
            "drop_ratio": drop_ratio,
            "maximum_drop_ratio": max_drop_ratio,
            "critical_groups": list(critical_groups),
        },
        "sources": source_report.get("sources", []),
        "source_candidate_count": source_report.get("candidate_count", 0),
        "source_channels": source_report.get("channels", {}),
        "channels": _build_channel_report(template, channel_data or {}, current_entries),
        "changes": _build_diff(previous_entries, current_entries),
        "workers": worker_state,
    }

    try:
        _atomic_write_json(report_path, report)
        if approved:
            if final.is_file() and previous_entries:
                _atomic_copy(final, last_good)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate, final)
            # On the first successful publish there is no former result to back up.
            # Seed last-known-good now so the next failed run can always fall back.
            if not last_good.is_file():
                _atomic_copy(final, last_good)
    except Exception as exc:
        reasons.append(f"publish_io_error:{exc}")
        report["published"] = False
        report["publish_reasons"] = reasons
        try:
            _atomic_write_json(report_path, report)
        except Exception:
            pass
        approved = False

    if not approved and candidate.is_file():
        try:
            candidate.unlink()
        except OSError:
            pass

    return PublishResult(
        published=approved,
        reasons=reasons,
        coverage=coverage,
        channel_count=len(current_names),
        previous_channel_count=len(previous_names),
        report=report,
    )
