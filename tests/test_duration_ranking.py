import unittest
from unittest.mock import patch

from broader_vids.recommendations.ranking import CURRENT_RANKING_POLICY, rank_slate
from test_recommendations import candidate, quality


class DurationRankingTests(unittest.TestCase):
    def rank_one(
        self,
        duration,
        *,
        ranking_policy_version=CURRENT_RANKING_POLICY,
        learning=None,
        width=None,
        height=None,
        platform="youtube",
    ):
        item = candidate(1, platform)
        item["engagement_score"] = {"score": 50}
        item["ranking_adjustments"] = {"version": 2, "final_score": 1}
        observation = quality("fixture")
        observation.update(duration_seconds=duration, width=width, height=height)
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
        expected = {
            61: (9.9583, 19.9167),
            120: (7.5, 15.0),
            180: (5.0, 10.0),
            240: (2.5, 5.0),
            256.73: (1.8029, 3.6058),
            300: (0.0, 0.0),
            3600: (0.0, 0.0),
        }
        for duration, penalties in expected.items():
            for policy, penalty in (
                (2, penalties[0]),
                (3, penalties[1]),
                (CURRENT_RANKING_POLICY, penalties[1]),
            ):
                with self.subTest(duration=duration, policy=policy):
                    selected = self.rank_one(duration, ranking_policy_version=policy)
                    adjustment = selected["ranking_adjustments"]
                    self.assertEqual(adjustment["version"], 2)
                    self.assertEqual(adjustment["score_before_duration_penalty"], 80.0)
                    self.assertEqual(adjustment["short_video_penalty"], penalty)
                    self.assertEqual(adjustment["final_score"], selected["score"])
                    self.assertEqual(selected["score"], round(80 - penalty, 4))

    def test_portrait_penalty_uses_verified_dimensions_and_stacks_with_duration(self):
        dimensions = (
            (360, 640, 5),
            (640, 360, 0),
            (640, 640, 0),
            (None, 640, 0),
            (360, None, 0),
            (0, 640, 0),
            (True, 640, 0),
        )
        for platform in ("youtube", "bilibili"):
            for width, height, expected in dimensions:
                with self.subTest(platform=platform, width=width, height=height):
                    selected = self.rank_one(
                        600, width=width, height=height, platform=platform
                    )
                    adjustment = selected["ranking_adjustments"]
                    self.assertEqual(adjustment["portrait_video_penalty"], expected)
                    self.assertEqual(selected["score"], 80 - expected)
                    self.assertEqual(selected["quality_evidence"]["quality"], 80)
        for policy in (1, 2, 3):
            selected = self.rank_one(
                600, width=360, height=640, ranking_policy_version=policy
            )
            self.assertEqual(selected["score"], 80)
            self.assertNotIn(
                "portrait_video_penalty", selected.get("ranking_adjustments", {})
            )
        from broader_vids.policy import Policy

        item = candidate(1)
        item["engagement_score"] = {"score": 50}
        observation = {
            **quality("fixture"),
            "duration_seconds": 600,
            "width": 360,
            "height": 640,
        }
        scored = [
            rank_slate(
                [item],
                {item["canonical_id"]: observation},
                {"interests": ["mathematics"]},
                Policy().weights,
                ranking_policy_version=policy,
            )[0]
            for policy in (3, CURRENT_RANKING_POLICY)
        ]
        self.assertEqual(scored[0]["score"] - scored[1]["score"], 5)
        self.assertEqual(scored[0]["components"], scored[1]["components"])
        selected = self.rank_one(256.73, width=360, height=640, platform="bilibili")
        self.assertEqual(
            selected["ranking_adjustments"]["video_dimensions"],
            {"width": 360, "height": 640},
        )
        self.assertEqual(selected["ranking_adjustments"]["short_video_penalty"], 3.6058)
        self.assertEqual(selected["score"], 71.3942)

    def test_adjustment_follows_learned_score_and_is_not_reapplied(self):
        learning = {"profile": {}, "models": {}}
        for before, expected in ((73.25, 58.25), (4, 0)):
            with (
                self.subTest(before=before),
                patch("broader_vids.learning.features.capture", return_value={}),
                patch(
                    "broader_vids.learning.service.score_components", return_value={}
                ),
                patch(
                    "broader_vids.learning.service.final_score",
                    return_value=(before, {"quality": before}, {"base_score": before}),
                ) as final_score,
            ):
                selected = self.rank_one(180, learning=learning, width=360, height=640)
            self.assertEqual(final_score.call_count, 1)
            self.assertEqual(
                selected["ranking_adjustments"]["score_before_duration_penalty"], before
            )
            self.assertEqual(selected["ranking_adjustments"]["short_video_penalty"], 10)
            self.assertEqual(
                selected["ranking_adjustments"]["portrait_video_penalty"], 5
            )
            self.assertEqual(selected["score"], expected)
            self.assertEqual(selected["ranking_adjustments"]["final_score"], expected)

    def test_preview_uses_saved_duration_policy_and_interest_alignment(self):
        from contextlib import closing
        import tempfile
        from broader_vids.learning.alignment import fingerprint
        from broader_vids.preferences import set_profile
        from broader_vids.recommendations.preview import update_preview
        from broader_vids.recommendations.service import create_recommendation
        from broader_vids.storage import Store

        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["crime"])
            run = store.get(
                create_recommendation(
                    store,
                    "youtube",
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
            )
            self.assertEqual(
                run["settings"]["ranking_policy_version"], CURRENT_RANKING_POLICY
            )
            item = candidate(1)
            item["engagement_score"] = {"score": 50}
            observation = {
                **quality("fixture"),
                "duration_seconds": 180,
                "width": 360,
                "height": 640,
                "topics": {
                    "specific": ["Art theft"],
                    "broad": ["Art"],
                    "confidence": "high",
                },
            }
            key = item["canonical_id"]
            run["state"].update(shortlist=[item], evidence={key: observation})
            run["state"]["interest_alignment"] = {
                "records": {
                    key: {
                        "version": 1,
                        "matches": ["crime"],
                        "confidence": "high",
                        "fingerprint": fingerprint(item, observation, run["settings"]),
                    }
                }
            }
            for policy, penalty in (
                (1, 0),
                (2, 5),
                (3, 10),
                (CURRENT_RANKING_POLICY, 10),
            ):
                with (
                    self.subTest(policy=policy),
                    patch(
                        "broader_vids.recommendations.preview.cached_quality",
                        return_value=observation,
                    ),
                ):
                    run["settings"]["ranking_policy_version"] = policy
                    update_preview(store, run)
                selected = run["state"]["preview"][0]
                self.assertEqual(selected["interest_alignment"]["matches"], ["crime"])
                if policy >= 2:
                    adjustment = selected["ranking_adjustments"]
                    self.assertEqual(adjustment["short_video_penalty"], penalty)
                    self.assertAlmostEqual(
                        selected["score"],
                        adjustment["score_before_duration_penalty"]
                        - penalty
                        - (5 if policy >= 4 else 0),
                    )
                else:
                    self.assertNotIn("ranking_adjustments", selected)
            self.assertEqual(
                store.db.execute(
                    "SELECT COUNT(*) FROM recommendation_exposures"
                ).fetchone()[0],
                0,
            )

    def test_legacy_policy_preserves_score_and_shape(self):
        selected = self.rank_one(120, ranking_policy_version=1)
        self.assertEqual(selected["score"], 80.0)
        self.assertNotIn("ranking_adjustments", selected)
        with self.assertRaisesRegex(ValueError, "Unsupported ranking policy"):
            self.rank_one(120, ranking_policy_version=CURRENT_RANKING_POLICY + 1)


if __name__ == "__main__":
    unittest.main()
