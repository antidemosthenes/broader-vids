import tempfile
import json
import math
import threading
import time
import unittest
from unittest.mock import patch

from broader_vids.evidence.media import EvidencePending
from broader_vids.evidence.service import create_evidence
from broader_vids.preferences import set_profile
from broader_vids.recommendations.parallel import (
    assess_parallel,
    Runtime,
    process_candidate,
)
from broader_vids.recommendations.batch import create_batch, execute_batch, batch_report
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from broader_vids.recommendations.reports import candidate_failures
from test_recommendations import candidate, quality


class ParallelTests(unittest.TestCase):
    def test_validation_repair_can_use_the_saved_total_call_allowance(self):
        run, save = self.run_fixture(count=35, calls=37)
        first = run["state"]["shortlist"][0]["canonical_id"]

        def worker(root, work, *args):
            local = Store(root)
            try:
                child = local.get(work["evidence_run"])
                child["state"]["model_calls"] += 1
                child["state"]["attempts"].append(
                    {"work_token": work["preparation_token"]}
                )
                child["state"].setdefault("preparation_usage", {})[
                    work["preparation_token"]
                ] = 0
                calls = child["state"]["model_calls"]
                local.save(child)
            finally:
                local.close()
            if work["canonical_id"] == first and calls == 1:
                return {
                    "status": "pending",
                    "reason": "topics.specific[0] exceeds its bound",
                    "failure": {
                        "category": "validation",
                        "stage": "assessment",
                        "scope": "candidate",
                        "reason": "topics.specific[0] exceeds its bound",
                    },
                    "evidence_run": work["evidence_run"],
                }
            return quality(work["evidence_run"])

        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            worker=worker,
            quality_lookup=lambda *args: None,
        )
        self.assertEqual(run["state"]["model_calls"], 36)
        self.assertEqual(run["state"]["assessment_index"], 35)

    def test_saved_unknown_result_does_not_require_a_new_model_call(self):
        run, save = self.run_fixture(count=1, calls=0)
        identifier = create_evidence(
            self.store,
            run["state"]["shortlist"][0]["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(identifier)
        child["state"].update(
            phase="prepared",
            assessments={
                "augmented": {
                    "overall_quality": None,
                    "card": {
                        "overall_rationale": "Silent stills cannot establish music quality."
                    },
                }
            },
        )
        self.store.save(child)

        def unknown(root, work, settings, runtime, jar, cancelled):
            self.assertEqual(work["model_reservation"], 0)
            self.assertEqual(work["bytes"], 0)
            with patch(
                "broader_vids.recommendations.parallel.cached_quality",
                return_value=None,
            ):
                result = process_candidate(
                    root, work, settings, runtime, jar, cancelled
                )
            self.assertEqual(result["failure"]["category"], "insufficient_evidence")
            self.assertIn("music quality", result["reason"])
            return result

        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            worker=unknown,
            quality_lookup=lambda *args: None,
        )
        self.assertEqual(run["state"]["model_calls"], 0)
        self.assertEqual(run["state"]["assessment_index"], 1)

    def test_exhausted_candidate_media_is_unavailable_without_pausing_other_assessments(
        self,
    ):
        for category in (
            "media_size",
            "media_transfer",
            "media_timeout",
            "access",
            "storage",
        ):
            with self.subTest(category=category):
                run, save = self.run_fixture(count=2)
                first = run["state"]["shortlist"][0]["canonical_id"]

                def prepare(root, identifier, *args, **kwargs):
                    local = Store(root)
                    try:
                        child = local.get(identifier)
                        if child["settings"]["identity"]["canonical_id"] == first:
                            child["state"]["failure"] = {
                                "stage": "download",
                                "scope": "run"
                                if category == "storage"
                                else "platform"
                                if category == "access"
                                else "candidate",
                                "category": category,
                                "reason": "Fixture download failure",
                                "retryable": False,
                            }
                            child["state"]["retryable_failure"] = category in {
                                "access",
                                "storage",
                            }
                            child["reason"] = "Fixture download failure"
                            local.save(child)
                    finally:
                        local.close()

                def lookup(store, identifier):
                    child = store.get(identifier)
                    return (
                        None
                        if child["settings"]["identity"]["canonical_id"] == first
                        else quality(identifier)
                    )

                with patch(
                    "broader_vids.recommendations.parallel.prepare_bounded",
                    side_effect=prepare,
                ):
                    if category in {"access", "storage"}:
                        with self.assertRaises(EvidencePending):
                            assess_parallel(
                                self.store,
                                run,
                                save,
                                lambda: 100,
                                quality_lookup=lambda *a: None,
                                worker=lambda *a: process_candidate(*a),
                            )
                    else:
                        with patch(
                            "broader_vids.recommendations.parallel.cached_quality",
                            side_effect=lookup,
                        ):
                            assess_parallel(
                                self.store,
                                run,
                                save,
                                lambda: 100,
                                quality_lookup=lambda *a: None,
                            )
                        self.assertEqual(
                            run["state"]["evidence"][first]["status"], "unavailable"
                        )
                        self.assertEqual(run["state"]["assessment_index"], 2)

    def test_cancel_winning_a_semaphore_race_never_starts_work(self):
        runtime, cancelled = Runtime(), threading.Event()

        class Semaphore:
            released = False

            def acquire(self, timeout):
                cancelled.set()
                return True

            def release(self):
                self.released = True

        semaphore = Semaphore()
        with self.assertRaises(EvidencePending):
            with runtime.slot(semaphore, lambda: 10, cancelled):
                self.fail("A cancelled task was admitted")
        self.assertTrue(semaphore.released)

    def test_failed_batch_creation_does_not_leave_partial_children(self):
        real_create = self.store.create
        calls = []

        def interrupted(*args, **kwargs):
            calls.append(None)
            if len(calls) == 3:
                raise OSError("Fixture disk failure")
            return real_create(*args, **kwargs)

        with (
            patch.object(self.store, "create", side_effect=interrupted),
            self.assertRaises(OSError),
        ):
            create_batch(
                self.store, assessment_model="fixture-model", reasoning_effort="low"
            )
        self.assertEqual(self.store.list_runs(), [])

    def test_batch_child_cannot_run_outside_its_shared_coordinator(self):
        identifier = create_batch(
            self.store, assessment_model="fixture-model", reasoning_effort="low"
        )
        child = self.store.get(identifier)["state"]["children"]["youtube"]
        from broader_vids.recommendations.service import execute_recommendation

        with (
            patch("broader_vids.recommendations.service.execute_discovery"),
            self.assertRaisesRegex(ValueError, "batch"),
        ):
            execute_recommendation(self.store, child)

    def test_warm_review_does_not_need_another_download_reservation(self):
        run, save = self.run_fixture(count=1)
        child_id = create_evidence(
            self.store,
            run["state"]["shortlist"][0]["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(child_id)
        child["state"]["phase"] = "prepared"
        self.store.save(child)
        run["state"]["download_bytes"] = run["settings"]["max_bytes"]
        invoked = []

        def worker(root, work, *args):
            invoked.append(work)
            self.assertEqual(work["bytes"], 0)
            return quality(work["evidence_run"])

        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            worker=worker,
            quality_lookup=lambda *args: None,
        )
        self.assertEqual(len(invoked), 1)
        self.assertEqual(run["state"]["assessment_index"], 1)

    def test_only_owned_model_attempts_are_charged_on_recovery(self):
        run, save = self.run_fixture(count=1)
        item = run["state"]["shortlist"][0]
        identifier = create_evidence(
            self.store,
            item["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(identifier)
        child["state"].update(
            model_calls=2,
            attempts=[{"work_token": "mine"}, {"work_token": "another-client"}],
        )
        self.store.save(child)
        run["state"]["inflight"] = {
            item["canonical_id"]: {
                "evidence_run": identifier,
                "base_calls": 0,
                "base_bytes": 0,
                "preparation_token": "mine",
                "accounting_version": 2,
            }
        }
        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            quality_lookup=lambda store, key: quality(key),
        )
        self.assertEqual(run["state"]["model_calls"], 1)

    def test_an_evidence_lease_spans_preparation_and_assessment(self):
        item = candidate(1)
        identifier = create_evidence(
            self.store,
            item["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(identifier)
        child["state"]["phase"] = "prepared"
        self.store.save(child)
        checked = []

        def assert_owned(local, key, *args, **kwargs):
            other = Store(local.root)
            try:
                with self.assertRaises(ValueError):
                    with other.lease(key):
                        pass
                checked.append(None)
                return local.get(key)
            finally:
                other.close()

        work = {
            "evidence_run": identifier,
            "canonical_id": item["canonical_id"],
            "seconds": 30,
            "bytes": 0,
            "preparation_token": "mine",
        }
        with (
            patch(
                "broader_vids.recommendations.parallel.prepare_evidence",
                side_effect=assert_owned,
            ),
            patch(
                "broader_vids.recommendations.parallel.assess_evidence",
                side_effect=assert_owned,
            ),
            patch(
                "broader_vids.recommendations.parallel.cached_quality",
                return_value=quality(identifier),
            ),
        ):
            result = process_candidate(
                self.store.root,
                work,
                {"asr_model": None},
                Runtime(),
                None,
                threading.Event(),
            )
        self.assertIsNotNone(result.get("quality"))
        self.assertEqual(len(checked), 2)

    def test_unacknowledged_preparation_charges_its_reserved_upper_bound(self):
        run, save = self.run_fixture(count=1)

        def interrupted(root, work, *args):
            return {
                "status": "pending",
                "reason": "Preparation was interrupted before its receipt",
                "evidence_run": work["evidence_run"],
            }

        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            worker=interrupted,
            quality_lookup=lambda *args: None,
        )
        self.assertTrue(run["state"]["download_bytes_is_upper_bound"])
        self.assertEqual(run["state"]["download_bytes"], 4 * 128 * 1024 * 1024)
        self.assertEqual(run["state"]["assessment_index"], 1)
        self.assertEqual(
            next(iter(run["state"]["evidence"].values()))["status"], "unavailable"
        )
        self.assertLessEqual(
            run["state"]["download_bytes"], run["settings"]["max_bytes"]
        )

    def test_interrupted_reservations_are_reconciled_once_on_resume(self):
        run, save = self.run_fixture(count=1)
        item = run["state"]["shortlist"][0]
        identifier = create_evidence(
            self.store,
            item["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(identifier)
        child["state"]["model_calls"] = 1
        child["state"]["resources"]["media_download_bytes"] = 123
        child["state"]["download_attempts"] = [
            {"number": 1, "status": "failed", "error": "read timed out"},
            {"number": 2, "status": "completed"},
        ]
        self.store.save(child)
        run["state"]["inflight"] = {
            item["canonical_id"]: {
                "evidence_run": identifier,
                "base_calls": 0,
                "base_bytes": 0,
            }
        }
        for _ in range(2):
            assess_parallel(
                self.store,
                run,
                save,
                lambda: 100,
                quality_lookup=lambda store, key: quality(key),
            )
            self.assertEqual(run["state"]["model_calls"], 1)
            self.assertEqual(run["state"]["download_bytes"], 123)
            self.assertEqual(run["state"]["inflight"], {})
            self.assertNotIn(
                item["canonical_id"], run["state"].get("cache_hit_ids", [])
            )
            self.assertEqual(
                candidate_failures(self.store, run)[0]["status"], "recovered"
            )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(self.store, ["math"])

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def run_fixture(self, count=6, calls=37, platform="youtube"):
        identifier = create_recommendation(
            self.store,
            platform,
            max_assessments=calls,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        run["state"].update(
            phase="assess",
            shortlist=[candidate(i, platform) for i in range(1, count + 1)],
        )
        self.store.save(run)
        return run, lambda: self.store.save(run)

    def test_local_and_model_work_overlap_with_global_bounds_and_exact_costs(self):
        run, save = self.run_fixture()
        runtime = Runtime(preparations=2, models=2)
        lock = threading.Lock()
        active = {"prepare": 0, "model": 0}
        peak = dict(active)
        both = threading.Event()

        def worker(root, work, settings, runtime, jar, cancelled):
            with runtime.slot(runtime.preparations, lambda: 10, cancelled):
                with lock:
                    active["prepare"] += 1
                    peak["prepare"] = max(peak["prepare"], active["prepare"])
                time.sleep(0.03)
                with lock:
                    active["prepare"] -= 1
            with runtime.slot(runtime.models, lambda: 10, cancelled):
                with lock:
                    active["model"] += 1
                    peak["model"] = max(peak["model"], active["model"])
                    if active["model"] == 2:
                        both.set()
                self.assertTrue(both.wait(2))
                time.sleep(0.04)
                local = Store(root)
                try:
                    child = local.get(work["evidence_run"])
                    child["state"]["model_calls"] += 1
                    child["state"]["attempts"].append(
                        {"work_token": work["preparation_token"]}
                    )
                    child["state"]["resources"]["media_download_bytes"] += 123
                    child["state"]["preparation_receipt"] = work["preparation_token"]
                    child["state"].setdefault("preparation_usage", {})[
                        work["preparation_token"]
                    ] = 123
                    local.save(child)
                finally:
                    local.close()
                with lock:
                    active["model"] -= 1
            return quality(work["evidence_run"])

        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            runtime,
            worker,
            quality_lookup=lambda *a: None,
        )
        self.assertEqual(peak, {"prepare": 2, "model": 2})
        self.assertEqual(run["state"]["model_calls"], 6)
        self.assertEqual(run["state"]["download_bytes"], 6 * 123)
        self.assertEqual(run["state"]["assessment_index"], 6)
        self.assertEqual(run["state"]["inflight"], {})

    def test_media_reservations_settle_before_reducing_the_next_candidate_allowance(
        self,
    ):
        run, save = self.run_fixture(count=3)
        run["settings"]["max_bytes"] = 256 * 1024 * 1024
        self.store.db.execute(
            "UPDATE runs SET settings=json_set(settings,'$.max_bytes',?) WHERE id=?",
            (run["settings"]["max_bytes"], run["id"]),
        )
        self.store.db.commit()
        first = run["state"]["shortlist"][0]["canonical_id"]
        from concurrent.futures import wait as wait_for_futures

        settled = threading.Event()
        second = run["state"]["shortlist"][1]["canonical_id"]
        allowances = []

        def wait(*args, **kwargs):
            if second in run["state"]["evidence"]:
                settled.set()
            return wait_for_futures(*args, **kwargs)

        def worker(root, work, *args):
            allowances.append(work["bytes"])
            if work["canonical_id"] == first:
                self.assertTrue(settled.wait(2))
            local = Store(root)
            try:
                child = local.get(work["evidence_run"])
                child["state"].setdefault("preparation_usage", {})[
                    work["preparation_token"]
                ] = 64 * 1024 * 1024
                local.save(child)
            finally:
                local.close()
            return quality(work["evidence_run"])

        with (
            patch("broader_vids.recommendations.parallel.wait", side_effect=wait),
            patch(
                "broader_vids.recommendations.parallel.create_evidence",
                wraps=create_evidence,
            ) as create,
        ):
            assess_parallel(
                self.store,
                run,
                save,
                lambda: 100,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
        self.assertEqual(allowances, [128 * 1024 * 1024] * 3)
        self.assertEqual(create.call_count, 3)
        self.assertEqual(run["state"]["download_bytes"], 192 * 1024 * 1024)

    def test_model_reservations_prevent_parallel_overspending(self):
        run, save = self.run_fixture(count=4, calls=1)
        invoked = []

        def worker(root, work, *args):
            invoked.append(work["canonical_id"])
            local = Store(root)
            child = local.get(work["evidence_run"])
            child["state"]["model_calls"] += 1
            child["state"]["attempts"].append({"work_token": work["preparation_token"]})
            local.save(child)
            local.close()
            return quality(work["evidence_run"])

        with self.assertRaises(EvidencePending):
            assess_parallel(
                self.store,
                run,
                save,
                lambda: 100,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
        self.assertEqual(len(invoked), 1)
        self.assertEqual(run["state"]["model_calls"], 1)
        self.assertEqual(run["state"]["assessment_index"], 1)

    def test_both_platforms_run_concurrently_under_one_deadline(self):
        identifier = create_batch(
            self.store,
            max_seconds=100,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        barrier = threading.Barrier(2)
        bounds, runtimes = [], []

        def runner(local, child, *, max_seconds, runtime, batch_id):
            self.assertEqual(local.get(child)["settings"]["batch_id"], batch_id)
            bounds.append(max_seconds)
            runtimes.append(runtime)
            barrier.wait(2)
            run = local.get(child)
            run.update(status="completed", reason="Fixture")
            local.save(run)
            return run

        result = execute_batch(self.store, identifier, runner)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(0 < value < 100 for value in bounds))
        self.assertIs(runtimes[0], runtimes[1])
        self.assertLess(result["elapsed"], 100)
        result["elapsed"] = 100
        result["status"] = "paused"
        self.store.save(result)
        execute_batch(self.store, identifier, lambda *a, **k: self.fail("Budget reset"))

    def test_unlimited_batches_and_worker_checkpoints_remain_json_safe(self):
        identifier = create_batch(
            self.store, assessment_model="fixture-model", reasoning_effort="low"
        )
        batch = self.store.get(identifier)
        for key in (identifier, *batch["state"]["children"].values()):
            saved = self.store.get(key)
            saved["elapsed"] = 2400
            self.store.save(saved)

        def runner(local, child, *, max_seconds, **kwargs):
            self.assertIsNone(max_seconds)
            run = local.get(child)
            run["state"].update(
                phase="assess", shortlist=[candidate(1, run["platform"])]
            )

            def worker(root, work, *args):
                self.assertIsNone(work["parent_deadline"])
                self.assertLessEqual(work["seconds"], 300)
                self.assertTrue(math.isfinite(work["deadline"]))
                json.dumps(work, allow_nan=False)
                other = Store(root)
                try:
                    json.dumps(other.get(child)["state"], allow_nan=False)
                finally:
                    other.close()
                return quality(work["evidence_run"])

            assess_parallel(
                local,
                run,
                lambda: local.save(run),
                lambda: math.inf,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
            run.update(status="completed", reason="Fixture")
            local.save(run)
            return run

        result = execute_batch(self.store, identifier, runner)
        self.assertEqual(result["status"], "completed", result["state"]["results"])
        report, _ = batch_report(self.store, identifier)
        self.assertIsNone(report["shared_max_seconds"])
        self.assertGreaterEqual(report["elapsed_seconds"], 2400)
        for child in report["platforms"].values():
            self.assertIsNone(child["settings"]["max_seconds"])
        json.dumps(report, allow_nan=False)

    def test_manual_cancellation_preserves_the_candidate_retry_allowance(self):
        run, save = self.run_fixture(count=1)
        first = run["state"]["shortlist"][0]["canonical_id"]
        calls = []

        def worker(root, work, *args):
            calls.append(work["evidence_run"])
            local = Store(root)
            try:
                child = local.get(work["evidence_run"])
                child["state"].setdefault("preparation_usage", {})[
                    work["preparation_token"]
                ] = 0
                local.save(child)
            finally:
                local.close()
            if len(calls) <= 3:
                return {
                    "status": "pending",
                    "evidence_run": work["evidence_run"],
                    "reason": "User cancelled",
                    "failure": {
                        "scope": "candidate",
                        "category": "cancelled",
                        "stage": "worker",
                        "reason": "User cancelled",
                        "retryable": True,
                    },
                }
            return quality(work["evidence_run"])

        for _ in range(3):
            with self.assertRaises(EvidencePending):
                assess_parallel(
                    self.store,
                    run,
                    save,
                    lambda: 100,
                    worker=worker,
                    quality_lookup=lambda *a: None,
                )
            self.assertEqual(run["state"]["candidate_attempt_counts"][first], 0)
        assess_parallel(
            self.store,
            run,
            save,
            lambda: 100,
            worker=worker,
            quality_lookup=lambda *a: None,
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual(run["state"]["candidate_attempt_counts"][first], 1)
        self.assertEqual(run["state"]["candidate_retry_counts"][first], 0)
        self.assertEqual(candidate_failures(self.store, run), [])

    def test_both_platforms_retry_candidate_exceptions_three_times_then_continue(self):
        for platform, recover in (
            (platform, recover)
            for platform in ("youtube", "bilibili")
            for recover in (False, True)
        ):
            with self.subTest(platform=platform, recover=recover):
                run, save = self.run_fixture(count=7, platform=platform)
                first = run["state"]["shortlist"][0]["canonical_id"]
                barrier = threading.Barrier(4)
                initial = {
                    item["canonical_id"] for item in run["state"]["shortlist"][:4]
                }
                counts = {}
                identifiers = []

                def worker(root, work, *args):
                    key = work["canonical_id"]
                    counts[key] = counts.get(key, 0) + 1
                    self.assertFalse(args[-1].is_set())
                    local = Store(root)
                    try:
                        child = local.get(work["evidence_run"])
                        child["state"].setdefault("preparation_usage", {})[
                            work["preparation_token"]
                        ] = 0
                        local.save(child)
                    finally:
                        local.close()
                    if counts[key] == 1 and key in initial:
                        barrier.wait(2)
                    if key == first:
                        identifiers.append(work["evidence_run"])
                        if not recover or counts[key] < 4:
                            if recover:
                                raise RuntimeError("Fixture candidate exception")
                            raise PermissionError(
                                "Fixture candidate exception: [WinError 32] file is in use"
                            )
                    return quality(work["evidence_run"])

                assess_parallel(
                    self.store,
                    run,
                    save,
                    lambda: 100,
                    worker=worker,
                    quality_lookup=lambda *a: None,
                )
                self.assertEqual(counts[first], 4)
                self.assertEqual(len(set(identifiers)), 1)
                self.assertEqual(run["state"]["candidate_retry_counts"][first], 3)
                self.assertEqual(run["state"]["assessment_index"], 7)
                self.assertEqual(
                    run["state"]["evidence"][first].get("quality") is not None, recover
                )
                self.assertEqual(
                    sum(
                        value.get("quality") is not None
                        for value in run["state"]["evidence"].values()
                    ),
                    7 if recover else 6,
                )
                failure = candidate_failures(self.store, run)[0]
                self.assertEqual(
                    (failure["status"], failure["candidate_retries"]),
                    ("recovered" if recover else "skipped", 3),
                )
                self.assertEqual(failure["stage"], "worker")
                self.assertIn(
                    "Fixture candidate exception",
                    run["state"]["first_failure"]["reason"],
                )
                saved_counts = dict(counts)
                if not recover:
                    run["state"]["evidence"][first]["status"] = "pending"
                    save()
                assess_parallel(
                    self.store,
                    run,
                    save,
                    lambda: 100,
                    worker=worker,
                    quality_lookup=lambda local, key: None
                    if key in identifiers and not recover
                    else quality(key),
                )
                self.assertEqual(counts.get(first), saved_counts[first])
