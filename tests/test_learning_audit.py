from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.evaluation import evaluate, metrics, cluster_interval
from broader_vids.learning.features import capture, quality_features, quality_prior
from broader_vids.learning.feedback import save_detail
from broader_vids.learning.scoped import fit_scoped, quality_prediction
from broader_vids.learning.service import train, change_epoch
from broader_vids.preferences import set_profile, user_action
from broader_vids.storage import Store, utc_now
from test_learning import example
from test_recommendations import candidate


class LearningAuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.profile = set_profile(self.store, ["mathematics"])

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def cohort(self, skipped=None):
        skipped = skipped or {}
        self.keys = []
        for i in range(30):
            platform = "youtube" if i < 15 else "bilibili"
            item = {
                **candidate(i + 1, platform),
                "quality_evidence": example()["quality_evidence"],
            }
            if platform == "bilibili":
                item["video_id"] = "BV1" + "ABCDEFGHJKLMNPQR"[i - 15] + "11111111"
                item["canonical_id"] = "bilibili:" + item["video_id"] + ":p1"
                item["url"] = "https://www.bilibili.com/video/" + item["video_id"]
            key = item["canonical_id"]
            self.keys.append(key)
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO video_state VALUES(?,?,?,0,0,0,?)",
                    (key, platform, json.dumps(item), utc_now()),
                )
                self.store.db.execute(
                    "INSERT INTO calibration_items VALUES(?,?,?,?,0)",
                    (
                        key,
                        "audit-cohort",
                        "train" if i % 15 < 10 else "holdout",
                        json.dumps(
                            {
                                "features": capture(item),
                                "item": {
                                    k: item[k]
                                    for k in (
                                        "canonical_id",
                                        "platform",
                                        "title",
                                        "url",
                                    )
                                },
                                "created_at": utc_now(),
                                "after_action_id": 0,
                            }
                        ),
                    ),
                )
                if platform == "bilibili":
                    self.store.db.execute(
                        "INSERT INTO engagement_snapshots VALUES(?,?)",
                        (
                            key,
                            json.dumps(
                                {
                                    "provenance_status": "complete",
                                    "description": "",
                                    "declared_copyright": 1,
                                }
                            ),
                        ),
                    )
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i, key in enumerate(self.keys):
                for dimension in ("interest", "quality"):
                    if i not in skipped.get(dimension, set()):
                        user_action(
                            self.store, key, "up" if i % 2 else "down", kind=dimension
                        )

    def test_topic_anchor_survives_clear_and_current_profile_reopen(self):
        self.cohort()
        key = self.keys[0]
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            save_detail(self.store, key, "topic_interest", "low", target="networks")
            save_detail(self.store, key, "topic_interest", "clear")
        expected = list(self.store.db.execute("SELECT * FROM feedback_scopes"))[0][1]
        self.store.close()
        self.store = Store(self.directory.name)
        actual = self.store.db.execute(
            "SELECT payload FROM feedback_scopes"
        ).fetchone()[0]
        self.assertEqual(json.loads(actual), json.loads(expected))
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            save_detail(self.store, key, "topic_interest", "high", target="networks")
        self.assertEqual(
            self.store.db.execute("SELECT payload FROM feedback_scopes").fetchone()[0],
            actual,
        )

    def test_another_dislike_cannot_raise_interest_by_changing_its_topic_prior(self):
        row = {
            "id": "first",
            "dimension": "topic_interest",
            "action_id": 1,
            "split": "train",
            "target": "networks",
            "value": "low",
            "features": {"domains": ["ecology"]},
        }
        first = fit_scoped([row], self.profile)["groups"]["topic_interest"]["networks"]
        second = {
            **row,
            "id": "second",
            "action_id": 2,
            "features": {"domains": ["mathematics"]},
        }
        updated = fit_scoped([row, second], self.profile)["groups"]["topic_interest"][
            "networks"
        ]
        self.assertLess(updated["mean"], first["mean"])

    def test_cluster_resampling_does_not_treat_creator_repeats_as_new_groups(self):
        small = cluster_interval([0.2, 0.8], ["a", "b"])
        repeated = cluster_interval([0.2] * 10 + [0.8] * 10, ["a"] * 10 + ["b"] * 10)
        for left, right in zip(small, repeated):
            self.assertAlmostEqual(left, right)
        self.assertIsNone(cluster_interval([0.2] * 10, ["a"] * 10))
        self.assertEqual(metrics([(1, 0.8, 0.5)] * 10, ["a"] * 10)["creator_groups"], 1)

    def test_unknown_level_depth_does_not_certify_known_level_predictions(self):
        self.cohort()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i, key in enumerate(self.keys):
                save_detail(
                    self.store,
                    key,
                    "depth",
                    "deeper" if i % 2 else "suitable",
                    target="topology",
                    level="introductory" if i == 10 else "unknown",
                )
        result = evaluate(self.store)
        self.assertIn("depth", result["required_heads"])
        self.assertEqual(result["heads"]["depth"]["training_observations"], 0)
        self.assertEqual(result["heads"]["depth"]["held_out"]["n"], 1)
        self.assertFalse(result["heads"]["depth"]["passed"])

    def test_topic_prior_is_independent_of_row_order(self):
        rows = [
            {
                "id": str(i),
                "dimension": "topic_interest",
                "action_id": i,
                "split": "train",
                "target": "networks",
                "value": "low",
                "features": {"domains": domains},
            }
            for i, domains in enumerate((["mathematics"], ["ecology"]))
        ]
        self.assertEqual(
            fit_scoped(rows, self.profile)["groups"],
            fit_scoped(list(reversed(rows)), self.profile)["groups"],
        )

    def test_ordinal_prediction_retains_uncertainty_for_unseen_metadata_effects(self):
        features = capture(example())
        rows = [
            {
                "id": str(i),
                "dimension": "quality_level",
                "action_id": i,
                "split": "train",
                "value": "high" if i % 2 else "medium",
                "features": features,
            }
            for i in range(20)
        ]
        snapshot = train({"profile": self.profile, "ratings": [], "details": rows})
        ordinal = snapshot["structured"]["quality"]
        baseline = quality_prediction(features, ordinal)
        modified = {**features, "originality_indicators": ["new_metadata_effect"]}
        updated = quality_prediction(modified, ordinal)
        self.assertGreater(
            updated["stages"][0]["logit_variance"],
            baseline["stages"][0]["logit_variance"],
        )
        self.assertEqual(
            updated["stages"][0]["logit_mean"], baseline["stages"][0]["logit_mean"]
        )

    def test_unknown_originality_cannot_borrow_a_high_holistic_grade(self):
        item = example()
        item["quality_evidence"]["content_quality"] = 95
        item["quality_evidence"]["criteria"]["originality"] = {
            "status": "unknown",
            "grade": None,
        }
        features = capture(item)
        self.assertIsNone(features["criteria"]["originality"]["value"])
        high = deepcopy(features)
        high["criteria"]["originality"].update(value=1, assessed=True)
        self.assertLess(quality_prior(features), quality_prior(high))
        legacy = deepcopy(features)
        legacy["criteria"]["originality"]["value"] = 1
        self.assertEqual(quality_prior(features), quality_prior(legacy))
        self.assertEqual(quality_features(features)["missing:originality"], 1)

    def test_metadata_epoch_changes_for_provenance_but_not_view_counts(self):
        self.cohort()
        key = self.keys[0]
        metadata = {"views": 10, "description": ""}
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)",
                (key, json.dumps(metadata)),
            )
        initial = change_epoch(self.store)
        metadata["views"] = 20
        with self.store.db:
            self.store.db.execute(
                "UPDATE engagement_snapshots SET payload=? WHERE canonical_id=?",
                (json.dumps(metadata), key),
            )
        self.assertEqual(change_epoch(self.store), initial)
        metadata["description"] = "https://www.youtube.com/watch?v=GAkVaD1ihi4"
        with self.store.db:
            self.store.db.execute(
                "UPDATE engagement_snapshots SET payload=? WHERE canonical_id=?",
                (json.dumps(metadata), key),
            )
        self.assertNotEqual(change_epoch(self.store), initial)


if __name__ == "__main__":
    unittest.main()
