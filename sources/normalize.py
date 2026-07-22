from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import utils.constants as constants
from utils.alias import Alias
from utils.tools import get_name_value

from sources.base import Candidate

SUPPORTED_SCHEMES = {"http", "https", "rtmp", "rtsp", "rtp", "udp"}


def decode_playlist(data: bytes) -> tuple[str, str]:
    """Decode common Chinese playlist encodings without replacing bad bytes."""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("playlist", data, 0, min(len(data), 1), "unsupported text encoding")


def _is_supported_url(value: str) -> bool:
    return urlsplit(value).scheme.lower() in SUPPORTED_SCHEMES


def parse_playlist_content(
    content: str,
    *,
    source_id: str,
    source_type: str,
    source_priority: int,
    source_path: str | None = None,
    discovered_at: str | None = None,
) -> list[Candidate]:
    """Parse TXT/M3U through the project's existing parser into candidates."""
    is_m3u = "#EXTM3U" in content.upper() or "#EXTINF" in content.upper()
    parsed = get_name_value(
        content,
        pattern=constants.multiline_m3u_pattern if is_m3u else constants.multiline_txt_pattern,
        open_headers=is_m3u,
    )
    aliases = Alias()
    timestamp = discovered_at or datetime.now(timezone.utc).isoformat()
    candidates: list[Candidate] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()

    for item in parsed:
        channel_name = (item.get("name") or "").strip()
        raw_url = (item.get("value") or "").strip()
        url, _, _ = raw_url.partition("$")
        headers = item.get("headers") or None
        if not channel_name or not url or not _is_supported_url(url):
            continue
        canonical_name = aliases.get_primary(channel_name)
        header_key = tuple(sorted((headers or {}).items()))
        key = (canonical_name, url, header_key)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            Candidate(
                channel_name=channel_name,
                canonical_name=canonical_name,
                url=url,
                headers=headers,
                tvg_logo=item.get("tvg_logo") or None,
                source_id=source_id,
                source_type=source_type,
                source_priority=source_priority,
                discovered_at=timestamp,
                source_path=source_path,
            )
        )
    return candidates
