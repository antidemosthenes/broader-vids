import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.annotations import apply_annotation, validate_topics
from broader_vids.learning.depth import integrate, PRIOR
from broader_vids.learning.feedback import context_from_features, save_detail
from broader_vids.learning.features import capture
from broader_vids.learning.migration import migrate_binary_feedback
from broader_vids.learning.scoped import fit_scoped, interest_prediction
from broader_vids.learning.service import capture_rating, ensure_model, source_data
from broader_vids.preferences import (
    get_profile,
    set_profile,
    user_action,
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

    def test_uncited_semantic_labels_are_rejected(self):
        topics = {**self.annotation["topics"], "evidence_ids": ["invented"]}
        with self.assertRaises(ValueError):
            validate_topics(topics, {"C01"})

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

    def test_pending_level_resolves_after_content_labeling(self):
        save_detail(
            self.store, self.key, "depth", "deeper", target="functional analysis"
        )
        self.label()
        row = source_data(self.store)["details"][0]
        self.assertEqual(row["level"], "intermediate")
        self.assertEqual(
            ensure_model(self.store)["structured"]["pending_depth_level"], 0
        )

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

    def test_known_level_uses_exact_categorical_counts(self):
        row = {
            "id": self.key,
            "dimension": "depth",
            "target": "functional analysis",
            "value": "suitable",
            "level": "intermediate",
            "split": "train",
            "action_id": 1,
            "features": capture(self.item),
        }
        scoped = fit_scoped([row], self.profile)
        features = {
            **capture(self.item),
            "topics": ["functional analysis"],
            "audience_level": "intermediate",
        }
        prediction = interest_prediction(features, 0.75, scoped)
        self.assertAlmostEqual(prediction["depth"]["suitability"], 2 / 3)
        self.assertFalse(prediction["depth"]["directional_transfer"])

    def test_depth_quadrature_is_directional_and_normalized(self):
        higher = integrate([("introductory", "deeper")] * 20)
        lower = integrate([("advanced", "lighter")] * 20)
        self.assertGreater(higher["mean"], PRIOR["mean"])
        self.assertLess(lower["mean"], PRIOR["mean"])
        for vector in higher["predictions"].values():
            self.assertAlmostEqual(sum(vector), 1)

    def test_binary_migration_maps_supported_dimensions_and_keeps_missing_fields_empty(
        self,
    ):
        self.label()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(self.store, self.key, "down", "Specific preference", "interest")
            user_action(self.store, self.key, "up", "Good quality", "quality")
            user_action(
                self.store, self.key, "down", "Unclear overall reason", "overall"
            )
        summary = migrate_binary_feedback(self.store)
        self.assertEqual(summary["mapped"], {"topic_interest": 1, "quality_level": 1})
        self.assertEqual(summary["unmapped_overall"], 1)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_ratings").fetchone()[
                0
            ],
            0,
        )
        rows = {
            r["dimension"]: json.loads(r["payload"])
            for r in self.store.db.execute("SELECT * FROM feedback_details")
        }
        self.assertEqual(rows["topic_interest"]["target"], "functional analysis")
        self.assertEqual(rows["topic_interest"]["value"], "low")
        self.assertEqual(rows["quality_level"]["value"], "high")
        self.assertNotIn("depth", rows)
        with closing(sqlite3.connect(Path(summary["backup"]))) as backup:
            self.assertEqual(
                backup.execute("SELECT COUNT(*) FROM feedback_ratings").fetchone()[0], 3
            )
        with self.assertRaises(ValueError):
            user_action(self.store, self.key, "up")
        self.assertEqual(migrate_binary_feedback(self.store), summary)
        self.assertEqual(
            ensure_model(self.store)["models"]["quality"]["observations"], 0
        )

    def test_migration_without_verified_topics_does_not_delete_votes(self):
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(self.store, self.key, "up", kind="interest")
        with self.assertRaises(ValueError):
            migrate_binary_feedback(self.store)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_ratings").fetchone()[
                0
            ],
            1,
        )

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

    def test_tag_rules_trim_deduplicate_and_keep_platforms_separate(self):
        update_tag_rule(self.store, "bilibili", " YouDub ")
        revision = get_profile(self.store)["revision"]
        update_tag_rule(self.store, "bilibili", "youdub")
        self.assertEqual(get_profile(self.store)["revision"], revision)
        self.assertEqual(
            get_profile(self.store)["excluded_tags"], {"bilibili": ["YouDub"]}
        )
        update_tag_rule(self.store, "youtube", "YouDub")
        update_tag_rule(self.store, "bilibili", "YOUDUB", remove=True)
        self.assertEqual(
            get_profile(self.store)["excluded_tags"],
            {"bilibili": [], "youtube": ["YouDub"]},
        )
        update_tag_rule(self.store, "bilibili", "视频翻译", remove=True)
        self.assertEqual(
            get_profile(self.store)["disabled_builtin_tags"], {"bilibili": ["视频翻译"]}
        )
        revision = get_profile(self.store)["revision"]
        update_tag_rule(self.store, "bilibili", "视频翻译", remove=True)
        self.assertEqual(get_profile(self.store)["revision"], revision)
        update_tag_rule(self.store, "bilibili", "视频翻译")
        self.assertEqual(
            get_profile(self.store)["disabled_builtin_tags"], {"bilibili": []}
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
