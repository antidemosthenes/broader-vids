import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import weakref
import wave
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from broader_vids.evidence.assessment import assess_evidence
from broader_vids.evidence.media import file_hash
from broader_vids.evidence.retention import compact_evidence, owned_file
from broader_vids.evidence.service import (
    atomic_json,
    create_evidence,
    prepare_evidence,
    verify_bundle,
)
from broader_vids.evidence.media import EvidencePending
from broader_vids.maintenance import cleanup_storage
from broader_vids.recommendations.quality import cached_quality
from broader_vids.storage import Store
from test_evidence import card


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "state")
        self.identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            reasoning_effort="low",
            assessment_model="fixture-model",
        )
        run = self.store.get(self.identifier)
        root = self.store.root / "evidence" / self.identifier
        root.mkdir(parents=True)
        media, captions, frame = (
            root / "media.mp4",
            root / "captions.json",
            root / "frame-1.jpg",
        )
        media.write_bytes(b"Fixture media bytes")
        atomic_json(
            captions,
            [{"id": "C00001", "start": 0, "end": 10, "text": "Caption evidence"}],
        )
        frame.write_bytes(b"Fixture frame bytes")
        run["state"].update(
            phase="prepared",
            metadata={
                "title": "Fixture",
                "source": "yt_dlp_playable_unit",
                "duration_seconds": 10,
            },
            media={
                "path": str(media),
                "sha256": file_hash(media),
                "bytes": media.stat().st_size,
                "duration_seconds": 10,
            },
            captions={
                "source": "author",
                "path": str(captions),
                "sha256": file_hash(captions),
            },
            caption_excerpt={"path": str(captions), "sha256": file_hash(captions)},
            frames=[
                {
                    "id": "F01",
                    "path": str(frame),
                    "sha256": file_hash(frame),
                    "timestamp": 1,
                }
            ],
        )
        self.store.save(run)
        self.media = media

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def assess(self, mode="augmented"):
        return assess_evidence(
            self.store, self.identifier, mode, lambda *args: (card(mode), {})
        )

    def test_learning_evidence_is_readable_but_not_reused_by_new_jobs(self):
        self.assess()
        run = self.store.get(self.identifier)
        run["settings"]["kind"] = "learning_evidence"
        run["settings"].pop("cache_key", None)
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=? WHERE id=?",
                (json.dumps(run["settings"]), self.identifier),
            )
        self.assertIsNotNone(cached_quality(self.store, self.identifier))
        self.assertEqual(list(self.store.iter_runs(kind="evidence")), [])
        fresh = self.store.get(
            create_evidence(
                self.store,
                "https://www.youtube.com/watch?v=imYQJOgUx7Y",
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
        )
        self.assertNotEqual(fresh["id"], self.identifier)
        self.assertNotIn("inherited_from", fresh["state"])
        self.assertIsNone(fresh["state"].get("media"))

    def test_metadata_only_job_cannot_be_selected_as_a_reusable_bundle(self):
        self.assess()
        identity = self.store.get(self.identifier)["settings"]["identity"]
        self.store.create(
            "youtube",
            {"kind": "metadata", "identity": identity},
            {"phase": "completed", "snapshot": {"views": 1}},
        )
        review = create_evidence(
            self.store,
            identity["url"],
            originality_context={"signals": [{"id": "M1", "kind": "source"}]},
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(review)
        self.assertEqual(run["state"]["inherited_from"], self.identifier)
        self.assertEqual(run["state"]["resources"]["media_download_bytes"], 0)
        verify_bundle(self.store, run)

    def test_interrupted_bundle_inheritance_reuses_its_existing_checkpoint(self):
        self.assess()
        identity = self.store.get(self.identifier)["settings"]["identity"]
        context = {"signals": [{"id": "M1", "kind": "source"}]}
        with patch(
            "broader_vids.evidence.service.inherit_bundle",
            side_effect=ValueError("Interrupted inheritance"),
        ):
            with self.assertRaises(ValueError):
                create_evidence(
                    self.store,
                    identity["url"],
                    originality_context=context,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
        children = [
            run["id"]
            for run in self.store.iter_runs(kind="evidence")
            if run["id"] != self.identifier
        ]
        self.assertEqual(len(children), 1)
        repaired = create_evidence(
            self.store,
            identity["url"],
            originality_context=context,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        self.assertEqual(repaired, children[0])
        self.assertEqual(self.store.get(repaired)["state"]["phase"], "prepared")
        verify_bundle(self.store, self.store.get(repaired))

    def test_calibration_revision_preserves_votes_and_original_scorecards(self):
        from broader_vids.learning.evidence import record_review, latest_revisions
        from broader_vids.learning.service import capture_rating, source_data
        from broader_vids.preferences import user_action, set_profile
        from broader_vids.recommendations.originality import assessment_context

        self.assess()
        set_profile(self.store, ["mathematics"])
        identity = self.store.get(self.identifier)["settings"]["identity"]
        observation = cached_quality(self.store, self.identifier)
        item = {
            **identity,
            "title": "Fixture",
            "domains": ["mathematics"],
            "quality_evidence": observation,
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,0,?)",
                (identity["canonical_id"], "youtube", json.dumps(item), "fixture"),
            )
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(
                self.store,
                identity["canonical_id"],
                "down",
                "Keep this explicit judgment",
                "quality",
            )
        before = [
            tuple(row) for row in self.store.db.execute("SELECT * FROM user_actions")
        ]
        original = self.store.get(self.identifier)
        metadata = {
            "description": "https://www.youtube.com/watch?v=GAkVaD1ihi4",
            "declared_copyright": 2,
        }
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)",
                (identity["canonical_id"], json.dumps(metadata)),
            )
        review = create_evidence(
            self.store,
            identity["url"],
            originality_context=assessment_context({"engagement": metadata}),
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        value = card()
        value["content_quality"] = 65
        assess_evidence(self.store, review, "augmented", lambda *args: (value, {}))
        revision = record_review(self.store, identity["canonical_id"], review)
        self.assertEqual(
            record_review(self.store, identity["canonical_id"], review), revision
        )
        self.assertEqual(
            [tuple(row) for row in self.store.db.execute("SELECT * FROM user_actions")],
            before,
        )
        self.assertEqual(self.store.get(self.identifier), original)
        self.assertEqual(
            capture_rating(self.store, identity["canonical_id"])["evidence_run"], review
        )
        self.assertEqual(
            source_data(self.store)["ratings"][0]["features"]["quality_revision"],
            revision,
        )
        self.assertFalse(
            source_data(self.store)["ratings"][0]["quality_review_pending"]
        )
        self.assertEqual(len(latest_revisions(self.store)), 1)

    def test_automatic_cleanup_retains_verified_quality_and_prevents_new_calls(self):
        result = self.assess()
        self.assertFalse(self.media.exists())
        self.assertEqual(result["state"]["media_retention"]["status"], "released")
        verify_bundle(self.store, result)
        with self.assertRaises(ValueError):
            verify_bundle(self.store, result, require_media=True)
        self.assertIsNotNone(cached_quality(self.store, self.identifier))
        assess_evidence(
            self.store,
            self.identifier,
            "augmented",
            lambda *args: self.fail("A cached score was reassessed"),
        )
        before = self.store.get(self.identifier)
        self.assertEqual(compact_evidence(self.store, self.identifier)["files"], 0)
        self.assertEqual(self.store.get(self.identifier), before)
        frame = Path(result["state"]["frames"][0]["path"])
        frame.write_bytes(b"Changed")
        with self.assertRaises(ValueError):
            cached_quality(self.store, self.identifier)

    def test_unfinished_and_baseline_only_media_is_preserved(self):
        preview = cleanup_storage(self.store)
        self.assertEqual(preview["retained_runs"], 1)
        self.assess("baseline")
        cleanup_storage(self.store, apply=True)
        self.assertTrue(self.media.is_file())
        self.assertEqual(self.store.get(self.identifier)["state"]["model_calls"], 1)

    def test_dry_run_and_busy_lease_do_not_mutate_evidence(self):
        with patch("broader_vids.evidence.retention.compact_evidence"):
            self.assess()
        before = self.store.get(self.identifier)
        stray = self.media.parent / "media.f123.webm.part"
        stray.write_bytes(b"obsolete intermediate")
        preview = cleanup_storage(self.store)
        self.assertEqual(preview["files"], 2)
        self.assertEqual(self.store.get(self.identifier), before)
        with self.store.lease(self.identifier):
            result = cleanup_storage(self.store, apply=True)
        self.assertEqual(len(result["issues"]), 1)
        self.assertTrue(self.media.exists())
        applied = cleanup_storage(self.store, apply=True)
        self.assertEqual(applied["files"], 2)
        self.assertFalse(stray.exists())

    def test_interrupted_deletion_recovers_without_losing_the_cached_score(self):
        original_unlink = Path.unlink

        def reject(path, *args, **kwargs):
            if path == self.media:
                raise PermissionError("Fixture sharing violation")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", reject):
            result = self.assess()
        self.assertEqual(
            result["state"]["media_retention"]["status"], "release_pending"
        )
        self.assertIsNotNone(cached_quality(self.store, self.identifier))
        self.media.unlink()
        self.assertIsNotNone(cached_quality(self.store, self.identifier))
        compact_evidence(self.store, self.identifier)
        self.assertEqual(
            self.store.get(self.identifier)["state"]["media_retention"]["status"],
            "released",
        )

    def test_missing_or_tampered_media_without_a_receipt_is_not_excused(self):
        self.media.unlink()
        with self.assertRaises(ValueError):
            verify_bundle(self.store, self.store.get(self.identifier))
        self.assertEqual(cleanup_storage(self.store)["retained_runs"], 1)

    def test_cleanup_refuses_a_symbolic_link_inside_an_evidence_directory(self):
        external = self.root / "external.mp4"
        external.write_bytes(b"Do not delete")
        link = self.media.parent / "media.symlink.mp4"
        try:
            link.symlink_to(external)
        except OSError:
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    owned_file(self.store, self.identifier, self.media)
        else:
            with self.assertRaises(ValueError):
                owned_file(self.store, self.identifier, link)
        self.assertEqual(external.read_bytes(), b"Do not delete")

    def test_released_metadata_reviews_reuse_small_assets_without_a_download(self):
        self.assess()
        review = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            reasoning_effort="medium",
            assessment_model="fixture-model",
        )
        copied = self.store.get(review)
        verify_bundle(self.store, copied)
        self.assertEqual(copied["state"]["inherited_from"], self.identifier)
        self.assertEqual(copied["state"]["resources"]["media_download_bytes"], 0)
        self.assertFalse(Path(copied["state"]["media"]["path"]).exists())
        self.assertTrue(Path(copied["state"]["frames"][0]["path"]).exists())

    def test_busy_source_does_not_leave_a_new_unprepared_review(self):
        self.assess()
        before = [run["id"] for run in self.store.list_runs()]
        with self.store.lease(self.identifier), self.assertRaises(ValueError):
            create_evidence(
                self.store,
                "https://www.youtube.com/watch?v=imYQJOgUx7Y",
                reasoning_effort="medium",
                assessment_model="fixture-model",
            )
        self.assertEqual([run["id"] for run in self.store.list_runs()], before)

    def test_cleanup_rejects_external_paths_and_never_deletes_user_originals(self):
        original = self.root / "original.mp4"
        original.write_bytes(b"User file")
        for path in (original, self.media.parent / ".." / "outside.mp4"):
            with self.assertRaises(ValueError):
                owned_file(self.store, self.identifier, path)
        self.assess()
        self.assertEqual(original.read_bytes(), b"User file")

    def test_atomic_write_removes_partial_files_after_failure(self):
        target = self.media.parent / "result.json"
        atomic_json(target, {"preserve": True})
        with self.assertRaises(ValueError):
            atomic_json(target, {"bad": float("nan")})
        self.assertEqual(json.loads(target.read_text()), {"preserve": True})
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_concurrent_atomic_writes_leave_one_complete_document_and_no_temps(self):
        target = self.media.parent / "result.json"
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(
                    atomic_json, target, {"index": i, "payload": "x" * 4000}
                )
                for i in range(20)
            ]
            for future in futures:
                future.result()
        result = json.loads(target.read_text())
        self.assertIn(result["index"], range(20))
        self.assertEqual(result["payload"], "x" * 4000)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_failed_metadata_fetch_closes_its_network_client(self):
        closed = []

        class Fetcher:
            def __init__(self, *args):
                pass

            def metadata(self, *args):
                raise EvidencePending("Fixture metadata failure")

            def close(self):
                closed.append(True)

        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        prepare_evidence(self.store, identifier, fetcher_type=Fetcher)
        self.assertEqual(closed, [True])

    def test_released_receipt_is_bound_to_its_original_media_descriptor(self):
        result = self.assess()
        result = copy.deepcopy(result)
        result["state"]["media"]["sha256"] = "unrelated-media"
        with self.assertRaises(ValueError):
            verify_bundle(self.store, result)


