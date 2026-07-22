import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from utils.speed import get_result


class HlsHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        # Deliberately unsupported: the probe must fall back to GET.
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/live.m3u8":
            body = (
                "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
                "#EXTINF:2.0,\n/one.ts\n#EXTINF:2.0,\n/two.ts\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        elif path in {"/one.ts", "/two.ts"}:
            body = b"x" * (256 * 1024)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
        else:
            body = b"missing"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SpeedIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), HlsHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    async def test_head_failure_falls_back_to_hls_get_and_estimates_bitrate(self):
        result = await get_result(self.base_url + "/live.m3u8", filter_resolution=False, timeout=3)
        self.assertTrue(result["playable"])
        self.assertGreater(result["download_speed_mbps"], 0)
        self.assertAlmostEqual(1048.576, result["bitrate_kbps"], places=1)
        self.assertTrue(result["bitrate_estimated"])
        self.assertIsNone(result["failure_reason"])

    async def test_http_error_has_reason_and_is_not_playable(self):
        result = await get_result(self.base_url + "/missing", filter_resolution=False, timeout=2)
        self.assertFalse(result["playable"])
        self.assertEqual("http_error", result["failure_reason"])


if __name__ == "__main__":
    unittest.main()
