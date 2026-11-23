import unittest
from unittest.mock import patch

from broader_vids.learning.feedback import reconcile_current_reviews, save_detail
from broader_vids.preferences import pending_videos, user_action, video_states
from broader_vids.recommendations.service import _publish, create_recommendation
from broader_vids.storage import Store
import test_learning
from test_learning import example


class ReviewedWatchedTests(unittest.TestCase):
    setUp = test_learning.LearningStorageTests.setUp
    tearDown = test_learning.LearningStorageTests.tearDown
    cohort = test_learning.LearningStorageTests.cohort
    publication_fixture = test_learning.LearningStorageTests.publication_fixture

    def review(self, key):
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for dimension, value, target in (
                ("topic_interest", "high", "topology"),
                ("depth", "suitable", "topology"),
                ("quality_level", "high", ""),
                ("domain_interest", "high", "mathematics"),
            ):
                save_detail(self.store, key, dimension, value, target=target)

    def watched(self, key):
        return bool(video_states(self.store, identifiers=[key])[key]["watched"])

    def test_pre_rated_calibration_video_is_watched_on_publication(self):
        self.cohort()
        key = example()["canonical_id"]
        self.review(key)
        self.assertFalse(self.watched(key))
        run, observation = self.publication_fixture()
        with patch(
            "broader_vids.recommendations.service.cached_quality",
            return_value=observation,
        ):
            _publish(self.store, run)
        selected = {item["canonical_id"] for item in run["state"]["selected"]}
        self.assertIn(key, selected)
        self.assertTrue(self.watched(key))

    def test_recommendation_entrypoint_backfills_current_list_before_dashboard(self):
        keys = [example(i)["canonical_id"] for i in range(1, 4)]
        for key in keys:
            self.review(key)
        user_action(self.store, keys[1], "unwatched")
        with self.store.db:
            self.store.db.executemany(
                "UPDATE video_state SET recommended=1 WHERE canonical_id=?",
                [(key,) for key in keys],
            )
        self.assertEqual(
            {item["canonical_id"] for item in pending_videos(self.store, "youtube")},
            set(keys),
        )
        for day, items in enumerate(([example(3)], [example(1), example(2)]), 1):
            run_id = self.store.create(
                "youtube",
                {"kind": "recommendation"},
                {
                    "selected": items,
                    "preview": [],
                    "published_at": "fixture",
                    "phase": "published",
                },
            )
            self.store.db.execute(
                "UPDATE runs SET created_at=? WHERE id=?", (f"2026-01-0{day}", run_id)
            )
            run = self.store.get(run_id)
            run["status"] = "completed"
            self.store.save(run)
        create_recommendation(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        self.assertTrue(self.watched(keys[0]))
        self.assertFalse(self.watched(keys[1]))
        self.assertFalse(self.watched(keys[2]))
        self.assertNotIn(
            keys[0],
            {item["canonical_id"] for item in pending_videos(self.store, "youtube")},
        )
        user_action(self.store, keys[0], "unwatched")
        reconcile_current_reviews(self.store)
        other = Store(self.directory.name)
        try:
            self.assertFalse(
                video_states(other, identifiers=[keys[0]])[keys[0]]["watched"]
            )
        finally:
            other.close()

    def test_manual_unwatch_during_final_review_is_not_overwritten(self):
        key = example()["canonical_id"]
        self.store.db.execute(
            "UPDATE video_state SET recommended=1 WHERE canonical_id=?", (key,)
        )
        self.store.db.commit()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for dimension, value, target in (
                ("topic_interest", "high", "topology"),
                ("depth", "suitable", "topology"),
                ("quality_level", "high", ""),
            ):
                save_detail(self.store, key, dimension, value, target=target)
            from broader_vids.learning.service import capture_rating

            def unwatch_before_save(store, identifier):
                features = capture_rating(store, identifier)
                other = Store(self.directory.name)
                try:
                    user_action(other, key, "unwatched")
                finally:
                    other.close()
                return features

            with patch(
                "broader_vids.learning.service.capture_rating",
                side_effect=unwatch_before_save,
            ):
                save_detail(
                    self.store, key, "domain_interest", "high", target="mathematics"
                )
        self.assertFalse(self.watched(key))
