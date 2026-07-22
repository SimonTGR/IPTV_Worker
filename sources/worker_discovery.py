from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sources.base import Candidate, SourceAdapter, SourceResult
from sources.http_client import REDIRECT_STATUSES, SourceHttpClient, redact_url, validate_http_url
from sources.http_playlist import looks_like_html
from sources.normalize import decode_playlist, parse_playlist_content
from utils.alias import Alias


class WorkerDiscoveryAdapter(SourceAdapter):
    def __init__(self, source, base_dir, state=None):
        super().__init__(source, base_dir, state)
        self._forced_refresh_used = False

    def _client(self) -> SourceHttpClient:
        return SourceHttpClient(
            timeout=self.source.get("timeout", 15),
            retries=self.source.get("retries", 2),
            max_redirects=self.source.get("max_redirects", 5),
            max_bytes=self.source.get("max_response_size", 2 * 1024 * 1024),
            headers=self.source.get("headers"),
        )

    def _cached_result(self) -> SourceResult | None:
        if self.source.get("refresh_each_run", True):
            return None
        metadata = self.state.get("metadata") or {}
        expires_at = metadata.get("expires_at")
        if not expires_at:
            return None
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
                return None
            candidates = [Candidate(**item) for item in self.state.get("candidates", [])]
        except (TypeError, ValueError):
            return None
        cached_metadata = dict(metadata)
        cached_metadata["from_cache"] = True
        return SourceResult(
            source_id=self.source_id,
            source_type=self.source_type,
            candidates=candidates,
            metadata=cached_metadata,
        )

    def _channel_paths(self) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        for name, path in (self.source.get("channel_paths") or {}).items():
            entries.append((str(name).strip(), str(path).strip(), "channel_paths"))

        mapping_file = self.source.get("channel_list_file")
        if mapping_file:
            path = Path(mapping_file if os.path.isabs(mapping_file) else os.path.join(self.base_dir, mapping_file))
            content, _ = decode_playlist(path.read_bytes())
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "," not in line:
                    continue
                name, channel_path = (part.strip() for part in line.split(",", 1))
                entries.append((name, channel_path, os.path.relpath(path, self.base_dir).replace("\\", "/")))
        return entries

    @staticmethod
    def _join_channel_url(base_url: str, channel_path: str) -> str:
        base = urlsplit(validate_http_url(base_url))
        parsed_path = urlsplit(channel_path)
        if parsed_path.scheme:
            validate_http_url(channel_path)
            path, query = parsed_path.path, parsed_path.query
        else:
            if not channel_path.startswith("/"):
                raise ValueError("channel path must start with /")
            path, query = parsed_path.path, parsed_path.query
        return urlunsplit((base.scheme, base.netloc, path, query, ""))

    def _mapped_candidates(self, dynamic_entry: str | None, discovered_at: str) -> list[Candidate]:
        output_mode = self.source.get("output_url", "worker")
        base_url = dynamic_entry if output_mode == "redirect" and dynamic_entry else self.source["url"]
        aliases = Alias()
        candidates = []
        for name, channel_path, mapping_source in self._channel_paths():
            if not name or not channel_path:
                continue
            candidates.append(
                Candidate(
                    channel_name=name,
                    canonical_name=aliases.get_primary(name),
                    url=self._join_channel_url(base_url, channel_path),
                    source_id=self.source_id,
                    source_type=self.source_type,
                    source_priority=self.priority,
                    discovered_at=discovered_at,
                    source_path=mapping_source,
                    dynamic_base=bool(dynamic_entry),
                )
            )
        return candidates

    def collect(self, force_refresh: bool = False) -> SourceResult:
        if force_refresh:
            if self._forced_refresh_used:
                return SourceResult(
                    source_id=self.source_id,
                    source_type=self.source_type,
                    success=False,
                    status="failed",
                    errors=["forced refresh already used for this run"],
                )
            self._forced_refresh_used = True
        else:
            cached = self._cached_result()
            if cached:
                return cached

        result = SourceResult(source_id=self.source_id, source_type=self.source_type)
        discovered_at = datetime.now(timezone.utc).isoformat()
        response_mode = self.source.get("response_mode", "auto")
        stop_on_redirect = response_mode == "redirect_base" or bool(self.source.get("channel_paths")) or bool(
            self.source.get("channel_list_file")
        )
        try:
            response = self._client().fetch(self.source["url"], stop_on_redirect=stop_on_redirect)
            content_type = response.headers.get("Content-Type", "")
            dynamic_entry = response.final_url if response.status_code in REDIRECT_STATUSES else None
            response_kind = "redirect_base" if dynamic_entry else "status_text"
            status_text = None

            if response.status_code == 200:
                if not response.body:
                    raise ValueError("empty Worker response")
                text, _ = decode_playlist(response.body)
                if looks_like_html(content_type, text):
                    raise ValueError("HTML response is not a Worker playlist or status")
                playlist_candidates = parse_playlist_content(
                    text,
                    source_id=self.source_id,
                    source_type=self.source_type,
                    source_priority=self.priority,
                    source_path=redact_url(response.final_url),
                    discovered_at=discovered_at,
                )
                if playlist_candidates:
                    result.candidates = playlist_candidates
                    response_kind = "playlist"
                else:
                    status_text = " ".join(text.split())[:120]
                    result.candidates = self._mapped_candidates(None, discovered_at)
            elif response.status_code in REDIRECT_STATUSES:
                result.candidates = self._mapped_candidates(dynamic_entry, discovered_at)
            else:
                raise ValueError(f"unexpected HTTP status {response.status_code}")

            ttl_seconds = max(0, int(self.source.get("cache_ttl_seconds", 300)))
            result.metadata = {
                "response_kind": response_kind,
                "http_status": response.status_code,
                "final_entry": redact_url(response.final_url),
                "redirect_count": len(response.redirects),
                "status_text": status_text,
                "refresh_each_run": bool(self.source.get("refresh_each_run", True)),
                "forced_refresh": force_refresh,
                "from_cache": False,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            }
        except Exception as exc:
            result.success = False
            result.status = "failed"
            result.errors.append(str(exc))
        return result


def result_state(result: SourceResult) -> dict:
    return {
        "success": result.success,
        "status": result.status,
        "files": result.files,
        "errors": result.errors,
        "metadata": result.metadata,
        "candidates": [asdict(candidate) for candidate in result.candidates],
        "state_data": result.state_data,
    }
