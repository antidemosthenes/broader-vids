from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from opencc import OpenCC
from broader_vids.discovery import languages
from broader_vids.discovery.languages import QueryLanguageError, search_query
from broader_vids.discovery.sources import BilibiliDiscovery, YouTubeDiscovery
from broader_vids.exploration.proposals import query_fields
from test_discovery import card


class QueryLanguageTests(unittest.TestCase):
    def test_pairs_keep_the_same_fields_and_normalize_simplified_chinese(self):
        pair, anchors = query_fields(
            {
                "queries": {
                    "youtube": "Banach space applications in finance",
                    "bilibili": "巴拿赫空間 金融應用",
                },
                "anchors": ["Banach", "巴拿赫空間"],
            }
        )
        self.assertEqual(
            pair,
            {
                "youtube": "Banach space applications in finance",
                "bilibili": "巴拿赫空间 金融应用",
            },
        )
        self.assertEqual(anchors, ["Banach", "巴拿赫空间"])
        self.assertEqual(
            search_query("youtube", "Bézier curves π C++"), "Bézier curves π C++"
        )
        self.assertEqual(
            search_query("bilibili", "Python 機器學習 教學"), "Python 机器学习 教学"
        )

    def test_wrong_scripts_and_empty_keywords_are_rejected(self):
        for platform, query in [
            ("youtube", "数学 教学"),
            ("youtube", "ゲーム tutorial"),
            ("youtube", "математика tutorial"),
            ("bilibili", "physics tutorial"),
            ("bilibili", "ゲーム 教学"),
            ("youtube", ""),
            ("bilibili", "   "),
            ("youtube", "a" * 201),
        ]:
            with (
                self.subTest(platform=platform, query=query),
                self.assertRaises(QueryLanguageError),
            ):
                search_query(platform, query)

    def test_requests_use_validated_keywords_and_creator_names_remain_exact(self):
        http = Mock()
        http.get.return_value = card(1, creator=7)
        bili = BilibiliDiscovery(http)
        bili.search("線性代數 視頻", page=2)
        params = parse_qs(urlsplit(http.get.call_args.args[0]).query)
        self.assertEqual(params["keyword"], ["线性代数 视频"])
        self.assertEqual(params["page"], ["2"])
        found, _ = bili.creator("7", "3Blue1Brown")
        self.assertEqual(len(found), 1)
        self.assertEqual(
            parse_qs(urlsplit(http.get.call_args.args[0]).query)["keyword"],
            ["3Blue1Brown"],
        )
        http.post_json.return_value = {}
        with patch(
            "broader_vids.discovery.sources.youtube_page", return_value=([], None)
        ):
            YouTubeDiscovery(http).search("linear algebra applications", "fixture")
        self.assertEqual(
            http.post_json.call_args.args[1]["query"], "linear algebra applications"
        )
        http.reset_mock()
        with self.assertRaises(QueryLanguageError):
            YouTubeDiscovery(http).search("线性代数", "fixture")
        with self.assertRaises(QueryLanguageError):
            bili.search("linear algebra")
        http.get.assert_not_called()
        http.post_json.assert_not_called()

    def test_parallel_conversion_initializes_one_shared_dictionary(self):
        with (
            patch.object(languages, "_converter", None),
            patch("opencc.OpenCC", wraps=OpenCC) as constructor,
        ):
            with ThreadPoolExecutor(max_workers=8) as pool:
                result = list(
                    pool.map(
                        lambda _: search_query("bilibili", "數學 機率論"), range(32)
                    )
                )
            self.assertEqual(result, ["数学 机率论"] * 32)
            self.assertEqual(constructor.call_count, 1)
