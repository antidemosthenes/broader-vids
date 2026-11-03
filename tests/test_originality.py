import unittest

from broader_vids.recommendations.engagement import parse_bilibili
from broader_vids.recommendations.originality import provenance, assessment_context
from broader_vids.recommendations.ranking import screen, rank_slate
from broader_vids.policy import Policy
from test_recommendations import candidate, quality


class OriginalityTests(unittest.TestCase):
    def test_combined_metadata_keeps_disclosures_and_archive_counts(self):
        value = {
            "code": 0,
            "data": {
                "View": {
                    "bvid": "BV1VbnR6eEbf",
                    "title": "【中配】Game development",
                    "desc": "YouDub\nhttps://www.youtube.com/watch?v=OfObWnrucRY",
                    "copyright": 2,
                    "stat": {"view": 100, "like": 10},
                    "pages": [],
                },
                "Tags": [{"tag_name": "YouDub"}],
            },
        }
        snapshot = parse_bilibili(value, "BV1VbnR6eEbf")
        evidence = provenance({"engagement": snapshot})
        self.assertEqual(snapshot["views"], 100)
        self.assertEqual(snapshot["tags_status"], "observed")
        self.assertEqual(
            {s["id"] for s in evidence["signals"]}, {"M2", "M3", "M4", "M5"}
        )
        self.assertFalse(evidence["excluded"])
        self.assertFalse(evidence["audio_detection_performed"])
        self.assertIsNotNone(assessment_context({"engagement": snapshot}))

    def test_exact_tag_blocks_fresh_and_pending_selection_without_eviction(self):
        items = [candidate(n, "bilibili") for n in range(1, 102)]
        from broader_vids.identity import identify

        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        for index, item in enumerate(items):
            item.update(
                identify(
                    "https://www.bilibili.com/video/BV1aaaaaaa"
                    + alphabet[index // 58]
                    + alphabet[index % 58]
                )
            )
        items[0]["engagement"]["tags"] = ["视频翻译"]
        items[1]["engagement"]["tags"] = ["视频翻译教程"]
        profile = {"interests": ["mathematics"]}
        shortlist, rejected = screen(items, [], {}, profile)
        self.assertEqual(rejected[items[0]["canonical_id"]], "excluded_translation_tag")
        self.assertFalse(provenance(items[1])["excluded"])
        shortlist, _ = screen(items, [items[0]], {}, profile)
        self.assertNotIn(
            items[0]["canonical_id"], {i["canonical_id"] for i in shortlist}
        )
        self.assertFalse(any(i["carryover"] for i in shortlist))
        observations = {
            i["canonical_id"]: quality(i["canonical_id"]) for i in shortlist
        }
        picks = rank_slate(shortlist, observations, profile, Policy().weights)
        self.assertNotIn(items[0]["canonical_id"], {i["canonical_id"] for i in picks})

    def test_source_links_and_dub_labels_are_indicators_not_proof(self):
        for item in (
            {"title": "中配 原创动画"},
            {"description": "https://youtu.be/OfObWnrucRY"},
            {"tags": ["ＹｏｕＤｕｂ"]},
        ):
            self.assertFalse(provenance(item)["excluded"])
            self.assertFalse(provenance(item)["authorship_verified"])
        fake = provenance(
            {"description": "https://youtube.com.evil.test/watch?v=OfObWnrucRY"}
        )
        self.assertFalse(fake["signals"])
        self.assertIsNone(assessment_context({"engagement": {"declared_copyright": 1}}))
