from contextlib import contextmanager
import io
import json
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from broader_vids.cli.arguments import parser
from broader_vids.http import AccessPaused, PublicHttp
from broader_vids.recommendations.engagement import metadata_fresh
from broader_vids.recommendations.metadata import metadata_session, refresh_metadata
from broader_vids.recommendations.service import enrich_buffer
from broader_vids.storage import Store, utc_now
from test_recommendations import candidate


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

    def test_failed_enrichment_preserves_cursor_and_successful_resume(self):
        http = self.http()
        run = self.store.get(http.run["id"])
        run["settings"].update(
            use_browser=False, profile={"interests": ["mathematics"]}
        )
        item = candidate(1, "bilibili")
        run["state"] = {
            "phase": "enrich",
            "buffer": [item],
            "enrichment_index": 0,
            "issues": [],
        }

        def save():
            self.store.save(run)

        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            side_effect=AccessPaused("HTTP 412"),
        ):
            with self.assertRaises(AccessPaused):
                enrich_buffer(self.store, run, http, lambda: 60, save)
        saved = self.store.get(run["id"])
        self.assertEqual(saved["state"]["enrichment_index"], 0)
        self.assertEqual(saved["state"]["phase"], "enrich")
        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            return_value={"observed_at": utc_now(), "metadata_version": 2},
        ) as request:
            enrich_buffer(self.store, run, http, lambda: 60, save)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(run["state"]["enrichment_index"], 1)
        self.assertEqual(run["state"]["phase"], "assess")
        self.assertNotIn("metadata_route_paused", run["state"])

    def test_cli_and_platform_validation(self):
        args = parser().parse_args(["metadata", URL, "--use-browser"])
        self.assertTrue(args.use_browser)
        with self.assertRaises(ValueError):
            refresh_metadata(
                self.store,
                "https://www.youtube.com/watch?v=GAkVaD1ihi4",
                use_browser=True,
            )
        self.assertEqual(list(self.store.iter_runs()), [])

    def test_recent_incomplete_bilibili_cache_requires_refresh(self):
        cached = {"metadata_version": 2, "observed_at": utc_now()}
        self.assertFalse(metadata_fresh(cached, "bilibili"))
        self.assertTrue(metadata_fresh(cached, "youtube"))
        self.assertTrue(
            metadata_fresh({**cached, "provenance_status": "complete"}, "bilibili")
        )

    def test_pending_metadata_failure_pauses_and_retries_without_refetching_buffer(
        self,
    ):
        http = self.http()
        run = self.store.get(http.run["id"])
        run["settings"].update(
            use_browser=False, profile={"interests": ["mathematics"]}
        )
        item, pending = candidate(1, "bilibili"), candidate(2, "bilibili")
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,1,?)",
                (pending["canonical_id"], "bilibili", json.dumps(pending), utc_now()),
            )
        run["state"] = {
            "phase": "enrich",
            "buffer": [item],
            "enrichment_index": 0,
            "issues": [],
        }

        def save():
            self.store.save(run)

        snapshot = {
            **pending["engagement"],
            "observed_at": utc_now(),
            "metadata_version": 2,
            "provenance_status": "complete",
        }
        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            side_effect=[snapshot, AccessPaused("HTTP 412")],
        ):
            with self.assertRaises(AccessPaused):
                enrich_buffer(self.store, run, http, lambda: 60, save)
        self.assertEqual(run["state"]["enrichment_index"], 1)
        self.assertEqual(run["state"]["phase"], "enrich")
        self.assertEqual(run["state"]["issues"][-1]["stage"], "pending_metadata")
        with patch(
            "broader_vids.recommendations.service.collect_snapshot",
            return_value=snapshot,
        ) as request:
            enrich_buffer(self.store, run, http, lambda: 60, save)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(
            request.call_args.args[1]["canonical_id"], pending["canonical_id"]
        )
        self.assertEqual(run["state"]["phase"], "assess")
        self.assertTrue(
            any(
                i["canonical_id"] == pending["canonical_id"] and i["carryover"]
                for i in run["state"]["shortlist"]
            )
        )

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


if __name__ == "__main__":
    unittest.main()
