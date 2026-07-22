from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

REDIRECT_STATUSES = {301, 302, 307, 308}
SENSITIVE_QUERY_KEYS = ("token", "tk", "key", "auth", "sign", "signature", "secret", "password")
URL_PATTERN = re.compile(r"https?://[^\s\]\[{}<>'\"]+")


class SourceHttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    redirects: tuple[str, ...]


def validate_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise SourceHttpError("only absolute HTTP/HTTPS URLs are allowed")
    if parsed.username or parsed.password:
        raise SourceHttpError("credentials in source URLs are not allowed")
    return url


def redact_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            safe_value = "***" if any(marker in key.lower() for marker in SENSITIVE_QUERY_KEYS) else value
            query.append((key, safe_value))
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))
    except Exception:
        return "<invalid-url>"


def redact_text(value: str) -> str:
    """Redact sensitive query values in URLs embedded in logs or errors."""
    return URL_PATTERN.sub(lambda match: redact_url(match.group(0)) or "", str(value))


def sanitize_report_value(value):
    """Recursively sanitize report fields without changing operational URLs."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): sanitize_report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_report_value(item) for item in value]
    return value


class SourceHttpClient:
    def __init__(
        self,
        *,
        timeout: float = 15,
        retries: int = 2,
        max_redirects: int = 5,
        max_bytes: int = 2 * 1024 * 1024,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ):
        self.timeout = max(0.1, float(timeout))
        self.retries = max(0, int(retries))
        self.max_redirects = min(5, max(0, int(max_redirects)))
        self.max_bytes = max(1, int(max_bytes))
        self.headers = dict(headers or {})
        self.session = session or requests.Session()

    def _request_once(self, url: str) -> tuple[requests.Response, bytes]:
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers=self.headers,
            stream=True,
        )
        body = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > self.max_bytes:
                    raise SourceHttpError(f"response exceeds {self.max_bytes} byte limit")
        finally:
            response.close()
        return response, bytes(body)

    def _request_with_retries(self, url: str) -> tuple[requests.Response, bytes]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response, body = self._request_once(url)
                if response.status_code >= 500 and attempt < self.retries:
                    continue
                return response, body
            except (requests.RequestException, SourceHttpError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
        raise SourceHttpError(f"request failed: {type(last_error).__name__}")

    def fetch(self, url: str, *, stop_on_redirect: bool = False) -> FetchedResponse:
        requested_url = validate_http_url(url)
        current_url = requested_url
        visited = {current_url}
        redirects: list[str] = []

        while True:
            response, body = self._request_with_retries(current_url)
            status = response.status_code
            headers = {str(key): str(value) for key, value in response.headers.items()}
            if status not in REDIRECT_STATUSES:
                return FetchedResponse(requested_url, current_url, status, headers, body, tuple(redirects))

            location = response.headers.get("Location")
            if not location:
                raise SourceHttpError(f"HTTP {status} response is missing Location")
            next_url = validate_http_url(urljoin(current_url, location))
            redirects.append(next_url)
            if next_url in visited:
                raise SourceHttpError("redirect loop detected")
            if len(redirects) > self.max_redirects:
                raise SourceHttpError(f"redirect limit exceeded ({self.max_redirects})")
            if stop_on_redirect:
                return FetchedResponse(requested_url, next_url, status, headers, body, tuple(redirects))
            visited.add(next_url)
            current_url = next_url
