from __future__ import annotations

from sources.base import SourceAdapter, SourceResult
from sources.http_client import SourceHttpClient, redact_url
from sources.normalize import decode_playlist, parse_playlist_content


def looks_like_html(content_type: str, text: str) -> bool:
    prefix = text.lstrip()[:128].lower()
    return "text/html" in content_type.lower() or prefix.startswith(("<!doctype html", "<html"))


class HttpPlaylistAdapter(SourceAdapter):
    def _client(self) -> SourceHttpClient:
        return SourceHttpClient(
            timeout=self.source.get("timeout", 15),
            retries=self.source.get("retries", 2),
            max_redirects=self.source.get("max_redirects", 5),
            max_bytes=self.source.get("max_response_size", 2 * 1024 * 1024),
            headers=self.source.get("headers"),
        )

    def collect(self, force_refresh: bool = False) -> SourceResult:
        result = SourceResult(source_id=self.source_id, source_type=self.source_type)
        try:
            response = self._client().fetch(self.source["url"])
            content_type = response.headers.get("Content-Type", "")
            if response.status_code != 200:
                raise ValueError(f"unexpected HTTP status {response.status_code}")
            if not response.body:
                raise ValueError("empty response")
            text, encoding = decode_playlist(response.body)
            if looks_like_html(content_type, text):
                raise ValueError("HTML response is not a playlist")
            result.candidates = parse_playlist_content(
                text,
                source_id=self.source_id,
                source_type=self.source_type,
                source_priority=self.priority,
                source_path=redact_url(response.final_url),
            )
            if not result.candidates:
                raise ValueError("response contains no playlist entries")
            result.metadata = {
                "response_kind": "playlist",
                "http_status": response.status_code,
                "final_entry": redact_url(response.final_url),
                "redirect_count": len(response.redirects),
                "encoding": encoding,
            }
        except Exception as exc:
            result.success = False
            result.status = "failed"
            result.errors.append(str(exc))
        return result
