import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import utils.channel as channel
import utils.speed as speed
from utils.content_verifier import ContentVerifier, KnownBadFingerprints, fingerprint_similarity
from utils.ffmpeg.probe import _parse_probe_data
from utils.stability import apply_stability_history


class ProbeMetricTests(unittest.TestCase):
    def test_ffprobe_bitrate_is_separate_from_download_speed(self):
        result = _parse_probe_data({
            "format": {"bit_rate": "4500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                 "avg_frame_rate": "25/1", "bit_rate": "4000000"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        })
        self.assertEqual(4500, result["bitrate_kbps"])
        self.assertNotIn("speed", result)

    def test_ffmpeg_text_parser_reports_media_bitrate_not_speed(self):
        parsed = speed.get_video_info(
            "Video: h264, 1920x1080, 25 fps\nAudio: aac\nvideo: 1000KiB audio: 100KiB time=00:00:02.00"
        )
        self.assertGreater(parsed["bitrate_kbps"], 4000)
        self.assertNotIn("speed", parsed)

    def test_attempts_produce_success_ratio_and_compatibility_aliases(self):
        result = speed.get_avg_result([
            {"playable": True, "download_speed_mbps": 4, "delay_ms": 20, "bitrate_kbps": 4000,
             "resolution": "1920x1080", "failure_reason": None},
            {"playable": False, "download_speed_mbps": 0, "delay_ms": -1, "resolution": None,
             "failure_reason": "timeout"},
        ])
        self.assertEqual(0.5, result["success_ratio"])
        self.assertTrue(result["playable"])
        self.assertEqual(result["speed"], result["download_speed_mbps"])
        self.assertEqual(result["delay"], result["delay_ms"])

    def test_attempts_preserve_unknown_download_speed(self):
        result = speed.get_avg_result([
            {"playable": True, "download_speed_mbps": None, "delay_ms": 20,
             "bitrate_kbps": 2500, "resolution": "1920x1080", "failure_reason": None},
        ])
        self.assertIsNone(result["download_speed_mbps"])
        self.assertTrue(result["playable"])
        self.assertTrue(channel.is_valid_speed_result(result))

    def test_unknown_speed_does_not_bypass_explicit_unplayable_state(self):
        result = {"playable": False, "download_speed_mbps": None, "delay_ms": 20}
        self.assertFalse(channel.is_valid_speed_result(result))

    def test_stability_history_tracks_failures_without_storing_url(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory, "history.json")
            url = "https://example.test/live.m3u8?token=secret"
            success = {"组": {"频道": [{"url": url, "playable": True, "source_id": "test"}]}}
            failure = {"组": {"频道": [{"url": url, "playable": False, "source_id": "test"}]}}
            apply_stability_history(success, str(state_path))
            apply_stability_history(failure, str(state_path))
            item = failure["组"]["频道"][0]
            self.assertEqual(0.5, item["success_ratio"])
            self.assertEqual(1, item["consecutive_failures"])
            self.assertNotIn(url, state_path.read_text(encoding="utf-8"))


class RealtimeProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rtsp_media_is_playable_without_fabricated_download_speed(self):
        data = {
            "url": "rtsp://example.test/live", "host": "example.test", "resolution": None,
            "ipv_type": "ipv4", "origin": "subscribe", "id": 1, "name": "test",
        }
        speed.invalidate_speed_cache(data)
        output = (
            "Video: h264, 1280x720, 25 fps\nAudio: aac\n"
            "video: 1000KiB audio: 100KiB time=00:00:02.00"
        )
        with patch.object(speed, "ffmpeg_url", AsyncMock(return_value=output)):
            result = await speed.get_speed(data, timeout=1)
        self.assertTrue(result["playable"])
        self.assertIsNone(result["download_speed_mbps"])
        self.assertGreater(result["bitrate_kbps"], 0)
        self.assertIsNone(result["failure_reason"])


class FakeFingerprinter:
    def __init__(self, values):
        self.values = values

    async def fingerprint_url(self, url, headers=None):
        return self.values.get(url)


class ContentVerificationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def item(url, host, **extra):
        return {
            "url": url,
            "host": host,
            "playable": True,
            "download_speed_mbps": 2,
            "delay_ms": 20,
            **extra,
        }

    async def test_three_unrelated_channels_with_same_fingerprint_are_rejected(self):
        fingerprint = "a" * 64
        grouped = {"组": {
            "频道一": [self.item("https://relay.test/1", "relay.test")],
            "频道二": [self.item("https://relay.test/2", "relay.test")],
            "频道三": [self.item("https://relay.test/3", "relay.test")],
        }}
        values = {item["url"]: {"fingerprint": fingerprint, "short_loop": False}
                  for items in grouped["组"].values() for item in items}
        verifier = ContentVerifier(FakeFingerprinter(values), known_bad=KnownBadFingerprints("missing.json"))
        await verifier.verify_grouped_results(grouped)
        reasons = [items[0]["failure_reason"] for items in grouped["组"].values()]
        self.assertEqual(["placeholder_fingerprint"] * 3, reasons)

    async def test_same_channel_backups_do_not_trigger_cross_channel_placeholder(self):
        fingerprint = "b" * 64
        items = [self.item(f"https://relay.test/{index}", "relay.test") for index in range(3)]
        grouped = {"组": {"同一频道": items}}
        values = {item["url"]: {"fingerprint": fingerprint, "short_loop": False} for item in items}
        await ContentVerifier(FakeFingerprinter(values), known_bad=KnownBadFingerprints("missing.json")).verify_grouped_results(grouped)
        self.assertTrue(all(item["content_verified"] for item in items))

    async def test_known_bad_and_unverified_relay_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            store = KnownBadFingerprints(str(Path(directory, "known.json")))
            store.add("c" * 64, "promotion")
            bad = self.item("https://direct.test/bad", "direct.test")
            unknown = self.item("https://live.catvod.com/?id=x&tk=y", "live.catvod.com")
            grouped = {"组": {"坏内容": [bad], "未知中转": [unknown]}}
            verifier = ContentVerifier(
                FakeFingerprinter({bad["url"]: {"fingerprint": "c" * 64, "short_loop": False}}),
                known_bad=store,
            )
            await verifier.verify_grouped_results(grouped)
            self.assertEqual("wrong_content", bad["failure_reason"])
            self.assertEqual("content_unverified", unknown["failure_reason"])
            self.assertFalse(bad["playable"])
            self.assertTrue(unknown["playable"])

    async def test_untrusted_short_loop_is_wrong_content(self):
        item = self.item("https://live.catvod.com/?id=loop", "live.catvod.com")
        grouped = {"组": {"频道": [item]}}
        values = {item["url"]: {"fingerprint": "d" * 64, "short_loop": True}}
        await ContentVerifier(FakeFingerprinter(values), known_bad=KnownBadFingerprints("missing.json")).verify_grouped_results(grouped)
        self.assertEqual("wrong_content", item["failure_reason"])

    def test_fingerprint_similarity_is_perceptual_hamming_ratio(self):
        base = "0" * 64
        one_bit = ("0" * 63) + "1"
        self.assertGreater(fingerprint_similarity(base, one_bit), 0.99)


class RankingTests(unittest.TestCase):
    def test_verified_content_wins_and_backup_host_is_diversified(self):
        old_sort = speed.sort_by
        try:
            speed.sort_by = ["playable", "content_verified", "stability", "download_speed", "url"]
            items = [
                {"url": "https://a.test/primary", "host": "a.test", "ipv_type": "ipv4", "playable": True,
                 "content_verified": True, "stability": 1, "download_speed_mbps": 10, "delay_ms": 10},
                {"url": "https://a.test/backup", "host": "a.test", "ipv_type": "ipv4", "playable": True,
                 "content_verified": True, "stability": 1, "download_speed_mbps": 9, "delay_ms": 10},
                {"url": "https://b.test/backup", "host": "b.test", "ipv_type": "ipv4", "playable": True,
                 "content_verified": True, "stability": 0.95, "download_speed_mbps": 8, "delay_ms": 10},
                {"url": "https://fast-unverified.test/live", "host": "fast-unverified.test", "ipv_type": "ipv4",
                 "playable": True, "content_verified": False, "stability": 1, "download_speed_mbps": 20,
                 "delay_ms": 5},
            ]
            ranked = speed.get_sort_result(items, supply=True, filter_speed=False, filter_resolution=False)
            self.assertEqual("https://a.test/primary", ranked[0]["url"])
            self.assertEqual("b.test", url_host(ranked[1]["url"]))
        finally:
            speed.sort_by = old_sort


def url_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname


if __name__ == "__main__":
    unittest.main()
