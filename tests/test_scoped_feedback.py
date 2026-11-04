import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.feedback import save_detail, details
from broader_vids.learning.features import capture
from broader_vids.learning.scoped import (
    category_posterior,
    interest_prediction,
    quality_prediction,
)
from broader_vids.learning.service import (
    ensure_model,
    source_data,
    train,
    score_components,
)
from broader_vids.learning.calibration import evaluate, finalize_ready_cohort
from broader_vids.preferences import set_profile, user_action, video_states
from broader_vids.storage import Store
from test_learning import example


class ScopedFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.profile = set_profile(self.store, ["mathematics", "music"])
        item = example()
        item["origins"] = [{"family_id": "topology", "domains": ["mathematics"]}]
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,0,?)",
                (item["canonical_id"], "youtube", json.dumps(item), "fixture"),
            )
        self.key = item["canonical_id"]
        self.features = capture(item)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def save(self, dimension, value, **options):
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            return save_detail(self.store, self.key, dimension, value, **options)

    def test_specific_dislike_does_not_train_parent_or_sibling_dislikes(self):
        self.save("topic_interest", "low", target="topology")
        snapshot = ensure_model(self.store)
        scoped = snapshot["structured"]
        self.assertEqual(snapshot["models"]["interest"]["observations"], 0)
        self.assertEqual(scoped["groups"]["domain_interest"], {})
        prediction = interest_prediction(self.features, 0.75, scoped)
        self.assertLess(prediction["probability"], 0.75)
        sibling = {
            **self.features,
            "canonical_id": "youtube:0000000002A",
            "topics": ["algebra"],
        }
        self.assertEqual(
            interest_prediction(sibling, 0.75, scoped)["probability"], 0.75
        )
        parent = {**sibling, "topics": []}
        self.assertEqual(interest_prediction(parent, 0.75, scoped)["probability"], 0.75)

    def test_explicit_broad_preference_and_specific_preference_are_independent(self):
        self.save("domain_interest", "high", target="mathematics")
        broad = ensure_model(self.store)["structured"]["groups"]["domain_interest"]
        self.save("topic_interest", "low", target="topology")
        scoped = ensure_model(self.store)["structured"]
        self.assertEqual(scoped["groups"]["domain_interest"], broad)
        self.assertEqual(
            interest_prediction(self.features, 0.75, scoped)["scope"], "topic_interest"
        )

    def test_depth_changes_suitability_without_changing_quality_or_domain(self):
        self.save("depth", "deeper", target="topology", level="introductory")
        snapshot = ensure_model(self.store)
        before, _ = score_components(
            self.features, self.profile, snapshot["models"], {}
        )
        after, _ = score_components(
            self.features,
            self.profile,
            snapshot["models"],
            {},
            structured=snapshot["structured"],
        )
        self.assertLess(after["interest"], before["interest"])
        self.assertEqual(after["quality"], before["quality"])
        self.assertEqual(snapshot["structured"]["groups"]["domain_interest"], {})
        advanced = {
            **self.features,
            "canonical_id": "youtube:0000000002A",
            "audience_level": "advanced",
        }
        advanced_prediction = interest_prediction(
            advanced, 0.75, snapshot["structured"]
        )
        self.assertGreater(advanced_prediction["probability"], 0.75)
        self.assertTrue(advanced_prediction["depth"]["directional_transfer"])

    def test_unknown_depth_is_saved_without_inventing_an_audience_level(self):
        self.save("depth", "deeper", target="topology")
        scoped = ensure_model(self.store)["structured"]
        self.assertEqual(scoped["pending_depth_level"], 1)
        self.assertEqual(
            interest_prediction(self.features, 0.75, scoped)["probability"], 0.75
        )

    def test_editing_one_dimension_never_duplicates_observations_or_erases_another(
        self,
    ):
        self.save("quality_level", "high")
        for value in ("low", "medium", "high", "low"):
            self.save("topic_interest", value, target="topology")
        snapshot = ensure_model(self.store)
        self.assertEqual(
            snapshot["structured"]["groups"]["topic_interest"]["topology"][
                "observations"
            ],
            1,
        )
        self.save("topic_interest", "clear")
        self.assertEqual(set(details(self.store)[self.key]), {"quality_level"})
        state = video_states(self.store)[self.key]
        self.assertFalse(state["watched"])
        self.assertFalse(state["dismissed"])

    def test_medium_is_a_real_ordinal_category_and_probabilities_normalize(self):
        rows = []
        for i in range(1000):
            value = "low" if i % 10 < 2 else "medium" if i % 10 < 8 else "high"
            rows.append(
                {
                    "id": str(i),
                    "dimension": "quality_level",
                    "action_id": i,
                    "split": "train",
                    "value": value,
                    "features": self.features,
                }
            )
        snapshot = train({"profile": self.profile, "ratings": [], "details": rows})
        ordinal = snapshot["structured"]["quality"]
        prediction = quality_prediction(self.features, ordinal)
        self.assertEqual(ordinal["observations"], 1000)
        self.assertEqual([m["observations"] for m in ordinal["models"]], [1000, 800])
        self.assertAlmostEqual(sum(prediction["probabilities"].values()), 1)
        self.assertGreater(prediction["probabilities"]["medium"], 0.5)
        self.assertAlmostEqual(prediction["probability"], 0.5, delta=0.06)

    def test_dirichlet_mean_recovers_frequency_and_variance_contracts(self):
        small = category_posterior([2, 6, 2], 0.9)
        large = category_posterior([20000, 60000, 20000], 0.9)
        self.assertAlmostEqual(large["mean"], 0.5, delta=0.0001)
        self.assertLess(large["sd"], small["sd"])
        self.assertTrue(all(p > 0 for p in large["probabilities"]))

    def test_holdout_structured_feedback_and_creator_peers_never_train(self):
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO calibration_items VALUES(?,?,?,?,0)",
                (
                    self.key,
                    "cohort",
                    "holdout",
                    json.dumps({"features": self.features}),
                ),
            )
        self.save("topic_interest", "low", target="topology")
        snapshot = ensure_model(self.store)
        self.assertEqual(snapshot["structured"]["groups"]["topic_interest"], {})
        self.assertEqual(source_data(self.store)["details"][0]["split"], "holdout")

    def test_abstentions_do_not_require_thirty_paired_votes(self):
        from test_learning import LearningStorageTests

        fixture = LearningStorageTests()
        fixture.store = self.store
        fixture.cohort()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i in range(1, 31):
                user_action(
                    self.store,
                    example(i)["canonical_id"],
                    "up" if i % 2 else "down",
                    kind="quality",
                )
                if i not in (1, 2, 3):
                    user_action(
                        self.store,
                        example(i)["canonical_id"],
                        "up" if i % 2 else "down",
                        kind="interest",
                    )
        report = evaluate(self.store)
        self.assertEqual(report["channel_videos"], {"interest": 27, "quality": 30})
        self.assertFalse(any("30 distinct" in reason for reason in report["reasons"]))
        self.assertTrue(finalize_ready_cohort(self.store))
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM feedback_ratings WHERE dimension='interest'"
            ).fetchone()[0],
            27,
        )

    def test_new_provenance_cannot_validate_or_train_stale_quality_features(self):
        item = example()
        evidence_id = self.store.create("youtube", {"kind": "evidence"}, {})
        item["quality_evidence"]["evidence_run"] = evidence_id
        frozen = {
            "item": {k: item[k] for k in ("canonical_id", "platform", "title", "url")},
            "features": capture(item),
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO calibration_items VALUES(?,?,?,?,0)",
                (self.key, "cohort", "train", json.dumps(frozen)),
            )
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)",
                (
                    self.key,
                    json.dumps(
                        {
                            "description": "https://www.youtube.com/watch?v=GAkVaD1ihi4",
                            "declared_copyright": 2,
                        }
                    ),
                ),
            )
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(self.store, self.key, "down", "Translated repost", "quality")
        snapshot = ensure_model(self.store)
        self.assertTrue(source_data(self.store)["ratings"][0]["quality_review_pending"])
        self.assertEqual(snapshot["models"]["quality"]["observations"], 0)
        self.assertTrue(
            any(
                "provenance-aware" in reason
                for reason in evaluate(self.store)["reasons"]
            )
        )
        self.assertEqual(
            video_states(self.store)[self.key]["ratings"]["quality"]["vote"], "down"
        )

    def test_audience_level_requires_evidence_and_does_not_change_quality_grade(self):
        from broader_vids.evidence.assessment import validate_card
        from test_evidence import card

        value = card()
        value["audience"] = {
            "level": "introductory",
            "confidence": "medium",
            "evidence_ids": ["C00001"],
            "rationale": "Defines prerequisite concepts before examples.",
        }
        assessed = validate_card(value, {"C00001"}, "baseline")
        self.assertEqual(assessed["content_quality"], card()["content_quality"])
        self.assertEqual(assessed["audience"]["level"], "introductory")
        value["audience"]["evidence_ids"] = ["C99999"]
        with self.assertRaises(ValueError):
            validate_card(value, {"C00001"}, "baseline")


if __name__ == "__main__":
    unittest.main()
