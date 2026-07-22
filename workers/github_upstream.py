from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sources.http_client import redact_text


class UpstreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpstreamFile:
    path: str
    blob_sha: str
    commit_sha: str
    content: bytes
    size: int
    sha256: str


class GitHubUpstreamClient:
    def __init__(self, owner: str, repository: str, ref: str, *, token: str | None = None,
                 timeout: float = 20, session: requests.Session | None = None):
        self.owner = owner
        self.repository = repository
        self.ref = ref
        self.timeout = max(1.0, float(timeout))
        self.session = session or requests.Session()
        retry = Retry(total=2, connect=2, read=2, status=2, backoff_factor=0.3,
                      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        if session is None:
            self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "iptv-worker-sync/1",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    @property
    def api_root(self) -> str:
        return f"https://api.github.com/repos/{quote(self.owner)}/{quote(self.repository)}"

    def _json_get(self, url: str, **params):
        try:
            response = self.session.get(url, params=params or None, timeout=self.timeout)
        except requests.RequestException as exc:
            raise UpstreamError(f"GitHub request failed: {redact_text(str(exc))}") from exc
        if response.status_code != 200:
            raise UpstreamError(f"GitHub returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError("GitHub returned invalid JSON") from exc

    def fetch_file(self, path: str) -> UpstreamFile:
        if not path or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
            raise UpstreamError("invalid upstream path")
        commit = self._json_get(f"{self.api_root}/commits/{quote(self.ref, safe='')}")
        commit_sha = str(commit.get("sha") or "")
        if not commit_sha:
            raise UpstreamError("GitHub commit response has no SHA")
        item = self._json_get(f"{self.api_root}/contents/{quote(path, safe='/')}", ref=commit_sha)
        if item.get("type") != "file" or item.get("path") != path:
            raise UpstreamError("upstream response is not the allowlisted file")
        if item.get("encoding") != "base64" or not item.get("content"):
            raise UpstreamError("upstream file content is unavailable")
        try:
            encoded = "".join(str(item["content"]).split())
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise UpstreamError("upstream file has invalid base64 content") from exc
        declared_size = int(item.get("size") or len(content))
        if declared_size != len(content):
            raise UpstreamError("upstream file size mismatch")
        blob_sha = str(item.get("sha") or "")
        if not blob_sha:
            raise UpstreamError("upstream file response has no blob SHA")
        return UpstreamFile(
            path=path,
            blob_sha=blob_sha,
            commit_sha=commit_sha,
            content=content,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
