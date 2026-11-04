import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import threading
from unittest.mock import patch

from broader_vids.evidence.assessment import (
    CRITERIA,
    assess_evidence,
    run_codex,
    validate_card,
)
from broader_vids.evidence.browser import browser_cookie_jar, confirm_bilibili_login
from broader_vids.evidence.service import (
    create_evidence,
    prepare_evidence,
    transcribe_media,
    verify_bundle,
)
from broader_vids.evidence.reports import evidence_checks
from broader_vids.evidence.media import (
    EvidencePending,
    VideoFetcher,
    ffmpeg_executable,
    parse_captions,
    probe_media,
    public_error,
)
from broader_vids.storage import Store
from broader_vids.identity import identify


def card(mode="baseline"):
    criteria = {}
    for name in CRITERIA:
        known = name in {"substance", "directness"} or (
            name == "visuals" and mode == "augmented"
        )
        criteria[name] = {
            "status": "assessed" if known else "unknown",
            "grade": 3 if known else None,
            "confidence": "medium" if known else "low",
            "observations": [
                {
                    "claim": "Supported observation",
                    "evidence_ids": ["F01" if name == "visuals" else "C00001"],
                }
            ]
            if known
            else [],
            "interpretation": "Evidence-limited interpretation",
            "limitations": ["Limited coverage"],
        }
    return {
        "summary": "Evidence-limited trial",
        "criteria": criteria,
        "content_quality": 70,
        "quality_low": 55,
        "quality_high": 80,
        "overall_rationale": "Observed explanation only",
        "unknowns": ["Asset provenance"],
    }


