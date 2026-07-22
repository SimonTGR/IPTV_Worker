import base64
import json
import tempfile
import unittest
from pathlib import Path

from workers.cloudflare import CloudflareError, CloudflareWorkersClient, UploadedVersion
from workers.github_upstream import GitHubUpstreamClient, UpstreamFile
from workers.sync import WorkerSynchronizer, load_manifest
from workers.validate import SmokeResponse, WorkerValidationError, validate_smoke_response, validate_worker_source


VALID_WORKER = b"export default { async fetch(request) { return new Response('ok'); } };"


class FakeResponse:
    def __init__(self, status=200, payload=None, body=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_content(self, chunk_size):
        yield self._body

    def close(self):
        pass


class FakeGitHubSession:
    def __init__(self, content=VALID_WORKER):
        self.headers = {}
        self.content = content
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append((url, params))
        if "/commits/" in url:
            return FakeResponse(payload={"sha": "commit-sha"})
        encoded = base64.b64encode(self.content).decode("ascii")
        encoded = encoded[:20] + "\n" + encoded[20:]
        return FakeResponse(payload={
            "type": "file", "path": "广东/gdty.js", "sha": "blob-sha",
            "size": len(self.content), "encoding": "base64", "content": encoded,
        })


class GitHubUpstreamTests(unittest.TestCase):
    def test_fetches_exact_allowlisted_path_at_resolved_commit(self):
        session = FakeGitHubSession()
        client = GitHubUpstreamClient("owner", "repo", "main", session=session)
        result = client.fetch_file("广东/gdty.js")
        self.assertEqual(VALID_WORKER, result.content)
        self.assertEqual("commit-sha", result.commit_sha)
        self.assertEqual("blob-sha", result.blob_sha)
        self.assertEqual({"ref": "commit-sha"}, session.urls[1][1])

    def test_rejects_path_traversal_before_network(self):
        session = FakeGitHubSession()
        client = GitHubUpstreamClient("owner", "repo", "main", session=session)
        with self.assertRaisesRegex(Exception, "invalid upstream path"):
            client.fetch_file("../secret")
        self.assertFalse(session.urls)


class ValidationTests(unittest.TestCase):
    def test_rejects_html_and_non_module_worker(self):
        with self.assertRaisesRegex(WorkerValidationError, "worker_source_is_html"):
            validate_worker_source(b"<html>404</html>", max_size=1024)
        with self.assertRaisesRegex(WorkerValidationError, "missing_module_fetch"):
            validate_worker_source(b"console.log('not a worker')", max_size=1024)

    def test_redirect_smoke_requires_http_location(self):
        rule = {"allowed_status": [302], "require_http_location_on_redirect": True}
        good = SmokeResponse(302, {"Location": "http://example.test/live"}, b"", "https://preview.test/")
        self.assertEqual(302, validate_smoke_response(good, rule)["status_code"])
        bad = SmokeResponse(302, {"Location": "file:///secret"}, b"", "https://preview.test/")
        with self.assertRaisesRegex(WorkerValidationError, "invalid_redirect"):
            validate_smoke_response(bad, rule)


class FakeApiSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET":
            return FakeResponse(payload={"success": True, "result": {
                "deployments": [{"versions": [{"percentage": 100, "version_id": "old-version"}]}]
            }})
        if url.endswith("/versions"):
            return FakeResponse(payload={"success": True, "result": {"id": "new-version"}})
        return FakeResponse(payload={"success": True, "result": {"id": "deployment-id"}})


class FakePublicSession:
    def __init__(self, status=200, body=b"Worker IPTV", headers=None):
        self.headers = {}
        self.status = status
        self.body = body
        self.response_headers = headers or {"Content-Type": "text/plain"}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, dict(self.headers)))
        return FakeResponse(self.status, body=self.body, headers=self.response_headers)


class FailingApiSession:
    def __init__(self):
        self.headers = {}

    def request(self, method, url, timeout=None, **kwargs):
        import requests
        raise requests.ConnectionError(f"failed at {url}")


class CloudflareClientTests(unittest.TestCase):
    def test_upload_smoke_then_deploy_uses_separate_public_session(self):
        api = FakeApiSession()
        public = FakePublicSession()
        client = CloudflareWorkersClient("account", "top-secret", api_session=api, public_session=public)
        self.assertEqual("old-version", client.get_active_version("iptv-gd"))
        uploaded = client.upload_version(
            "iptv-gd", VALID_WORKER, preview_alias="sync", preview_url="https://sync-iptv-gd.example.workers.dev/",
            message="test", tag="blob",
        )
        smoke = client.smoke_test(uploaded.preview_url, {"path": "/", "allowed_status": [200]})
        deployment = client.deploy_version("iptv-gd", uploaded.version_id, message="test")
        self.assertEqual("deployment-id", deployment)
        self.assertEqual(200, smoke["status_code"])
        self.assertNotIn("Authorization", public.headers)
        metadata = json.loads(api.calls[1][2]["files"]["metadata"][1])
        self.assertEqual("worker.js", metadata["main_module"])
        self.assertEqual("new-version", api.calls[2][2]["json"]["versions"][0]["version_id"])

    def test_failed_smoke_never_calls_deployment_api(self):
        api = FakeApiSession()
        public = FakePublicSession(status=500, body=b"error")
        client = CloudflareWorkersClient("account", "token", api_session=api, public_session=public)
        uploaded = client.upload_version(
            "iptv-gd", VALID_WORKER, preview_alias="sync", preview_url="https://sync-iptv-gd.example.workers.dev/",
            message="test", tag="blob",
        )
        with self.assertRaises(CloudflareError):
            client.smoke_test(uploaded.preview_url, {"path": "/", "allowed_status": [200]}, attempts=1)
        self.assertEqual(1, len(api.calls))

    def test_api_errors_do_not_disclose_account_id(self):
        client = CloudflareWorkersClient(
            "account-secret", "token-secret", api_session=FailingApiSession(),
            public_session=FakePublicSession(),
        )
        with self.assertRaises(CloudflareError) as raised:
            client.get_active_version("iptv-gd")
        self.assertNotIn("account-secret", str(raised.exception))
        self.assertNotIn("token-secret", str(raised.exception))


