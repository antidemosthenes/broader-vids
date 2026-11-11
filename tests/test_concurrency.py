from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
import unittest
import sys
import subprocess
from unittest.mock import patch

from broader_vids.evidence.preparation_worker import record_preparation_receipt
from broader_vids.evidence.preparation_worker import prepare_bounded
from broader_vids.processes import start_owned
from broader_vids.preferences import set_profile
from broader_vids.recommendations.batch import create_batch, execute_batch
from broader_vids.recommendations.timing import recover_elapsed
from broader_vids.storage import Store


class ConcurrencyTests(unittest.TestCase):
    def test_killed_preparation_retains_elapsed_time_and_releases_its_lease(self):
        identifier = self.store.create(
            "youtube", {"kind": "evidence", "rubric_version": 5}, {}
        )

        def sleeping(command, **kwargs):
            return start_owned(
                [sys.executable, "-c", "import time;time.sleep(10)"], **kwargs
            )

        with (
            patch(
                "broader_vids.evidence.preparation_worker.start_owned",
                side_effect=sleeping,
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            prepare_bounded(
                self.store.root, identifier, None, 0.3, 0, None, threading.Event()
            )
        self.assertGreaterEqual(self.store.get(identifier)["elapsed"], 0.3)
        with self.store.lease(identifier):
            pass

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_delegated_lease_cannot_be_stolen_or_released_by_its_borrower(self):
        identifier = self.store.create("youtube", {}, {})
        other = Store(self.store.root)
        try:
            with self.store.lease(identifier) as owner:
                with other.lease(identifier, owner=owner):
                    pass
                with self.assertRaises(ValueError):
                    with other.lease(identifier):
                        pass
                with self.assertRaises(ValueError):
                    with other.lease(identifier, owner="incorrect-owner"):
                        pass
            with other.lease(identifier) as next_owner:
                self.assertNotEqual(next_owner, owner)
        finally:
            other.close()

    def test_receipts_preserve_intervening_assessments_and_each_other(self):
        identifier = self.store.create(
            "youtube",
            {"kind": "evidence"},
            {"resources": {"media_download_bytes": 12}, "assessments": {}},
        )
        stale = self.store.get(identifier)
        other = Store(self.store.root)
        current = other.get(identifier)
        current["state"]["assessments"]["augmented"] = {
            "preserve": "newer model result"
        }
        current["status"] = "assessed"
        other.save(current)
        other.close()
        barrier = threading.Barrier(2)

        def receipt(token):
            local = Store(self.store.root)
            try:
                barrier.wait(2)
                record_preparation_receipt(
                    local,
                    identifier,
                    token,
                    stale["state"]["resources"]["media_download_bytes"],
                )
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(receipt, token) for token in ("first", "second")]
            for future in futures:
                future.result()
        saved = self.store.get(identifier)
        self.assertEqual(saved["state"]["assessments"], current["state"]["assessments"])
        self.assertEqual(saved["status"], "assessed")
        self.assertEqual(saved["state"]["preparation_usage"], {"first": 0, "second": 0})

    def test_crash_recovery_never_resets_or_double_charges_elapsed_time(self):
        for now, expected in ((150, 52), (500, 100), (50, 100)):
            run = {
                "settings": {"max_seconds": 100},
                "elapsed": 3,
                "state": {"active_attempt": {"started_wall": 100, "prior_elapsed": 2}},
            }
            with patch(
                "broader_vids.recommendations.timing.time.time", return_value=now
            ):
                recover_elapsed(run)
                recover_elapsed(run)
            self.assertEqual(run["elapsed"], expected)
            self.assertTrue(run["state"]["elapsed_is_upper_bound"])
            self.assertNotIn("active_attempt", run["state"])

    def test_interrupt_during_submission_cancels_already_started_children(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(
            self.store,
            max_seconds=100,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        original_submit = ThreadPoolExecutor.submit
        submissions, observed = [], []
        started = threading.Event()

        def runner(local, child, *, runtime, **kwargs):
            started.set()
            observed.append(runtime.cancelled.wait(2))
            run = local.get(child)
            run.update(status="paused", reason="Cancelled fixture")
            local.save(run)
            return run

        def submit(executor, *args, **kwargs):
            submissions.append(None)
            if len(submissions) == 2:
                self.assertTrue(started.wait(2))
                raise KeyboardInterrupt()
            return original_submit(executor, *args, **kwargs)

        before = time.monotonic()
        with (
            patch.object(ThreadPoolExecutor, "submit", submit),
            self.assertRaises(KeyboardInterrupt),
        ):
            execute_batch(self.store, identifier, runner)
        self.assertEqual(observed, [True])
        self.assertLess(time.monotonic() - before, 2)
        saved = self.store.get(identifier)
        self.assertEqual(saved["status"], "paused")
        self.assertNotIn("active_attempt", saved["state"])

    def test_platform_pause_stops_its_sibling_and_preserves_the_first_cause(self):
        from broader_vids.recommendations.batch import batch_report

        set_profile(self.store, ["math"])
        cases = (
            ("youtube", "discovery", False),
            ("bilibili", "enrich", False),
            ("youtube", "assess", False),
            ("bilibili", "selection", True),
        )
        for platform, phase, crash in cases:
            with self.subTest(platform=platform, phase=phase, crash=crash):
                identifier = create_batch(
                    self.store,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
                entered = threading.Barrier(2)
                cause = f"Original {phase} failure"
                cancelled = []

                def runner(local, child, *, runtime, **kwargs):
                    run = local.get(child)
                    entered.wait(2)
                    if run["platform"] == platform:
                        run["state"]["phase"] = phase
                        run.update(status="paused", reason=cause)
                        local.save(run)
                        if crash:
                            raise ValueError(cause)
                    else:
                        cancelled.append(runtime.cancelled.wait(2))
                        run["state"]["checkpoint_marker"] = "retained"
                        run.update(status="paused", reason="Sibling cancellation")
                        local.save(run)
                    return run

                result = execute_batch(self.store, identifier, runner)
                self.assertEqual(cancelled, [True])
                self.assertEqual(result["status"], "paused")
                self.assertEqual(
                    result["state"]["stop_cause"],
                    {
                        "platform": platform,
                        "phase": phase,
                        "reason": cause,
                    },
                )
                self.assertIn(cause, result["reason"])
                report, _ = batch_report(self.store, identifier)
                self.assertEqual(report["stop_cause"], result["state"]["stop_cause"])
                self.assertIn(
                    cause,
                    (
                        self.store.root / "recommendations" / identifier / "digest.md"
                    ).read_text(encoding="utf-8"),
                )
                sibling = next(
                    child
                    for p, child in result["state"]["children"].items()
                    if p != platform
                )
                self.assertEqual(
                    self.store.get(sibling)["state"]["checkpoint_marker"], "retained"
                )

                def resume(local, child, *, runtime, **kwargs):
                    self.assertFalse(runtime.cancelled.is_set())
                    run = local.get(child)
                    run.update(status="completed", reason="Recovered fixture")
                    local.save(run)
                    return run

                resumed = execute_batch(self.store, identifier, resume)
                self.assertEqual(resumed["status"], "completed")
                self.assertNotIn("stop_cause", resumed["state"])

    def test_platform_pause_keeps_an_already_completed_sibling(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(
            self.store,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        completed = threading.Event()

        def runner(local, child, **kwargs):
            run = local.get(child)
            if run["platform"] == "youtube":
                run["state"]["phase"] = "published"
                run.update(status="completed", reason="Published fixture")
                local.save(run)
                completed.set()
            else:
                self.assertTrue(completed.wait(2))
                run.update(status="paused", reason="Bilibili fixture failure")
                local.save(run)
            return run

        result = execute_batch(self.store, identifier, runner)
        self.assertEqual(result["status"], "paused")
        youtube = self.store.get(result["state"]["children"]["youtube"])
        self.assertEqual(youtube["status"], "completed")
        self.assertEqual(youtube["state"]["phase"], "published")
        self.assertIn("Bilibili fixture failure", result["reason"])

    def test_partial_publication_does_not_cancel_the_other_platform(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(
            self.store,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        partial_reported = threading.Event()

        def report(local, child):
            if local.get(child)["platform"] == "bilibili":
                partial_reported.set()

        def runner(local, child, *, runtime, **kwargs):
            run = local.get(child)
            if run["platform"] == "bilibili":
                run.update(
                    status="partial", reason="Final list has fewer eligible videos"
                )
            else:
                self.assertTrue(partial_reported.wait(2))
                self.assertFalse(runtime.cancelled.is_set())
                run.update(status="completed", reason="Completed fixture")
            run["state"]["phase"] = "published"
            local.save(run)
            return run

        with patch(
            "broader_vids.recommendations.batch.write_recommendation_report",
            side_effect=report,
        ):
            result = execute_batch(self.store, identifier, runner)
        self.assertEqual(result["state"]["results"]["youtube"]["status"], "completed")
        self.assertEqual(result["state"]["results"]["bilibili"]["status"], "partial")
        self.assertNotIn("stop_cause", result["state"])

    def test_publication_rechecks_cancellation_after_acquiring_the_write_lock(self):
        from broader_vids.http import BudgetExhausted
        from broader_vids.recommendations.service import (
            create_recommendation,
            _publish_scored,
        )

        set_profile(self.store, ["math"])
        run = self.store.get(
            create_recommendation(
                self.store,
                "youtube",
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
        )
        connection = self.store.db
        cancelled = threading.Event()

        class Connection:
            def __enter__(self):
                return connection.__enter__()

            def __exit__(self, *args):
                return connection.__exit__(*args)

            def execute(self, sql, *args):
                result = connection.execute(sql, *args)
                if sql == "BEGIN IMMEDIATE":
                    cancelled.set()
                return result

        def check():
            if cancelled.is_set():
                raise BudgetExhausted("Sibling platform paused")

        with (
            patch.object(self.store, "db", Connection()),
            patch(
                "broader_vids.recommendations.service.round_learning", return_value={}
            ),
            patch("broader_vids.recommendations.service._publish_locked") as publish,
            self.assertRaisesRegex(BudgetExhausted, "Sibling platform paused"),
        ):
            _publish_scored(self.store, run, check)
        self.assertEqual(publish.call_count, 1)
        self.assertTrue(publish.call_args.kwargs["prepare_only"])
        self.assertFalse(connection.in_transaction)

    def test_completed_children_can_be_recovered_after_the_deadline(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(
            self.store,
            max_seconds=100,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        for child in run["state"]["children"].values():
            saved = self.store.get(child)
            saved.update(status="completed", reason="Completed fixture")
            self.store.save(saved)
        run["elapsed"] = 100
        self.store.save(run)
        result = execute_batch(
            self.store, identifier, lambda *a, **k: self.fail("New work started")
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(set(result["state"]["results"]), {"youtube", "bilibili"})
