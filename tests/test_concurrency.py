from concurrent.futures import ThreadPoolExecutor
import json
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
from broader_vids.http import PublicHttp, BudgetExhausted
from broader_vids.preferences import set_profile
from broader_vids.recommendations.batch import create_batch, execute_batch
from broader_vids.recommendations.timing import recover_elapsed
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
)
from broader_vids.evidence.media import EvidencePending
from broader_vids.storage import Store


class ConcurrencyTests(unittest.TestCase):
    def test_resume_does_not_repeatedly_subtract_shutdown_headroom(self):
        set_profile(self.store, ["math"])
        identifier = create_recommendation(self.store, "youtube", max_seconds=100)
        run = self.store.get(identifier)
        run["state"]["phase"] = "assess"
        self.store.save(run)
        with patch(
            "broader_vids.recommendations.parallel.assess_parallel",
            side_effect=EvidencePending("Fixture pause", retryable=True),
        ):
            for _ in range(3):
                saved = execute_recommendation(self.store, identifier, max_seconds=50)
                self.assertEqual(saved["status"], "paused")
                self.assertEqual(saved["settings"]["max_seconds"], 100)
                self.assertNotIn("active_attempt", saved["state"])

    def test_killed_preparation_retains_elapsed_time_and_releases_its_lease(self):
        identifier = self.store.create("youtube", {"kind": "evidence"}, {})

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

    def test_lease_exit_does_not_delete_a_replacement_owners_lock(self):
        identifier = self.store.create("youtube", {}, {})
        lock = self.store.root / (identifier + ".lock")
        with self.store.lease(identifier):
            lock.write_text(
                json.dumps({"pid": 123, "token": "replacement-owner"}), encoding="utf-8"
            )
        self.assertTrue(lock.exists())
        self.assertEqual(json.loads(lock.read_text())["token"], "replacement-owner")
        lock.unlink()

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

    def test_parent_cancel_stops_http_before_a_new_request(self):
        identifier = self.store.create(
            "youtube", {"max_requests": 10, "max_seconds": 10}, {}
        )
        http = PublicHttp(self.store, self.store.get(identifier))
        http.cancelled = threading.Event()
        http.cancelled.set()
        with (
            patch.object(http.opener, "open") as opened,
            self.assertRaises(BudgetExhausted),
        ):
            http.get("https://www.youtube.com/")
        opened.assert_not_called()
        self.assertEqual(self.store.request_count(identifier), 0)

    def test_interrupt_during_submission_cancels_already_started_children(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(self.store, max_seconds=100)
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

    def test_completed_children_can_be_recovered_after_the_deadline(self):
        set_profile(self.store, ["math"])
        identifier = create_batch(self.store, max_seconds=100)
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
