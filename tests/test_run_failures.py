from contextlib import closing
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import json

from broader_vids.evidence.media import EvidencePending, VideoFetcher
from broader_vids.evidence.preparation_worker import prepare_bounded
from broader_vids.evidence.service import create_evidence
from broader_vids.evidence.assessment import run_codex
from broader_vids.preferences import set_profile
from broader_vids.recommendations.parallel import (
    Runtime,
    assess_parallel,
    process_candidate,
)
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from broader_vids.http import PublicHttp, BudgetExhausted, AccessPaused
from broader_vids.recommendations.metadata import SnapshotPool
from test_recommendations import candidate, quality


class RunFailureTests(unittest.TestCase):
    def test_provider_limit_uses_the_structured_cause_and_redacts_credentials(self):
        path = self.store.root / "response.json"
        stdout = "\n".join(
            [
                json.dumps({"type": "thread.started"}),
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {"message": "Usage limit reached. SESSDATA=secret"},
                    }
                ),
            ]
        )
        with (
            patch(
                "broader_vids.evidence.assessment.shutil.which", return_value="codex"
            ),
            patch(
                "broader_vids.evidence.assessment.run_bounded",
                side_effect=[
                    subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", ""),
                    subprocess.CompletedProcess([], 1, stdout, ""),
                ],
            ),
        ):
            with self.assertRaises(EvidencePending) as caught:
                run_codex(
                    "fixture",
                    self.store.root / "schema.json",
                    path,
                    [],
                    10,
                    model="fixture-model",
                    effort="low",
                )
        self.assertEqual(caught.exception.scope, "run")
        self.assertIn("Usage limit", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(path.with_suffix(".diagnostic.json").is_file())

    def test_bilibili_metadata_access_cause_survives_cancellation_of_an_earlier_candidate(
        self,
    ):
        rid = create_recommendation(
            self.store,
            "bilibili",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        http = PublicHttp(self.store, self.store.get(rid), anonymous_session=True)
        barrier = threading.Barrier(2)

        def collect(client, item):
            barrier.wait(2)
            if item["canonical_id"] == candidate(2, "bilibili")["canonical_id"]:
                raise AccessPaused("HTTP 429 metadata access failed")
            self.assertTrue(pool.cancelled.wait(2))
            raise BudgetExhausted("Sibling retrieval cancelled")

        with SnapshotPool(self.store, http, lambda: 10, collect, enabled=True) as pool:
            pool.prefetch(
                [candidate(1, "bilibili"), candidate(2, "bilibili")], lambda i: True
            )
            with self.assertRaisesRegex(AccessPaused, "429"):
                pool.get(candidate(1, "bilibili"), lambda: None)

    def test_bilibili_metadata_pool_overlaps_responses_and_shares_request_bounds(
        self,
    ):
        rid = create_recommendation(
            self.store,
            "bilibili",
            max_requests=2,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        now = [0.0]
        http = PublicHttp(
            self.store,
            self.store.get(rid),
            anonymous_session=True,
            clock=lambda: now[0],
            sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
        )
        responses = threading.Barrier(2)
        starts = []
        active = [0, 0]
        lock = threading.Lock()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def geturl(self):
                return "https://api.bilibili.com/x/web-interface/view"

            def read(self, size):
                with lock:
                    starts.append(now[0])
                    active[0] += 1
                    active[1] = max(active[1], active[0])
                responses.wait(timeout=3)
                with lock:
                    active[0] -= 1
                return b"{}"

        def collect(client, item):
            return client.get_json("https://api.bilibili.com/x/web-interface/view")

        items = [
            candidate(1, "bilibili"),
            candidate(2, "bilibili"),
            candidate(3, "bilibili"),
        ]
        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            with SnapshotPool(
                self.store, http, lambda: 10, collect, enabled=True
            ) as pool:
                pool.prefetch(items, lambda i: True)
                self.assertEqual(pool.get(items[0], lambda: None), {})
                self.assertEqual(pool.get(items[1], lambda: None), {})
                pool.prefetch(items[2:], lambda i: True)
                with self.assertRaises(BudgetExhausted):
                    pool.get(items[2], lambda: None)
        self.assertEqual(len(starts), 2)
        self.assertEqual(now[0], 1.0)
        self.assertEqual(active[1], 2)
        self.assertEqual(self.store.request_count(rid), 2)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        set_profile(self.store, ["mathematics"])

    def run_fixture(self, count):
        rid = create_recommendation(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(rid)
        run["state"].update(
            phase="assess", shortlist=[candidate(i) for i in range(1, count + 1)]
        )
        self.store.save(run)
        return run

    def test_candidate_failure_report_separates_download_from_assessment_failure(self):
        run = self.run_fixture(2)
        completed_item, failed_item = run["state"]["shortlist"]
        completed_id = create_evidence(
            self.store,
            completed_item["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        completed = self.store.get(completed_id)
        completed["state"].update(
            phase="prepared",
            media={"sha256": "fixture"},
            download_attempts=[{"number": 1, "status": "completed"}],
            captions={"source": "unavailable", "reason": "No separate subtitle track."},
            frames=[{"id": "F01"}, {"id": "F02"}],
            assessments={
                "augmented": {
                    "card": {
                        "overall_rationale": "Visible evidence did not support a score.",
                        "unknowns": ["The lesson progression is unclear."],
                        "criteria": {
                            "substance": {"status": "unknown"},
                            "presentation": {"status": "assessed"},
                        },
                    }
                }
            },
        )
        self.store.save(completed)
        failed_id = create_evidence(
            self.store,
            failed_item["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        failed = self.store.get(failed_id)
        failed["state"]["download_attempts"] = [
            {"number": 1, "status": "failed", "error": "Temporary transfer failed."}
        ]
        self.store.save(failed)
        run["state"]["evidence"] = {
            completed_item["canonical_id"]: {
                "status": "unavailable",
                "evidence_run": completed_id,
                "reason": "Evidence did not support a defensible score.",
                "failure": {
                    "stage": "assessment",
                    "category": "insufficient_evidence",
                    "retryable": False,
                },
            },
            failed_item["canonical_id"]: {
                "status": "unavailable",
                "evidence_run": failed_id,
                "reason": "Transfer failed.",
                "failure": {
                    "stage": "download",
                    "category": "media_transfer",
                    "retryable": False,
                },
            },
        }
        run["state"]["candidate_retry_counts"] = {failed_item["canonical_id"]: 1}
        run["state"]["candidate_attempt_counts"] = {failed_item["canonical_id"]: 2}
        self.store.save(run)

        from broader_vids.recommendations.reports import candidate_failures

        failures = {
            entry["canonical_id"]: entry
            for entry in candidate_failures(self.store, run)
        }
        assessment = failures[completed_item["canonical_id"]]
        self.assertEqual(assessment["download_outcome"], "completed")
        self.assertEqual(assessment["completed_download_attempts"], 1)
        self.assertEqual(assessment["stop_reason"], "assessment_insufficient_evidence")
        self.assertEqual(
            assessment["assessment_summary"],
            {
                "overall_rationale": "Visible evidence did not support a score.",
                "unknowns": ["The lesson progression is unclear."],
                "unknown_criteria": ["substance"],
                "caption_source": "unavailable",
                "caption_reason": "No separate subtitle track.",
                "frame_count": 2,
            },
        )
        transfer = failures[failed_item["canonical_id"]]
        self.assertEqual(transfer["download_outcome"], "failed")
        self.assertEqual(transfer["failed_download_attempts"], 1)
        self.assertEqual(transfer["candidate_retries"], 1)
        self.assertEqual(transfer["stop_reason"], "non_retryable_failure")
        failed = self.store.get(failed_id)
        failed["state"]["download_attempts"] = []
        self.store.save(failed)
        empty = candidate_failures(self.store, run)[1]
        self.assertEqual(empty["download_outcome"], "not_attempted")
        failed["state"]["download_attempts"] = [
            {"number": number, "status": "failed"} for number in range(1, 5)
        ]
        self.store.save(failed)
        exhausted = candidate_failures(self.store, run)[1]
        self.assertEqual(exhausted["stop_reason"], "retry_exhausted")

    def test_access_failure_preserves_the_cause_when_other_workers_cancel(self):
        run = self.run_fixture(6)
        barrier = threading.Barrier(4)
        first = run["state"]["shortlist"][0]["canonical_id"]

        def worker(root, work, settings, runtime, jar, cancelled):
            barrier.wait(2)
            if work["canonical_id"] == first:
                return {
                    "status": "pending",
                    "reason": "HTTP Error 403: access check required",
                    "evidence_run": work["evidence_run"],
                }
            self.assertTrue(cancelled.wait(2))
            return {
                "status": "pending",
                "reason": "The parent cancelled this worker.",
                "failure": {
                    "scope": "candidate",
                    "category": "cancelled",
                    "stage": "worker",
                    "reason": "The parent cancelled this worker.",
                },
                "evidence_run": work["evidence_run"],
            }

        with self.assertRaisesRegex(EvidencePending, "403"):
            assess_parallel(
                self.store,
                run,
                lambda: self.store.save(run),
                lambda: 100,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
        self.assertIn("403", run["state"]["first_failure"]["reason"])
        self.assertEqual(len(run["state"]["evidence"]), 4)

    def test_exhausted_candidate_is_resolved_without_more_worker_calls(self):
        run = self.run_fixture(1)
        rid = create_evidence(
            self.store,
            run["state"]["shortlist"][0]["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        child = self.store.get(rid)
        child["state"].update(phase="prepared", model_calls=2, submitted_images=8)
        self.store.save(child)
        assess_parallel(
            self.store,
            run,
            lambda: self.store.save(run),
            lambda: 100,
            worker=lambda *a: self.fail("Exhausted worker launched"),
            quality_lookup=lambda *a: None,
        )
        self.assertEqual(run["state"]["assessment_index"], 1)
        self.assertEqual(
            next(iter(run["state"]["evidence"].values()))["status"], "unavailable"
        )

    def test_preparation_queue_wait_does_not_consume_its_stage_allowance(self):
        rid = create_evidence(
            self.store,
            candidate(1)["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        runtime = Runtime(preparations=1)
        held_slot = threading.BoundedSemaphore(1)
        held_slot.acquire()
        waiting_for_slot = threading.Event()

        class ObservedSemaphore:
            def acquire(self, timeout=None):
                waiting_for_slot.set()
                return held_slot.acquire(timeout=timeout)

            def release(self):
                held_slot.release()

        class ControlledClock:
            def __init__(self):
                self.value = 100.0
                self.lock = threading.Lock()

            def monotonic(self):
                with self.lock:
                    return self.value

            def advance(self, seconds):
                with self.lock:
                    self.value += seconds

        runtime.preparations = ObservedSemaphore()
        clock = ControlledClock()
        seen = []

        def prepare(root, key, asr, seconds, *args, **kwargs):
            seen.append(seconds)
            with closing(Store(root)) as store:
                run = store.get(key)
                run["state"].update(phase="prepared", assessments={"augmented": {}})
                store.save(run)

        work = {
            "evidence_run": rid,
            "seconds": 0.1,
            "bytes": 1,
            "parent_deadline": clock.value + 10,
            "preparation_token": "queue-test",
        }
        with (
            patch(
                "broader_vids.recommendations.parallel.prepare_bounded",
                side_effect=prepare,
            ),
            patch(
                "broader_vids.recommendations.parallel.cached_quality",
                return_value=quality(rid),
            ),
        ):
            with patch("broader_vids.recommendations.parallel.time", clock):
                results = []
                errors = []

                def run_candidate():
                    try:
                        results.append(
                            process_candidate(
                                self.store.root,
                                work,
                                {"asr_model": None},
                                runtime,
                                None,
                                threading.Event(),
                            )
                        )
                    except BaseException as error:
                        errors.append(error)

                thread = threading.Thread(target=run_candidate)
                thread.start()
                try:
                    self.assertTrue(waiting_for_slot.wait(timeout=10))
                    clock.advance(1)
                finally:
                    held_slot.release()
                    thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
                if errors:
                    raise errors[0]
                result = results[0]
        self.assertIsNotNone(result.get("quality"))
        self.assertAlmostEqual(seen[0], 0.1)
        self.assertAlmostEqual(
            self.store.get(rid)["state"]["timings"]["preparation_queue_seconds"],
            1,
        )

    def test_killed_preparation_has_a_child_failure_and_bounded_byte_receipt(self):
        rid = create_evidence(
            self.store,
            candidate(1)["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )

        def timeout(*args, **kwargs):
            with closing(Store(self.store.root)) as store:
                run = store.get(rid)
                run.update(status="running", reason="")
                run["state"]["stage"] = "download"
                run["state"]["resources"]["media_download_bytes"] = 1024 * 1024
                run["state"]["preparation_progress"] = {
                    "token": "timeout-test",
                    "stage": "download",
                    "new_bytes": 1024 * 1024,
                    "unreported_byte_bound": 2 * 1024 * 1024,
                }
                store.save(run)
            raise subprocess.TimeoutExpired("media preparation", 1)

        with (
            patch(
                "broader_vids.evidence.preparation_worker._prepare_bounded_owned",
                side_effect=timeout,
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            prepare_bounded(
                self.store.root,
                rid,
                None,
                1,
                128 * 1024 * 1024,
                None,
                threading.Event(),
                attempt_token="timeout-test",
            )
        run = self.store.get(rid)
        self.assertEqual(run["status"], "pending")
        self.assertEqual(run["state"]["failure"]["category"], "media_timeout")
        self.assertFalse(run["state"]["retryable_failure"])
        self.assertEqual(
            run["state"]["preparation_usage"]["timeout-test"], 3 * 1024 * 1024
        )
        self.assertTrue(
            run["state"]["preparation_usage_details"]["timeout-test"]["upper_bound"]
        )

    def test_stream_size_is_checked_before_transfer_and_resume_allows_a_larger_file(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fetcher = VideoFetcher(1024, time.monotonic() + 30)
            self.addCleanup(fetcher.close)
            info = {"url": "https://primary.bilivideo.com/video", "filesize": 4096}
            with (
                patch.object(fetcher.ydl, "process_ie_result") as download,
                self.assertRaisesRegex(EvidencePending, "new bytes"),
            ):
                fetcher.download(dict(info), directory)
            download.assert_not_called()
            (Path(directory) / "media.mp4.part").write_bytes(b"x" * 3072)
            fetcher.max_file_bytes = 8192

            def finish(*args, **kwargs):
                fetcher._progress(
                    {
                        "filename": str(Path(directory) / "media.mp4"),
                        "downloaded_bytes": 4096,
                    }
                )
                (Path(directory) / "media.mp4").write_bytes(b"x" * 4096)

            with patch.object(fetcher.ydl, "process_ie_result", side_effect=finish):
                result = fetcher.download(dict(info), directory)
            self.assertEqual(result.stat().st_size, 4096)
            self.assertEqual(fetcher.downloaded_bytes, 1024)
