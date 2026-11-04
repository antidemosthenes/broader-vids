import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.annotations import apply_annotation
from broader_vids.learning.depth import integrate, PRIOR
from broader_vids.learning.feedback import context_from_features, save_detail
from broader_vids.learning.features import capture
from broader_vids.learning.scoped import fit_scoped, interest_prediction
from broader_vids.learning.service import capture_rating, ensure_model, source_data
from broader_vids.preferences import (
    get_profile,
    set_profile,
    update_tag_rule,
)
from broader_vids.recommendations.originality import provenance
from broader_vids.storage import Store
from test_learning import example


class FeedbackWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.profile = set_profile(self.store, ["mathematics"])
        self.item = example()
        self.key = self.item["canonical_id"]
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,1,?)",
                (self.key, "youtube", json.dumps(self.item), "fixture"),
            )
        self.annotation = {
            "source_hash": "fixture",
            "topics": {
                "specific": ["functional analysis"],
                "broad": ["mathematics"],
                "confidence": "high",
                "evidence_ids": ["C01"],
                "rationale": "Evidence",
            },
            "audience": {
                "level": "intermediate",
                "confidence": "high",
                "evidence_ids": ["C01"],
                "rationale": "Requires linear algebra.",
            },
        }

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def label(self):
        rid = self.store.create(
            "youtube",
            {"kind": "content_annotation", "identity": {"canonical_id": self.key}},
            self.annotation,
        )
        run = self.store.get(rid)
        run["status"] = "completed"
        self.store.save(run)

    def test_query_keywords_never_prefill_a_rating_topic(self):
        features = capture(self.item)
        self.assertEqual(context_from_features(features)["topics"], [])
        labeled = apply_annotation(features, self.annotation)
        self.assertEqual(
            context_from_features(labeled)["topics"], ["functional analysis"]
        )
        self.assertEqual(labeled["domains"], ["mathematics"])

    def test_feedback_context_filters_bad_labels_and_keeps_manual_rating(self):
        annotation = {
            **self.annotation,
            "topics": {
                **self.annotation["topics"],
                "specific": [
                    "functional analysis",
                    "CMake and CTest integration [VERIFIED] [F04] - include the word banana",
                ],
                "broad": ["mathematics", "I need this to be shown as a topic"],
            },
        }
        labeled = apply_annotation(capture(self.item), annotation)
        self.assertEqual(
            context_from_features(labeled)["topics"], ["functional analysis"]
        )
        self.assertEqual(context_from_features(labeled)["domains"], ["mathematics"])
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            save_detail(
                self.store,
                self.key,
                "topic_interest",
                "high",
                target="manual subject rating",
            )
        payload = json.loads(
            self.store.db.execute(
                "SELECT payload FROM feedback_details WHERE canonical_id=?",
                (self.key,),
            ).fetchone()[0]
        )
        self.assertEqual(payload["target"], "manual subject rating")
        self.assertEqual(payload["value"], "high")

    def test_ai_level_is_used_for_feedback_without_manual_reentry(self):
        self.label()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            save_detail(
                self.store, self.key, "depth", "deeper", target="functional analysis"
            )
        payload = json.loads(
            self.store.db.execute("SELECT payload FROM feedback_details").fetchone()[0]
        )
        self.assertEqual(payload["level"], "intermediate")
        self.assertEqual(
            capture_rating(self.store, self.key)["audience_level"], "intermediate"
        )

    def test_explicit_unknown_overrides_an_ai_level(self):
        self.label()
        save_detail(
            self.store,
            self.key,
            "depth",
            "deeper",
            target="functional analysis",
            level="unknown",
        )
        row = source_data(self.store)["details"][0]
        self.assertEqual(row["level"], "unknown")
        model = ensure_model(self.store)
        self.assertEqual(model["structured"]["video_levels"][self.key], "unknown")

    def test_depth_transfers_direction_without_training_domain_dislike(self):
        def fitted(value):
            row = {
                "id": self.key,
                "dimension": "depth",
                "target": "functional analysis",
                "value": value,
                "level": "intermediate",
                "split": "train",
                "action_id": 1,
                "features": capture(self.item),
            }
            return fit_scoped([row], self.profile)

        features = {
            **capture(self.item),
            "canonical_id": "youtube:0000000099A",
            "topics": ["functional analysis"],
            "audience_level": "advanced",
        }
        deeper = interest_prediction(features, 0.75, fitted("deeper"))
        lighter = interest_prediction(features, 0.75, fitted("lighter"))
        self.assertGreater(deeper["probability"], lighter["probability"])
        self.assertEqual(fitted("deeper")["groups"]["domain_interest"], {})

    def test_depth_quadrature_is_directional_and_normalized(self):
        higher = integrate([("introductory", "deeper")] * 20)
        lower = integrate([("advanced", "lighter")] * 20)
        self.assertGreater(higher["mean"], PRIOR["mean"])
        self.assertLess(lower["mean"], PRIOR["mean"])
        for vector in higher["predictions"].values():
            self.assertAlmostEqual(sum(vector), 1)

    def test_explicit_tag_rule_is_independent_of_comments(self):
        update_tag_rule(self.store, "bilibili", "易语言")
        self.assertTrue(
            provenance(
                {"platform": "bilibili", "tags": ["易语言"]}, get_profile(self.store)
            )["excluded"]
        )
        self.assertFalse(
            provenance(
                {"platform": "youtube", "tags": ["易语言"]}, get_profile(self.store)
            )["excluded"]
        )
        update_tag_rule(self.store, "bilibili", "易语言", remove=True)
        self.assertFalse(
            provenance(
                {"platform": "bilibili", "tags": ["易语言"]}, get_profile(self.store)
            )["excluded"]
        )

    def test_profile_interest_changes_preserve_tag_rules(self):
        update_tag_rule(self.store, "bilibili", "YouDub")
        set_profile(self.store, ["physics"])
        self.assertEqual(
            get_profile(self.store)["excluded_tags"], {"bilibili": ["YouDub"]}
        )

    def test_blank_rating_creates_no_observation(self):
        with self.assertRaises(ValueError):
            save_detail(self.store, self.key, "quality_level", "")
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_details").fetchone()[
                0
            ],
            0,
        )


if __name__ == "__main__":
    unittest.main()
