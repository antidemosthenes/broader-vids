from contextlib import contextmanager
import io
import json
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from broader_vids.http import AccessPaused, BudgetExhausted, PublicHttp
from broader_vids.recommendations.engagement import (
    BilibiliVideoUnavailable,
    collect_snapshot,
)
from broader_vids.recommendations.metadata import metadata_session, refresh_metadata
from broader_vids.storage import Store


VIDEO = "BV1ig87zeE9q"
URL = "https://www.bilibili.com/video/" + VIDEO
KEY = "bilibili:" + VIDEO + ":p1"


def payload():
    return {
        "code": 0,
        "data": {
            "View": {
                "bvid": VIDEO,
                "title": "Functional analysis",
                "desc": "https://www.youtube.com/watch?v=GAkVaD1ihi4",
                "copyright": 2,
                "stat": {"view": 17, "like": 0, "coin": 0, "favorite": 0},
                "pages": [{"page": 1, "cid": 123, "duration": 843}],
            },
            "Tags": [{"tag_name": "mathematics"}],
        },
    }


class Reply(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())

    def geturl(self):
        return "https://api.bilibili.com/x/web-interface/view/detail?bvid=" + VIDEO


YOUTUBE_URL = "https://www.youtube.com/watch?v=0000000001A"


class YouTubeReply(io.BytesIO):
    def __init__(self, player):
        body = "var ytInitialPlayerResponse = " + json.dumps(player) + ";"
        super().__init__(body.encode())

    def geturl(self):
        return YOUTUBE_URL + "&hl=en"


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def http(self):
        key = self.store.create("bilibili", {"max_seconds": 60, "max_requests": 10}, {})
        return PublicHttp(self.store, self.store.get(key), anonymous_session=True)

    @contextmanager
    def browser(self, root):
        class Browser:
            def cookies(self, urls):
                return [
                    {
                        "name": name,
                        "value": "private-test-value",
                        "domain": ".bilibili.com",
                        "path": "/",
                        "expires": -1,
                    }
                    for name in ("SESSDATA", "DedeUserID")
                ]

        yield Browser()

    def test_not_found_retries_do_not_swallow_access_schema_or_budget_errors(self):
        item = {"platform": "bilibili", "video_id": VIDEO, "canonical_id": KEY}
        missing = {"code": -404, "message": "Not found", "data": None}
        cases = (
            ([missing, missing, payload()], None, 3, 2),
            ([missing] * 4, BilibiliVideoUnavailable, 4, 4),
            ([missing, {"code": -101}], AccessPaused, 2, 1),
            ([missing, BudgetExhausted("request limit")], BudgetExhausted, 2, 1),
            ([[]], AccessPaused, 1, 0),
        )
        for replies, error_type, count, failures in cases:
            with self.subTest(error=error_type, count=count):
                http = Mock(
                    last_receipt={"request_id": 1},
                    metadata_failure_count=Mock(return_value=0),
                )
                http.get_json.side_effect = replies
                if error_type:
                    with self.assertRaises(error_type) as caught:
                        collect_snapshot(http, item)
                    if error_type is BilibiliVideoUnavailable:
                        self.assertEqual(caught.exception.attempts, 4)
                    elif error_type is AccessPaused:
                        self.assertNotIsInstance(
                            caught.exception, BilibiliVideoUnavailable
                        )
                else:
                    self.assertTrue(collect_snapshot(http, item)["identity_verified"])
                self.assertEqual(http.get_json.call_count, count)
                self.assertEqual(http.metadata_failed.call_count, failures)

    def test_not_found_limit_survives_request_budget_pause_and_new_client(self):
        item = {"platform": "bilibili", "video_id": VIDEO, "canonical_id": KEY}
        missing = {"code": -404, "message": "Not found", "data": None}
        identifier = self.store.create(
            "bilibili", {"max_seconds": 60, "max_requests": 2}, {}
        )
        run = self.store.get(identifier)
        now = [0.0]

        def client():
            return PublicHttp(
                self.store,
                run,
                anonymous_session=True,
                clock=lambda: now[0],
                sleeper=lambda delay: now.__setitem__(0, now[0] + delay),
            )

        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=lambda *args, **kwargs: Reply(missing),
        ) as request:
            with self.assertRaises(BudgetExhausted):
                collect_snapshot(client(), item)
            self.assertEqual(request.call_count, 2)
            run["settings"]["max_requests"] = 8
            self.store.save(run)
            with self.assertRaises(BilibiliVideoUnavailable) as caught:
                collect_snapshot(client(), item)
            self.assertEqual(caught.exception.attempts, 4)
            self.assertEqual(request.call_count, 4)
            with self.assertRaises(BilibiliVideoUnavailable):
                collect_snapshot(client(), item)
            self.assertEqual(request.call_count, 4)
        self.assertEqual(self.store.request_count(identifier), 4)
        other = self.http()
        with patch(
            "urllib.request.OpenerDirector.open", return_value=Reply(payload())
        ) as request:
            self.assertTrue(collect_snapshot(other, item)["identity_verified"])
        self.assertEqual(request.call_count, 1)

    def test_authenticated_refresh_preserves_source_clues_and_private_state(self):
        before = self.store.db.execute("SELECT COUNT(*) FROM user_actions").fetchone()[
            0
        ]
        with (
            patch(
                "broader_vids.recommendations.metadata.bilibili_browser", self.browser
            ),
            patch(
                "urllib.request.OpenerDirector.open", return_value=Reply(payload())
            ) as request,
        ):
            result = refresh_metadata(self.store, URL, use_browser=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(request.call_count, 1)
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["provenance_status"], "complete")
        self.assertEqual(snapshot["receipt"]["transport"], "isolated_browser_http")
        self.assertEqual(snapshot["declared_copyright"], 2)
        self.assertEqual(
            {s["kind"] for s in result["originality_evidence"]["signals"]},
            {"youtube_source_link", "uploader_copyright_declaration"},
        )
        self.assertNotIn("private-test-value", json.dumps(result))
        saved = self.store.db.execute(
            "SELECT payload FROM engagement_snapshots WHERE canonical_id=?", (KEY,)
        ).fetchone()[0]
        self.assertNotIn("private-test-value", saved)
        self.assertEqual(json.loads(saved), snapshot)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM user_actions").fetchone()[0],
            before,
        )
        self.assertEqual(list(self.store.iter_runs(kind="evidence")), [])

    def test_412_retains_previous_snapshot_and_records_one_failed_request(self):
        previous = json.dumps({"previous": True})
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)", (KEY, previous)
            )
        error = urllib.error.HTTPError(URL, 412, "blocked", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error) as request:
            result = refresh_metadata(self.store, URL)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["status"], "paused")
        self.assertIn("--use-browser", result["reason"])
        self.assertIsNone(result["snapshot"])
        self.assertEqual(
            self.store.requests(result["run_id"])[0]["outcome"], "unresolved"
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT payload FROM engagement_snapshots"
            ).fetchone()[0],
            previous,
        )

    def test_incomplete_provenance_is_not_cached_as_success(self):
        value = payload()
        del value["data"]["Tags"]
        with patch("urllib.request.OpenerDirector.open", return_value=Reply(value)):
            result = refresh_metadata(self.store, URL)
        self.assertEqual(result["status"], "paused")
        self.assertIn("missing", result["reason"])
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM engagement_snapshots"
            ).fetchone()[0],
            0,
        )

    def test_session_cookies_are_restored_after_failure(self):
        http = self.http()
        original = list(http.cookies)
        with patch(
            "broader_vids.recommendations.metadata.bilibili_browser", self.browser
        ):
            with self.assertRaisesRegex(AccessPaused, "fixture"):
                with metadata_session(self.store, http, True):
                    self.assertEqual(
                        {c.name for c in http.cookies}, {"SESSDATA", "DedeUserID"}
                    )
                    raise AccessPaused("fixture")
        self.assertEqual(list(http.cookies), original)
        self.assertEqual(http.transport, "anonymous_http")

    def test_missing_login_does_not_make_an_anonymous_request(self):
        @contextmanager
        def signed_out(root):
            class Browser:
                def cookies(self, urls):
                    return []

            yield Browser()

        with (
            patch("broader_vids.recommendations.metadata.bilibili_browser", signed_out),
            patch("urllib.request.OpenerDirector.open") as request,
        ):
            result = refresh_metadata(self.store, URL, use_browser=True)
        self.assertEqual(result["status"], "paused")
        self.assertIn("login bilibili", result["reason"])
        request.assert_not_called()

    def test_failed_request_clears_previous_success_receipt(self):
        http = self.http()
        http.last_receipt = {"request_id": 999, "sha256": "old"}
        error = urllib.error.HTTPError(URL, 412, "blocked", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(AccessPaused):
                http.get(
                    URL.replace(
                        "www.bilibili.com/video/",
                        "api.bilibili.com/x/web-interface/view/detail?bvid=",
                    )
                )
        self.assertIsNone(http.last_receipt)

    @staticmethod
    def youtube_http(store, run, **options):
        return PublicHttp(store, run, sleeper=lambda _: None, **options)

    def test_youtube_incomplete_metadata_retries_once_with_verified_receipts(self):
        complete = {
            "videoDetails": {
                "videoId": "0000000001A",
                "channelId": "UC" + "a" * 22,
                "viewCount": "1000",
                "lengthSeconds": "600",
            },
            "microformat": None,
        }
        for incomplete in (
            {},
            {"videoDetails": None},
            {
                "playabilityStatus": {
                    "status": "ERROR",
                    "reason": "An error occurred. Please try again later.",
                }
            },
        ):
            with (
                self.subTest(response=incomplete),
                patch(
                    "urllib.request.OpenerDirector.open",
                    side_effect=[YouTubeReply(incomplete), YouTubeReply(complete)],
                ) as request,
            ):
                result = refresh_metadata(
                    self.store, YOUTUBE_URL, http_factory=self.youtube_http
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(request.call_count, 2)
            self.assertTrue(result["snapshot"]["identity_verified"])
            self.assertEqual(result["snapshot"]["duration_seconds"], 600)
            receipts = self.store.requests(result["run_id"])
            self.assertEqual([r["outcome"] for r in receipts], ["unresolved", "ok"])
            diagnostic = json.loads(receipts[0]["detail"])["metadata_validation"]
            self.assertEqual(diagnostic["attempt"], 1)
            self.assertEqual(diagnostic["requested_video_id"], "0000000001A")
            self.assertEqual(
                result["snapshot"]["receipt"]["request_id"], receipts[1]["id"]
            )

    def test_youtube_metadata_denials_and_limits_preserve_previous_snapshot(self):
        key = "youtube:0000000001A"
        previous = json.dumps({"previous": True})
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO engagement_snapshots VALUES(?,?)", (key, previous)
            )
        cases = (
            ({"videoDetails": {"videoId": "0000000002A"}}, 1),
            (
                {
                    "playabilityStatus": {
                        "status": "LOGIN_REQUIRED",
                        "reason": "Sign in to confirm you are not a bot. access_token=private-token",
                    }
                },
                1,
            ),
            (
                {
                    "playabilityStatus": {
                        "status": "UNPLAYABLE",
                        "reason": "Video unavailable",
                    }
                },
                1,
            ),
            ({}, 2),
        )
        for player, count in cases:
            with (
                self.subTest(player=player),
                patch(
                    "urllib.request.OpenerDirector.open",
                    side_effect=[YouTubeReply(player) for _ in range(count)],
                ) as request,
            ):
                result = refresh_metadata(
                    self.store, YOUTUBE_URL, http_factory=self.youtube_http
                )
            self.assertEqual(result["status"], "paused")
            self.assertEqual(request.call_count, count)
            self.assertIn("0000000001A", result["reason"])
            receipts = self.store.requests(result["run_id"])
            self.assertTrue(all(r["outcome"] == "unresolved" for r in receipts))
            self.assertNotIn("private-token", json.dumps(result) + json.dumps(receipts))
            self.assertEqual(
                self.store.db.execute(
                    "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                    (key,),
                ).fetchone()[0],
                previous,
            )
        run = self.store.get(
            self.store.create("youtube", {"max_seconds": 60, "max_requests": 1}, {})
        )
        http = PublicHttp(self.store, run, anonymous_session=True)
        with patch(
            "urllib.request.OpenerDirector.open", return_value=YouTubeReply({})
        ) as request:
            with self.assertRaises(BudgetExhausted):
                collect_snapshot(
                    http,
                    {
                        "platform": "youtube",
                        "url": YOUTUBE_URL,
                        "video_id": "0000000001A",
                        "canonical_id": key,
                    },
                )
        self.assertEqual(request.call_count, 1)
        error = urllib.error.HTTPError(YOUTUBE_URL, 429, "blocked", {}, None)
        with patch("urllib.request.OpenerDirector.open", side_effect=error) as request:
            result = refresh_metadata(
                self.store, YOUTUBE_URL, http_factory=self.youtube_http
            )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
