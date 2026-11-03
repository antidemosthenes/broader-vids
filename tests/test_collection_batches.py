from collections import Counter
import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

from broader_vids.evidence.media import EvidencePending
from broader_vids.http import BudgetExhausted, PublicHttp
from broader_vids.preferences import set_profile
from broader_vids.recommendations.budgets import increase_request_limit
from broader_vids.recommendations.collection import (
    collect_eligible_buffer,
    refill_candidates,
)
from broader_vids.recommendations.collection_policy import collection_lane
from broader_vids.recommendations.ranking import screen
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store, utc_now
from test_recommendations import candidate


def item(index, lane, likes=100):
    result = candidate(index)
    result["origins"] = [
        {
            "family_id": lane,
            "domains": ["mathematics" if lane == "familiar" else "ecology"],
            "new_topic": lane == "exploratory",
        }
    ]
    result["engagement"] = {"views": 1000, "likes": likes}
    return result


class CollectionBatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        self.profile = set_profile(self.store, ["math"])
        self.fetched = []

    def run_data(self, candidates, max_requests=320):
        identifier = create_recommendation(
            self.store,
            "youtube",
            max_requests=max_requests,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        run["state"].update(phase="enrich", candidate_pool=candidates)
        self.store.save(run)
        return run

    def snapshot(self, http, value):
        if http is not None:
            http.check()
            request = self.store.begin_request(
                http.run["id"], "https://www.youtube.com/watch"
            )
            self.store.end_request(request, "ok")
        self.fetched.append(value["canonical_id"])
        return {
            **value["engagement"],
            "duration_seconds": 300,
            "observed_at": utc_now(),
        }

    def collect(self, run, refill=lambda *args, **kwargs: False, http=None):
        collect_eligible_buffer(
            self.store,
            run,
            http,
            lambda: 100,
            lambda: self.store.save(run),
            refill=refill,
            snapshot_collector=self.snapshot,
        )

    def test_repeated_batches_fill_deficient_lanes_with_their_best_survivors(self):
        familiar = [
            item(i + 1, "familiar", 0 if i < 20 else 100 + i) for i in range(200)
        ]
        exploratory = [
            item(1000 + i, "exploratory", 0 if i < 25 or 29 <= i < 69 else 200 + i)
            for i in range(250)
        ]
        run = self.run_data(familiar + exploratory)
        self.collect(run)
        state = run["state"]
        self.assertEqual(len(state["buffer"]), 100)
        self.assertEqual(
            state["collection"]["counts"], {"familiar": 71, "exploratory": 29}
        )
        self.assertEqual(
            [h["scanned"] for h in state["collection"]["history"]], [100, 100, 100]
        )
        self.assertEqual(len(self.fetched), 300)
        self.assertEqual(len(set(self.fetched)), 300)
        self.assertNotIn("active", state["collection"])
        selected = {value["canonical_id"] for value in state["buffer"]}
        self.assertTrue(
            all(value["canonical_id"] in selected for value in exploratory[176:185])
        )
        self.assertTrue(
            all(value["canonical_id"] not in selected for value in exploratory[85:176])
        )
        shortlisted, _ = screen(state["buffer"], [], {}, self.profile)
        self.assertEqual(len(shortlisted), 35)
        self.assertEqual(len({v["canonical_id"] for v in shortlisted}), 35)

    def test_refill_requests_only_the_missing_lane_and_duplicates_do_not_fill_slots(
        self,
    ):
        initial = [item(i + 1, "familiar") for i in range(71)] + [
            item(i + 200, "exploratory", 0) for i in range(29)
        ]
        run = self.run_data(initial)
        lanes = []
        added = [item(i + 1000, "exploratory", 100 + i) for i in range(100)]

        def refill(store, run, remaining, save, *, lane):
            lanes.append(lane)
            run["state"]["candidate_pool"].extend([*initial[:10], *added])
            save()
            return True

        self.collect(run, refill)
        self.assertEqual(lanes, ["exploratory"])
        self.assertEqual(len(self.fetched), 200)
        selected = {value["canonical_id"] for value in run["state"]["buffer"]}
        self.assertEqual(len(selected), 100)
        self.assertTrue({value["canonical_id"] for value in added[-29:]} <= selected)
        self.assertEqual(
            run["state"]["collection"]["counts"], {"familiar": 71, "exploratory": 29}
        )

    def test_request_pause_keeps_partial_batch_and_resume_does_not_fetch_processed_items(
        self,
    ):
        values = [item(i + 1, "familiar") for i in range(71)] + [
            item(i + 200, "exploratory") for i in range(29)
        ]
        run = self.run_data(values, max_requests=20)
        with self.assertRaises(BudgetExhausted):
            self.collect(run, http=PublicHttp(self.store, run, anonymous_session=True))
        saved = copy.deepcopy(self.store.get(run["id"]))
        self.assertEqual(saved["state"]["collection"]["active"]["index"], 20)
        self.assertEqual(saved["state"]["buffer"], [])
        increase_request_limit(self.store, run["id"], 160)
        resumed = self.store.get(run["id"])
        self.assertEqual(resumed["state"], saved["state"])
        self.collect(
            resumed, http=PublicHttp(self.store, resumed, anonymous_session=True)
        )
        self.assertEqual(len(resumed["state"]["buffer"]), 100)
        self.assertEqual(len(set(self.fetched)), 100)
        self.assertEqual(len(self.fetched), 100)
        self.assertEqual(self.store.request_count(run["id"]), 100)
        self.assertNotIn("active", resumed["state"]["collection"])

    def test_source_exhaustion_does_not_replace_missing_exploration_with_familiar_videos(
        self,
    ):
        values = [item(i + 1, "familiar") for i in range(200)] + [
            item(i + 1000, "exploratory") for i in range(10)
        ]
        run = self.run_data(values)
        with self.assertRaisesRegex(EvidencePending, "0 familiar and 19 exploratory"):
            self.collect(run)
        self.assertEqual(len(run["state"]["buffer"]), 81)
        self.assertEqual(
            run["state"]["collection"]["counts"], {"familiar": 71, "exploratory": 10}
        )
        self.assertEqual(run["state"]["shortlist"], [])
        self.assertEqual(
            Counter(
                collection_lane(value, self.profile) for value in run["state"]["buffer"]
            )["familiar"],
            71,
        )

    def test_bilibili_refill_never_reuses_authenticated_metadata_cookies(self):
        source = self.store.create("bilibili", {"kind": "discovery"}, {"plan": []})
        identifier = create_recommendation(
            self.store,
            "bilibili",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        run["state"].update(
            discovery_run=source,
            candidate_pool=[],
            refill_tasks=[{"query": "数学", "done": False, "page": 2}],
        )
        visitor = Mock(cookies={"SESSDATA": "private-test-value"})
        provider = Mock()
        provider.search.side_effect = ValueError("End isolated request test")
        with (
            patch("broader_vids.recommendations.collection.PublicHttp") as factory,
            patch(
                "broader_vids.discovery.sources.BilibiliDiscovery",
                return_value=provider,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "End isolated request test"):
                refill_candidates(
                    self.store, run, lambda: 60, lambda: None, visitor_session=visitor
                )
        self.assertNotIn("cookie_jar", factory.call_args.kwargs)

    def test_targeted_continuation_keeps_attribution_and_stops_repeated_cursor(self):
        plans = [
            {
                "query": "algebra",
                "family_id": "familiar",
                "domains": ["mathematics"],
                "new_topic": False,
            },
            {
                "query": "ecology",
                "family_id": "exploratory",
                "domains": ["ecology"],
                "new_topic": True,
            },
        ]
        source = self.store.create(
            "youtube",
            {"kind": "discovery", "buffer_limit": 1000},
            {
                "client_version": "fixture",
                "candidates": {},
                "creators": {},
                "plan": plans,
                "pages": [
                    {
                        **p,
                        "source": "search",
                        "continuation": p["family_id"] + "-cursor",
                    }
                    for p in plans
                ],
            },
        )
        run = self.run_data([])
        run["state"]["discovery_run"] = source
        visitor = PublicHttp(self.store, run, anonymous_session=True)
        provider = Mock()
        provider.search.return_value = (
            [item(1001, "exploratory")],
            "exploratory-cursor",
        )
        with (
            patch(
                "broader_vids.recommendations.collection.PublicHttp",
                return_value=Mock(last_receipt={}),
            ) as factory,
            patch(
                "broader_vids.discovery.sources.YouTubeDiscovery", return_value=provider
            ),
        ):
            self.assertTrue(
                refill_candidates(
                    self.store,
                    run,
                    lambda: 100,
                    lambda: self.store.save(run),
                    lane="exploratory",
                    visitor_session=visitor,
                )
            )
            self.assertFalse(
                refill_candidates(
                    self.store,
                    run,
                    lambda: 100,
                    lambda: self.store.save(run),
                    lane="exploratory",
                )
            )
        self.assertIs(factory.call_args.kwargs["cookie_jar"], visitor.cookies)
        self.assertIs(factory.call_args.kwargs["clock"], visitor.clock)
        provider.search.assert_called_once_with(
            "ecology", "fixture", "exploratory-cursor"
        )
        origin = run["state"]["candidate_pool"][0]["origins"][-1]
        self.assertTrue(origin["new_topic"])
        self.assertEqual(origin["family_id"], "exploratory")
        run["state"]["refill_tasks"] = None
        provider.reset_mock()
        provider.search.side_effect = [
            ([item(1002, "exploratory")], "next-cursor"),
            ([item(1002, "exploratory")], "third-cursor"),
        ]
        with (
            patch(
                "broader_vids.recommendations.collection.PublicHttp",
                return_value=Mock(last_receipt={}),
            ),
            patch(
                "broader_vids.discovery.sources.YouTubeDiscovery", return_value=provider
            ),
        ):
            for expected in (True, True, False):
                self.assertEqual(
                    refill_candidates(
                        self.store,
                        run,
                        lambda: 100,
                        lambda: self.store.save(run),
                        lane="exploratory",
                    ),
                    expected,
                )
        self.assertEqual(provider.search.call_count, 2)

    def test_more_than_320_requests_resumes_the_next_batch_without_relaxing_balance(
        self,
    ):
        familiar = [item(i + 1, "familiar") for i in range(71)]
        exploratory = [
            item(i + 1000, "exploratory", 100 + i if 109 <= i < 129 or i >= 229 else 0)
            for i in range(329)
        ]
        run = self.run_data(familiar + exploratory)
        with self.assertRaises(BudgetExhausted):
            self.collect(run, http=PublicHttp(self.store, run, anonymous_session=True))
        self.assertEqual(self.store.request_count(run["id"]), 320)
        self.assertEqual(run["state"]["collection"]["batches_completed"], 3)
        self.assertEqual(
            run["state"]["collection"]["counts"], {"familiar": 71, "exploratory": 20}
        )
        self.assertEqual(run["state"]["collection"]["active"]["index"], 20)
        self.assertEqual(run["state"]["shortlist"], [])
        increase_request_limit(self.store, run["id"], 500)
        resumed = self.store.get(run["id"])
        self.collect(
            resumed, http=PublicHttp(self.store, resumed, anonymous_session=True)
        )
        self.assertEqual(
            resumed["state"]["collection"]["counts"],
            {"familiar": 71, "exploratory": 29},
        )
        self.assertEqual(resumed["state"]["collection"]["batches_completed"], 4)
        self.assertEqual(self.store.request_count(run["id"]), 400)
        self.assertEqual(len(set(self.fetched)), len(self.fetched))
        self.assertNotIn("active", resumed["state"]["collection"])
