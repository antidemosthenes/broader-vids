from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch, Mock

from broader_vids.evidence.media import EvidencePending
from broader_vids.preferences import set_profile
from broader_vids.recommendations.collection import (
    collect_eligible_buffer,
    refill_candidates,
)
from broader_vids.recommendations.screening import rejection_reason
from broader_vids.storage import Store, utc_now
from test_recommendations import candidate


class ScreeningPolicyTests(unittest.TestCase):
    def test_refill_preserves_attribution_after_discovery_cache_is_full(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            initial = {
                candidate(i)["canonical_id"]: candidate(i) for i in range(1, 1001)
            }
            source = store.create(
                "youtube",
                {"kind": "discovery", "buffer_limit": 1000},
                {
                    "client_version": "fixture-client",
                    "candidates": initial,
                    "creators": {},
                    "plan": [{"query": "math", "anchors": [["math"], ["algebra"]]}],
                    "pages": [
                        {
                            "source": "search",
                            "query": "math",
                            "family_id": "math",
                            "domains": ["mathematics"],
                            "continuation": "next-token",
                        }
                    ],
                },
            )
            existing = {**candidate(1), "title": "Verified metadata title"}
            identifier = store.create(
                "youtube",
                {"max_seconds": 1800, "max_requests": 160},
                {"discovery_run": source, "candidate_pool": [existing]},
            )
            run = store.get(identifier)
            returned = [candidate(1), *(candidate(i) for i in range(1001, 1045))]
            for item in returned:
                item.pop("origins")
                item["title"] = "Math and algebra"
            provider = Mock()
            provider.search.return_value = (returned, "third-token")
            with (
                patch(
                    "broader_vids.recommendations.collection.PublicHttp",
                    return_value=Mock(last_receipt={"request_id": 123}),
                ),
                patch(
                    "broader_vids.discovery.sources.YouTubeDiscovery",
                    return_value=provider,
                ),
            ):
                refill_candidates(store, run, lambda: 100, lambda: store.save(run))
            pool = store.get(identifier)["state"]["candidate_pool"]
            self.assertEqual(len(pool), 45)
            self.assertEqual(pool[0]["title"], "Verified metadata title")
            for item in pool:
                origin = item["origins"][-1]
                self.assertEqual(origin["query"], "math")
                self.assertEqual(origin["domains"], ["mathematics"])
                self.assertEqual(origin["anchor_hits"], 2)
                self.assertEqual(origin["receipt"], {"request_id": 123})
            self.assertEqual(pool[-1]["origins"][-1]["rank"], 45)
            self.assertEqual(len(store.get(source)["state"]["candidates"]), 1000)

    def test_duration_and_metric_boundaries(self):
        item = candidate(1)
        for duration, accepted in [
            (60, False),
            (60.001, True),
            (5400, True),
            (5400.001, False),
            (None, False),
        ]:
            with self.subTest(duration=duration):
                item["duration_seconds"] = duration
                self.assertEqual(rejection_reason(item) is None, accepted)
        item["duration_seconds"] = 300
        for metrics, accepted in [
            ({"views": 100, "likes": 3}, True),
            ({"views": 99, "likes": 3}, False),
            ({"views": 100, "likes": 2}, False),
            ({"views": 1000, "likes": 10}, True),
            ({"views": 1001, "likes": 10}, False),
            ({"views": 1000, "likes": None}, False),
        ]:
            with self.subTest(metrics=metrics):
                item["engagement"] = metrics
                self.assertEqual(rejection_reason(item) is None, accepted)
        item = candidate(1, "bilibili")
        item["engagement"] = {"views": 1000, "likes": 10, "coins": 1, "favorites": 2}
        self.assertIsNone(rejection_reason(item))
        for key in ("coins", "favorites"):
            original = item["engagement"][key]
            for value in (original - 1, None):
                item["engagement"][key] = value
                self.assertIsNotNone(rejection_reason(item))
            item["engagement"][key] = original

    def test_refill_replaces_rejected_candidates_until_100_pass(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, ["mathematics"])
            bad = [{**candidate(i), "duration_seconds": 60} for i in range(1, 21)]
            run = {
                "platform": "youtube",
                "settings": {"use_browser": False, "profile": profile},
                "state": {
                    "buffer": [],
                    "candidate_pool": bad + [candidate(i) for i in range(21, 101)],
                },
            }
            refills, snapshots = [], []

            def refill(store, run, remaining, save):
                refills.append(True)
                run["state"]["candidate_pool"].extend(
                    candidate(i) for i in range(101, 121)
                )
                return True

            def snapshot(http, item):
                snapshots.append(item["canonical_id"])
                return {
                    **item["engagement"],
                    "duration_seconds": 300,
                    "observed_at": utc_now(),
                }

            collect_eligible_buffer(
                store,
                run,
                None,
                lambda: 100,
                lambda: None,
                refill=refill,
                snapshot_collector=snapshot,
            )
            self.assertEqual(len(run["state"]["buffer"]), 100)
            self.assertEqual(len(run["state"]["screening_rejections"]), 20)
            self.assertEqual(len(snapshots), 100)
            self.assertEqual(len(refills), 1)
            self.assertEqual(run["state"]["pool_index"], 120)

    def test_exhaustion_does_not_start_quality_with_fewer_than_100(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, ["mathematics"])
            run = {
                "platform": "youtube",
                "settings": {"use_browser": False, "profile": profile},
                "state": {"buffer": [], "candidate_pool": [candidate(1)]},
            }
            with self.assertRaisesRegex(EvidencePending, "Only 1/100"):
                collect_eligible_buffer(
                    store,
                    run,
                    None,
                    lambda: 100,
                    lambda: None,
                    refill=lambda *a: False,
                    snapshot_collector=lambda h, i: {
                        **i["engagement"],
                        "observed_at": utc_now(),
                    },
                )
            self.assertEqual(len(run["state"]["buffer"]), 1)

    def test_search_continuation_preserves_origins_and_uses_parent_budget(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            source = store.create(
                "youtube",
                {"kind": "discovery", "buffer_limit": 1000},
                {
                    "client_version": "fixture-client",
                    "candidates": {},
                    "creators": {},
                    "pages": [
                        {
                            "source": "search",
                            "query": "math",
                            "family_id": "math",
                            "domains": ["mathematics"],
                            "continuation": "next-token",
                        }
                    ],
                },
            )
            identifier = store.create(
                "youtube",
                {"max_seconds": 1800, "max_requests": 160},
                {"discovery_run": source, "candidate_pool": []},
            )
            run = store.get(identifier)
            http = Mock(last_receipt={"request_id": 123})
            provider = Mock()
            provider.search.return_value = ([candidate(1)], "third-token")
            with (
                patch(
                    "broader_vids.recommendations.collection.PublicHttp",
                    return_value=http,
                ) as transport,
                patch(
                    "broader_vids.discovery.sources.YouTubeDiscovery",
                    return_value=provider,
                ),
            ):
                self.assertTrue(
                    refill_candidates(store, run, lambda: 100, lambda: store.save(run))
                )
            provider.search.assert_called_once_with(
                "math", "fixture-client", "next-token"
            )
            self.assertEqual(transport.call_args.args[1]["id"], identifier)
            self.assertEqual(len(run["state"]["candidate_pool"]), 1)
            self.assertEqual(
                run["state"]["candidate_pool"][0]["origins"][0]["query"], "math"
            )
            self.assertEqual(run["state"]["refill_tasks"][0]["cursor"], "third-token")

    def test_refreshed_duration_cannot_admit_a_short_video(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, ["mathematics"])
            run = {
                "platform": "youtube",
                "settings": {"use_browser": False, "profile": profile},
                "state": {"buffer": [], "candidate_pool": [candidate(1)]},
            }
            with self.assertRaisesRegex(EvidencePending, "Only 0/100"):
                collect_eligible_buffer(
                    store,
                    run,
                    None,
                    lambda: 100,
                    lambda: None,
                    refill=lambda *a: False,
                    snapshot_collector=lambda h, i: {
                        **i["engagement"],
                        "duration_seconds": 60,
                        "observed_at": utc_now(),
                    },
                )
            self.assertEqual(
                run["state"]["screening_rejections"][candidate(1)["canonical_id"]],
                "duration_exclusion",
            )
