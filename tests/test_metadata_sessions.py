from contextlib import closing
from http.cookiejar import Cookie
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.evidence.failures import failure_details
from broader_vids.http import AccessPaused, BudgetExhausted, PublicHttp, RequestGate
from broader_vids.recommendations.engagement import (
    BilibiliVideoUnavailable,
    YouTubeMetadataError,
)
from broader_vids.recommendations.metadata import SnapshotPool
from broader_vids.storage import Store
from test_recommendations import candidate


def cookie(name, value="private-session-value", domain=".youtube.com"):
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        True,
        True,
        "/",
        True,
        True,
        None,
        True,
        None,
        None,
        {},
    )


class Reply(io.BytesIO):
    def __init__(self, url):
        self.url = url
        super().__init__(b"{}")

    def geturl(self):
        return self.url


class MetadataSessionTests(unittest.TestCase):
    def test_parallel_workers_keep_server_cookies_and_bilibili_remains_concurrent(self):
        for platform in ("youtube", "bilibili"):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
                closing(Store(directory)) as store,
            ):
                identifier = store.create(
                    platform, {"max_seconds": 60, "max_requests": 20}, {}
                )
                parent = PublicHttp(
                    store, store.get(identifier), anonymous_session=True
                )
                items = [
                    candidate(index + 1, platform)
                    for index in range(3 if platform == "youtube" else 2)
                ]
                barrier = threading.Barrier(2)
                calls = []
                lock = threading.Lock()

                def collect(client, item):
                    self.assertIs(client.cookies, parent.cookies)
                    if platform == "bilibili":
                        barrier.wait(timeout=3)
                        client.cookies.set_cookie(
                            cookie(item["canonical_id"], domain=".bilibili.com")
                        )
                    else:
                        with lock:
                            if calls:
                                self.assertIn(
                                    "VISITOR_INFO1_LIVE",
                                    {c.name for c in client.cookies},
                                )
                            client.cookies.set_cookie(cookie("VISITOR_INFO1_LIVE"))
                    with lock:
                        calls.append(item["canonical_id"])
                    return {"views": 1000, "likes": 30}

                with SnapshotPool(
                    store, parent, lambda: 60, collect, enabled=True
                ) as pool:
                    pool.prefetch(items, lambda _: True, max_pending=len(items))
                    self.assertIs(pool.gate, parent.request_gate)
                    results = [pool.get(item, lambda: None) for item in items]
                self.assertEqual(pool.futures, {})
                self.assertEqual(len(calls), len(items))
                self.assertNotIn("private-session-value", json.dumps(results))
                self.assertEqual(
                    len(list(parent.cookies)), 1 if platform == "youtube" else 2
                )

    def test_unavailable_bilibili_video_does_not_cancel_other_metadata_workers(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            identifier = store.create(
                "bilibili", {"max_seconds": 60, "max_requests": 20}, {}
            )
            parent = PublicHttp(store, store.get(identifier), anonymous_session=True)
            items = [candidate(index + 1, "bilibili") for index in range(3)]

            def collect(client, item):
                if item["canonical_id"] == items[0]["canonical_id"]:
                    raise BilibiliVideoUnavailable(item["video_id"])
                return {"views": 1000, "likes": 30}

            with SnapshotPool(store, parent, lambda: 60, collect, enabled=True) as pool:
                pool.prefetch(items, lambda _: True)
                with self.assertRaises(BilibiliVideoUnavailable):
                    pool.get(items[0], lambda: None)
                self.assertFalse(pool.cancelled.is_set())
                self.assertIsNone(pool.first_error)
                self.assertEqual(pool.get(items[1], lambda: None)["likes"], 30)
                pool.prefetch(items[2:], lambda _: True)
                self.assertEqual(pool.get(items[2], lambda: None)["likes"], 30)
            self.assertEqual(pool.futures, {})

    def test_youtube_access_denial_stops_queued_metadata_calls_and_preserves_first_error(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            identifier = store.create(
                "youtube", {"max_seconds": 60, "max_requests": 20}, {}
            )
            parent = PublicHttp(store, store.get(identifier), anonymous_session=True)
            items = [candidate(index + 1) for index in range(3)]
            calls = []

            def deny(client, item):
                calls.append(item["canonical_id"])
                raise YouTubeMetadataError(
                    item["video_id"],
                    {
                        "playabilityStatus": {
                            "status": "LOGIN_REQUIRED",
                            "reason": "Sign in to confirm you are not a bot",
                        }
                    },
                )

            with SnapshotPool(store, parent, lambda: 60, deny, enabled=True) as pool:
                pool.prefetch(items, lambda _: True, max_pending=3)
                with self.assertRaises(AccessPaused):
                    pool.get(items[0], lambda: None)
            self.assertEqual(calls, [items[0]["canonical_id"]])
            self.assertFalse(pool.first_error.retryable)
            failure = failure_details(pool.first_error, "enrich")
            self.assertEqual(failure["recovery"]["action"], "review_youtube_access")
            self.assertEqual(failure["recovery"]["url"], items[0]["url"])
            self.assertTrue(pool.cancelled.is_set())

    def test_metadata_requests_use_one_second_pacing_and_respect_deadline(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            for platform, interval, seconds in (
                ("youtube", 1, 30),
                ("bilibili", 1, 30),
                ("youtube", 1, 0.5),
            ):
                with self.subTest(platform=platform, seconds=seconds):
                    now, starts = [0.0], []

                    def sleep(delay):
                        now[0] += delay

                    identifier = store.create(
                        platform, {"max_seconds": seconds, "max_requests": 4}, {}
                    )
                    first = PublicHttp(
                        store,
                        store.get(identifier),
                        clock=lambda: now[0],
                        sleeper=sleep,
                        anonymous_session=True,
                    )
                    second = PublicHttp(
                        store,
                        store.get(identifier),
                        clock=lambda: now[0],
                        sleeper=sleep,
                        anonymous_session=True,
                    )
                    gate = RequestGate()
                    first.request_gate = second.request_gate = gate
                    url = (
                        "https://www.youtube.com/watch?v=0000000001A"
                        if platform == "youtube"
                        else "https://api.bilibili.com/x/web-interface/view"
                    )

                    def reply(request, timeout):
                        starts.append(now[0])
                        return Reply(request.full_url)

                    with SnapshotPool(
                        store, first, lambda: seconds, lambda *_: {}, enabled=True
                    ) as pool:
                        self.assertIs(pool.gate, gate)
                    with patch("urllib.request.OpenerDirector.open", side_effect=reply):
                        first.get(url)
                        if seconds < interval:
                            with self.assertRaises(BudgetExhausted):
                                second.get(url)
                            self.assertEqual(now[0], seconds)
                            self.assertEqual(starts, [0.0])
                        else:
                            second.get(url)
                            self.assertEqual(starts, [0.0, interval])
