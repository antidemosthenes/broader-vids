from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from broader_vids.evidence.bilibili_download import ReliableBilibiliIE
from broader_vids.evidence.media import EvidencePending, VideoFetcher


class DownloadStabilityTests(unittest.TestCase):
    def test_backup_route_survives_real_ytdlp_single_stream_reselection(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 30)
            self.addCleanup(fetcher.close)
            fetcher.ydl.params["check_formats"] = False
            primary = "https://primary.bilivideo.com/video"
            backup = "https://backup.bilivideo.com/video"
            info = fetcher.ydl.process_ie_result(
                {
                    "id": "fixture",
                    "title": "Fixture",
                    "extractor": "Bilibili",
                    "extractor_key": "BiliBili",
                    "formats": [
                        {
                            "format_id": "video",
                            "url": primary,
                            "backup_urls": [backup],
                            "height": 360,
                            "width": 640,
                            "ext": "mp4",
                            "vcodec": "h264",
                            "acodec": "none",
                        }
                    ],
                },
                download=False,
            )
            seen = []

            def transfer(selected):
                seen.append(selected["url"])
                if len(seen) == 1:
                    raise TimeoutError("read timed out")
                (Path(directory) / "media.mp4").write_bytes(b"fixture")

            with (
                patch.object(fetcher.ydl, "process_info", side_effect=transfer),
                patch("broader_vids.evidence.media.time.sleep"),
            ):
                fetcher.download(info, directory)
            self.assertEqual(seen, [primary, backup])

    def fetcher(self, directory, failures):
        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        seen = []

        def download(info, **kwargs):
            seen.append(info["url"])
            if failures:
                error = failures.pop(0)
                if error:
                    raise error
            (Path(directory) / "media.mp4").write_bytes(b"fixture")

        fetcher.ydl.process_ie_result = download
        return fetcher, seen

    def test_timeout_switches_to_offered_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(
                directory, [TimeoutError("read timed out"), None]
            )
            with patch("broader_vids.evidence.media.time.sleep"):
                fetcher.download(
                    {
                        "url": "https://primary.bilivideo.com/video",
                        "backup_urls": ["https://backup.bilivideo.com/video"],
                    },
                    directory,
                )
            self.assertEqual(
                seen,
                [
                    "https://primary.bilivideo.com/video",
                    "https://backup.bilivideo.com/video",
                ],
            )
            self.assertEqual(
                [a["status"] for a in fetcher.download_attempts],
                ["failed", "completed"],
            )

    def test_access_challenge_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(
                directory, [RuntimeError("HTTP Error 429: Too Many Requests")]
            )
            with self.assertRaises(EvidencePending):
                fetcher.download(
                    {
                        "url": "https://primary.bilivideo.com/video",
                        "backup_urls": ["https://backup.bilivideo.com/video"],
                    },
                    directory,
                )
            self.assertEqual(len(seen), 1)

    def test_retries_remain_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(
                directory, [TimeoutError("read timed out")] * 5
            )
            with (
                patch("broader_vids.evidence.media.time.sleep"),
                self.assertRaises(EvidencePending),
            ):
                fetcher.download(
                    {"url": "https://primary.bilivideo.com/video"}, directory
                )
            self.assertEqual(len(seen), 2)

    def test_slow_transfer_can_trigger_failover(self):
        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        fetcher._can_failover = True
        fetcher._stream_started = {"fixture": (time.monotonic() - 16, 0)}
        with self.assertRaises(ConnectionError):
            fetcher._progress(
                {
                    "filename": "fixture",
                    "status": "downloading",
                    "downloaded_bytes": 1024,
                }
            )

    def test_exhausted_deadline_does_not_launch_a_download(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(directory, [])
            fetcher.deadline = time.monotonic() - 1
            with self.assertRaises(EvidencePending):
                fetcher.download(
                    {"url": "https://primary.bilivideo.com/video"}, directory
                )
            self.assertEqual(seen, [])

    def test_fast_start_does_not_hide_a_later_slow_transfer(self):
        from collections import deque

        fetcher = VideoFetcher(16 * 1024 * 1024, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        fetcher._can_failover = True
        now = time.monotonic()
        fetcher._stream_started = {"fixture": (now - 30, 0)}
        fetcher._stream_samples = {
            "fixture": deque(
                [
                    (now - 30, 0),
                    (now - 16, 8 * 1024 * 1024),
                    (now - 15, 8 * 1024 * 1024 + 1024),
                ]
            )
        }
        with self.assertRaises(ConnectionError):
            fetcher._progress(
                {
                    "filename": "fixture",
                    "status": "downloading",
                    "downloaded_bytes": 8 * 1024 * 1024 + 2048,
                }
            )

    def test_missing_caption_deduction_survives_learned_ranking(self):
        from broader_vids.learning.service import final_score

        components = {"quality": 80, "interest": 50}
        snapshot = {
            "id": "fixture",
            "profile": {},
            "models": {"overall": {"observations": 0}},
        }
        weights = {"quality": 0.6, "interest": 0.4}
        features = {"raw_quality": 70, "coverage": 1}
        with (
            patch(
                "broader_vids.learning.service.score_components",
                return_value=(components, {}),
            ),
            patch("broader_vids.learning.service.overall_features", return_value={}),
            patch(
                "broader_vids.learning.service.predict",
                return_value={"probability": 0.5},
            ),
            patch("broader_vids.learning.service.missing_variance", return_value=0),
        ):
            baseline, _, _ = final_score(features, snapshot, components, weights)
            reduced, parts, _ = final_score(
                {**features, "missing_captions_penalty": 10},
                snapshot,
                components,
                weights,
            )
        self.assertEqual(parts["quality"], 70)
        self.assertEqual(baseline - reduced, 6)

    def test_resume_does_not_charge_previously_downloaded_bytes_again(self):
        fetcher = VideoFetcher(1000, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        fetcher._spent_base = 100
        fetcher._resume_offsets = {"fixture": 100}
        fetcher._progress({"filename": "fixture", "downloaded_bytes": 150})
        self.assertEqual(fetcher.downloaded_bytes, 150)
        with self.assertRaises(EvidencePending):
            fetcher._progress({"filename": "fixture", "downloaded_bytes": 1001})

    def test_offered_cdn_alternatives_are_preserved_and_fast_route_preferred(self):
        primary = "https://primary.bilivideo.com/video"
        backup = "https://backup.akamaized.net/video"
        info = {"dash": {"video": [{"baseUrl": primary, "backupUrl": [backup]}]}}
        with patch(
            "yt_dlp.extractor.bilibili.BiliBiliIE.extract_formats",
            return_value=[{"url": primary}],
        ):
            formats = ReliableBilibiliIE().extract_formats(info)
        self.assertEqual(formats[0]["url"], backup)
        self.assertEqual(formats[0]["backup_urls"], [primary])
