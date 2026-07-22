from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit


PUBLIC_REPOSITORY = "SimonTGR/IPTV_Playlist"
PUBLIC_EPG_URL = f"https://raw.githubusercontent.com/{PUBLIC_REPOSITORY}/main/epg.xml.gz"
BLOCKED_DIRECT_HOST_SUFFIXES = (".workers.dev",)


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


def build_public_playlists(root: Path) -> dict:
    source = root / "output" / "user_result.m3u"
    report_path = root / "output" / "report.json"
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

    direct_blocks = [block for block in blocks if not _blocked_for_direct(_stream_url(block))]
    if not direct_blocks:
        raise PublicationError("direct playlist has no channels after filtering")

    public = root / "public_output"
    public.mkdir(parents=True, exist_ok=True)
    full_lines = prefix + [line for block in blocks for line in block]
    direct_lines = prefix + [line for block in direct_blocks for line in block]
    (public / "full.m3u").write_text("\n".join(full_lines) + "\n", encoding="utf-8")
    (public / "live.m3u").write_text("\n".join(direct_lines) + "\n", encoding="utf-8")

    epg_source = root / "output" / "epg" / "epg.gz"
    if epg_source.is_file():
        shutil.copyfile(epg_source, public / "epg.xml.gz")

    status = {
        "schema_version": 1,
        "generated_at": report.get("generated_at"),
        "published": True,
        "direct_channel_count": len(direct_blocks),
        "full_channel_count": len(blocks),
        "excluded_from_direct": len(blocks) - len(direct_blocks),
    }
    (public / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return status
