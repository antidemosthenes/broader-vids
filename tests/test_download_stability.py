from pathlib import Path
import http.server
import errno
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

from broader_vids.evidence.media import EvidencePending, VideoFetcher
from broader_vids.evidence.failures import failure_details


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

    def test_nominal_240p_format_is_selected_before_358p(self):
        fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 30)
        self.addCleanup(fetcher.close)
        fetcher.ydl.params["check_formats"] = False
        info = fetcher.ydl.process_ie_result(
            {
                "id": "fixture",
                "title": "Fixture",
                "extractor": "YouTube",
                "extractor_key": "Youtube",
                "formats": [
                    {
                        "format_id": str(height),
                        "url": f"https://example.invalid/{height}.mp4",
                        "height": height,
                        "width": 640,
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "none",
                    }
                    for height in (238, 358)
                ],
            },
            download=False,
        )
        self.assertEqual(info["format_id"], "238")

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

    def test_both_platforms_retry_metadata_three_times_then_recover_or_skip(self):
        for platform in ("youtube", "bilibili"):
            for recover in (False, True):
                with self.subTest(platform=platform, recover=recover):
                    fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 60)
                    try:
                        video_id = (
                            "0000000001A" if platform == "youtube" else "BV1111111111"
                        )
                        identity = {
                            "url": "https://example.invalid/video",
                            "video_id": video_id,
                            "platform": platform,
                            "part_index": 1,
                        }
                        responses = [TimeoutError("read timed out")] * 3 + (
                            [{"id": video_id, "duration": 300}]
                            if recover
                            else [TimeoutError("read timed out")]
                        )
                        with (
                            patch.object(
                                fetcher.ydl, "extract_info", side_effect=responses
                            ) as extract,
                            patch("broader_vids.evidence.media.time.sleep"),
                        ):
                            if recover:
                                self.assertEqual(
                                    fetcher.metadata(identity)["id"], video_id
                                )
                            else:
                                with self.assertRaises(EvidencePending) as caught:
                                    fetcher.metadata(identity)
                                self.assertFalse(caught.exception.retryable)
                                self.assertEqual(
                                    caught.exception.category, "media_transfer"
                                )
                            self.assertEqual(extract.call_count, 4)
                        self.assertEqual(len(fetcher.metadata_attempts), 4)
                        self.assertEqual(
                            fetcher.metadata_attempts[-1]["status"],
                            "completed" if recover else "failed",
                        )
                    finally:
                        fetcher.close()

    def test_bilibili_selects_smaller_codec_at_the_same_resolution(self):
        fetcher = VideoFetcher(128 * 1024 * 1024, time.monotonic() + 60)
        self.addCleanup(fetcher.close)
        fetcher.ydl.params["check_formats"] = False
        formats = [
            {
                "format_id": name,
                "height": height,
                "width": 640,
                "url": f"https://example.invalid/{name}.mp4",
                "ext": "mp4",
                "vcodec": codec,
                "acodec": "none",
                "tbr": bitrate,
                "quality": height,
            }
            for name, height, codec, bitrate in (
                ("h264", 360, "avc1.64001E", 495.694),
                ("hevc", 360, "hvc1.1.6.L120.90", 284.966),
                ("av1", 360, "av01.0.08M.08.0.110.01.01.01.0", 276.963),
                ("large", 720, "av01.0.08M.08.0.110.01.01.01.0", 200),
            )
        ]

        def extract(*args, **kwargs):
            return fetcher.ydl.process_ie_result(
                {
                    "id": "fixture",
                    "title": "Fixture",
                    "duration": 2439.769,
                    "extractor": "Bilibili",
                    "extractor_key": "BiliBili",
                    "formats": formats,
                },
                download=False,
            )

        with patch.object(fetcher.ydl, "extract_info", side_effect=extract):
            info = fetcher.metadata(
                {"platform": "bilibili", "part_index": 1, "url": "fixture"}
            )
        self.assertEqual(info["format_id"], "av1")
        self.assertLess(info["filesize_approx"], fetcher.byte_limit)

    def test_oversized_streams_stop_before_transfer_with_exact_or_estimated_size(self):
        for field in ("filesize", "filesize_approx"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fetcher, seen = self.fetcher(directory, [])
                with self.assertRaises(EvidencePending) as caught:
                    fetcher.download(
                        {
                            "url": "https://example.invalid/video",
                            field: 2 * 1024 * 1024,
                        },
                        directory,
                    )
                self.assertEqual(caught.exception.category, "media_size")
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(seen, [])

    def test_access_challenge_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(
                directory, [RuntimeError("HTTP Error 429: Too Many Requests")]
            )
            with self.assertRaises(EvidencePending) as caught:
                fetcher.download(
                    {
                        "url": "https://primary.bilivideo.com/video",
                        "backup_urls": ["https://backup.bilivideo.com/video"],
                    },
                    directory,
                )
            self.assertEqual(len(seen), 1)
            self.assertEqual(caught.exception.scope, "platform")
            self.assertEqual(caught.exception.category, "access")

    def test_permanent_media_errors_skip_without_false_access_challenges(self):
        for message in (
            "HTTP Error 404: Not Found",
            "HTTP Error 410: Gone",
            "Unsupported media format",
            "invalid response length 1403",
            "read timed out after 1429 ms",
        ):
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as directory,
            ):
                fetcher, seen = self.fetcher(directory, [RuntimeError(message)] * 5)
                with (
                    patch("broader_vids.evidence.media.time.sleep"),
                    self.assertRaises(EvidencePending) as caught,
                ):
                    fetcher.download(
                        {"url": "https://primary.bilivideo.com/video"}, directory
                    )
                self.assertEqual(caught.exception.scope, "candidate")
                self.assertEqual(caught.exception.category, "media_transfer")
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(len(seen), 4 if "timed out" in message else 1)

    def test_local_storage_failure_stops_shared_work_without_retry(self):
        for error in (
            PermissionError("write denied"),
            OSError(errno.ENOSPC, "No space left on device"),
            RuntimeError("ERROR: unable to open for writing"),
        ):
            with (
                self.subTest(error=str(error)),
                tempfile.TemporaryDirectory() as directory,
            ):
                fetcher, seen = self.fetcher(directory, [error] * 5)
                with self.assertRaises(EvidencePending) as caught:
                    fetcher.download(
                        {"url": "https://primary.bilivideo.com/video"}, directory
                    )
                self.assertEqual(len(seen), 1)
                self.assertEqual(caught.exception.scope, "run")
                self.assertEqual(caught.exception.category, "storage")
                self.assertEqual(failure_details(error, "worker")["scope"], "run")
                self.assertFalse(caught.exception.retryable)

    def test_file_locks_retry_three_times_then_recover_or_skip_on_both_platforms(self):
        for host in ("googlevideo.com", "bilivideo.com"):
            for recover in (False, True):
                with (
                    self.subTest(host=host, recover=recover),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    error = PermissionError(errno.EACCES, "File is in use")
                    error.winerror = 32
                    wrapped = RuntimeError(
                        "ERROR: Unable to rename file: [WinError 32] "
                        "The process cannot access the file because it is being used by another process"
                    )
                    failures = [error, wrapped, error, None if recover else wrapped]
                    fetcher, seen = self.fetcher(directory, failures)
                    with patch("broader_vids.evidence.media.time.sleep"):
                        if recover:
                            self.assertTrue(
                                fetcher.download(
                                    {"url": f"https://{host}/video"}, directory
                                ).is_file()
                            )
                        else:
                            with self.assertRaises(EvidencePending) as caught:
                                fetcher.download(
                                    {"url": f"https://{host}/video"}, directory
                                )
                            failure = failure_details(caught.exception, "download")
                            self.assertEqual(failure["scope"], "candidate")
                            self.assertEqual(failure["category"], "media_transfer")
                            self.assertFalse(failure["retryable"])
                    self.assertEqual(len(seen), 4)
                    for lock in (error, wrapped):
                        self.assertEqual(
                            failure_details(lock, "download")["scope"], "candidate"
                        )
                        self.assertEqual(
                            failure_details(lock, "download")["category"], "file_in_use"
                        )

    def test_concurrent_fragment_contexts_close_every_owned_file_on_failure(self):
        from concurrent.futures import ThreadPoolExecutor
        from broader_vids.evidence.downloader import _closing_downloader

        opened = []
        barrier = threading.Barrier(2)

        class SlowHandle:
            def __init__(self):
                self.stream = tempfile.TemporaryFile()

            @property
            def closed(self):
                time.sleep(0.01)
                return self.stream.closed

            def close(self):
                self.stream.close()

        class FragmentDownloader:
            def sanitize_open(self, name):
                handle = SlowHandle()
                opened.append(handle)
                if name != "seed":
                    barrier.wait(2)
                return handle, name

            def download(self):
                self.sanitize_open("seed")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(self.sanitize_open, ("first", "second")))
                raise TimeoutError("Fixture interruption")

        try:
            with self.assertRaises(TimeoutError):
                _closing_downloader(FragmentDownloader)().download()
            self.assertEqual(len(opened), 3)
            self.assertTrue(all(handle.closed for handle in opened))
        finally:
            for handle in opened:
                handle.close()

    def test_real_hls_closes_interrupted_output_before_retry_or_cancellation(self):
        from yt_dlp.downloader.common import FileDownloader

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    b"#EXTINF:10,\n/a.ts\n#EXTINF:10,\n/b.ts\n#EXT-X-ENDLIST\n"
                    if self.path.startswith("/list")
                    else b"x" * 65536
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
        )
        thread.start()
        try:
            for mode in (
                "retry",
                "cancelled",
                "media_size",
                "lock_recovers",
                "lock_remains",
            ):
                with (
                    self.subTest(mode=mode),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    fetcher = VideoFetcher(1024 * 1024, time.monotonic() + 30)
                    opened, triggered = [], []
                    original_open = FileDownloader.sanitize_open
                    original_replace = __import__("os").replace
                    rename_attempts = []

                    def rename(old, new):
                        if (
                            mode.startswith("lock_")
                            and Path(old).name == "media.mp4.part"
                        ):
                            rename_attempts.append(True)
                            if mode == "lock_remains" or len(rename_attempts) <= 3:
                                error = PermissionError(
                                    errno.EACCES, "Fixture file in use"
                                )
                                error.winerror = 32
                                raise error
                        return original_replace(old, new)

                    def track_open(fd, *args, **kwargs):
                        result = original_open(fd, *args, **kwargs)
                        opened.append(result[0])
                        return result

                    def interrupt(progress):
                        fetcher._progress(progress)
                        if (
                            not mode.startswith("lock_")
                            and not triggered
                            and progress.get("downloaded_bytes", 0) > 0
                        ):
                            triggered.append(True)
                            if mode == "retry":
                                raise TimeoutError(
                                    "The media transfer attempt timed out within its time slice."
                                )
                            raise EvidencePending("Fixture interruption", category=mode)

                    fetcher.ydl._progress_hooks[:] = [interrupt]
                    fetcher.ydl.params["fixup"] = "never"
                    info = fetcher.ydl.process_ie_result(
                        {
                            "id": "fixture",
                            "title": "Fixture",
                            "extractor": "Generic",
                            "extractor_key": "Generic",
                            "formats": [
                                {
                                    "format_id": "360",
                                    "height": 360,
                                    "url": f"http://127.0.0.1:{server.server_port}/list.m3u8",
                                    "ext": "mp4",
                                    "protocol": "m3u8_native",
                                    "vcodec": "h264",
                                    "acodec": "none",
                                }
                            ],
                        },
                        download=False,
                    )
                    try:
                        with (
                            patch.object(FileDownloader, "sanitize_open", track_open),
                            patch(
                                "broader_vids.evidence.downloader.os.replace", rename
                            ),
                            patch("broader_vids.evidence.media.time.sleep"),
                        ):
                            if mode in {"retry", "lock_recovers"}:
                                self.assertEqual(
                                    fetcher.download(info, directory).read_bytes(),
                                    b"x" * 131072,
                                )
                            else:
                                with self.assertRaises(EvidencePending):
                                    fetcher.download(info, directory)
                        if mode.startswith("lock_"):
                            self.assertEqual(len(rename_attempts), 4)
                            self.assertEqual(fetcher.downloaded_bytes, 131072)
                        else:
                            self.assertTrue(triggered)
                        self.assertTrue(opened)
                        self.assertTrue(all(handle.closed for handle in opened))
                        self.assertEqual(
                            len(fetcher.download_attempts), 2 if mode == "retry" else 1
                        )
                    finally:
                        for handle in opened:
                            handle.close()
                        fetcher.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_downloader_charges_full_restart_when_a_server_ignores_resume(self):
        body = b"x" * (256 * 1024)
        for limit, complete in ((512 * 1024, True), (300 * 1024, False)):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:

                class Handler(http.server.BaseHTTPRequestHandler):
                    count = 0

                    def do_GET(self):
                        type(self).count += 1
                        first = self.count == 1
                        self.send_response(206 if first else 200)
                        self.send_header("Content-Length", str(len(body)))
                        if first:
                            self.send_header(
                                "Content-Range", f"bytes 0-{len(body) - 1}/{len(body)}"
                            )
                        self.end_headers()
                        self.wfile.write(body[:65536] if first else body)
                        self.wfile.flush()
                        self.close_connection = True

                    def log_message(self, *args):
                        pass

                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(
                    target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
                )
                thread.start()
                fetcher = VideoFetcher(limit, time.monotonic() + 30)
                try:
                    info = fetcher.ydl.process_ie_result(
                        {
                            "id": "fixture",
                            "title": "Fixture",
                            "extractor": "Generic",
                            "extractor_key": "Generic",
                            "formats": [
                                {
                                    "format_id": "360",
                                    "height": 360,
                                    "width": 640,
                                    "url": f"http://127.0.0.1:{server.server_port}/video",
                                    "ext": "mp4",
                                    "vcodec": "h264",
                                    "acodec": "none",
                                }
                            ],
                        },
                        download=False,
                    )
                    with patch("broader_vids.evidence.media.time.sleep"):
                        if complete:
                            self.assertEqual(
                                fetcher.download(info, directory).read_bytes(), body
                            )
                        else:
                            with self.assertRaises(EvidencePending) as caught:
                                fetcher.download(info, directory)
                            self.assertEqual(caught.exception.category, "media_size")
                    self.assertEqual(Handler.count, 2)
                    self.assertEqual(fetcher.downloaded_bytes, len(body) + 65536)
                finally:
                    fetcher.close()
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_completed_stream_is_not_charged_again_on_a_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(directory, [])
            completed = Path(directory) / "media.fvideo.mp4"

            def transfer(info, **kwargs):
                seen.append(info["url"])
                if len(seen) == 1:
                    completed.write_bytes(b"x" * 60)
                    fetcher._progress(
                        {
                            "filename": str(completed),
                            "status": "finished",
                            "downloaded_bytes": 60,
                        }
                    )
                    raise TimeoutError("read timed out")
                fetcher._progress(
                    {
                        "filename": str(completed),
                        "status": "finished",
                        "downloaded_bytes": 60,
                    }
                )
                completed.unlink()
                (Path(directory) / "media.mp4").write_bytes(b"fixture")

            fetcher.ydl.process_ie_result = transfer
            with patch("broader_vids.evidence.media.time.sleep"):
                fetcher.download(
                    {"url": "https://primary.bilivideo.com/video"}, directory
                )
            self.assertEqual(fetcher.downloaded_bytes, 60)
            self.assertEqual(
                [attempt["new_bytes"] for attempt in fetcher.download_attempts], [60, 0]
            )

    def test_retries_remain_bounded_and_the_third_retry_can_recover(self):
        for domain, recover in (
            (domain, recover)
            for domain in ("googlevideo.com", "bilivideo.com")
            for recover in (False, True)
        ):
            with (
                self.subTest(recover=recover),
                tempfile.TemporaryDirectory() as directory,
            ):
                failure = (
                    RuntimeError(
                        "ERROR: The media transfer attempt timed out within its time slice."
                    )
                    if recover
                    else TimeoutError("read timed out")
                )
                failures = [failure] * (3 if recover else 5)
                if recover:
                    failures.append(None)
                fetcher, seen = self.fetcher(directory, failures)
                info = {
                    "url": f"https://primary.{domain}/video",
                    "backup_urls": [f"https://backup.{domain}/video"],
                }
                with patch("broader_vids.evidence.media.time.sleep"):
                    if recover:
                        self.assertTrue(fetcher.download(info, directory).is_file())
                    else:
                        with self.assertRaises(EvidencePending) as caught:
                            fetcher.download(info, directory)
                        self.assertEqual(caught.exception.category, "media_transfer")
                        self.assertFalse(caught.exception.retryable)
                self.assertEqual(len(seen), 4)
                self.assertEqual(
                    seen,
                    [
                        info["url"],
                        info["backup_urls"][0],
                        info["url"],
                        info["backup_urls"][0],
                    ],
                )
                self.assertEqual(
                    [attempt["number"] for attempt in fetcher.download_attempts],
                    [1, 2, 3, 4],
                )
                self.assertIsNone(fetcher._attempt_deadline)

    def test_attempt_time_slices_retry_without_resetting_the_shared_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(directory, [])
            clock = [100.0]
            fetcher.deadline = 140.0
            slices = []

            def transfer(info, **kwargs):
                seen.append(info["url"])
                slices.append(fetcher._attempt_deadline)
                if len(seen) < 4:
                    clock[0] = fetcher._attempt_deadline
                    fetcher._progress({"status": "downloading", "downloaded_bytes": 0})
                (Path(directory) / "media.mp4").write_bytes(b"fixture")

            fetcher.ydl.process_ie_result = transfer
            with (
                patch(
                    "broader_vids.evidence.media.time.monotonic",
                    side_effect=lambda: clock[0],
                ),
                patch("broader_vids.evidence.media.time.sleep"),
            ):
                fetcher.download(
                    {
                        "url": "https://primary.bilivideo.com/video",
                        "backup_urls": ["https://backup.bilivideo.com/video"],
                    },
                    directory,
                )
            self.assertEqual(len(seen), 4)
            self.assertEqual(slices, [110.0, 120.0, 130.0, None])
            self.assertEqual(fetcher.deadline, 140.0)

            fetcher, _ = self.fetcher(directory, [])
            fetcher.deadline = 140.0
            clock[0] = 100.0
            slices = []

            def healthy_transfer(info, **kwargs):
                slices.append(fetcher._attempt_deadline)
                clock[0] = 101.0
                fetcher._progress(
                    {
                        "filename": "fixture",
                        "status": "downloading",
                        "downloaded_bytes": 990,
                        "total_bytes": 1000,
                    }
                )
                clock[0] = 110.0
                fetcher._progress(
                    {
                        "filename": "fixture",
                        "status": "downloading",
                        "downloaded_bytes": 999,
                        "total_bytes": 1000,
                    }
                )
                (Path(directory) / "media.mp4").write_bytes(b"fixture")

            fetcher.ydl.process_ie_result = healthy_transfer
            with patch(
                "broader_vids.evidence.media.time.monotonic",
                side_effect=lambda: clock[0],
            ):
                fetcher.download(
                    {
                        "url": "https://primary.bilivideo.com/video",
                        "backup_urls": ["https://backup.bilivideo.com/video"],
                    },
                    directory,
                )
            self.assertEqual(slices, [110.0])

            fetcher, _ = self.fetcher(directory, [])
            fetcher.deadline = 140.0
            clock[0] = 100.0
            slices = []
            socket_timeouts = []

            def single_route_transfer(info, **kwargs):
                slices.append(fetcher._attempt_deadline)
                socket_timeouts.append(fetcher.ydl.params["socket_timeout"])
                (Path(directory) / "media.mp4").write_bytes(b"fixture")

            fetcher.ydl.process_ie_result = single_route_transfer
            with patch(
                "broader_vids.evidence.media.time.monotonic",
                side_effect=lambda: clock[0],
            ):
                fetcher.download(
                    {"url": "https://primary.bilivideo.com/video"}, directory
                )
            self.assertEqual(slices, [None])
            self.assertEqual(socket_timeouts, [15])

    def test_retry_resume_preserves_the_cumulative_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            fetcher, seen = self.fetcher(directory, [])
            fetcher.byte_limit = 100
            partial = Path(directory) / "media.mp4.part"

            def transfer(info, **kwargs):
                seen.append(info["url"])
                size = (partial.stat().st_size if partial.exists() else 0) + 40
                partial.write_bytes(b"x" * size)
                fetcher._progress(
                    {
                        "filename": str(partial.with_suffix("")),
                        "status": "downloading",
                        "downloaded_bytes": size,
                    }
                )
                raise TimeoutError("read timed out")

            fetcher.ydl.process_ie_result = transfer
            with (
                patch("broader_vids.evidence.media.time.sleep"),
                self.assertRaises(EvidencePending) as caught,
            ):
                fetcher.download(
                    {"url": "https://primary.bilivideo.com/video"}, directory
                )
            self.assertEqual(caught.exception.category, "media_size")
            self.assertEqual(len(seen), 3)
            self.assertEqual(fetcher.downloaded_bytes, 120)
            self.assertEqual(
                [attempt["new_bytes"] for attempt in fetcher.download_attempts],
                [40, 40, 40],
            )

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
        fetcher._stream_started = {"fixture": (time.monotonic() - 16, 0)}
        fetcher._stream_samples = {}
        fetcher._progress_positions = {}
        fetcher._finished = {}
        fetcher._progress(
            {
                "filename": "fixture",
                "status": "downloading",
                "downloaded_bytes": 1024,
                "total_bytes": 1025,
            }
        )

    def test_progress_sampling_stays_bounded_and_skips_single_route_streams(self):
        fetcher = VideoFetcher(32 * 1024 * 1024, 200.0)
        self.addCleanup(fetcher.close)
        clock = [100.0]
        step = 16 * 1024
        fetcher._can_failover = True
        with patch(
            "broader_vids.evidence.media.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            for downloaded in range(1001):
                clock[0] = 100 + downloaded / 40
                fetcher._progress(
                    {
                        "filename": "alternate-route",
                        "status": "downloading",
                        "downloaded_bytes": downloaded * step,
                    }
                )
                samples = fetcher._stream_samples["alternate-route"]
                self.assertLessEqual(len(samples), 64)
            self.assertGreaterEqual(len(samples), 50)
            fetcher._progress(
                {
                    "filename": "alternate-route",
                    "status": "finished",
                    "downloaded_bytes": 1000 * step,
                }
            )
            self.assertNotIn("alternate-route", fetcher._stream_samples)
            self.assertNotIn("alternate-route", fetcher._stream_started)

            fetcher._can_failover = False
            for downloaded in range(1, 21):
                clock[0] += 0.1
                fetcher._progress(
                    {
                        "filename": "single-route",
                        "status": "downloading",
                        "downloaded_bytes": downloaded * 50_000,
                    }
                )
            self.assertNotIn("single-route", fetcher._stream_samples)
            self.assertNotIn("single-route", fetcher._stream_started)
            self.assertEqual(fetcher.downloaded_bytes, 1000 * step + 20 * 50_000)

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
