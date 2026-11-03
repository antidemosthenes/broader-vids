import unittest
import math

from broader_vids.recommendations.personalization import (
    blended_screening_score,
    compile_screening_learning,
    screening_interest,
)
from broader_vids.recommendations.ranking import screen


def candidate():
    return {
        "canonical_id": "youtube:0000000001A",
        "platform": "youtube",
        "video_id": "0000000001A",
        "url": "https://www.youtube.com/watch?v=0000000001A",
        "title": "Example",
        "duration_seconds": 300,
        "origins": [{"domains": ["mathematics"]}],
        "engagement": {"views": 10000, "likes": 100},
    }


def learning(topic_mean=0.1, domain_mean=0.9):
    return {
        "structured": {
            "groups": {
                "topic_interest": {"quantum physics": {"mean": topic_mean}},
                "domain_interest": {"mathematics": {"mean": domain_mean}},
            },
            "video_topics": {},
            "video_levels": {},
        }
    }


class PersonalizedScreeningTests(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_screening_learning(learning())

    def test_unknown_candidate_stays_neutral(self):
        result = screening_interest({"title": "A talk"}, self.compiled)
        self.assertEqual(
            result, {"score": 50.0, "scope": "unknown", "matched_labels": []}
        )

    def test_explicit_low_topic_does_not_spill_into_broad_domain(self):
        result = screening_interest(
            {"title": "Quantum physics", "domains": ["mathematics"]},
            self.compiled,
        )
        self.assertEqual(result["scope"], "topic_interest")
        self.assertEqual(result["matched_labels"], ["quantum physics"])
        self.assertEqual(result["score"], 10)

    def test_domain_fallback_uses_only_explicit_metadata_domains(self):
        result = screening_interest(
            {
                "title": "An unrelated lecture",
                "origins": [{"domains": ["mathematics"]}],
            },
            self.compiled,
        )
        self.assertEqual(result["scope"], "domain_interest")
        self.assertEqual(result["score"], 90)

    def test_query_and_origin_text_are_not_topic_evidence(self):
        result = screening_interest(
            {
                "title": "A talk",
                "origins": [
                    {"query": "quantum physics", "family_id": "quantum physics"}
                ],
            },
            self.compiled,
        )
        self.assertEqual(result["scope"], "unknown")

    def test_verified_saved_video_topics_are_used(self):
        frozen = learning(0.2, 0.9)
        frozen["structured"]["groups"]["topic_interest"]["topology"] = {"mean": 0.8}
        frozen["structured"]["video_topics"]["video:1"] = [
            "quantum physics",
            "quantum physics",
            "topology",
        ]
        result = screening_interest(
            {"canonical_id": "video:1", "title": "unrelated"},
            compile_screening_learning(frozen),
        )
        self.assertEqual(result["scope"], "video_topics")
        self.assertEqual(result["score"], 50)

    def test_enriched_bilibili_tags_match_normalized_topics(self):
        compiled = compile_screening_learning(
            {
                "structured": {
                    "groups": {
                        "topic_interest": {
                            "quantum physics": {"mean": 0.1},
                            "topology": {"mean": 0.9},
                        }
                    }
                }
            }
        )
        result = screening_interest(
            {
                "title": "Quantum Physics, quantum physics",
                "engagement": {"tags": ["Quantum-Physics", "Topology", "topology"]},
            },
            compiled,
        )
        self.assertEqual(result["scope"], "topic_interest")
        self.assertEqual(result["matched_labels"], ["quantum physics", "topology"])
        self.assertEqual(result["score"], 50)

    def test_verified_video_level_applies_existing_topic_depth_fit(self):
        frozen = learning(0.2, 0.9)
        frozen["structured"]["video_topics"]["video:1"] = ["quantum physics"]
        frozen["structured"]["video_levels"]["video:1"] = "intermediate"
        frozen["structured"]["depth_models"] = {
            "quantum physics": {"predictions": {"intermediate": [0.01, 0.98, 0.01]}}
        }
        result = screening_interest(
            {"canonical_id": "video:1", "title": "unrelated"},
            compile_screening_learning(frozen),
        )
        self.assertGreater(result["score"], 20)

        from broader_vids.learning.depth import suitability

        models = {
            "quantum physics": {"predictions": {"intermediate": [0.01, 0.98, 0.01]}},
            "algebra": {"predictions": {"intermediate": [0.49, 0.02, 0.49]}},
        }
        self.assertEqual(
            suitability(
                models,
                ["quantum physics", "algebra", "quantum physics"],
                "intermediate",
            ),
            suitability(models, ["quantum physics", "algebra"], "intermediate"),
        )
        for probability in (0, 1):
            models["quantum physics"]["predictions"]["intermediate"] = [
                (1 - probability) / 2,
                probability,
                (1 - probability) / 2,
            ]
            fit = suitability(models, ["quantum physics"], "intermediate")
            self.assertTrue(math.isfinite(fit["log_odds_adjustment"]))

    def test_blend_is_twenty_percent_personalization(self):
        score, detail = blended_screening_score(
            80, {"title": "Quantum physics"}, self.compiled
        )
        self.assertEqual(score, 66)
        self.assertEqual(detail["base_score"], 80)
        self.assertEqual(detail["personalized_score"], 10)
        self.assertEqual(detail["blend"], 0.2)

    def test_screening_floors_remain_unchanged(self):
        low = candidate()
        low["duration_seconds"] = 20
        current, _ = screen(
            [low],
            [],
            {},
            {"interests": ["mathematics"]},
            policy_version=5,
            learning=self.compiled,
        )
        self.assertEqual(current, [])
        with self.assertRaisesRegex(ValueError, "Unsupported screening policy"):
            screen([low], [], {}, {"interests": ["mathematics"]}, policy_version=4)

    def test_policy_five_keeps_the_35_item_inspection_limit(self):
        candidates = []
        for index in range(40):
            item = candidate()
            video_id = f"{index + 1:010d}A"
            item.update(
                canonical_id=f"youtube:{video_id}",
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=f"Quantum physics lecture {index}",
            )
            candidates.append(item)
        selected, _ = screen(
            candidates,
            [],
            {},
            {"interests": ["mathematics"]},
            policy_version=5,
            learning={
                "structured": {
                    "groups": {"topic_interest": {"quantum physics": {"mean": 0.1}}}
                }
            },
        )
        self.assertEqual(len(selected), 35)
        self.assertEqual(
            [item["screening_score"] for item in selected],
            sorted((item["screening_score"] for item in selected), reverse=True),
        )


if __name__ == "__main__":
    unittest.main()
