from contextlib import closing
import json
import tempfile
import unittest

from broader_vids.learning.feedback import save_detail
from broader_vids.learning.service import capture_rating
from broader_vids.preferences import set_profile, user_action, current_recommendation_id
from broader_vids.storage import Store
from broader_vids.web.server import dashboard_state
from test_learning import example


def make_round(store, platform="youtube", *, published=False, items=True, index=1):
    item = example(index)
    item.update(
        content_topics=["functional analysis"],
        audience_level="intermediate",
        creator_name="Example creator",
    )
    item["quality_evidence"].update(
        topics={"specific": ["functional analysis"], "broad": ["mathematics"]},
        audience_level="intermediate",
    )
    if platform == "bilibili":
        item.update(
            canonical_id="bilibili:BV1Z6H56oEcQ:p1",
            platform=platform,
            url="https://www.bilibili.com/video/BV1Z6H56oEcQ",
        )
    rid = store.create(
        platform,
        {"kind": "recommendation", "weights": {}},
        {
            "selected": [item] if published and items else [],
            "preview": [item] if not published and items else [],
            "published_at": "fixture" if published else None,
            "shortlist": [item],
            "evidence": {item["canonical_id"]: {"quality": 75}},
            "issues": [],
        },
    )
    run = store.get(rid)
    run["status"] = "completed" if published else "paused"
    store.save(run)
    return rid, item


class DashboardRoundTests(unittest.TestCase):
    def test_previews_are_visible_without_publication_or_read_side_effects(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics"])
            for platform in ("youtube", "bilibili"):
                make_round(store, platform)
            before = store.db.total_changes
            data = dashboard_state(store)
            self.assertEqual(store.db.total_changes, before)
            for platform, run in data["runs"].items():
                self.assertTrue(run["provisional"])
                self.assertEqual(len(run["selected"]), 1)
                key = run["selected"][0]["canonical_id"]
                self.assertIn(
                    "functional analysis",
                    data["videos"][key]["feedback_context"]["topics"],
                )
                self.assertFalse(data["videos"][key]["pending"])
                self.assertFalse(data["videos"][key]["watched"])
                self.assertEqual(data["pending_items"][platform], [])
            self.assertEqual(
                store.db.execute("SELECT count(*) FROM video_state").fetchone()[0], 0
            )
            self.assertEqual(
                store.db.execute(
                    "SELECT count(*) FROM recommendation_exposures"
                ).fetchone()[0],
                0,
            )

    def test_latest_visible_list_wins_and_empty_round_does_not_hide_it(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics"])
            published, _ = make_round(store, published=True)
            preview, _ = make_round(store)
            make_round(store, items=False)
            self.assertEqual(dashboard_state(store)["runs"]["youtube"]["id"], preview)
            newer, _ = make_round(store, published=True)
            make_round(store, items=False)
            data = dashboard_state(store)["runs"]["youtube"]
            self.assertEqual(data["id"], newer)
            self.assertFalse(data["provisional"])
            self.assertNotEqual(published, newer)
            queries = []
            store.db.set_trace_callback(queries.append)
            self.assertEqual(current_recommendation_id(store, "youtube"), newer)
            store.db.set_trace_callback(None)
            self.assertEqual(len(queries), 1)
            empty, _ = make_round(store, "bilibili", items=False)
            self.assertEqual(current_recommendation_id(store, "bilibili"), empty)
            run = store.get(newer)
            run["state"]["preview"] = [example(2)]
            store.save(run)
            data = dashboard_state(store)
            self.assertEqual(
                data["runs"]["youtube"]["selected"][0]["canonical_id"],
                example(1)["canonical_id"],
            )
            self.assertNotIn(example(2)["canonical_id"], data["videos"])

    def test_preview_feedback_keeps_features_and_does_not_create_exposures(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics"])
            rid, item = make_round(store)
            key = item["canonical_id"]
            user_action(store, key, "watched")
            features = capture_rating(store, key)
            self.assertEqual(features["audience_level"], "intermediate")
            self.assertIn("functional analysis", features["topics"])
            save_detail(
                store,
                key,
                "depth",
                "suitable",
                target="functional analysis",
                level="intermediate",
            )
            save_detail(store, key, "quality_level", "high")
            data = dashboard_state(store)
            self.assertTrue(data["videos"][key]["watched"])
            self.assertEqual(
                data["videos"][key]["details"]["depth"]["value"], "suitable"
            )
            saved = json.loads(
                store.db.execute(
                    "SELECT payload FROM video_state WHERE canonical_id=?", (key,)
                ).fetchone()[0]
            )
            self.assertEqual(saved["title"], item["title"])
            self.assertEqual(saved["quality_evidence"], item["quality_evidence"])
            self.assertEqual(
                store.db.execute(
                    "SELECT count(*) FROM recommendation_exposures"
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(store.get(rid)["state"]["published_at"])

    def test_rating_first_and_duplicate_lists_share_saved_feedback(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics"])
            _, item = make_round(store)
            key = item["canonical_id"]
            save_detail(
                store, key, "topic_interest", "high", target="functional analysis"
            )
            user_action(store, key, "dismiss")
            make_round(store)
            data = dashboard_state(store)
            self.assertEqual(
                data["videos"][key]["details"]["topic_interest"]["value"], "high"
            )
            self.assertTrue(data["videos"][key]["dismissed"])
            self.assertIn("functional analysis", capture_rating(store, key)["topics"])
            user_action(store, key, "restore")
            self.assertFalse(dashboard_state(store)["videos"][key]["dismissed"])

    def test_earlier_pending_and_newer_preview_keep_their_own_feedback_context(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics"])
            _, older = make_round(store, published=True)
            stale = example(2)
            for item in (older, stale):
                store.db.execute(
                    "INSERT INTO video_state VALUES(?,?,?,0,0,1,'fixture')",
                    (item["canonical_id"], "youtube", json.dumps(item)),
                )
            store.db.commit()
            rid, current = make_round(store, index=2)
            current["quality_evidence"]["audience_level"] = "advanced"
            run = store.get(rid)
            run["state"]["preview"] = [current]
            store.save(run)
            data = dashboard_state(store)
            self.assertIn(
                "functional analysis",
                data["videos"][older["canonical_id"]]["feedback_context"]["topics"],
            )
            self.assertEqual(
                data["videos"][current["canonical_id"]]["feedback_context"][
                    "audience_level"
                ],
                "advanced",
            )
            self.assertEqual(
                capture_rating(store, current["canonical_id"])["audience_level"],
                "advanced",
            )
            self.assertTrue(data["videos"][older["canonical_id"]]["pending"])
            self.assertEqual(
                data["videos"][older["canonical_id"]]["feedback_context"][
                    "audience_level"
                ],
                "intermediate",
            )
