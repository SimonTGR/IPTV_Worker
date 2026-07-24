import unittest
from collections import defaultdict

import utils.constants as constants
import utils.speed as speed
from utils.alias import Alias
from utils.channel import format_channel_data, get_channel_data_from_file
from utils.tools import get_name_value


class ExistingPipelineBaselineTests(unittest.TestCase):
    def test_m3u_parser_keeps_headers_and_token_query(self):
        content = """#EXTM3U
#EXTINF:-1 tvg-logo=\"logo.png\",CCTV-5
#EXTVLCOPT:http-user-agent=Player/1.0
https://example.test/live.m3u8?token=must-stay
"""
        result = get_name_value(content, constants.multiline_m3u_pattern, open_headers=True)
        self.assertEqual("https://example.test/live.m3u8?token=must-stay", result[0]["value"])
        self.assertEqual("Player/1.0", result[0]["headers"]["User-Agent"])

    def test_alias_does_not_confuse_cctv5_and_cctv5_plus(self):
        aliases = Alias()
        self.assertEqual("CCTV-5 体育", aliases.get_primary("CCTV5高清"))
        self.assertEqual("CCTV-5+ 体育赛事", aliases.get_primary("CCTV5+体育赛事"))
        self.assertEqual("翡翠台", aliases.get_primary("翡翠台 (Back up 1)"))
        self.assertEqual("翡翠台", aliases.get_primary("TVB Jade"))

    def test_hong_kong_aliases_match_template_names(self):
        aliases = Alias()
        expected = {
            "翡翠台 (Back up 1)": "翡翠台",
            "TVB Jade": "翡翠台",
            "珍珠台": "明珠台",
            "Pearl": "明珠台",
            "J 2": "TVB Plus",
            "J2": "TVB Plus",
            "iNews 互動新聞台": "无线新闻台",
            "TVB News": "无线新闻台",
            "VIUTV1": "viuTV",
            "ViuTV": "viuTV",
            "Viu6": "viuTV6",
            "ViuTV6": "viuTV6",
            "鳳凰中文": "凤凰中文",
            "鳳凰資訊": "凤凰资讯",
        }
        self.assertEqual(expected, {name: aliases.get_primary(name) for name in expected})

    def test_source_metadata_survives_legacy_channel_shape(self):
        result = format_channel_data({
            "url": "https://example.test/live.m3u8?token=keep",
            "headers": {"User-Agent": "Player/1.0"},
            "source_id": "forum-inbox",
            "source_type": "file_inbox",
            "source_priority": 50,
        }, "local")
        self.assertEqual("forum-inbox", result["source_id"])
        self.assertEqual({"User-Agent": "Player/1.0"}, result["headers"])
        self.assertIn("token=keep", result["url"])

    def test_template_alias_matches_canonical_source_name(self):
        channels = defaultdict(lambda: defaultdict(list))
        result = get_channel_data_from_file(
            channels,
            ["央视,#genre#\n", "CCTV-1 综合\n"],
            ({}, {}),
            [],
            {"CCTV-1": [{"url": "https://example.test/cctv1.m3u8", "source_id": "worker"}]},
        )
        item = result["央视"]["CCTV-1 综合"][0]
        self.assertEqual("worker", item["source_id"])

    def test_sort_dimensions_control_order(self):
        old_sort_by = speed.sort_by
        try:
            speed.sort_by = ["delay", "speed"]
            items = [
                {"url": "http://slow-delay", "ipv_type": "ipv4", "delay": 80, "speed": 9},
                {"url": "http://fast-delay", "ipv_type": "ipv4", "delay": 20, "speed": 1},
            ]
            result = speed.get_sort_result(items, supply=True, filter_speed=False, filter_resolution=False)
            self.assertEqual("http://fast-delay", result[0]["url"])
        finally:
            speed.sort_by = old_sort_by


if __name__ == "__main__":
    unittest.main()
