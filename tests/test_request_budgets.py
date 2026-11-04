import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.cli.arguments import parser
from broader_vids.cli.startup import prepare_recommendation
from broader_vids.preferences import platform_lock, set_profile
from broader_vids.recommendations.batch import create_batch
from broader_vids.recommendations.budgets import (
    DEFAULT_METADATA_REQUESTS,
    increase_request_limit,
)
from broader_vids.recommendations.service import (
    create_recommendation,
)
from broader_vids.storage import Store


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
