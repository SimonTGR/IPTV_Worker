from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests


class R2Error(RuntimeError):
    """A safe, credential-free error raised by the R2 client."""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise R2Error(f"missing required secret: {name}")
    return value


def _safe_key(key: str) -> str:
    raw = str(key).replace("\\", "/")
    if ".." in PurePosixPath(raw).parts:
        raise R2Error("invalid object key")
    value = str(PurePosixPath(raw)).lstrip("/")
    if not value or value == "." or value.startswith("../"):
        raise R2Error("invalid object key")
    return value


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


class R2Client:
    """Small S3-compatible Cloudflare R2 client using standard-library SigV4.

    The client deliberately supports only the object operations this project needs.
    It never prints request URLs, access keys, or authorization headers.
    """

    service = "s3"
    algorithm = "AWS4-HMAC-SHA256"

    def __init__(
        self,
        endpoint: str | None = None,
        bucket: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        *,
        region: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = (endpoint or _required_env("R2_ENDPOINT")).rstrip("/")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise R2Error("R2_ENDPOINT must be an HTTPS endpoint without query parameters")
        self.bucket = bucket or _required_env("R2_BUCKET")
        if "/" in self.bucket or not self.bucket:
            raise R2Error("invalid R2 bucket name")
        self.access_key_id = access_key_id or _required_env("R2_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or _required_env("R2_SECRET_ACCESS_KEY")
        self.region = region or os.getenv("R2_REGION", "auto")
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "R2Client":
        return cls()

    def _url(self, key: str = "", query: str = "") -> str:
        path = "/" + quote(self.bucket, safe="-_.~")
        if key:
            path += "/" + quote(_safe_key(key), safe="/-_.~")
        return f"{self.endpoint}{path}" + (f"?{query}" if query else "")

    def _authorization_headers(self, method: str, key: str, payload: bytes, query: str = "") -> dict[str, str]:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        parsed = urlsplit(self.endpoint)
        canonical_uri = "/" + quote(self.bucket, safe="-_.~")
        if key:
            canonical_uri += "/" + quote(_safe_key(key), safe="/-_.~")
        payload_hash = hashlib.sha256(payload).hexdigest()
        canonical_headers = (
            f"host:{parsed.netloc}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join([
            method.upper(), canonical_uri, query, canonical_headers,
            signed_headers, payload_hash,
        ])
        scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = "\n".join([
            self.algorithm, amz_date, scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        signing_key = _sign(
            _sign(_sign(_sign(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp), self.region), self.service),
            "aws4_request",
        )
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "Host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": (
                f"{self.algorithm} Credential={self.access_key_id}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }

    def _request(
        self,
        method: str,
        *,
        key: str = "",
        query: str = "",
        payload: bytes = b"",
        headers: dict[str, str] | None = None,
        expected: Iterable[int] = (200,),
    ) -> requests.Response:
        request_headers = self._authorization_headers(method, key, payload, query)
        request_headers.update(headers or {})
        try:
            response = self.session.request(
                method, self._url(key, query), data=payload, headers=request_headers, timeout=30,
            )
        except requests.RequestException as exc:
            raise R2Error(f"R2 request failed: {type(exc).__name__}") from exc
        if response.status_code not in set(expected):
            error_code = ""
            try:
                root = ElementTree.fromstring(response.content)
                candidate = (root.findtext(".//{*}Code") or "").strip()
                if candidate.isascii() and candidate.replace("_", "").isalnum() and len(candidate) <= 64:
                    error_code = f" ({candidate})"
            except ElementTree.ParseError:
                pass
            response.close()
            raise R2Error(f"R2 returned HTTP {response.status_code}{error_code}")
        return response

    def get_bytes(self, key: str) -> bytes | None:
        response = self._request("GET", key=_safe_key(key), expected=(200, 404))
        try:
            return None if response.status_code == 404 else response.content
        finally:
            response.close()

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        self._request(
            "PUT", key=_safe_key(key), payload=data,
            headers={"Content-Type": content_type}, expected=(200, 201),
        ).close()

    def download_file(self, key: str, destination: str | os.PathLike[str]) -> bool:
        content = self.get_bytes(key)
        if content is None:
            return False
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return True

    def upload_file(self, key: str, source: str | os.PathLike[str], *, content_type: str) -> None:
        path = Path(source)
        if not path.is_file():
            raise R2Error(f"local file is missing: {path.name}")
        self.put_bytes(key, path.read_bytes(), content_type=content_type)

    def list_keys(self, prefix: str) -> list[str]:
        safe_prefix = _safe_key(prefix).rstrip("/") + "/"
        query = "list-type=2&prefix=" + quote(safe_prefix, safe="/-_.~")
        response = self._request("GET", query=query, expected=(200,))
        try:
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                raise R2Error("R2 list response is invalid") from exc
        finally:
            response.close()
        return [node.text for node in root.findall(".//{*}Key") if node.text]
