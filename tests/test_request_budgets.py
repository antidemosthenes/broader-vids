import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from broader_vids.cli.arguments import parser
from broader_vids.cli.startup import prepare_recommendation
from broader_vids.http import BudgetExhausted, PublicHttp
from broader_vids.identity import identify
from broader_vids.preferences import platform_lock, set_profile
from broader_vids.recommendations.batch import create_batch
from broader_vids.recommendations.budgets import (
    DEFAULT_METADATA_REQUESTS,
    increase_request_limit,
    request_budget,
)
from broader_vids.recommendations.collection import collect_eligible_buffer
from broader_vids.recommendations.reports import recommendation_report
from broader_vids.recommendations.service import (
    create_recommendation,
    enrich_buffer,
    execute_recommendation,
)
from broader_vids.storage import Store, utc_now
from test_recommendations import candidate


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        set_profile(self.store, ["mathematics"])

    def run_id(self, platform="bilibili", **options):
        return create_recommendation(
            self.store,
            platform,
            workers=1,
            assessment_model="fixture-model",
            reasoning_effort="low",
            **options,
        )

    def seed_requests(self, identifier, count):
        with self.store.db:
            self.store.db.executemany(
                "INSERT INTO requests(run_id,created_at,endpoint,outcome) VALUES(?,?,?,'ok')",
                [
                    (
                        identifier,
                        "fixture",
                        "https://api.bilibili.com/x/web-interface/nav",
                    )
                ]
                * count,
            )

    def test_default_has_rejection_headroom_and_explicit_limits_are_preserved(self):
        self.assertEqual(DEFAULT_METADATA_REQUESTS, 320)
        self.assertEqual(
            parser().parse_args(["recommend", "bilibili"]).max_requests, 320
        )
        self.assertEqual(self.store.get(self.run_id())["settings"]["max_requests"], 320)
        saved = self.run_id(max_requests=160)
        self.assertEqual(self.store.get(saved)["settings"]["max_requests"], 160)
        self.assertIsNone(parser().parse_args(["continue", saved]).max_requests)
        batch = create_batch(
            self.store, assessment_model="fixture-model", reasoning_effort="low"
        )
        for child in self.store.get(batch)["state"]["children"].values():
            self.assertEqual(self.store.get(child)["settings"]["max_requests"], 320)

    def test_budget_extension_reuses_current_balanced_collection_work(
        self,
    ):
        identifier = self.run_id(max_requests=160)
        run = self.store.get(identifier)
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        pool = []
        for index in range(240):
            item = candidate(index + 1, "bilibili")
            item.update(
                identify(
                    "https://www.bilibili.com/video/BV1aaaaaaa"
                    + alphabet[index // 58]
                    + alphabet[index % 58]
                )
            )
            item["origins"] = [
                {
                    "domains": ["mathematics" if index % 4 else "ecology"],
                    "new_topic": index % 4 == 0,
                }
            ]
            item["creator_id"] = str(index + 1000)
            item["engagement"] = {
                "views": 1000,
                "likes": 0 if index < 57 else 100,
                "coins": 10,
                "favorites": 20,
            }
            pool.append(item)
        run["state"].update(phase="enrich", candidate_pool=pool)
        self.store.save(run)
        self.seed_requests(identifier, 5)
        fetched = []

        def snapshot(http, item):
            http.check()
            fetched.append(item["canonical_id"])
            request_id = http.store.begin_request(
                identifier, "https://api.bilibili.com/x/web-interface/view/detail"
            )
            http.store.end_request(request_id, "ok")
            return {
                **item["engagement"],
                "creator_id": item["creator_id"],
                "metadata_version": 2,
                "provenance_status": "complete",
                "observed_at": utc_now(),
                "pages": [{"page": 1, "cid": 1, "duration": 300}],
            }

        client = PublicHttp(self.store, run, anonymous_session=True)
        with self.assertRaises(BudgetExhausted):
            collect_eligible_buffer(
                self.store,
                run,
                client,
                lambda: 100,
                lambda: self.store.save(run),
                snapshot_collector=snapshot,
            )
        self.assertLess(len(run["state"]["buffer"]), 100)
        partial = len(run["state"]["buffer"])
        self.assertEqual(self.store.request_count(identifier), 160)
        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            side_effect=snapshot,
        ):
            paused = execute_recommendation(
                self.store,
                identifier,
                prepare=Mock(side_effect=AssertionError("Unexpected download")),
                assess=Mock(side_effect=AssertionError("Unexpected model call")),
            )
        self.assertEqual(paused["status"], "paused")
        self.assertIn("160/160", paused["reason"])
        self.assertIn(f"{partial}/100", paused["reason"])
        self.assertEqual(paused["state"]["shortlist"], [])
        before = copy.deepcopy(paused)
        changes = increase_request_limit(self.store, identifier, 320)
        self.assertEqual(changes[0]["requests_used"], 160)
        resumed = self.store.get(identifier)
        self.assertEqual(resumed["state"], before["state"])
        self.assertEqual(resumed["elapsed"], before["elapsed"])
        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            side_effect=snapshot,
        ):
            enrich_buffer(
                self.store,
                resumed,
                PublicHttp(self.store, resumed, anonymous_session=True),
                lambda: 100,
                lambda: self.store.save(resumed),
            )
        self.assertEqual(len(resumed["state"]["buffer"]), 100)
        self.assertEqual(len(resumed["state"]["shortlist"]), 35)
        self.assertEqual(resumed["state"]["phase"], "assess")
        self.assertEqual(len(set(fetched)), len(fetched))
        self.assertEqual(
            request_budget(self.store, resumed),
            {"used": len(fetched) + 5, "limit": 320, "remaining": 315 - len(fetched)},
        )
        self.assertEqual(
            recommendation_report(self.store, identifier)["request_budget"]["used"],
            len(fetched) + 5,
        )
        self.assertEqual(len(self.store.events(identifier)), 1)

    def test_batch_update_is_atomic_busy_safe_and_preserves_completed_checkpoints(self):
        batch = create_batch(
            self.store,
            max_requests=160,
            assessment_model="fixture-model",
            reasoning_effort="medium",
        )
        root = self.store.get(batch)
        children = root["state"]["children"]
        youtube = self.store.get(children["youtube"])
        youtube.update(status="completed", elapsed=120)
        youtube["state"]["phase"] = "published"
        self.store.save(youtube)
        bili = self.store.get(children["bilibili"])
        bili.update(status="paused", elapsed=209)
        bili["state"].update(
            phase="enrich", pool_index=155, creator_rules={"revision": 7}
        )
        self.store.save(bili)
        self.seed_requests(bili["id"], 160)
        before = {
            key: copy.deepcopy(self.store.get(key))
            for key in (batch, *children.values())
        }
        with (
            self.store.lease(platform_lock("youtube")),
            self.assertRaisesRegex(ValueError, "locked"),
        ):
            increase_request_limit(self.store, batch, 320)
        self.assertEqual(self.store.get(bili["id"])["settings"]["max_requests"], 160)
        changes = increase_request_limit(self.store, batch, 320)
        self.assertEqual([entry["run_id"] for entry in changes], [bili["id"]])
        for key, old in before.items():
            current = self.store.get(key)
            self.assertEqual(current["state"], old["state"])
            self.assertEqual(current["elapsed"], old["elapsed"])
            self.assertEqual(current["status"], old["status"])
            expected = (
                {**old["settings"], "max_requests": 320}
                if key == bili["id"]
                else old["settings"]
            )
            self.assertEqual(current["settings"], expected)
        self.assertEqual(increase_request_limit(self.store, batch, 320), [])
        for invalid in (True, 0, 1001, 319):
            with self.assertRaises(ValueError):
                increase_request_limit(self.store, batch, invalid)
        with self.assertRaisesRegex(ValueError, "Continue batch"):
            increase_request_limit(self.store, bili["id"], 500)
        other = create_batch(
            self.store,
            max_requests=160,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        ids = self.store.get(other)["state"]["children"]
        settings = {**self.store.get(ids["youtube"])["settings"], "max_requests": 400}
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=? WHERE id=?",
                (json.dumps(settings), ids["youtube"]),
            )
        with self.assertRaisesRegex(ValueError, "cannot reduce"):
            increase_request_limit(self.store, other, 320)
        self.assertEqual(
            self.store.get(ids["bilibili"])["settings"]["max_requests"], 160
        )
        self.assertEqual(self.store.events(ids["bilibili"]), [])

    def test_continuation_changes_limits_only_after_ready_and_when_explicit(self):
        identifier = self.run_id(max_requests=160)
        for report, option, expected in (
            (
                {"status": "blocked", "elapsed_seconds": 0},
                ["--max-requests", "320"],
                160,
            ),
            ({"status": "ready", "elapsed_seconds": 0}, [], 160),
            ({"status": "ready", "elapsed_seconds": 0}, ["--max-requests", "320"], 320),
        ):
            with patch("broader_vids.cli.startup.preflight", return_value=report):
                prepare_recommendation(
                    self.store, parser().parse_args(["continue", identifier, *option])
                )
            self.assertEqual(
                self.store.get(identifier)["settings"]["max_requests"], expected
            )
        source = self.store.create("bilibili", {"kind": "discovery"}, {})
        with self.assertRaisesRegex(ValueError, "recommendation"):
            prepare_recommendation(
                self.store,
                parser().parse_args(["continue", source, "--max-requests", "320"]),
            )
        with (
            patch("broader_vids.cli.startup.preflight") as check,
            self.assertRaises(ValueError),
        ):
            prepare_recommendation(
                self.store,
                parser().parse_args(["continue", identifier, "--max-requests", "1001"]),
            )
        check.assert_not_called()
