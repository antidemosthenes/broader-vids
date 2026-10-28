import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from broader_vids.blacklist import sync_blacklist
from broader_vids.creators import (
    creator_id,
    display_name,
    enrich,
    excluded,
    initialize_round,
    set_exclusion,
    snapshot,
    status,
    video_creator,
)
from broader_vids.learning.feedback import save_detail
from broader_vids.learning.service import ensure_model
from broader_vids.policy import Policy
from broader_vids.preferences import set_profile, pending_videos
from broader_vids.recommendations.ranking import screen, rank_slate
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
)
from broader_vids.recommendations.parallel import assess_parallel
from broader_vids.recommendations.reports import recommendation_report
from broader_vids.storage import Store
from test_recommendations import candidate, quality


CHANNEL = "UC" + "a" * 22


class Response:
    ok = True

    def __init__(self, data):
        self.data = data

    def json(self):
        return {"code": 0, "data": self.data}


class Browser:
    def __init__(self, pages, *, account="99", total=None, version=1):
        self.request = self
        self.pages, self.account, self.total, self.version = (
            pages,
            account,
            total,
            version,
        )
        self.calls = []
        self.nav_calls = 0

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.endswith("/nav"):
            self.nav_calls += 1
            account = (
                self.account(self.nav_calls) if callable(self.account) else self.account
            )
            return Response({"isLogin": True, "mid": account})
        number = int(parse_qs(urlsplit(url).query)["pn"][0])
        values = (
            self.pages(number) if callable(self.pages) else self.pages.get(number, [])
        )
        if isinstance(values, Exception):
            raise values
        payload = {"list": [{"mid": mid, "uname": f"Creator {mid}"} for mid in values]}
        if self.total is not None:
            payload["total"] = self.total
        if self.version is not None:
            payload["re_version"] = self.version
        return Response(payload)


class CreatorExclusionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(self.store, ["mathematics"])

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_identity_is_platform_specific_and_name_independent(self):
        set_exclusion(self.store, "youtube", CHANNEL, True, "Same name")
        policy = snapshot(self.store, "youtube")
        self.assertTrue(
            excluded(
                {
                    "platform": "youtube",
                    "creator_id": CHANNEL,
                    "creator_name": "Renamed",
                },
                policy,
            )
        )
        self.assertFalse(
            excluded(
                {
                    "platform": "youtube",
                    "creator_id": "UC" + "b" * 22,
                    "creator_name": "Same name",
                },
                policy,
            )
        )
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], [])
        self.assertFalse(excluded({"platform": "youtube"}, policy))
        self.assertEqual(creator_id("bilibili", "00123"), "123")
        with self.assertRaises(ValueError):
            creator_id("youtube", "Same name")

    def test_creator_rules_do_not_change_the_learning_model(self):
        before = ensure_model(self.store)
        set_exclusion(self.store, "youtube", CHANNEL, True)
        self.assertEqual(ensure_model(self.store)["id"], before["id"])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_details").fetchone()[
                0
            ],
            0,
        )

    def test_known_bans_apply_before_screening_and_final_selection(self):
        items = [candidate(1), candidate(2)]
        items[0]["creator_id"] = CHANNEL
        items[1]["creator_id"] = None
        set_exclusion(self.store, "youtube", CHANNEL, True)
        policy = snapshot(self.store, "youtube")
        selected, rejected = screen(
            items, [items[0]], {}, {"interests": ["mathematics"]}, policy
        )
        self.assertEqual(
            [item["canonical_id"] for item in selected], [items[1]["canonical_id"]]
        )
        self.assertEqual(rejected[items[0]["canonical_id"]], "excluded_creator")
        for item in items:
            item["engagement_score"] = {"score": 50}
        ranked = rank_slate(
            items,
            {item["canonical_id"]: quality("fixture") for item in items},
            {"interests": ["mathematics"]},
            Policy().weights,
            creator_rules=policy,
        )
        self.assertEqual(
            [item["canonical_id"] for item in ranked], [items[1]["canonical_id"]]
        )

    def test_metadata_owner_wins_and_pending_release_preserves_feedback(self):
        item = candidate(1)
        key = item["canonical_id"]
        item["creator_id"] = "UC" + "b" * 22
        item["engagement"]["creator_id"] = item["creator_id"]
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,1,'fixture')",
                (key, "youtube", json.dumps(item)),
            )
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)",
                (
                    key,
                    json.dumps(
                        {
                            "identity_verified": True,
                            "creator_id": CHANNEL,
                            "creator_name": "Verified",
                        }
                    ),
                ),
            )
        save_detail(self.store, key, "quality_level", "high")
        set_exclusion(self.store, "youtube", CHANNEL, True)
        self.assertEqual(len(pending_videos(self.store, "youtube")), 1)
        self.assertEqual(video_creator(self.store, key)["creator_id"], CHANNEL)
        run = self.store.get(create_recommendation(self.store, "youtube"))
        initialize_round(self.store, run, lambda: 60)
        self.assertEqual(pending_videos(self.store, "youtube"), [])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_details").fetchone()[
                0
            ],
            1,
        )
        self.assertEqual(
            tuple(
                self.store.db.execute(
                    "SELECT watched,dismissed FROM video_state"
                ).fetchone()
            ),
            (0, 0),
        )
        saved = run["state"]["creator_rules"]
        set_exclusion(self.store, "youtube", CHANNEL, False)
        initialize_round(self.store, run, lambda: 60)
        self.assertEqual(run["state"]["creator_rules"], saved)
        self.assertEqual(pending_videos(self.store, "youtube"), [])

    def test_legacy_round_does_not_adopt_new_rules(self):
        set_exclusion(self.store, "youtube", CHANNEL, True)
        run = self.store.get(create_recommendation(self.store, "youtube"))
        run["settings"].pop("creator_policy_version")
        initialize_round(self.store, run, lambda: 60)
        self.assertEqual(run["state"]["creator_rules"], {})

    def test_calibration_creator_resolves_an_existing_minimal_video_record(self):
        item = candidate(1)
        key = item["canonical_id"]
        save_detail(self.store, key, "quality_level", "high")
        payload = {
            "item": {"canonical_id": key, "platform": "youtube"},
            "features": {"platform": "youtube", "creator": CHANNEL},
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO calibration_items VALUES(?,'fixture','train',?,0)",
                (key, json.dumps(payload)),
            )
        self.assertEqual(video_creator(self.store, key)["creator_id"], CHANNEL)
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO discovery_registry VALUES('youtube','creator',?,?,'fixture','fixture')",
                (CHANNEL, json.dumps({"id": CHANNEL, "name": "Cached channel name"})),
            )
        self.assertEqual(video_creator(self.store, key)["name"], "Cached channel name")
        set_exclusion(self.store, "youtube", CHANNEL, True)
        self.assertEqual(
            status(self.store)["creators"]["youtube"][0]["name"], "Cached channel name"
        )
        self.assertEqual(
            display_name(self.store, "youtube", CHANNEL, "Current name"), "Current name"
        )
        self.assertEqual(display_name(self.store, "youtube", "UC" + "z" * 22), "")

    def test_new_owner_without_a_name_does_not_inherit_another_creators_name(self):
        item = {
            "platform": "youtube",
            "creator_id": "UC" + "b" * 22,
            "creator_name": "Previous owner",
        }
        enrich(item, {"identity_verified": True, "creator_id": CHANNEL})
        self.assertEqual(item["creator_id"], CHANNEL)
        self.assertEqual(item["creator_name"], "")

    def test_bilibili_sync_runs_once_and_a_resumed_round_keeps_its_snapshot(self):
        run = self.store.get(create_recommendation(self.store, "bilibili"))
        with patch(
            "broader_vids.blacklist.sync_blacklist",
            return_value={"status": "failed", "error": "Fixture access failure"},
        ) as sync:
            initialize_round(self.store, run, lambda: 60)
            set_exclusion(self.store, "bilibili", "17", True)
            initialize_round(self.store, run, lambda: 60)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(run["state"]["creator_rules"]["excluded_ids"], [])
        self.assertEqual(run["state"]["blacklist_sync"]["status"], "failed")

    def test_parallel_and_sequential_assessment_skip_excluded_creators(self):
        item = candidate(1)
        item["creator_id"] = CHANNEL
        set_exclusion(self.store, "youtube", CHANNEL, True)
        run = self.store.get(create_recommendation(self.store, "youtube"))
        initialize_round(self.store, run, lambda: 60)
        run["state"].update(phase="assess", shortlist=[item])
        worker = Mock(side_effect=AssertionError("Excluded creator reached a worker"))
        assess_parallel(
            self.store, run, lambda: self.store.save(run), lambda: 60, worker=worker
        )
        worker.assert_not_called()
        self.assertEqual(
            run["state"]["evidence"][item["canonical_id"]]["reason"], "excluded_creator"
        )
        run["state"].update(evidence={}, assessment_index=0)
        self.store.save(run)
        prepare, assess = Mock(), Mock()
        result = execute_recommendation(
            self.store, run["id"], prepare=prepare, assess=assess
        )
        prepare.assert_not_called()
        assess.assert_not_called()
        self.assertEqual(result["state"]["selected"], [])
        self.assertEqual(
            result["state"]["evidence"][item["canonical_id"]]["reason"],
            "excluded_creator",
        )
        report = recommendation_report(self.store, run["id"])
        self.assertNotIn("excluded_ids", report["creator_rules"])
        self.assertNotIn("account_id", report["creator_rules"])

    def test_failed_atomic_import_preserves_existing_rules(self):
        sync_blacklist(self.store, context=Browser({1: [7]}, total=1))
        self.store.db.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON creator_exclusions WHEN NEW.source='bilibili_blacklist' AND NEW.creator_id='8' BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        self.store.db.commit()
        self.assertEqual(
            sync_blacklist(self.store, context=Browser({1: [8]}, total=1))["status"],
            "failed",
        )
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["7"])

    def test_time_limit_keeps_old_copy_and_stops_requests(self):
        sync_blacklist(self.store, context=Browser({1: [7]}, total=1))
        browser = Browser({1: [8]}, total=1)
        original = browser.get
        clock = [0]

        def get(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] += 10
            return result

        browser.get = get
        with patch(
            "broader_vids.blacklist.time.monotonic", side_effect=lambda: clock[0]
        ):
            result = sync_blacklist(self.store, context=browser, max_seconds=15)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["requests"], 2)
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["7"])

    def test_complete_mirror_keeps_manual_rules_and_partitions_accounts(self):
        set_exclusion(self.store, "bilibili", "3", True)
        self.assertEqual(
            sync_blacklist(self.store, context=Browser({1: [1, 2]}, total=2))["status"],
            "complete",
        )
        self.assertEqual(
            snapshot(self.store, "bilibili")["excluded_ids"], ["1", "2", "3"]
        )
        sync_blacklist(self.store, context=Browser({1: [2]}, total=1))
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["2", "3"])
        sync_blacklist(self.store, context=Browser({1: [4]}, account="100", total=1))
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["3", "4"])
        sync_blacklist(self.store, context=Browser({1: []}, total=0))
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["3"])

    def test_multi_page_import_without_total_fetches_until_empty(self):
        browser = Browser({1: list(range(1, 51)), 2: [51, 52]}, version=None)
        result = sync_blacklist(self.store, context=browser)
        self.assertEqual((result["status"], result["count"]), ("complete", 52))
        self.assertEqual(len(snapshot(self.store, "bilibili")["excluded_ids"]), 52)

    def test_failed_partial_repeated_or_changed_pages_keep_previous_copy(self):
        sync_blacklist(self.store, context=Browser({1: [7]}, total=1))
        last_success = status(self.store)["bilibili_sync"]["last_success"]
        for browser in (
            Browser({1: [1], 2: [1]}),
            Browser({1: [1]}, total=2),
            Browser({1: [1], 2: ValueError("blocked")}),
        ):
            result = sync_blacklist(self.store, context=browser)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], ["7"])
            self.assertEqual(
                status(self.store)["bilibili_sync"]["last_success"], last_success
            )

    def test_new_account_failure_does_not_reuse_another_accounts_rules(self):
        sync_blacklist(self.store, context=Browser({1: [7]}, total=1))
        result = sync_blacklist(
            self.store, context=Browser({1: [1], 2: [1]}, account="100")
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(snapshot(self.store, "bilibili")["excluded_ids"], [])

    def test_request_limit_and_changed_account_preserve_saved_rules(self):
        sync_blacklist(self.store, context=Browser({1: [7]}, total=1))
        for browser, limit in (
            (Browser(lambda number: [number]), 5),
            (Browser({1: [8]}, account=lambda call: "99" if call == 1 else "100"), 100),
        ):
            result = sync_blacklist(self.store, context=browser, max_requests=limit)
            self.assertEqual(result["status"], "failed")
            self.assertLessEqual(result["requests"], limit)


if __name__ == "__main__":
    unittest.main()