@unittest.skipUnless(
    importlib.util.find_spec("numpy"), "Optional ASR dependencies are required"
)
class TranscriptionMemoryTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("imageio_ffmpeg"), "FFmpeg is required"
    )
    def test_real_ffmpeg_chunk_uses_a_temporary_file_and_no_full_audio_capture(self):
        from broader_vids.evidence.asr_worker import transcribe_chunks

        class Model:
            def transcribe(self, audio, **kwargs):
                self.samples = len(audio)
                return iter(
                    [types.SimpleNamespace(start=0, end=0.5, text="Fixture")]
                ), types.SimpleNamespace(language="en")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0" * 16000)
            model = Model()
            cues, _ = transcribe_chunks(str(path), model, duration=0.5)
            self.assertEqual(model.samples, 8000)
            self.assertEqual(cues[0]["end"], 0.5)

    def test_chunking_bounds_audio_and_preserves_global_timestamps(self):
        from broader_vids.evidence.asr_worker import transcribe_chunks

        chunks, buffers, arrays = [], [], []

        def decoder(media, offset, seconds, target):
            chunks.append((offset, seconds))
            buffers.append(target)
            target.write(b"\0" * 16000 * 4)

        class Model:
            def transcribe(self, audio, **kwargs):
                if arrays and arrays[-1]() is not None:
                    raise AssertionError("The previous audio buffer was retained")
                arrays.append(weakref.ref(audio))
                self.assertions = (audio.nbytes, kwargs)
                return iter(
                    [types.SimpleNamespace(start=0, end=1, text="Speech")]
                ), types.SimpleNamespace(language="en")

        model = Model()
        cues, language = transcribe_chunks(
            "unused", model, duration=1201, decoder=decoder
        )
        self.assertEqual(chunks, [(0, 600), (600, 600), (1200, 1)])
        self.assertEqual([c["start"] for c in cues], [0, 600, 1200])
        self.assertEqual(language, "en")
        self.assertTrue(all(buffer.closed for buffer in buffers))
        self.assertTrue(all(array() is None for array in arrays))
        self.assertEqual(
            model.assertions, (64000, {"beam_size": 1, "vad_filter": True})
        )

    def test_oversized_audio_is_rejected_before_loading_an_array(self):
        from broader_vids.evidence.asr_worker import transcribe_chunks

        def oversized(media, offset, seconds, target):
            target.seek(600 * 16000 * 4 + 65536)
            target.write(b"\0")

        with patch("numpy.fromfile") as allocated, self.assertRaises(ValueError):
            transcribe_chunks("unused", object(), duration=600, decoder=oversized)
        allocated.assert_not_called()

    def test_decoder_failure_closes_its_temporary_buffer(self):
        from broader_vids.evidence.asr_worker import transcribe_chunks

        buffers = []

        def failure(media, offset, seconds, target):
            buffers.append(target)
            raise OSError("Fixture decoder failure")

        with self.assertRaises(OSError):
            transcribe_chunks("unused", object(), duration=10, decoder=failure)
        self.assertTrue(buffers[0].closed)
