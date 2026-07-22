from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sources.http_client import redact_text
from workers.validate import SmokeResponse, smoke_url, validate_smoke_response


class CloudflareError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadedVersion:
    version_id: str
    preview_url: str


class CloudflareWorkersClient:
    API_ROOT = "https://api.cloudflare.com/client/v4"

    def __init__(self, account_id: str, api_token: str, *, timeout: float = 20,
                 api_session: requests.Session | None = None,
                 public_session: requests.Session | None = None):
        if not account_id or not api_token:
            raise CloudflareError("Cloudflare credentials are missing")
        self.account_id = account_id
        self.timeout = max(1.0, float(timeout))
        self.api_session = api_session or requests.Session()
        self.public_session = public_session or requests.Session()
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.4,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"))
        if api_session is None:
            self.api_session.mount("https://", HTTPAdapter(max_retries=retry))
        self.api_session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "User-Agent": "iptv-worker-sync/1",
        })
        self.public_session.headers.update({"User-Agent": "iptv-worker-smoke/1"})

    def _script_url(self, worker_name: str, suffix: str) -> str:
        return (
            f"{self.API_ROOT}/accounts/{quote(self.account_id, safe='')}/workers/scripts/"
            f"{quote(worker_name, safe='')}/{suffix.lstrip('/')}"
        )

    def _api_json(self, method: str, url: str, **kwargs):
        try:
            response = self.api_session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            detail = (redact_text(str(exc)) or "").replace(self.account_id, "***")
            raise CloudflareError(f"Cloudflare request failed: {detail}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudflareError(f"Cloudflare returned invalid JSON (HTTP {response.status_code})") from exc
        if response.status_code >= 400 or not payload.get("success", False):
            messages = [str(item.get("message")) for item in payload.get("errors", []) if item.get("message")]
            detail = "; ".join(messages[:3]) or f"HTTP {response.status_code}"
            safe_detail = (redact_text(detail) or "").replace(self.account_id, "***")
            raise CloudflareError(f"Cloudflare API error: {safe_detail}")
        return payload.get("result")

    def get_active_version(self, worker_name: str) -> str | None:
        result = self._api_json("GET", self._script_url(worker_name, "deployments"))
        deployments = result.get("deployments", []) if isinstance(result, dict) else (result or [])
        if not deployments:
            return None
        versions = deployments[0].get("versions") or []
        if not versions:
            return None
        active = max(versions, key=lambda item: float(item.get("percentage") or 0))
        return active.get("version_id")

    def upload_version(self, worker_name: str, content: bytes, *, preview_alias: str,
                       preview_url: str, message: str, tag: str) -> UploadedVersion:
        metadata = {
            "main_module": "worker.js",
            "annotations": {
                "workers/alias": preview_alias,
                "workers/message": message[:1000],
                "workers/tag": tag[:100],
            },
        }
        files = {
            "metadata": (None, json.dumps(metadata, ensure_ascii=False), "application/json"),
            "worker.js": ("worker.js", content, "application/javascript+module"),
        }
        result = self._api_json(
            "POST", self._script_url(worker_name, "versions"),
            params={"bindings_inherit": "strict"}, files=files,
        )
        version_id = str((result or {}).get("id") or "")
        if not version_id:
            raise CloudflareError("Cloudflare version response has no version ID")
        return UploadedVersion(version_id=version_id, preview_url=preview_url)

    def smoke_test(self, preview_url: str, rule: dict, *, attempts: int = 3) -> dict[str, object]:
        target = smoke_url(preview_url, rule.get("path", "/"))
        last_error: Exception | None = None
        attempt_count = max(1, min(5, int(attempts)))
        for attempt in range(attempt_count):
            try:
                response = self.public_session.get(
                    target, timeout=self.timeout, allow_redirects=False, stream=True,
                )
                body = bytearray()
                try:
                    for chunk in response.iter_content(65536):
                        if chunk:
                            body.extend(chunk)
                        if len(body) > 262144:
                            raise CloudflareError("smoke response too large")
                finally:
                    response.close()
                smoke = SmokeResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=bytes(body),
                    url=target,
                )
                return validate_smoke_response(smoke, rule)
            except (requests.RequestException, CloudflareError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempt_count:
                    time.sleep(1)
        raise CloudflareError(f"Worker smoke test failed: {redact_text(str(last_error))}")

    def deploy_version(self, worker_name: str, version_id: str, *, message: str) -> str:
        body = {
            "strategy": "percentage",
            "versions": [{"percentage": 100, "version_id": version_id}],
            "annotations": {
                "workers/message": message[:1000],
                "workers/triggered_by": "iptv-worker-sync",
            },
        }
        result = self._api_json("POST", self._script_url(worker_name, "deployments"), json=body)
        deployment_id = str((result or {}).get("id") or "")
        if not deployment_id:
            raise CloudflareError("Cloudflare deployment response has no deployment ID")
        return deployment_id
