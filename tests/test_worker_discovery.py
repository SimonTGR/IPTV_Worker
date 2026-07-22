import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

from sources.http_client import redact_url
from sources.base import SourceResult
from sources.http_playlist import HttpPlaylistAdapter
from sources.registry import SourceRegistry
from sources.worker_discovery import WorkerDiscoveryAdapter
import utils.channel as channel


class MockSourceHandler(BaseHTTPRequestHandler):
    counts = {}

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", content_type="text/plain", location=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        self.counts[path] = self.counts.get(path, 0) + 1
        if path == "/playlist":
            self._send(200, b"#EXTM3U\n#EXTINF:-1,CCTV-1\nhttp://media.test/cctv1.m3u8\n")
        elif path == "/txt":
            self._send(200, "广东卫视,http://media.test/gd.m3u8\n".encode())
        elif path == "/status":
            self._send(200, b"Worker IPTV")
        elif path == "/redirect-playlist":
            self._send(302, location="/playlist")
        elif path == "/base":
            self._send(302, location="http://183.10.180.69:202/?token=secret")
        elif path == "/loop-a":
            self._send(302, location="/loop-b")
        elif path == "/loop-b":
            self._send(302, location="/loop-a")
        elif path.startswith("/chain/"):
            index = int(path.rsplit("/", 1)[1])
            self._send(302, location=f"/chain/{index + 1}")
        elif path == "/html":
            self._send(200, b"<!doctype html><html>error</html>", "text/html")
        elif path == "/empty":
            self._send(200)
        else:
            self._send(404, b"missing")


class WorkerDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        MockSourceHandler.counts = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockSourceHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def source(self, path, **overrides):
        source = {
            "id": "worker-test",
            "type": "worker_discovery",
            "url": self.base_url + path,
            "priority": 80,
            "timeout": 2,
            "retries": 0,
            "max_redirects": 5,
            "refresh_each_run": True,
        }
        source.update(overrides)
        return source

    def test_http_playlist_follows_redirect_and_parses_m3u(self):
        adapter = HttpPlaylistAdapter({
            "id": "http-test",
            "type": "http_playlist",
            "url": self.base_url + "/redirect-playlist",
            "timeout": 2,
            "retries": 0,
        }, ".")
        result = adapter.collect()
        self.assertTrue(result.success)
        self.assertEqual("CCTV-1", result.candidates[0].canonical_name)
        self.assertEqual(1, result.metadata["redirect_count"])

    def test_worker_parses_200_m3u_and_txt(self):
        m3u = WorkerDiscoveryAdapter(self.source("/playlist"), ".").collect()
        txt = WorkerDiscoveryAdapter(self.source("/txt"), ".").collect()
        self.assertTrue(m3u.success)
        self.assertTrue(txt.success)
        self.assertEqual("playlist", m3u.metadata["response_kind"])
        self.assertEqual("广东卫视", txt.candidates[0].canonical_name)

    def test_status_text_builds_only_explicit_proxy_paths(self):
        source = self.source(
            "/status",
            response_mode="proxy",
            output_url="worker",
            channel_paths={"广东体育": "/hls/1/index.m3u8"},
        )
        result = WorkerDiscoveryAdapter(source, ".").collect()
        self.assertTrue(result.success)
        self.assertEqual("status_text", result.metadata["response_kind"])
        self.assertEqual(self.base_url + "/hls/1/index.m3u8", result.candidates[0].url)

    def test_302_dynamic_base_is_success_and_outputs_worker_proxy(self):
        source = self.source(
            "/base",
            response_mode="redirect_base",
            output_url="worker",
            channel_paths={"CCTV-1": "/tsfile/live/1005_1.m3u8?key=txiptv"},
        )
        result = WorkerDiscoveryAdapter(source, ".").collect()
        self.assertTrue(result.success)
        self.assertEqual("redirect_base", result.metadata["response_kind"])
        self.assertEqual(self.base_url + "/tsfile/live/1005_1.m3u8?key=txiptv", result.candidates[0].url)
        self.assertEqual("***", parse_qs(urlsplit(result.metadata["final_entry"]).query)["token"][0])

    def test_redirect_loop_and_limit_are_rejected(self):
        loop = WorkerDiscoveryAdapter(self.source("/loop-a"), ".").collect()
        chain = WorkerDiscoveryAdapter(self.source("/chain/0"), ".").collect()
        self.assertFalse(loop.success)
        self.assertIn("redirect loop", loop.errors[0])
        self.assertFalse(chain.success)
        self.assertIn("redirect limit", chain.errors[0])

    def test_html_and_empty_response_are_rejected(self):
        html = WorkerDiscoveryAdapter(self.source("/html"), ".").collect()
        empty = WorkerDiscoveryAdapter(self.source("/empty"), ".").collect()
        self.assertFalse(html.success)
        self.assertFalse(empty.success)

    def test_ttl_cache_and_single_forced_refresh(self):
        source = self.source(
            "/status",
            refresh_each_run=False,
            cache_ttl_seconds=300,
            channel_paths={"广东体育": "/hls/1/index.m3u8"},
        )
        first_adapter = WorkerDiscoveryAdapter(source, ".")
        first = first_adapter.collect()
        initial_count = MockSourceHandler.counts["/status"]
        state = {"metadata": first.metadata, "candidates": [asdict(item) for item in first.candidates]}
        cached_adapter = WorkerDiscoveryAdapter(source, ".", state)
        cached = cached_adapter.collect()
        self.assertTrue(cached.metadata["from_cache"])
        self.assertEqual(initial_count, MockSourceHandler.counts["/status"])
        self.assertTrue(cached_adapter.collect(force_refresh=True).success)
        self.assertFalse(cached_adapter.collect(force_refresh=True).success)

    def test_registry_isolates_failed_worker_from_file_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "local.txt").write_text("CCTV-1,http://local.test/live\n", encoding="utf-8")
            registry_path = root / "sources.json"
            registry_path.write_text(json.dumps({
                "schema_version": 1,
                "sources": [
                    {"id": "local", "type": "file_inbox", "paths": ["inbox/*.txt"]},
                    {"id": "bad-worker", "type": "worker_discovery", "url": self.base_url + "/html",
                     "timeout": 2, "retries": 0},
                ],
            }), encoding="utf-8")
            collection = SourceRegistry(registry_path, root).collect()
            self.assertEqual(1, len(collection.candidates))
            self.assertTrue(collection.results[0].success)
            self.assertFalse(collection.results[1].success)

    def test_redact_url_only_hides_sensitive_values(self):
        redacted = redact_url("https://example.test/live?token=abc&tk=short&quality=hd&sign=xyz")
        query = parse_qs(urlsplit(redacted).query)
        self.assertEqual("***", query["token"][0])
        self.assertEqual("***", query["tk"][0])
        self.assertEqual("hd", query["quality"][0])
        self.assertEqual("***", query["sign"][0])


class WorkerProbeRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_worker_probe_refreshes_once_and_retries(self):
        data = {
            "测试": {
                "CCTV-1": [{
                    "id": 1,
                    "url": "http://worker.test/live.m3u8",
                    "host": "worker.test",
                    "origin": "local",
                    "ipv_type": "ipv4",
                    "resolution": None,
                    "extra_info": "",
                    "source_id": "worker-test",
                    "source_type": "worker_discovery",
                }]
            }
        }
        probe = AsyncMock(side_effect=[
            {"speed": 0, "delay": -1, "resolution": None},
            {"speed": 2, "delay": 20, "resolution": "1920x1080"},
        ])
        refreshed = SourceResult("worker-test", "worker_discovery", success=True)
        async def passthrough(_self, grouped):
            return grouped

        with patch.object(channel, "get_speed", probe), \
             patch.object(channel, "refresh_configured_source_once", return_value=refreshed) as refresh, \
             patch.object(channel, "invalidate_speed_cache") as invalidate, \
             patch.object(channel, "check_ffmpeg_installed_status", return_value=False), \
             patch.object(type(channel.config), "probe_attempts", new_callable=PropertyMock, return_value=1), \
             patch.object(channel.ContentVerifier, "verify_grouped_results", passthrough), \
             patch.object(channel, "apply_stability_history", return_value={}), \
             patch.object(channel, "mark_url_bad"), \
             patch.object(channel, "mark_url_good"):
            result = await channel.test_speed(data, ipv6=True)

        self.assertEqual(2, probe.await_count)
        refresh.assert_called_once_with("worker-test")
        invalidate.assert_called_once()
        self.assertEqual(2, result["测试"]["CCTV-1"][0]["speed"])


if __name__ == "__main__":
    unittest.main()
