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
from broader_vids.learning.annotations import topics_schema, validate_topics
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
from broader_vids.recommendations.timing import live_elapsed
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

    def test_running_elapsed_report_advances_without_rewriting_the_checkpoint(self):
        run = self.run_fixture(1)
        run.update(status="running", elapsed=7)
        run["state"]["active_attempt"] = {"started_wall": 100, "prior_elapsed": 7}
        with patch("broader_vids.recommendations.timing.time.time", return_value=105):
            self.assertEqual(live_elapsed(run), 12)
        self.assertEqual(run["elapsed"], 7)
        run["status"] = "paused"
        self.assertEqual(live_elapsed(run), 7)

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

    def test_generation_and_validation_share_the_topic_length_bound(self):
        schema = topics_schema()
        for key in ("specific", "broad"):
            self.assertEqual(schema["properties"][key]["items"]["maxLength"], 120)
        topics = {
            "specific": ["x" * 121],
            "broad": [],
            "confidence": "medium",
            "evidence_ids": ["F01"],
            "rationale": "Visible evidence",
        }
        with self.assertRaisesRegex(ValueError, r"topics.specific\[0\].*121"):
            validate_topics(topics, {"F01"})

    def test_candidate_failure_does_not_cancel_later_candidates(self):
        run = self.run_fixture(7)
        seen = []
        first = run["state"]["shortlist"][0]["canonical_id"]

        def worker(root, work, settings, runtime, jar, cancelled):
            seen.append(work["canonical_id"])
            self.assertFalse(cancelled.is_set())
            if work["canonical_id"] == first:
                return {
                    "status": "pending",
                    "evidence_run": work["evidence_run"],
                    "reason": "One candidate transfer timed out.",
                }
            time.sleep(0.01)
            return quality(work["evidence_run"])

        with self.assertRaises(EvidencePending):
            assess_parallel(
                self.store,
                run,
                lambda: self.store.save(run),
                lambda: 100,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
        self.assertEqual(len(seen), 7)
        self.assertEqual(
            sum(
                v.get("quality") is not None for v in run["state"]["evidence"].values()
            ),
            6,
        )
        self.assertIn("transfer timed out", run["state"]["first_failure"]["reason"])

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
        runtime.preparations.acquire()
        release = threading.Timer(0.15, runtime.preparations.release)
        release.start()
        self.addCleanup(release.cancel)
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
            "parent_deadline": time.monotonic() + 3,
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
            result = process_candidate(
                self.store.root,
                work,
                {"asr_model": None},
                runtime,
                None,
                threading.Event(),
            )
        self.assertIsNotNone(result.get("quality"))
        self.assertAlmostEqual(seen[0], 0.1)
        self.assertGreater(
            self.store.get(rid)["state"]["timings"]["preparation_queue_seconds"], 0.1
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
        self.assertEqual(run["state"]["failure"]["category"], "timeout")
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
