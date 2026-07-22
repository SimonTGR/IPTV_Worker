import json
import tempfile
import unittest
from pathlib import Path

from cloud.r2 import R2Client, R2Error
from cloud.workflow import WorkflowError, download_inputs, upload_published_outputs


class FakeResponse:
    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.content = content
        self.closed = False

    def close(self):
        self.closed = True


class FakeR2Session:
    def __init__(self):
        self.calls = []
        self.objects = {
            "input/pending_sources/demo.m3u": b"#EXTM3U\n",
            "state/source_state.json": b'{"schema_version": 1}\n',
        }

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, data, dict(headers or {})))
        if "list-type=2" in url:
            keys = "".join(f"<Contents><Key>{key}</Key></Contents>" for key in self.objects)
            return FakeResponse(content=(f"<ListBucketResult>{keys}</ListBucketResult>").encode())
        key = url.split("/bucket/", 1)[-1]
        if method == "GET":
            return FakeResponse(200, self.objects[key]) if key in self.objects else FakeResponse(404)
        if method == "PUT":
            self.objects[key] = data
            return FakeResponse(200)
        return FakeResponse(405)


def fake_client(session):
    return R2Client(
        "https://account.r2.cloudflarestorage.com", "bucket", "access-key", "secret-key",
        session=session,
    )


class R2ClientTests(unittest.TestCase):
    def test_put_is_sigv4_signed_without_leaking_secret(self):
        session = FakeR2Session()
        client = fake_client(session)
        client.put_bytes("output/user_result.m3u", b"#EXTM3U\n")
        method, url, _, headers = session.calls[-1]
        self.assertEqual("PUT", method)
        self.assertTrue(url.endswith("/bucket/output/user_result.m3u"))
        self.assertIn("AWS4-HMAC-SHA256", headers["Authorization"])
        self.assertNotIn("secret-key", headers["Authorization"])
        self.assertNotIn("secret-key", str(headers))

    def test_missing_object_returns_none(self):
        self.assertIsNone(fake_client(FakeR2Session()).get_bytes("state/missing.json"))

    def test_rejects_unsafe_object_key(self):
        with self.assertRaisesRegex(R2Error, "invalid object key"):
            fake_client(FakeR2Session()).get_bytes("../secret")
        with self.assertRaisesRegex(R2Error, "invalid object key"):
            fake_client(FakeR2Session()).get_bytes("output/../secret")


class WorkflowTests(unittest.TestCase):
    def test_download_inputs_uses_only_supported_extensions_and_restores_state(self):
        session = FakeR2Session()
        session.objects["input/pending_sources/ignore.exe"] = b"no"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(1, download_inputs(fake_client(session), root))
            self.assertEqual(b"#EXTM3U\n", (root / "config/pending_sources/demo.m3u").read_bytes())
            self.assertFalse((root / "config/pending_sources/ignore.exe").exists())
            self.assertTrue((root / "state/source_state.json").is_file())

    def test_unpublished_result_is_not_uploaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            (output / "report.json").write_text(json.dumps({"published": False}), encoding="utf-8")
            session = FakeR2Session()
            with self.assertRaisesRegex(WorkflowError, "R2 output remains unchanged"):
                upload_published_outputs(fake_client(session), root)
            self.assertFalse(any(method == "PUT" for method, *_ in session.calls))

    def test_published_result_uploads_playlist_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            state = root / "state"
            output.mkdir()
            state.mkdir()
            (output / "report.json").write_text(json.dumps({"published": True}), encoding="utf-8")
            (output / "user_result.m3u").write_text("#EXTM3U\n", encoding="utf-8")
            (state / "last_good_result.m3u").write_text("#EXTM3U\n", encoding="utf-8")
            session = FakeR2Session()
            upload_published_outputs(fake_client(session), root)
            uploaded = [url for method, url, *_ in session.calls if method == "PUT"]
            self.assertTrue(any(url.endswith("output/user_result.m3u") for url in uploaded))
            self.assertTrue(any(url.endswith("output/report.json") for url in uploaded))
