import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from utils.aggregator import ResultAggregator
from utils.publisher import parse_m3u, publish_candidate


def write_template(path: Path, groups):
    lines = []
    for group, names in groups.items():
        lines.append(f"{group},#genre#")
        lines.extend(names)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_m3u(path: Path, entries):
    lines = ["#EXTM3U"]
    for group, name, url in entries:
        lines.extend([f'#EXTINF:-1 group-title="{group}",{name}', url])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PublisherTests(unittest.TestCase):
    def test_atomic_publish_backs_up_previous_and_redacts_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            report = root / "report.json"
            last_good = root / "state" / "last_good_result.m3u"
            groups = {"央视频道": ["CCTV-1"], "广东频道": ["广东体育"], "卫视频道": ["广东卫视"]}
            write_template(template, groups)
            write_m3u(final, [
                ("央视频道", "CCTV-1", "https://old.test/cctv1"),
                ("广东频道", "广东体育", "https://old.test/gd"),
                ("卫视频道", "广东卫视", "https://old.test/sat"),
            ])
            previous = final.read_bytes()
            entries = [
                ("央视频道", "CCTV-1", "https://new.test/cctv1?token=secret"),
                ("广东频道", "广东体育", "https://new.test/gd"),
                ("卫视频道", "广东卫视", "https://new.test/sat"),
            ]
            write_m3u(candidate, entries)
            channel_data = {
                group: {name: [{
                    "url": url, "playable": True, "source_id": "fixture",
                    "download_speed_mbps": 2.5, "bitrate_kbps": 3000,
                    "resolution": "1920x1080", "delay_ms": 30, "success_ratio": 1,
                }]}
                for group, name, url in entries
            }

            result = publish_candidate(
                candidate, final_path=final, report_path=report, last_good_path=last_good,
                template_path=template, channel_data=channel_data,
                critical_groups=["央视频道", "广东频道", "卫视频道"],
            )

            self.assertTrue(result.published)
            self.assertEqual(previous, last_good.read_bytes())
            self.assertIn("new.test", final.read_text(encoding="utf-8"))
            self.assertFalse(candidate.exists())
            report_text = report.read_text(encoding="utf-8")
            self.assertNotIn("secret", report_text)
            report_data = json.loads(report_text)
            self.assertTrue(report_data["published"])
            self.assertEqual(1.0, report_data["validation"]["coverage"])
            self.assertEqual(2.5, report_data["channels"]["CCTV-1"]["selected"][0]["download_speed_mbps"])
            self.assertFalse(list(root.rglob("*.tmp.*")))

    def test_coverage_failure_preserves_formal_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            report = root / "report.json"
            write_template(template, {"测试": ["一", "二", "三"]})
            write_m3u(final, [("测试", "一", "https://old.test/one")])
            original = final.read_bytes()
            last_good = root / "last.m3u"
            last_good.write_bytes(b"existing-last-good")
            write_m3u(candidate, [("测试", "一", "https://new.test/one")])

            result = publish_candidate(
                candidate, final_path=final, report_path=report,
                last_good_path=last_good, template_path=template,
                min_coverage=0.70, critical_groups=[],
            )

            self.assertFalse(result.published)
            self.assertEqual(original, final.read_bytes())
            self.assertEqual(b"existing-last-good", last_good.read_bytes())
            self.assertFalse(candidate.exists())
            self.assertTrue(any(reason.startswith("coverage_below_threshold") for reason in result.reasons))
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["published"])

    def test_first_successful_publish_seeds_last_known_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            last_good = root / "state" / "last_good_result.m3u"
            write_template(template, {"测试": ["一"]})
            write_m3u(candidate, [("测试", "一", "https://new.test/one")])

            result = publish_candidate(
                candidate, final_path=final, report_path=root / "report.json",
                last_good_path=last_good, template_path=template,
                min_coverage=1, critical_groups=[],
            )

            self.assertTrue(result.published)
            self.assertEqual(final.read_bytes(), last_good.read_bytes())

    def test_abnormal_channel_drop_preserves_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            names = ["一", "二", "三", "四"]
            write_template(template, {"测试": names})
            write_m3u(final, [("测试", name, f"https://old.test/{index}") for index, name in enumerate(names)])
            original = final.read_bytes()
            write_m3u(candidate, [("测试", name, f"https://new.test/{index}") for index, name in enumerate(names[:3])])

            result = publish_candidate(
                candidate, final_path=final, report_path=root / "report.json",
                last_good_path=root / "last.m3u", template_path=template,
                min_coverage=0.50, max_drop_ratio=0.20, critical_groups=[],
            )

            self.assertFalse(result.published)
            self.assertEqual(original, final.read_bytes())
            self.assertIn("channel_drop_exceeded:0.2500", result.reasons)

    def test_invalid_candidate_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            write_template(template, {"测试": ["一"]})
            write_m3u(final, [("测试", "一", "https://old.test/one")])
            original = final.read_bytes()
            candidate.write_text("not an m3u", encoding="utf-8")

            result = publish_candidate(
                candidate, final_path=final, report_path=root / "report.json",
                last_good_path=root / "last.m3u", template_path=template,
                min_coverage=0, critical_groups=[],
            )

            self.assertFalse(result.published)
            self.assertEqual(original, final.read_bytes())
            self.assertIn("invalid_m3u_header", result.reasons)

    def test_parser_rejects_non_media_scheme(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "bad.m3u")
            write_m3u(path, [("测试", "一", "file:///secret")])
            with self.assertRaisesRegex(ValueError, "invalid_playlist_url_scheme"):
                parse_m3u(path)

    def test_unhandled_error_blocks_otherwise_valid_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "demo.txt"
            candidate = root / "candidate.m3u"
            final = root / "user_result.m3u"
            write_template(template, {"测试": ["一"]})
            write_m3u(candidate, [("测试", "一", "https://new.test/one")])

            result = publish_candidate(
                candidate, final_path=final, report_path=root / "report.json",
                last_good_path=root / "last.m3u", template_path=template,
                min_coverage=1, critical_groups=[], unhandled_errors=["unhandled_exception:test"],
            )

            self.assertFalse(result.published)
            self.assertFalse(final.exists())
            self.assertIn("unhandled_exception:test", result.reasons)


class StagingAggregatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregator_writes_only_candidate_when_output_path_is_set(self):
        item = {
            "url": "https://example.test/live", "host": "example.test", "origin": "local",
            "ipv_type": "ipv4", "playable": True, "download_speed_mbps": 2,
            "delay_ms": 20, "resolution": "1920x1080", "extra_info": "", "id": 1,
        }
        data = {"测试": {"频道": [item]}}
        aggregator = ResultAggregator(
            data, output_path="output/candidate.m3u", stat_logger=Mock(), result={},
        )
        with patch("utils.aggregator.write_channel_to_file") as writer:
            await aggregator._atomic_write_sorted_view(data)
        self.assertEqual("output/candidate.m3u", writer.call_args.kwargs["final_path"])
        self.assertFalse(writer.call_args.kwargs["include_auxiliary"])


if __name__ == "__main__":
    unittest.main()
