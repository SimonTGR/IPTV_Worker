from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit


class WorkerValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SmokeResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    url: str


def validate_worker_source(content: bytes, *, max_size: int) -> dict[str, object]:
    if not content:
        raise WorkerValidationError("empty_worker_source")
    if len(content) > max(1, int(max_size)):
        raise WorkerValidationError("worker_source_too_large")
    if b"\x00" in content:
        raise WorkerValidationError("worker_source_contains_nul")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerValidationError("worker_source_not_utf8") from exc
    prefix = text.lstrip()[:256].lower()
    if prefix.startswith(("<!doctype html", "<html")):
        raise WorkerValidationError("worker_source_is_html")
    if not re.search(r"\bexport\s+default\b", text) or not re.search(r"\bfetch\b", text):
        raise WorkerValidationError("worker_source_missing_module_fetch")
    return {
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "module_syntax": True,
    }


def smoke_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise WorkerValidationError("invalid_preview_url")
    if not path.startswith("/"):
        raise WorkerValidationError("invalid_smoke_path")
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def validate_smoke_response(response: SmokeResponse, rule: dict) -> dict[str, object]:
    allowed = {int(value) for value in rule.get("allowed_status", [200])}
    if response.status_code not in allowed:
        raise WorkerValidationError(f"smoke_http_status:{response.status_code}")
    if response.status_code in {301, 302, 307, 308} and rule.get("require_http_location_on_redirect", True):
        location = response.headers.get("Location") or response.headers.get("location") or ""
        target = urlsplit(urljoin(response.url, location))
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise WorkerValidationError("smoke_invalid_redirect_location")
    if response.status_code == 200:
        if rule.get("require_body_on_200", True) and not response.body:
            raise WorkerValidationError("smoke_empty_body")
        prefix = response.body.lstrip()[:256].lower()
        content_type = (response.headers.get("Content-Type") or response.headers.get("content-type") or "").lower()
        if rule.get("reject_html", True) and (
            "text/html" in content_type or prefix.startswith((b"<!doctype html", b"<html"))
        ):
            raise WorkerValidationError("smoke_html_response")
    return {
        "status_code": response.status_code,
        "location_present": bool(response.headers.get("Location") or response.headers.get("location")),
        "body_size": len(response.body),
    }
