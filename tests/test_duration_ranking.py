import unittest
from unittest.mock import patch

from broader_vids.recommendations.ranking import rank_slate
from test_recommendations import candidate, quality


class DurationRankingTests(unittest.TestCase):
    def rank_one(self, duration, *, ranking_policy_version=2, learning=None):
        item = candidate(1)
        item["engagement_score"] = {"score": 50}
        observation = quality("fixture")
        observation["duration_seconds"] = duration
        weights = {
            "quality": 1.0,
            "interest": 0.0,
            "engagement": 0.0,
            "recency": 0.0,
            "diversity": 0.0,
        }
        return rank_slate(
            [item],
            {item["canonical_id"]: observation},
            {"interests": ["mathematics"]},
            weights,
            learning=learning,
            ranking_policy_version=ranking_policy_version,
        )[0]

    def test_duration_penalty_boundaries(self):
        expected = {61: 9.9583, 120: 7.5, 180: 5.0, 240: 2.5, 300: 0.0}
        for duration, penalty in expected.items():
            with self.subTest(duration=duration):
                selected = self.rank_one(duration)
                adjustment = selected["ranking_adjustments"]
                self.assertEqual(adjustment["version"], 2)
                self.assertEqual(adjustment["score_before_duration_penalty"], 80.0)
                self.assertEqual(adjustment["short_video_penalty"], penalty)
                self.assertEqual(adjustment["final_score"], selected["score"])
                self.assertEqual(selected["score"], round(80 - penalty, 4))

    def test_adjustment_follows_learned_score_and_is_not_reapplied(self):
        item = candidate(2)
        item["engagement_score"] = {"score": 50}
        observation = quality("fixture")
        observation["duration_seconds"] = 180
        learning = {"profile": {}, "models": {}}
        with (
            patch("broader_vids.learning.features.capture", return_value={}),
            patch("broader_vids.learning.service.score_components", return_value={}),
            patch(
                "broader_vids.learning.service.final_score",
                return_value=(73.25, {"quality": 73.25}, {"base_score": 73.25}),
            ) as final_score,
        ):
            selected = rank_slate(
                [item],
                {item["canonical_id"]: observation},
                {"interests": ["mathematics"]},
                {
                    "quality": 1.0,
                    "interest": 0.0,
                    "engagement": 0.0,
                    "recency": 0.0,
                    "diversity": 0.0,
                },
                learning=learning,
                ranking_policy_version=2,
            )[0]
        self.assertEqual(final_score.call_count, 1)
        self.assertEqual(
            selected["ranking_adjustments"]["score_before_duration_penalty"], 73.25
        )
        self.assertEqual(selected["ranking_adjustments"]["short_video_penalty"], 5.0)
        self.assertEqual(selected["score"], 68.25)
        self.assertEqual(
            selected["ranking_adjustments"]["final_score"], selected["score"]
        )

    def test_default_policy_preserves_legacy_score_and_shape(self):
        selected = self.rank_one(120, ranking_policy_version=1)
        self.assertEqual(selected["score"], 80.0)
        self.assertNotIn("ranking_adjustments", selected)


if __name__ == "__main__":
    unittest.main()
