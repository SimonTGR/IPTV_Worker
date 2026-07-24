from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit


PUBLIC_REPOSITORY = "SimonTGR/IPTV_Playlist"
PUBLIC_EPG_URL = f"https://raw.githubusercontent.com/{PUBLIC_REPOSITORY}/main/epg.xml.gz"
BLOCKED_DIRECT_HOST_SUFFIXES = (".workers.dev", ".chinacert.cftest5.cn")


class PublicationError(RuntimeError):
    pass


def _rewrite_epg_header(line: str) -> str:
    if not line.startswith("#EXTM3U"):
        raise PublicationError("playlist is missing the #EXTM3U header")
    rewritten = re.sub(r'\s+(?:x-tvg-url|url-tvg)="[^"]*"', "", line)
    return f'{rewritten} x-tvg-url="{PUBLIC_EPG_URL}"'


def _channel_blocks(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    prefix: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("#EXTINF"):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            prefix.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return prefix, blocks


def _stream_url(block: list[str]) -> str | None:
    for line in block[1:]:
        value = line.strip()
        if value and not value.startswith("#"):
            return value
    return None


def _blocked_for_direct(url: str | None) -> bool:
    if not url:
        return True
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return True
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in BLOCKED_DIRECT_HOST_SUFFIXES)


def _probe_media_block(block: list[str]) -> bool:
    url = _stream_url(block)
    if not url:
        return False
    headers = []
    for line in block[1:]:
        value = line.strip()
        if value.lower().startswith("#extvlcopt:http-user-agent="):
            headers.append("User-Agent: " + value.split("=", 1)[1])
        elif value.lower().startswith("#extvlcopt:http-referrer="):
            headers.append("Referer: " + value.split("=", 1)[1])
    command = [
        "ffprobe", "-v", "error", "-rw_timeout", "12000000",
        "-analyzeduration", "3000000", "-probesize", "3000000",
    ]
    if headers:
        command += ["-headers", "\r\n".join(headers) + "\r\n"]
    command += ["-show_entries", "stream=codec_type", "-of", "json", url]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=18, check=False)
        if completed.returncode != 0:
            return False
        data = json.loads(completed.stdout or "{}")
        return any(stream.get("codec_type") in {"video", "audio"} for stream in data.get("streams", []))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def _media_verified_blocks(
    blocks: list[list[str]], probe: Callable[[list[str]], bool],
) -> list[list[str]]:
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(blocks)))) as executor:
        verdicts = list(executor.map(probe, blocks))
    return [block for block, valid in zip(blocks, verdicts) if valid]


def build_public_playlists(
    root: Path | str, *, media_probe: Callable[[list[str]], bool] | None = None,
) -> dict:
    root_path = Path(root)
    source = root_path / "output" / "user_result.m3u"
    report_path = root_path / "output" / "report.json"
    if not source.is_file() or not report_path.is_file():
        raise PublicationError("verified playlist or report is missing")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationError("verified report is invalid") from exc
    if report.get("published") is not True:
        raise PublicationError("refusing to publish an unverified playlist")

    lines = source.read_text(encoding="utf-8-sig").splitlines()
    if not lines:
        raise PublicationError("verified playlist is empty")
    lines[0] = _rewrite_epg_header(lines[0])
    prefix, blocks = _channel_blocks(lines)
    if not blocks:
        raise PublicationError("verified playlist has no channels")

    verified_blocks = _media_verified_blocks(blocks, media_probe or _probe_media_block)
    direct_blocks = [block for block in verified_blocks if not _blocked_for_direct(_stream_url(block))]
    if not direct_blocks:
        raise PublicationError("direct playlist has no channels after filtering")

    public = root_path / "public_output"
    public.mkdir(parents=True, exist_ok=True)
    full_lines = prefix + [line for block in verified_blocks for line in block]
    direct_lines = prefix + [line for block in direct_blocks for line in block]
    (public / "full.m3u").write_text("\n".join(full_lines) + "\n", encoding="utf-8")
    (public / "live.m3u").write_text("\n".join(direct_lines) + "\n", encoding="utf-8")

    epg_source = root_path / "output" / "epg" / "epg.gz"
    if epg_source.is_file():
        shutil.copyfile(epg_source, public / "epg.xml.gz")

    status = {
        "schema_version": 1,
        "generated_at": report.get("generated_at"),
        "published": True,
        "direct_channel_count": len(direct_blocks),
        "full_channel_count": len(verified_blocks),
        "excluded_from_direct": len(verified_blocks) - len(direct_blocks),
        "failed_final_media_probe": len(blocks) - len(verified_blocks),
    }
    (public / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status
