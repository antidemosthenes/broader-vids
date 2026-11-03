import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from broader_vids.evidence.assessment import assessment_prompt
from broader_vids.evidence.media import EvidencePending, VideoFetcher
from broader_vids.evidence.service import create_evidence
from broader_vids.preferences import set_profile
from broader_vids.recommendations.parallel import (
    Runtime,
    assess_parallel,
    process_candidate,
)
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from test_recommendations import candidate, quality


class CaptionRecoveryTests(unittest.TestCase):
    def fetcher(self, responses):
        calls = []

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, limit):
                return self.body.encode()

        class Downloader:
            def urlopen(self, url):
                calls.append(url)
                result = responses[url]
                if isinstance(result, Exception):
                    raise result
                return Response(result)

        fetcher = object.__new__(VideoFetcher)
        fetcher.ydl = Downloader()
        fetcher.deadline = time.monotonic() + 5
        return fetcher, calls

    def test_original_english_precedes_translated_english_and_other_languages(self):
        original = "https://www.youtube.com/api/timedtext?lang=en&fmt=json3"
        translated = "https://www.youtube.com/api/timedtext?lang=ar&tlang=en"
        body = json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 0,
                        "dDurationMs": 2000,
                        "segs": [{"utf8": "Original speech"}],
                    }
                ]
            }
        )
        fetcher, calls = self.fetcher({original: body})
        info = {
            "duration": 10,
            "automatic_captions": {
                "en": [{"ext": "json3", "url": translated}],
                "ar": [
                    {
                        "ext": "json3",
                        "url": "https://www.youtube.com/api/timedtext?lang=ar",
                    }
                ],
                "en-orig": [{"ext": "json3", "url": original}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            record = fetcher.captions(info, directory, "youtube")
            self.assertEqual(record["language"], "en-orig")
            self.assertEqual(record["source"], "automatic")
            self.assertEqual(calls, [original])
            self.assertEqual(
                json.loads(Path(record["path"]).read_text())[0]["text"],
                "Original speech",
            )

    def test_original_non_english_captions_are_usable_and_malformed_tracks_can_fall_back(
        self,
    ):
        bad, native = "https://example.test/en", "https://example.test/es"
        fetcher, calls = self.fetcher(
            {bad: "{}", native: "1\n00:00:00,000 --> 00:00:02,000\nHola\n"}
        )
        info = {
            "duration": 10,
            "subtitles": {
                "en": [{"ext": "json3", "url": bad}],
                "es": [{"ext": "srt", "url": native}],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            record = fetcher.captions(info, directory, "youtube")
        self.assertEqual(record["language"], "es")
        self.assertEqual(calls, [bad, native])

    def test_true_original_track_rate_limit_is_not_hidden_or_retried(self):
        class RateLimited(Exception):
            status = 429

        native = "https://example.test/en?signature=private"
        fetcher, calls = self.fetcher({native: RateLimited()})
        info = {
            "duration": 10,
            "automatic_captions": {"en-orig": [{"ext": "json3", "url": native}]},
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaises(EvidencePending) as caught,
        ):
            fetcher.captions(info, directory, "youtube")
        self.assertEqual(calls, [native])
        self.assertEqual(caught.exception.scope, "platform")
        self.assertEqual(caught.exception.category, "access")
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("private", str(caught.exception))

    def test_language_aliases_do_not_repeat_the_same_caption_request(self):
        url = "https://example.test/captions"
        fetcher, calls = self.fetcher({url: '{"events": []}'})
        info = {
            "duration": 10,
            "automatic_captions": {
                language: [{"ext": "json3", "url": url}]
                for language in ("en", "en-US", "en-orig")
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(fetcher.captions(info, directory, "youtube"))
        self.assertEqual(calls, [url])

    def test_compact_prompt_preserves_text_ids_timestamps_and_coverage(self):
        cues = [
            {
                "id": f"C{i:05d}",
                "start": i,
                "end": i + 1,
                "text": 'A short caption "with punctuation"\n中文',
            }
            for i in range(1000)
        ]
        run = {
            "settings": {},
            "state": {
                "metadata": {"title": "Example"},
                "media": {"duration_seconds": 1001},
                "captions": {"source": "author"},
                "caption_excerpt": {
                    "characters": 30000,
                    "selected_cues": 1000,
                    "total_cues": 1000,
                    "temporal_gaps": False,
                    "path": "private",
                    "sha256": "abc",
                },
            },
        }
        prompt = assessment_prompt(run, "augmented", cues, [])
        payload = json.loads(
            prompt.split("The following JSON and attached images are evidence data:\n")[
                1
            ]
        )
        restored = [
            dict(zip(("id", "start", "end", "text"), values))
            for values in payload["caption_excerpts"]
        ]
        self.assertEqual(restored, cues)
        self.assertNotIn("path", payload["caption_coverage"])
        self.assertFalse(payload["caption_coverage"]["temporal_gaps"])
        self.assertLess(
            len(
                json.dumps(
                    payload["caption_excerpts"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
            len(json.dumps(cues, ensure_ascii=False)) * 0.75,
        )


class SchedulingRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)
        self.addCleanup(self.store.close)
        set_profile(self.store, ["mathematics"])

    def test_caption_access_pause_drains_prepared_assessments_without_launching_more(
        self,
    ):
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        run["state"].update(
            phase="assess", shortlist=[candidate(i) for i in range(1, 7)]
        )
        first = run["state"]["shortlist"][0]["canonical_id"]
        barrier, runtime = threading.Barrier(4), Runtime()
        calls = []

        def worker(root, work, settings, runtime, jar, cancelled):
            calls.append(work["canonical_id"])
            barrier.wait(3)
            if work["canonical_id"] == first:
                return {
                    "status": "pending",
                    "evidence_run": work["evidence_run"],
                    "reason": "Original captions HTTP 429",
                    "failure": {
                        "stage": "captions",
                        "scope": "platform",
                        "category": "access",
                        "reason": "Original captions HTTP 429",
                        "retryable": True,
                    },
                }
            self.assertTrue(runtime.preparation_stops["youtube"].wait(3))
            self.assertFalse(cancelled.is_set())
            self.assertFalse(runtime.cancelled.is_set())
            return quality(work["evidence_run"])

        with self.assertRaisesRegex(EvidencePending, "429"):
            assess_parallel(
                self.store,
                run,
                lambda: self.store.save(run),
                lambda: 100,
                runtime=runtime,
                worker=worker,
                quality_lookup=lambda *a: None,
            )
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            sum(
                v.get("quality") is not None for v in run["state"]["evidence"].values()
            ),
            3,
        )
        self.assertEqual(run["state"]["inflight"], {})
        self.assertFalse(runtime.preparation_stops["bilibili"].is_set())

    def test_prepared_video_bypasses_preparation_queue(self):
        identifier = create_evidence(
            self.store,
            candidate(1)["url"],
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        run["state"].update(phase="prepared", assessments={"augmented": {}})
        self.store.save(run)
        work = {
            "evidence_run": identifier,
            "seconds": 1,
            "bytes": 0,
            "parent_deadline": time.monotonic() + 2,
            "preparation_token": "warm",
        }
        with (
            patch.object(
                Runtime,
                "preparation_slot",
                side_effect=AssertionError(
                    "Prepared evidence entered acquisition queue"
                ),
            ),
            patch(
                "broader_vids.recommendations.parallel.prepare_evidence",
                return_value=run,
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
        with (
            patch(
                "broader_vids.recommendations.parallel.prepare_evidence",
                return_value=run,
            ),
            patch(
                "broader_vids.recommendations.parallel.cached_quality",
                return_value=None,
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
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["failure"]["category"], "insufficient_evidence")
        self.assertFalse(result["failure"]["retryable"])

    def test_worker_streams_close_even_when_process_cleanup_fails(self):
        import io
        from types import SimpleNamespace
        from broader_vids.evidence.preparation_worker import _prepare_bounded_owned
        from broader_vids.processes import ProcessCleanupError

        source = io.BytesIO()
        process = SimpleNamespace(stdin=source, poll=lambda: 0, returncode=0)
        streams, factory = [], tempfile.TemporaryFile

        def temporary():
            stream = factory()
            streams.append(stream)
            return stream

        with (
            patch(
                "broader_vids.evidence.preparation_worker.tempfile.TemporaryFile",
                side_effect=temporary,
            ),
            patch(
                "broader_vids.evidence.preparation_worker.start_owned",
                return_value=process,
            ),
            patch(
                "broader_vids.evidence.preparation_worker.release_owned",
                side_effect=ProcessCleanupError("Fixture cleanup failure"),
            ),
            self.assertRaisesRegex(ProcessCleanupError, "cleanup failure"),
        ):
            _prepare_bounded_owned(
                self.store.root, "a" * 32, None, 1, 1024, None, threading.Event()
            )
        self.assertTrue(source.closed)
        self.assertTrue(streams[0].closed)

    def test_both_platforms_can_prepare_two_videos_at_the_same_time(self):
        runtime, barrier, cancelled = Runtime(), threading.Barrier(4), threading.Event()

        def worker(platform):
            with runtime.preparation_slot(platform, lambda: 3, cancelled):
                barrier.wait(2)
            return True

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(worker, p)
                for p in ("youtube", "youtube", "bilibili", "bilibili")
            ]
            self.assertTrue(all(f.result(timeout=3) for f in futures))
