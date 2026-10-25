import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from deepvids.assessment import CRITERIA, assess_evidence, validate_card
from deepvids.browser_access import browser_cookie_jar, confirm_bilibili_login
from deepvids.evidence import add_evidence_frame, create_evidence, evidence_checks, evidence_report, prepare_evidence, verify_bundle
from deepvids.evidence_media import EvidencePending, VideoFetcher, compact_captions, ffmpeg_executable, identify, parse_captions, probe_media, public_error
from deepvids.storage import Store


def card(mode="baseline"):
    criteria = {}
    for name in CRITERIA:
        known = name in {"substance", "directness"} or (name == "visuals" and mode == "augmented")
        criteria[name] = {"status": "assessed" if known else "unknown", "grade": 3 if known else None,
            "confidence": "medium" if known else "low", "observations": [{"claim": "Supported observation", "evidence_ids": ["F01" if name == "visuals" else "C00001"]}] if known else [],
            "interpretation": "Evidence-limited interpretation", "limitations": ["Limited coverage"]}
    return {"summary": "Evidence-limited trial", "criteria": criteria, "content_quality": 70,
            "quality_low": 55, "quality_high": 80, "overall_rationale": "Observed explanation only", "unknowns": ["Narration type", "Asset provenance"]}


class CaptionTests(unittest.TestCase):
    def test_inline_bilibili_ai_captions_do_not_require_a_caption_url(self):
        fetcher = object.__new__(VideoFetcher)
        class NoNetwork:
            def urlopen(self, *args):
                raise AssertionError("Inline subtitles must not trigger another download")
        fetcher.ydl = NoNetwork()
        info = {"duration": 10, "subtitles": {"ai-zh": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:03,000\n示例字幕\n"}]}}
        with tempfile.TemporaryDirectory() as directory:
            result = fetcher.captions(info, directory, "bilibili")
            self.assertEqual(result["source"], "automatic")
            self.assertEqual(result["delivery"], "extractor_inline")
            self.assertEqual(result["cue_count"], 1)
            self.assertEqual(result["download_bytes"], 0)
    def test_caption_formats_timestamps_and_duplicates(self):
        text = 'WEBVTT\n\n00:00.000 --> 00:02.000\nHello &amp; <b>world</b>\n\n00:01.000 --> 00:03.000\nHello &amp; world\n\n00:04.000 --> 00:06.000\nConclusion\n'
        cues = parse_captions(text, "vtt", 6)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Hello & world")
        self.assertEqual(cues[1]["start"], 4)
        raw = {"body": [{"from": 1, "to": 3, "content": "中文"}, {"from": 20, "to": 22, "content": "outside video"}]}
        self.assertEqual(len(parse_captions(json.dumps(raw), "json", 10)), 1)
        with self.assertRaises(ValueError):
            parse_captions('{}', "json3", 10)

    def test_compaction_keeps_start_middle_conclusion_with_budget(self):
        cues = [{"id": f"C{i:05d}", "start": i, "end": i + 1, "text": "x" * 100} for i in range(1000)]
        selected = compact_captions(cues, 1000)
        self.assertLessEqual(sum(len(c["text"]) for c in selected), 1000)
        self.assertEqual(selected[0]["id"], "C00000")
        self.assertEqual(selected[-1]["id"], "C00999")
        self.assertTrue(any(490 < c["start"] < 510 for c in selected))

    def test_identity_preserves_bilibili_playable_parts_and_rejects_other_hosts(self):
        self.assertEqual(identify('https://www.bilibili.com/video/BV1ig87zeE9q?p=3')["part_index"], 3)
        for url in ('http://www.youtube.com/watch?v=imYQJOgUx7Y', 'https://www.youtube.com.evil/watch?v=imYQJOgUx7Y', 'https://user@www.youtube.com/watch?v=imYQJOgUx7Y'):
            with self.assertRaises(ValueError):
                identify(url)

    def test_scorecards_require_actual_evidence_and_leave_audio_unknown(self):
        validate_card(card(), {"C00001"}, "baseline")
        invalid = card()
        invalid["criteria"]["narration"].update(status="assessed", grade=3)
        with self.assertRaises(ValueError):
            validate_card(invalid, {"C00001"}, "baseline")
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
        message = public_error(ValueError('HTTP 412 SESSDATA=secret-value https://example.com/?token=private bili_jct=csrf-value'))
        self.assertNotIn('secret-value', message)
        self.assertNotIn('private', message)
        self.assertNotIn('csrf-value', message)
        self.assertIn('412', message)

    def test_probe_enforces_exact_90_minute_boundary(self):
        reply = subprocess.CompletedProcess([], 0, b'', b'Duration: 01:30:00.00\nStream #0:0: Video: h264, yuv420p, 320x180\n')
        with patch('deepvids.evidence_media.ffmpeg_executable', return_value='fixture'), patch('subprocess.run', return_value=reply):
            self.assertEqual(probe_media('fixture')['duration_seconds'], 5400)
        reply.stderr = reply.stderr.replace(b'01:30:00.00', b'01:30:00.01')
        with patch('deepvids.evidence_media.ffmpeg_executable', return_value='fixture'), patch('subprocess.run', return_value=reply):
            with self.assertRaises(EvidencePending):
                probe_media('fixture')

    def test_cookie_scope_and_explicit_confirmation(self):
        class Browser:
            def cookies(self, urls):
                return [{"name": "SESSDATA", "value": "private", "domain": ".bilibili.com", "path": "/", "expires": -1},
                        {"name": "unrelated", "value": "private", "domain": ".example.com", "path": "/", "expires": -1}]
        jar = browser_cookie_jar(Browser())
        self.assertEqual([c.name for c in jar], ["SESSDATA"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                confirm_bilibili_login(directory)
            access = Path(directory) / 'access/bilibili'
            access.mkdir(parents=True)
            (access / 'browser.lock').write_text('fixture')
            self.assertTrue(confirm_bilibili_login(directory)['confirmation_requested'])
            self.assertTrue((access / 'confirm.request').is_file())


@unittest.skipUnless(importlib.util.find_spec('PIL') and importlib.util.find_spec('imageio_ffmpeg'), 'Optional evidence dependencies are required')
class EvidencePipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='deepvids evidence ')
        self.root = Path(self.directory.name)
        self.store = Store(self.root / 'state')
        self.media = self.root / 'original clip.mp4'
        subprocess.run([ffmpeg_executable(), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=10:duration=10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(self.media)], check=True, timeout=30)
        self.captions = self.root / 'captions.vtt'
        self.captions.write_text('WEBVTT\n\n00:00.000 --> 00:04.000\nAn explanation.\n\n00:05.000 --> 00:09.500\nA conclusion.\n', encoding='utf-8')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def prepare(self):
        identifier = create_evidence(self.store, 'https://www.youtube.com/watch?v=imYQJOgUx7Y', self.media, self.captions)
        run = prepare_evidence(self.store, identifier)
        self.assertEqual(run['status'], 'prepared')
        return identifier

    @unittest.skipUnless(importlib.util.find_spec('yt_dlp'), 'yt-dlp is required')
    def test_downloader_requests_bilibili_subtitles_before_choosing_asr(self):
        from yt_dlp.extractor.bilibili import BiliBiliIE
        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
        extractor = BiliBiliIE(fetcher.ydl)
        available = {'ai-zh': [{'ext': 'srt', 'data': 'Native subtitle payload'}]}
        with patch.object(extractor, '_get_subtitles', return_value=available):
            self.assertEqual(extractor.extract_subtitles('BV1ig87zeE9q', 1), available)

    def test_local_pipeline_cache_scorecards_resource_counts_and_tamper_detection(self):
        identifier = self.prepare()
        calls = []
        def model(prompt, schema, output, images, timeout):
            mode = 'augmented' if images else 'baseline'
            calls.append(mode)
            return card(mode), {'input_tokens': 100, 'output_tokens': 50}
        completed = assess_evidence(self.store, identifier, model_runner=model)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(calls, ['baseline', 'augmented'])
        self.assertLessEqual(completed['state']['submitted_images'], 4)
        assess_evidence(self.store, identifier, model_runner=model)
        self.assertEqual(len(calls), 2)
        self.assertEqual(create_evidence(self.store, 'https://www.youtube.com/watch?v=imYQJOgUx7Y', self.media, self.captions), identifier)
        frame = Path(completed['state']['frames'][0]['path'])
        frame.write_bytes(b'altered')
        with self.assertRaises(ValueError):
            verify_bundle(self.store, completed)

    def test_failure_consumes_call_budget_and_never_invents_score(self):
        identifier = self.prepare()
        def invalid(*args):
            return {'bad': 'response'}, None
        result = assess_evidence(self.store, identifier, 'baseline', invalid)
        self.assertEqual(result['status'], 'assessment_pending')
        self.assertEqual(result['state']['model_calls'], 1)
        self.assertEqual(result['state']['assessments'], {})
        result = assess_evidence(self.store, identifier, 'baseline', invalid)
        self.assertEqual(result['state']['model_calls'], 2)
        with self.assertRaises(ValueError):
            assess_evidence(self.store, identifier, 'baseline', invalid)

    def test_incomplete_speech_evidence_is_pending_not_empty_or_success(self):
        identifier = create_evidence(self.store, 'https://www.youtube.com/watch?v=imYQJOgUx7Y', self.media)
        result = prepare_evidence(self.store, identifier)
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(result['state']['frames'])
        self.assertIsNone(result['state']['captions'])
        with self.assertRaises(ValueError):
            assess_evidence(self.store, identifier)

    def test_targeted_frames_need_a_reason_and_respect_eight_image_cap(self):
        identifier = self.prepare()
        with self.assertRaises(ValueError):
            add_evidence_frame(self.store, identifier, 3, '')
        with self.assertRaises(ValueError):
            add_evidence_frame(self.store, identifier, 11, 'Inspect a slide')
        for index in range(4):
            add_evidence_frame(self.store, identifier, 2 + index, 'Inspect small text', 1024)
        self.assertEqual(len(self.store.get(identifier)['state']['frames']), 8)
        with self.assertRaises(ValueError):
            add_evidence_frame(self.store, identifier, 7, 'More context')

    def test_interrupted_frame_extraction_resumes_without_redownloading(self):
        identifier = create_evidence(self.store, 'https://www.youtube.com/watch?v=imYQJOgUx7Y', self.media, self.captions)
        from deepvids.evidence import extract_frame
        calls = []
        def interrupted(media, timestamp, target):
            calls.append(timestamp)
            if len(calls) == 2:
                raise EvidencePending('Fixture interruption')
            return extract_frame(media, timestamp, target)
        with patch('deepvids.evidence.extract_frame', side_effect=interrupted):
            partial = prepare_evidence(self.store, identifier)
        self.assertEqual(partial['status'], 'pending')
        self.assertEqual(len(partial['state']['frame_positions_done']), 1)
        completed = prepare_evidence(self.store, identifier)
        self.assertEqual(completed['status'], 'prepared')
        self.assertEqual(len(completed['state']['frame_positions_done']), 4)
        self.assertEqual(completed['state']['resources']['media_download_bytes'], 0)
        self.assertFalse(evidence_checks(self.store, completed)['network_resolved_playable_unit'])


if __name__ == '__main__':
    unittest.main()
