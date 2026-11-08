from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.storage import Store
from broader_vids.preferences import set_profile
from broader_vids.discovery.service import create_discovery
from broader_vids.exploration.domains import seed_interests, nodes
from broader_vids.exploration.coverage import (
    memory,
    transaction,
    record_search,
    save_memory,
    export_summary,
)
from broader_vids.exploration.allocation import allocate, select_branches
from broader_vids.exploration.rotation import _add_branches
from broader_vids.exploration.planning import prepare_plan, observe_query
from broader_vids.exploration.proposals import validate_response
import test_rotation
from test_exploration import proposal


class BalancedExplorationTests(unittest.TestCase):
    def test_root_allocations_ignore_branch_count_and_freeze_before_expansion(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, ["mathematics", "crime"])
            seed_interests(store)
            roots = {n["label"]: n for n in nodes(store).values()}
            for domain, count in (("mathematics", 100), ("crime", 2)):
                for start in range(0, count, 60):
                    with store.db:
                        _add_branches(
                            store,
                            {
                                "branches": [
                                    {
                                        "parent_id": roots[domain]["id"],
                                        "label": f"{domain} concept {i}",
                                        "aliases": [],
                                    }
                                    for i in range(start, min(count, start + 60))
                                ]
                            },
                            {roots[domain]["id"]: roots[domain]},
                        )
            state = {"seed": 42, "profile": profile}
            with transaction(store):
                allocate(store, state, "batch", "youtube")
            allocations = list(state["domain_allocations"])
            self.assertEqual(Counter(allocations), {"mathematics": 6, "crime": 6})
            with store.db:
                _add_branches(
                    store,
                    {
                        "branches": [
                            {
                                "parent_id": roots["crime"]["id"],
                                "label": f"crime added {i}",
                                "aliases": [],
                            }
                            for i in range(8)
                        ]
                    },
                    {roots["crime"]["id"]: roots["crime"]},
                )
            with transaction(store):
                allocate(store, state, "batch", "youtube")
                select_branches(store, state, "batch", "youtube")
            self.assertEqual(state["domain_allocations"], allocations)
            self.assertEqual(
                Counter(state["allocation_domains"].values()),
                {"mathematics": 6, "crime": 6},
            )
            self.assertEqual(len({n["id"] for n in state["selected"]}), 12)
            saved = [n["id"] for n in state["selected"]]
            with transaction(store):
                select_branches(store, state, "batch", "youtube")
            self.assertEqual([n["id"] for n in state["selected"]], saved)

    def test_forty_interests_rotate_independently_and_capacity_is_reported(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, [f"interest {i}" for i in range(40)])
            seed_interests(store)
            seen = set()
            previous = []
            for number in range(5):
                state = {"seed": number + 10, "profile": profile}
                with transaction(store):
                    allocate(store, state, f"round-{number}", "youtube")
                    select_branches(store, state, f"round-{number}", "youtube")
                    for node in state["selected"]:
                        root = state["allocation_domains"][node["id"]]
                        record_search(
                            store,
                            f"round-{number}",
                            node["id"],
                            platform="youtube",
                            domain=root,
                        )
                        seen.add(root)
                previous.append(state["domain_allocations"])
            self.assertEqual(len(seen), 40)
            value = memory(store)
            self.assertFalse(value["root_usage"]["bilibili"])
            self.assertLessEqual(
                max(c["searches"] for c in value["root_usage"]["youtube"].values())
                - min(c["searches"] for c in value["root_usage"]["youtube"].values()),
                1,
            )
            self.assertNotEqual(previous[0], previous[1])
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            profile = set_profile(store, ["crime"])
            seed_interests(store)
            state = {"seed": 4, "profile": profile}
            with transaction(store):
                allocate(store, state, "short", "bilibili")
                select_branches(store, state, "short", "bilibili")
            self.assertEqual(len(state["selected"]), 1)
            self.assertTrue(
                any(r.get("capacity") for r in state["rotation_relaxations"])
            )

    def test_promoted_interest_uses_familiar_baseline_and_exports_subfield_counts(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["mathematics", "crime"])
            seed_interests(store)
            value = memory(store)
            value["root_usage"]["youtube"] = {
                "mathematics": {"searches": 100, "last_used": 0},
                "crime": {"searches": 250, "last_used": 0},
            }
            value["root_profiles"] = {"youtube": ["mathematics"]}
            with store.db:
                save_memory(store, value)
            state = {"seed": 2, "profile": set_profile(store, ["mathematics", "crime"])}
            with transaction(store):
                allocate(store, state, "promoted", "youtube")
            self.assertEqual(
                memory(store)["root_usage"]["youtube"]["crime"]["searches"], 100
            )
            crime = next(n for n in nodes(store).values() if n["label"] == "crime")
            with store.db:
                _add_branches(
                    store,
                    {
                        "branches": [
                            {
                                "parent_id": crime["id"],
                                "label": "Criminal psychology",
                                "aliases": [],
                            }
                        ]
                    },
                    {crime["id"]: crime},
                )
            state = {
                "seed": 2,
                "profile": set_profile(store, ["mathematics", "criminal psychology"]),
            }
            with transaction(store):
                allocate(store, state, "subfield", "bilibili")
                select_branches(store, state, "subfield", "bilibili")
                node = next(
                    n for n in state["selected"] if n["label"] == "Criminal psychology"
                )
                self.assertEqual(
                    state["allocation_domains"][node["id"]], "criminal psychology"
                )
                record_search(
                    store,
                    "subfield",
                    node["id"],
                    platform="bilibili",
                    domain="criminal psychology",
                )
            self.assertEqual(
                export_summary(store)["root_usage"]["bilibili"]["criminal psychology"][
                    "searches"
                ],
                1,
            )

    def test_concurrent_search_observations_preserve_both_platform_queries(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["crime"])
            seed_interests(store)
            node = next(iter(nodes(store).values()))
            owner = store.create("both", {"kind": "recommendation_batch"}, {})
            sources = []
            for platform in ("youtube", "bilibili"):
                rid = store.create(
                    platform,
                    {"kind": "discovery"},
                    {"exploration": {"coverage_owner": owner, "rotation_version": 2}},
                )
                request = store.begin_request(rid, "fixture")
                receipt = {"request_id": request, "transport": "anonymous_http"}
                store.end_request(request, "ok", json.dumps(receipt))
                sources.append((rid, receipt))
            start = threading.Barrier(2)
            legacy_read = threading.Barrier(2)

            def protected_nodes(local):
                current = nodes(local)
                if not local.db.in_transaction:
                    legacy_read.wait(5)
                return current

            def work(pair):
                rid, receipt = pair
                with closing(Store(directory)) as local:
                    run = local.get(rid)
                    platform = run["platform"]
                    task = {
                        "node_id": node["id"],
                        "query": platform + " query",
                        "new_topic": False,
                        "anchors": [["case"]],
                        "allocation_domain": "crime",
                    }
                    items = [
                        {
                            "canonical_id": "fixture",
                            "title": "case" if platform == "youtube" else "unrelated",
                        }
                    ]
                    start.wait(5)
                    observe_query(local, run, task, items, receipt)
                    observe_query(local, run, task, items, receipt)

            with (
                patch(
                    "broader_vids.exploration.planning.nodes",
                    side_effect=protected_nodes,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                list(executor.map(work, sources))
            updated = nodes(store)[node["id"]]
            self.assertEqual(
                updated["queries"],
                {"youtube": "youtube query", "bilibili": "bilibili query"},
            )
            self.assertEqual(updated["status"], "active")
            self.assertEqual(memory(store)["usage"][node["id"]]["searches"], 2)

    def test_shared_proposals_reuse_identity_but_preexisting_duplicates_are_rejected(
        self,
    ):
        value = proposal("Forensic entomology")
        response = {"existing_queries": [], "proposals": [value]}
        _, first, _ = validate_response(response, {}, [], False, 5, novelty_baseline=[])
        current = {first[0]["id"]: first[0]}
        _, second, rejected = validate_response(
            response, current, [], False, 5, novelty_baseline=[]
        )
        self.assertFalse(rejected)
        self.assertEqual(second[0]["id"], first[0]["id"])
        self.assertTrue(second[0]["shared_existing"])
        _, duplicate, rejected = validate_response(
            response, current, [], False, 5, novelty_baseline=list(current)
        )
        self.assertFalse(duplicate)
        self.assertEqual(len(rejected), 1)

    def test_parallel_platforms_can_select_same_topics_and_share_new_catalog_entries(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(
                store,
                [
                    "mathematics",
                    "physics",
                    "travel",
                    "coding",
                    "video games",
                    "cooking",
                ],
            )
            ids = [
                create_discovery(
                    store,
                    p,
                    seed=19,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
                for p in ("youtube", "bilibili")
            ]
            batch = store.create("both", {"kind": "recommendation_batch"}, {})
            owners = [
                store.create(p, {"kind": "recommendation", "batch_id": batch}, {})
                for p in ("youtube", "bilibili")
            ]
            barrier = threading.Barrier(2)

            def work(pair):
                rid, owner = pair
                with closing(Store(directory)) as local:
                    run = local.get(rid)

                    def model(*args):
                        if "branches" in json.loads(args[1].read_text())["properties"]:
                            barrier.wait(5)
                        return test_rotation.RotationTests().model(*args)

                    prepare_plan(
                        local,
                        run,
                        lambda: local.save(run),
                        lambda: 100,
                        model,
                        usage_owner=owner,
                    )
                    return run["state"]["exploration"]

            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = list(executor.map(work, zip(ids, owners)))
            self.assertEqual(
                {n["id"] for n in first["selected"]},
                {n["id"] for n in second["selected"]},
            )
            self.assertEqual(
                {n["id"] for n in first["new_nodes"]},
                {n["id"] for n in second["new_nodes"]},
            )
            self.assertEqual(len(first["new_nodes"]), 5)
            self.assertEqual(len(second["new_nodes"]), 5)
            self.assertEqual(len(nodes(store)), 41)