class FakeGitHubClient:
    def __init__(self, blob_sha="new-blob", content=VALID_WORKER):
        self.file = UpstreamFile(
            path="广东/gdty.js", blob_sha=blob_sha, commit_sha="commit-sha",
            content=content, size=len(content), sha256="content-sha",
        )

    def fetch_file(self, path):
        return UpstreamFile(
            path=path, blob_sha=self.file.blob_sha, commit_sha=self.file.commit_sha,
            content=self.file.content, size=self.file.size, sha256=self.file.sha256,
        )


class FakeCloudflareClient:
    def __init__(self, fail_smoke=False):
        self.fail_smoke = fail_smoke
        self.uploads = []
        self.deployments = []

    def get_active_version(self, worker_name):
        return "old-active"

    def upload_version(self, worker_name, content, **kwargs):
        self.uploads.append(worker_name)
        return UploadedVersion("new-version", kwargs["preview_url"])

    def smoke_test(self, preview_url, rule):
        if self.fail_smoke:
            raise CloudflareError("smoke failed")
        return {"status_code": 200}

    def deploy_version(self, worker_name, version_id, **kwargs):
        self.deployments.append((worker_name, version_id))
        return "deployment-id"


class WorkerSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(Path(__file__).resolve().parents[1] / "workers" / "manifest.json")

    def test_manifest_contains_confirmed_upstream_paths(self):
        self.assertEqual("广东/gdty.js", self.manifest["targets"]["guangdong"]["path"])
        self.assertEqual("潮州/chaozhou.js", self.manifest["targets"]["chaozhou"]["path"])

    def test_unchanged_sha_does_not_upload_or_deploy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            state.write_text(json.dumps({
                "schema_version": 1, "targets": {"guangdong": {"upstream_blob_sha": "same"}}
            }), encoding="utf-8")
            cloudflare = FakeCloudflareClient()
            sync = WorkerSynchronizer(
                self.manifest, state_path=str(state), report_path=str(root / "report.json"),
                github_client=FakeGitHubClient(blob_sha="same"), cloudflare_client=cloudflare,
            )
            report = sync.run(["guangdong"], deploy=True, mode="manual")
            self.assertTrue(report["success"])
            self.assertEqual("unchanged", report["targets"][0]["status"])
            self.assertFalse(cloudflare.uploads)
            self.assertFalse(cloudflare.deployments)

    def test_failed_smoke_preserves_old_active_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({
                "schema_version": 1,
                "targets": {"guangdong": {"upstream_blob_sha": "old-blob", "active_version": "old-active"}},
            }), encoding="utf-8")
            cloudflare = FakeCloudflareClient(fail_smoke=True)
            sync = WorkerSynchronizer(
                self.manifest, state_path=str(state_path), report_path=str(root / "report.json"),
                github_client=FakeGitHubClient(), cloudflare_client=cloudflare,
            )
            report = sync.run(["guangdong"], deploy=True, mode="manual")
            item = report["targets"][0]
            self.assertFalse(report["success"])
            self.assertEqual("failed", item["status"])
            self.assertEqual("old-active", item["previous_active_version"])
            self.assertFalse(cloudflare.deployments)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("old-active", state["targets"]["guangdong"]["active_version"])

    def test_success_activates_only_tested_version_and_triggers_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloudflare = FakeCloudflareClient()
            triggers = []
            sync = WorkerSynchronizer(
                self.manifest, state_path=str(root / "state.json"), report_path=str(root / "report.json"),
                github_client=FakeGitHubClient(), cloudflare_client=cloudflare,
                trigger_update=lambda: triggers.append(True) or True,
            )
            report = sync.run(["guangdong"], deploy=True, mode="manual")
            self.assertTrue(report["success"])
            self.assertEqual([("iptv-gd", "new-version")], cloudflare.deployments)
            self.assertEqual([True], triggers)
            self.assertEqual("triggered", report["playlist_update_trigger"])
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("new-version", state["targets"]["guangdong"]["active_version"])

    def test_auto_mode_is_refused_until_manifest_enables_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sync = WorkerSynchronizer(
                self.manifest, state_path=str(root / "state.json"), report_path=str(root / "report.json"),
                github_client=FakeGitHubClient(), cloudflare_client=FakeCloudflareClient(),
            )
            with self.assertRaisesRegex(Exception, "auto mode is disabled"):
                sync.run(["guangdong"], deploy=True, mode="auto")


if __name__ == "__main__":
    unittest.main()