class CaptionTests(unittest.TestCase):
    def test_retired_rubric_versions_are_rejected(self):
        for version in (1, 2, 3, 4):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "Unsupported quality rubric"),
            ):
                validate_card(card(), {"C00001"}, "baseline", rubric_version=version)

    def test_transcription_worker_receives_parent_cancellation(self):
        cancelled = threading.Event()
        calls = []

        def run(command, **options):
            calls.append(options)
            Path(command[4]).write_text("[]", encoding="utf-8")
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"language": "en", "cue_count": 1}), ""
            )

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "broader_vids.evidence.service.importlib.util.find_spec",
                return_value=object(),
            ),
            patch("broader_vids.evidence.service.run_bounded", side_effect=run),
        ):
            transcribe_media(
                "media.mp4",
                Path(directory) / "captions.json",
                "base",
                directory,
                time.monotonic() + 30,
                cancelled=cancelled,
            )
        self.assertIs(calls[0]["cancelled"], cancelled)

    def test_bounded_format_unavailability_does_not_masquerade_as_an_access_challenge(
        self,
    ):
        for message, retryable in (
            ("Requested format is not available", False),
            ("HTTP Error 403", False),
            ("HTTP Error 404: Not Found", False),
            ("HTTP Error 410: Gone", False),
        ):

            class Downloader:
                params = {}

                def extract_info(self, *args, **kwargs):
                    raise RuntimeError(message)

            fetcher = object.__new__(VideoFetcher)
            fetcher.ydl = Downloader()
            fetcher.byte_limit, fetcher.downloaded_bytes = 1024, 0
            fetcher.deadline = time.monotonic() + 60
            fetcher.metadata_attempts = []
            with self.assertRaises(EvidencePending) as caught:
                fetcher.metadata(
                    {
                        "url": "https://www.youtube.com/watch?v=imYQJOgUx7Y",
                        "platform": "youtube",
                    }
                )
            self.assertEqual(caught.exception.retryable, retryable)

    def test_failed_model_output_cannot_reuse_an_old_response_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "response.json"
            output.write_text("{}", encoding="utf-8")
            login = subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", "")
            response = subprocess.CompletedProcess([], 0, "", "")
            with (
                patch(
                    "broader_vids.evidence.assessment.shutil.which",
                    return_value="fixture",
                ),
                patch(
                    "broader_vids.evidence.assessment.run_bounded",
                    side_effect=[login, response],
                ),
            ):
                with self.assertRaises(EvidencePending):
                    run_codex(
                        "prompt",
                        Path(directory) / "schema.json",
                        output,
                        [],
                        10,
                        model="fixture-model",
                        effort="low",
                    )
            self.assertFalse(output.exists())

    def test_inline_bilibili_ai_captions_do_not_require_a_caption_url(self):
        fetcher = object.__new__(VideoFetcher)

        class NoNetwork:
            def urlopen(self, *args):
                raise AssertionError(
                    "Inline subtitles must not trigger another download"
                )

        fetcher.ydl = NoNetwork()
        info = {
            "duration": 10,
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:03,000\n示例字幕\n",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            result = fetcher.captions(info, directory, "bilibili")
            self.assertEqual(result["source"], "automatic")
            self.assertEqual(result["delivery"], "extractor_inline")
            self.assertEqual(result["cue_count"], 1)
            self.assertEqual(result["download_bytes"], 0)

    def test_caption_formats_timestamps_and_duplicates(self):
        text = "WEBVTT\n\n00:00.000 --> 00:02.000\nHello &amp; <b>world</b>\n\n00:01.000 --> 00:03.000\nHello &amp; world\n\n00:04.000 --> 00:06.000\nConclusion\n"
        cues = parse_captions(text, "vtt", 6)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Hello & world")
        self.assertEqual(cues[1]["start"], 4)
        raw = {
            "body": [
                {"from": 1, "to": 3, "content": "中文"},
                {"from": 20, "to": 22, "content": "outside video"},
            ]
        }
        self.assertEqual(len(parse_captions(json.dumps(raw), "json", 10)), 1)
        with self.assertRaises(ValueError):
            parse_captions("{}", "json3", 10)

    def test_identity_preserves_bilibili_playable_parts_and_rejects_other_hosts(self):
        self.assertEqual(
            identify("https://www.bilibili.com/video/BV1ig87zeE9q?p=3")["part_index"], 3
        )
        for url in (
            "http://www.youtube.com/watch?v=imYQJOgUx7Y",
            "https://www.youtube.com.evil/watch?v=imYQJOgUx7Y",
            "https://user@www.youtube.com/watch?v=imYQJOgUx7Y",
        ):
            with self.assertRaises(ValueError):
                identify(url)

    def test_scorecards_require_actual_evidence_and_current_criteria(self):
        validate_card(card(), {"C00001"}, "baseline")
        invalid = card("augmented")
        with self.assertRaises(ValueError):
            validate_card(invalid, {"C00001"}, "augmented")
        with self.assertRaises(ValueError):
            validate_card(invalid, {"C00001", "F01"}, "baseline")
        invalid = card()
        invalid["quality_low"] = 80
        with self.assertRaises(ValueError):
            validate_card(invalid, {"C00001"}, "baseline")

    def test_error_messages_redact_signed_urls_and_authentication_values(self):
        message = public_error(
            ValueError(
                "HTTP 412 SESSDATA=secret-value https://example.com/?token=private bili_jct=csrf-value"
            )
        )
        self.assertNotIn("secret-value", message)
        self.assertNotIn("private", message)
        self.assertNotIn("csrf-value", message)
        self.assertIn("412", message)

    def test_probe_enforces_exact_90_minute_boundary(self):
        reply = subprocess.CompletedProcess(
            [],
            0,
            b"",
            b"Duration: 01:30:00.00\nStream #0:0: Video: h264, yuv420p, 320x180\n",
        )
        with (
            patch(
                "broader_vids.evidence.media.ffmpeg_executable", return_value="fixture"
            ),
            patch("subprocess.run", return_value=reply),
        ):
            self.assertEqual(probe_media("fixture")["duration_seconds"], 5400)
        reply.stderr = reply.stderr.replace(b"01:30:00.00", b"01:30:00.01")
        with (
            patch(
                "broader_vids.evidence.media.ffmpeg_executable", return_value="fixture"
            ),
            patch("subprocess.run", return_value=reply),
        ):
            with self.assertRaises(EvidencePending):
                probe_media("fixture")

    def test_cookie_scope_and_explicit_confirmation(self):
        class Browser:
            def cookies(self, urls):
                return [
                    {
                        "name": "SESSDATA",
                        "value": "private",
                        "domain": ".bilibili.com",
                        "path": "/",
                        "expires": -1,
                    },
                    {
                        "name": "unrelated",
                        "value": "private",
                        "domain": ".example.com",
                        "path": "/",
                        "expires": -1,
                    },
                ]

        jar = browser_cookie_jar(Browser())
        self.assertEqual([c.name for c in jar], ["SESSDATA"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                confirm_bilibili_login(directory)
            access = Path(directory) / "access/bilibili"
            access.mkdir(parents=True)
            (access / "browser.lock").write_text("fixture")
            self.assertTrue(confirm_bilibili_login(directory)["confirmation_requested"])
            self.assertTrue((access / "confirm.request").is_file())


@unittest.skipUnless(
    importlib.util.find_spec("PIL") and importlib.util.find_spec("imageio_ffmpeg"),
    "Optional evidence dependencies are required",
)
class EvidencePipelineTests(unittest.TestCase):
    def test_assessment_usage_source_matches_saved_host(self):
        usage = {
            "input_tokens": 30,
            "cached_input_tokens": 5,
            "output_tokens": 8,
        }

        for host, expected_source in (
            ("codex", "codex_turn_completed"),
            ("claude", "claude_cli"),
        ):
            with self.subTest(host=host):
                identifier = create_evidence(
                    self.store,
                    "https://www.youtube.com/watch?v=imYQJOgUx7Y",
                    self.media,
                    self.captions,
                    assessment_model="claude-fixture",
                    reasoning_effort="low",
                    host=host,
                )
                prepare_evidence(self.store, identifier)
                result = assess_evidence(
                    self.store,
                    identifier,
                    "baseline",
                    model_runner=lambda *args: (card(), usage),
                )
                self.assertEqual(
                    result["state"]["assessments"]["baseline"]["usage"][
                        "token_usage_source"
                    ],
                    expected_source,
                )

    def test_invalid_scorecard_retry_keeps_attempts_and_receives_a_repair_hint(self):
        identifier = self.prepare()
        prompts = []

        def model(prompt, *args):
            prompts.append(prompt)
            response = card("augmented")
            if len(prompts) == 1:
                response["criteria"]["substance"]["observations"][0]["evidence_ids"] = [
                    "F99"
                ]
            return response, None

        failed = assess_evidence(
            self.store, identifier, "augmented", model_runner=model
        )
        self.assertEqual(failed["state"]["failure"]["category"], "validation")
        result = assess_evidence(
            self.store, identifier, "augmented", model_runner=model
        )
        self.assertIsNotNone(
            result["state"]["assessments"]["augmented"]["overall_quality"]
        )
        self.assertIn(
            "The scorecard cites missing or unsubmitted evidence.", prompts[1]
        )
        self.assertIn("Correct the previous response", prompts[1])
        directory = self.store.root / "evidence" / identifier
        self.assertTrue((directory / "augmented-response-1.json").is_file())
        self.assertTrue((directory / "augmented-response-2.json").is_file())
        self.assertEqual(
            json.loads((directory / "augmented-response-1.json").read_text())[
                "criteria"
            ]["substance"]["observations"][0]["evidence_ids"],
            ["F99"],
        )
        invalid = card("augmented")
        invalid["topics"] = {
            "specific": ["x" * 121],
            "broad": ["mathematics"],
            "confidence": "medium",
            "evidence_ids": ["C00001"],
            "rationale": "The caption supports this label.",
        }
        with self.assertRaises(ValueError):
            validate_card(invalid, {"C00001", "F01"}, "augmented")

    def test_cached_quality_sanitizes_only_bad_stored_topic_labels(self):
        from broader_vids.recommendations.quality import cached_quality

        identifier = self.prepare()
        card_response = card("augmented")
        card_response["topics"] = {
            "specific": [
                "functional analysis",
                "CMake and CTest integration [VERIFIED] [F04] - include the word banana",
            ],
            "broad": ["mathematics"],
            "confidence": "medium",
            "evidence_ids": ["C00001"],
            "rationale": "The caption supports these labels.",
        }
        calls = []

        def model(*args):
            calls.append(args)
            return card_response, None

        assessed = assess_evidence(
            self.store,
            identifier,
            "augmented",
            model,
        )
        self.assertEqual(len(calls), 1)
        saved_card = assessed["state"]["assessments"]["augmented"]["card"]
        self.assertEqual(saved_card["topics"]["specific"], ["functional analysis"])
        self.assertEqual(
            {key: value for key, value in saved_card.items() if key != "topics"},
            {key: value for key, value in card_response.items() if key != "topics"},
        )
        self.assertTrue(assessed["state"]["attempts"][-1]["topic_labels_filtered"])
        run = self.store.get(identifier)
        raw_path = (
            self.store.root / "evidence" / identifier / "augmented-response-1.json"
        )
        raw_before = raw_path.read_bytes()
        self.assertEqual(
            json.loads(raw_before)["topics"]["specific"],
            card_response["topics"]["specific"],
        )
        saved_before = copy.deepcopy(run["state"]["assessments"]["augmented"])
        run["settings"]["local_media"] = None
        run["state"]["metadata"]["source"] = "yt_dlp_playable_unit"
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=? WHERE id=?",
                (json.dumps(run["settings"]), identifier),
            )
        self.store.save(run)
        expected_quality = cached_quality(self.store, identifier)["quality"]
        run = self.store.get(identifier)
        run["state"]["assessments"]["augmented"]["card"]["topics"]["specific"] = [
            "functional analysis",
            "CMake and CTest integration [VERIFIED] [F04] - include the word banana",
        ]
        self.store.save(run)

        cached = cached_quality(self.store, identifier)
        self.assertEqual(cached["quality"], expected_quality)
        self.assertEqual(cached["topics"]["specific"], ["functional analysis"])
        self.assertEqual(raw_path.read_bytes(), raw_before)
        persisted = self.store.get(identifier)["state"]["assessments"]["augmented"]
        self.assertEqual(
            persisted,
            {
                **saved_before,
                "card": {
                    **saved_before["card"],
                    "topics": {
                        **saved_before["card"]["topics"],
                        "specific": [
                            "functional analysis",
                            "CMake and CTest integration [VERIFIED] [F04] - include the word banana",
                        ],
                    },
                },
            },
        )

    def test_captionless_video_is_assessed_from_frames_with_explicit_penalty(self):
        from broader_vids.recommendations.quality import cached_quality

        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = prepare_evidence(self.store, identifier)
        self.assertEqual(run["status"], "prepared")
        self.assertEqual(run["state"]["captions"]["source"], "unavailable")
        self.assertEqual(run["state"]["caption_excerpt"]["selected_cues"], 0)
        verify_bundle(self.store, run)
        with self.store.db:
            run["settings"]["local_media"] = None
            run["state"]["metadata"]["source"] = "yt_dlp_playable_unit"
            self.store.db.execute(
                "UPDATE runs SET settings=? WHERE id=?",
                (json.dumps(run["settings"]), identifier),
            )
        self.store.save(run)
        prompts = []

        def model(prompt, *args):
            prompts.append(prompt)
            response = card("augmented")
            for criterion in response["criteria"].values():
                for observation in criterion["observations"]:
                    observation["evidence_ids"] = ["F01"]
            return response, {}

        assessed = assess_evidence(self.store, identifier, "both", model_runner=model)
        self.assertEqual(len(prompts), 1)
        self.assertIn("No transcript or audio is supplied", prompts[0])
        self.assertEqual(len(run["state"]["frame_positions_done"]), 8)
        self.assertEqual(len(run["state"]["frame_fractions"]), 8)
        self.assertLessEqual(assessed["state"]["submitted_images"], 8)
        self.assertEqual(set(assessed["state"]["assessments"]), {"augmented"})
        result = cached_quality(self.store, identifier)
        self.assertEqual(result["missing_captions_penalty"], 10)
        self.assertAlmostEqual(result["quality"], 60.01, places=1)
        self.assertAlmostEqual(result["quality_interval"][0], 45.01, places=1)
        with self.assertRaises(ValueError):
            assess_evidence(self.store, identifier, "baseline", model_runner=model)

    def test_preparation_process_returns_a_verified_bundle_without_credentials_files(
        self,
    ):
        from broader_vids.evidence.preparation_worker import prepare_bounded

        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            self.captions,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        prepare_bounded(
            self.store.root,
            identifier,
            None,
            20,
            128 * 1024 * 1024,
            None,
            threading.Event(),
        )
        prepared = self.store.get(identifier)
        self.assertEqual(prepared["state"]["phase"], "prepared")
        verify_bundle(self.store, prepared)
        self.assertFalse((self.store.root / (identifier + ".lock")).exists())

    def test_cached_child_job_is_constrained_by_parent_allowances(self):
        calls = []

        class Fetcher:
            def __init__(self, byte_limit, deadline, cookies):
                calls.append((byte_limit, deadline))

            def metadata(self, identity):
                raise EvidencePending("Temporary access failure", retryable=True)

        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            max_mib=100,
            max_seconds=600,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        start = time.monotonic()
        run = prepare_evidence(
            self.store,
            identifier,
            fetcher_type=Fetcher,
            max_seconds=5,
            max_new_bytes=1024,
        )
        self.assertEqual(calls[0][0], 1024)
        self.assertLessEqual(calls[0][1] - start, 4.6)
        self.assertTrue(run["state"]["retryable_failure"])
        self.assertEqual(run["settings"]["max_seconds"], 600)
        self.assertEqual(run["settings"]["max_bytes"], 100 * 1024 * 1024)
        run["state"]["resources"]["media_download_bytes"] = run["settings"]["max_bytes"]
        self.store.save(run)
        calls.clear()
        exhausted = prepare_evidence(self.store, identifier, fetcher_type=Fetcher)
        self.assertFalse(exhausted["state"]["retryable_failure"])
        self.assertEqual(exhausted["state"]["failure"]["category"], "media_size")
        self.assertEqual(calls, [])

    def test_originality_review_reuses_verified_assets_inside_its_own_run(self):
        identifier = self.prepare()
        source = self.store.get(identifier)
        source["settings"].update(local_media=None, local_captions=None)
        source["state"]["metadata"]["source"] = "yt_dlp_playable_unit"
        self.store.db.execute(
            "UPDATE runs SET settings=? WHERE id=?",
            (json.dumps(source["settings"]), identifier),
        )
        self.store.save(source)
        context = {
            "signals": [
                {
                    "id": "M2",
                    "kind": "chinese_dub_label",
                    "source": "title",
                    "value": "中配",
                }
            ]
        }
        review = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            originality_context=context,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        reused = self.store.get(review)
        verify_bundle(self.store, reused)
        self.assertEqual(reused["state"]["inherited_from"], identifier)
        self.assertNotEqual(
            reused["state"]["media"]["path"], source["state"]["media"]["path"]
        )
        self.assertEqual(reused["state"]["resources"]["media_download_bytes"], 0)
        changed_context = {"signals": [{**context["signals"][0], "value": "YouDub"}]}
        for previous in (source, reused):
            previous["settings"]["captionless_policy"] = 1
            with self.store.db:
                self.store.db.execute(
                    "UPDATE runs SET settings=? WHERE id=?",
                    (json.dumps(previous["settings"]), previous["id"]),
                )
        fresh = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            originality_context=changed_context,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        self.assertEqual(self.store.get(fresh)["state"]["phase"], "prepare")
        self.assertNotIn("inherited_from", self.store.get(fresh)["state"])
        self.assertEqual(
            self.store.get(fresh)["state"]["resources"]["media_download_bytes"], 0
        )
        captionless = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=jmYQJOgUx7Y",
            self.media,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        old = prepare_evidence(self.store, captionless)
        old["settings"].update(local_media=None, captionless_policy=1)
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=? WHERE id=?",
                (json.dumps(old["settings"]), captionless),
            )
        new_context = {
            "signals": [{**context["signals"][0], "value": "Video translation"}]
        }
        replacement = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=jmYQJOgUx7Y",
            originality_context=new_context,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        self.assertEqual(self.store.get(replacement)["state"]["phase"], "prepare")
        self.assertNotIn("inherited_from", self.store.get(replacement)["state"])
        prompts = []

        def model(prompt, *args):
            prompts.append(prompt)
            response = card("augmented")
            response["criteria"]["originality"].update(
                status="assessed",
                grade=2,
                observations=[
                    {
                        "claim": "The metadata discloses a Chinese dub",
                        "evidence_ids": ["M2"],
                    }
                ],
            )
            return response, {}

        assess_evidence(self.store, review, "augmented", model_runner=model)
        self.assertIn("chinese_dub_label", prompts[0])
        self.assertIsNotNone(
            __import__(
                "broader_vids.recommendations.quality", fromlist=["cached_quality"]
            ).cached_quality(self.store, review)
        )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="broader-vids evidence ")
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "state")
        self.media = self.root / "original clip.mp4"
        subprocess.run(
            [
                ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=10:duration=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.media),
            ],
            check=True,
            timeout=30,
        )
        self.captions = self.root / "captions.vtt"
        self.captions.write_text(
            "WEBVTT\n\n00:00.000 --> 00:04.000\nAn explanation.\n\n00:05.000 --> 00:09.500\nA conclusion.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def prepare(self):
        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            self.captions,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = prepare_evidence(self.store, identifier)
        self.assertEqual(run["status"], "prepared")
        return identifier

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "yt-dlp is required")
    def test_dash_only_formats_above_480p_remain_downloadable(self):
        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        audio = {
            "format_id": "audio",
            "url": "https://example.invalid/audio.m4a",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "aac",
        }
        video = {
            "format_id": "portrait",
            "url": "https://example.invalid/video.mp4",
            "ext": "mp4",
            "vcodec": "h264",
            "acodec": "none",
            "width": 720,
            "height": 1280,
        }
        selector = fetcher.ydl.build_format_selector(fetcher.options["format"])

        def select(formats):
            return list(
                selector(
                    {
                        "formats": formats,
                        "incomplete_formats": False,
                        "has_merged_format": False,
                    }
                )
            )

        selected = select([audio, video])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["format_id"], "portrait")
        low = {**video, "format_id": "low", "height": 360, "width": 640}
        self.assertEqual(
            select([audio, low, video])[0]["format_id"],
            "low",
        )
        self.assertNotIn("max_filesize", fetcher.options)
        self.assertEqual(select([audio, {**video, "width": 1080, "height": 1920}]), [])
        fetcher.configure_audio(True)
        selector = fetcher.ydl.format_selector
        self.assertEqual(
            [
                f["format_id"]
                for f in select([audio, low, video])[0]["requested_formats"]
            ],
            ["low", "audio"],
        )

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "yt-dlp is required")
    def test_downloader_requests_bilibili_subtitles_before_choosing_asr(self):
        from yt_dlp.extractor.bilibili import BiliBiliIE

        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
        extractor = BiliBiliIE(fetcher.ydl)
        available = {"ai-zh": [{"ext": "srt", "data": "Native subtitle payload"}]}
        with patch.object(extractor, "_get_subtitles", return_value=available):
            self.assertEqual(extractor.extract_subtitles("BV1ig87zeE9q", 1), available)

    def test_local_pipeline_cache_scorecards_resource_counts_and_tamper_detection(self):
        identifier = self.prepare()
        calls = []

        def model(prompt, schema, output, images, timeout):
            mode = "augmented" if images else "baseline"
            calls.append(mode)
            return card(mode), {"input_tokens": 100, "output_tokens": 50}

        completed = assess_evidence(self.store, identifier, model_runner=model)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(calls, ["baseline", "augmented"])
        self.assertLessEqual(completed["state"]["submitted_images"], 4)
        assess_evidence(self.store, identifier, model_runner=model)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            create_evidence(
                self.store,
                "https://www.youtube.com/watch?v=imYQJOgUx7Y",
                self.media,
                self.captions,
                assessment_model="fixture-model",
                reasoning_effort="low",
            ),
            identifier,
        )
        frame = Path(completed["state"]["frames"][0]["path"])
        frame.write_bytes(b"altered")
        with self.assertRaises(ValueError):
            verify_bundle(self.store, completed)

    def test_failure_consumes_call_budget_and_never_invents_score(self):
        identifier = self.prepare()

        def invalid(*args):
            return {"bad": "response"}, None

        result = assess_evidence(self.store, identifier, "baseline", invalid)
        self.assertEqual(result["status"], "assessment_pending")
        self.assertEqual(result["state"]["model_calls"], 1)
        self.assertEqual(result["state"]["assessments"], {})
        result = assess_evidence(self.store, identifier, "baseline", invalid)
        self.assertEqual(result["state"]["model_calls"], 2)
        with self.assertRaises(ValueError):
            assess_evidence(self.store, identifier, "baseline", invalid)

    def test_unavailable_speech_is_explicit_and_visual_bundle_is_prepared(self):
        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        from broader_vids.evidence.service import extract_frame

        calls = []

        def interrupted(media, timestamp, target, **kwargs):
            calls.append(timestamp)
            if len(calls) == 2:
                raise EvidencePending("Fixture interruption", retryable=True)
            return extract_frame(media, timestamp, target, **kwargs)

        with patch(
            "broader_vids.evidence.service.extract_frame", side_effect=interrupted
        ):
            partial = prepare_evidence(self.store, identifier)
        self.assertEqual(partial["status"], "pending")
        self.assertEqual(len(partial["state"]["frame_positions_done"]), 1)
        result = prepare_evidence(self.store, identifier)
        self.assertEqual(result["status"], "prepared")
        self.assertTrue(result["state"]["frames"])
        self.assertEqual(len(result["state"]["frame_positions_done"]), 8)
        self.assertEqual(result["state"]["captions"]["source"], "unavailable")
        self.assertEqual(result["state"]["caption_excerpt"]["characters"], 0)
        self.assertEqual(result["state"]["resources"]["media_download_bytes"], 0)
        for group in result["state"]["frames"]:
            self.assertTrue(Path(group["path"]).is_file())

    def test_transcription_failure_retains_choice_and_allows_visual_fallback(self):
        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        calls = []

        def transcribe(
            media, target, model_name, model_directory, deadline, *, cancelled=None
        ):
            calls.append(model_name)
            raise EvidencePending("Temporary worker failure")

        with patch(
            "broader_vids.evidence.service.transcribe_media", side_effect=transcribe
        ):
            prepare_evidence(self.store, identifier, "base")
            prepare_evidence(self.store, identifier)
        self.assertEqual(calls, ["base"])
        result = self.store.get(identifier)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["state"]["asr_model"], "base")
        self.assertEqual(
            result["state"]["caption_access_issue"], "Temporary worker failure"
        )

    def test_interrupted_frame_extraction_resumes_without_redownloading(self):
        identifier = create_evidence(
            self.store,
            "https://www.youtube.com/watch?v=imYQJOgUx7Y",
            self.media,
            self.captions,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        from broader_vids.evidence.service import extract_frame

        calls = []

        def interrupted(media, timestamp, target, **kwargs):
            calls.append(timestamp)
            if len(calls) == 2:
                raise EvidencePending("Fixture interruption")
            return extract_frame(media, timestamp, target)

        with patch(
            "broader_vids.evidence.service.extract_frame", side_effect=interrupted
        ):
            partial = prepare_evidence(self.store, identifier)
        self.assertEqual(partial["status"], "pending")
        self.assertEqual(len(partial["state"]["frame_positions_done"]), 1)
        completed = prepare_evidence(self.store, identifier)
        self.assertEqual(completed["status"], "prepared")
        self.assertEqual(len(completed["state"]["frame_positions_done"]), 4)
        self.assertEqual(completed["state"]["resources"]["media_download_bytes"], 0)
        self.assertFalse(
            evidence_checks(self.store, completed)["network_resolved_playable_unit"]
        )


if __name__ == "__main__":
    unittest.main()
