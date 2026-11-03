from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
import json
import tempfile
import threading
import unittest

from broader_vids.discovery.service import create_discovery, execute_discovery
from broader_vids.exploration.coverage import (
    memory,
    round_number,
    save_memory,
    transaction,
)
from broader_vids.exploration.domains import nodes, seed_interests
from broader_vids.exploration.planning import prepare_plan
from broader_vids.exploration.rotation import _add_branches, _select
from broader_vids.learning.feedback import save_detail
from broader_vids.learning.service import ensure_model
from broader_vids.preferences import set_profile
from broader_vids.recommendations.learning_snapshot import (
    freeze_learning,
    round_learning,
)
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from test_discovery import FixtureHttp
from test_exploration import proposal
from test_preference_transfer import seed, example


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(
            self.store,
            ["mathematics", "physics", "travel", "coding", "video games", "cooking"],
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def model(self, prompt, schema, output, images, timeout):
        context = json.loads(prompt.split("\n", 1)[1])
        if "branches" in json.loads(schema.read_text())["properties"]:
            return {
                "branches": [
                    {
                        "parent_id": node["id"],
                        "label": f"{node['label']} branch {i}",
                        "aliases": [],
                    }
                    for node in context["selected"]
                    for i in range(5)
                ]
            }, {}
        return {
            "existing_queries": [
                {
                    "node_id": node["id"],
                    "queries": proposal(node["label"])["queries"],
                    "fallback_queries": {
                        "youtube": node["label"],
                        "bilibili": node["label"] + " 原理",
                    },
                    "anchors": ["Topic"],
                }
                for node in context["selected"]
            ],
            "proposals": [
                proposal(f"New concept {i}") for i in range(context["needed"])
            ],
        }, {}

    def create(self, platform="youtube"):
        return create_discovery(
            self.store,
            platform,
            seed=19,
            planning_policy=2,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )

    def test_new_planner_selects_branches_and_resume_does_not_reroll(self):
        identifier = self.create()
        run = self.store.get(identifier)
        from broader_vids.http import BudgetExhausted

        with self.assertRaises(BudgetExhausted):
            prepare_plan(
                self.store,
                run,
                lambda: self.store.save(run),
                lambda: 0,
                lambda *a: self.fail("Expired model call"),
            )
        prepare_plan(
            self.store, run, lambda: self.store.save(run), lambda: 100, self.model
        )
        state = run["state"]["exploration"]
        self.assertEqual(len(state["selected"]), 12)
        self.assertTrue(all(node["parents"] for node in state["selected"]))
        self.assertEqual(len(state["new_nodes"]), 5)
        self.assertEqual(len(state["attempts"]), 2)
        self.assertEqual(len(run["state"]["plan"]), 17)
        selected = [node["id"] for node in state["selected"]]
        prepare_plan(
            self.store,
            run,
            lambda: self.store.save(run),
            lambda: 100,
            lambda *a: self.fail("Model rerun"),
        )
        self.assertEqual(selected, [node["id"] for node in state["selected"]])
        from broader_vids.exploration.rotation import prepare_rotation

        state["rotation_ready"] = False
        state["selected"] = list(reversed(state["selected"]))
        prepare_rotation(
            self.store,
            run,
            lambda: self.store.save(run),
            lambda: 100,
            lambda *a: self.fail("Model rerun"),
        )
        self.assertEqual(selected, [node["id"] for node in state["selected"]])

    def test_shared_batch_platforms_reserve_complementary_branches_concurrently(self):
        identifiers = [self.create(platform) for platform in ("youtube", "bilibili")]
        batch = self.store.create("both", {"kind": "recommendation_batch"}, {})
        owners = [
            self.store.create(
                platform, {"kind": "recommendation", "batch_id": batch}, {}
            )
            for platform in ("youtube", "bilibili")
        ]
        barrier = threading.Barrier(2)

        def work(pair):
            identifier, owner = pair
            with closing(Store(self.directory.name)) as local:
                run = local.get(identifier)

                def model(*args):
                    if "branches" in json.loads(args[1].read_text())["properties"]:
                        barrier.wait(5)
                    return self.model(*args)

                prepare_plan(
                    local,
                    run,
                    lambda: local.save(run),
                    lambda: 100,
                    model,
                    usage_owner=owner,
                )
                return run["state"]["exploration"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(work, zip(identifiers, owners)))
        self.assertEqual(first["coverage_round"], second["coverage_round"])
        self.assertFalse(
            {n["id"] for n in first["selected"]} & {n["id"] for n in second["selected"]}
        )
        self.assertEqual(memory(self.store)["sequence"], 1)

    def test_recommended_branch_cooldown_preserves_other_branches_of_the_interest(self):
        seed_interests(self.store)
        parent = next(
            n for n in nodes(self.store).values() if n["label"] == "mathematics"
        )
        with self.store.db:
            _add_branches(
                self.store,
                {
                    "branches": [
                        {
                            "parent_id": parent["id"],
                            "label": f"Math branch {i}",
                            "aliases": [],
                        }
                        for i in range(15)
                    ]
                },
                {parent["id"]: parent},
            )
        branches = [
            n for n in nodes(self.store).values() if n["parents"] == [parent["id"]]
        ]
        blocked = branches[:3]
        with transaction(self.store):
            round_number(self.store, "prior")
            value = memory(self.store)
            value["usage"] = {
                n["id"]: {"searches": 0, "last_used": 1, "last_recommended": 1}
                for n in blocked
            }
            save_memory(self.store, value)
            state = {"seed": 2, "profile": {"interests": ["mathematics"]}}
            _select(self.store, state, "next", "youtube")
        selected = {n["id"] for n in state["selected"]}
        self.assertFalse(selected & {n["id"] for n in blocked})
        self.assertEqual(
            sum(n["domains"] == ["mathematics"] for n in state["selected"]), 12
        )
        self.assertFalse(
            any(entry["cooldown"] for entry in state["rotation_relaxations"])
        )
        from broader_vids.exploration.coverage import reserve

        with transaction(self.store):
            reserve(self.store, "paired", "bilibili", sorted(selected))
            paired = {"seed": 2, "profile": {"interests": ["mathematics"]}}
            _select(self.store, paired, "paired", "youtube")
        self.assertFalse(
            {n["id"] for n in paired["selected"]} & {n["id"] for n in blocked}
        )
        self.assertTrue(
            all(
                entry["shared_platform"] and not entry["cooldown"]
                for entry in paired["rotation_relaxations"]
            )
        )

    def test_failed_optional_catalog_can_use_the_remaining_query_call(self):
        identifier = self.create()
        calls = []

        def model(*args):
            calls.append(True)
            if len(calls) == 1:
                raise TimeoutError("Fixture catalog timeout")
            return self.model(*args)

        run = self.store.get(identifier)
        prepare_plan(self.store, run, lambda: self.store.save(run), lambda: 100, model)
        self.assertTrue(run["state"]["exploration"]["ready"])
        self.assertEqual(len(calls), 2)
        self.assertIn("catalog_warning", run["state"]["exploration"])

    def test_failed_plan_writes_do_not_persist_partial_query_or_node_state(self):
        from unittest.mock import patch
        from broader_vids.evidence.media import EvidencePending

        for failure in ("insert", "save"):
            with self.subTest(failure=failure):
                identifier = self.create()
                run = self.store.get(identifier)
                state = run["state"]["exploration"]

                def model(*args):
                    response, usage = self.model(*args)
                    if "proposals" in response:
                        response["proposals"] = [
                            proposal(f"Atomic {failure} {i}") for i in range(5)
                        ]
                    return response, usage

                def save():
                    attempt = state["attempts"][-1] if state["attempts"] else {}
                    if (
                        failure == "save"
                        and attempt.get("stage") != "subdivision"
                        and attempt.get("status") == "completed"
                    ):
                        raise OSError("Fixture save failure")
                    self.store.save(run)

                with (
                    patch(
                        "broader_vids.exploration.planning.put_node",
                        side_effect=ValueError("Fixture insert failure"),
                    )
                    if failure == "insert"
                    else nullcontext()
                ):
                    with self.assertRaises(EvidencePending):
                        prepare_plan(self.store, run, save, lambda: 100, model)
                persisted = self.store.get(identifier)["state"]["exploration"]
                self.assertEqual(persisted["queries"], {})
                self.assertEqual(persisted["new_nodes"], [])
                self.assertFalse(
                    any(
                        node["label"].startswith(f"Atomic {failure}")
                        for node in nodes(self.store).values()
                    )
                )

    def test_concurrent_search_coverage_updates_do_not_lose_counts(self):
        from broader_vids.exploration.coverage import record_search

        identifier = next(iter(nodes(self.store))) if nodes(self.store) else None
        if identifier is None:
            seed_interests(self.store)
            identifier = next(iter(nodes(self.store)))
        barrier = threading.Barrier(2)

        def work(index):
            with closing(Store(self.directory.name)) as local:
                barrier.wait(5)
                for _ in range(10):
                    record_search(local, "shared", identifier)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(work, range(2)))
        value = memory(self.store)
        self.assertEqual(value["sequence"], 1)
        self.assertEqual(value["usage"][identifier]["searches"], 20)
        for number in range(3):
            round_number(self.store, f"later-{number}")
        record_search(self.store, "shared", identifier)
        value = memory(self.store)
        self.assertEqual(value["usage"][identifier]["last_used"], value["sequence"])

    def test_coverage_merge_rebases_local_parents_and_preserves_import_ages(self):
        from unittest.mock import patch
        from broader_vids.exploration.coverage import (
            export_summary,
            import_summary,
            validate_summary,
        )
        from broader_vids.exploration.domains import node_id

        seed_interests(self.store)
        parent = next(
            n for n in nodes(self.store).values() if n["label"] == "mathematics"
        )
        with transaction(self.store):
            _add_branches(
                self.store,
                {
                    "branches": [
                        {"parent_id": parent["id"], "label": "topology", "aliases": []}
                    ]
                },
                {parent["id"]: parent},
            )
        root_id, child_id = node_id("topology"), node_id("covering spaces")
        root = {
            "id": root_id,
            "label": "topology",
            "aliases": [],
            "parents": [],
            "domains": ["topology"],
            "depth": 1,
            "origin": "exploration",
            "status": "active",
        }
        child = {
            **root,
            "id": child_id,
            "label": "covering spaces",
            "parents": [root_id],
            "depth": 2,
            "origin": "subdivision",
        }
        summary = {
            "version": 1,
            "sequence": 10,
            "nodes": [root, child],
            "usage": {
                child_id: {"searches": 2, "last_used": 10, "last_recommended": 10}
            },
        }
        with transaction(self.store):
            import_summary(self.store, summary)
        merged = nodes(self.store)[child_id]
        self.assertEqual(merged["depth"], 3)
        self.assertEqual(merged["domains"], ["mathematics"])
        self.assertEqual(set(merged["ancestors"]), {root_id, parent["id"]})
        validate_summary(export_summary(self.store))
        round_number(self.store, "later")
        with transaction(self.store):
            import_summary(self.store, summary)
        self.assertEqual(memory(self.store)["usage"][child_id]["last_recommended"], 10)
        extra = {
            **root,
            "id": node_id("extra topic"),
            "label": "extra topic",
            "aliases": [],
        }
        oversized = {**summary, "nodes": [root, child, extra]}
        before = nodes(self.store)
        with patch("broader_vids.exploration.coverage.MAX_NODES", len(before)):
            with self.assertRaisesRegex(ValueError, "1,024"):
                with transaction(self.store):
                    import_summary(self.store, oversized)
        self.assertEqual(nodes(self.store), before)
        extra["aliases"] = ["physics"]
        with self.assertRaisesRegex(ValueError, "alias conflicts"):
            with transaction(self.store):
                import_summary(self.store, oversized)
        self.assertEqual(nodes(self.store), before)

    def test_planner_context_is_bounded_for_a_large_multilingual_hierarchy(self):
        from broader_vids.exploration.proposals import proposal_prompt

        current = {
            str(i): {
                "id": str(i),
                "label": f"Topic {i}",
                "aliases": ["分支" * 60] * 6,
                "parents": [],
                "depth": 1,
                "status": "active",
            }
            for i in range(1024)
        }
        prompt = proposal_prompt(
            current, [], [], {"interests": ["mathematics"]}, 5, False, policy_version=2
        )
        context = json.loads(prompt.split("\n", 1)[1])
        self.assertTrue(context["map_is_partial"])
        self.assertLess(len(prompt.encode("utf-8")), 192 * 1024)

    def test_weak_result_retries_the_same_concept_once_with_its_short_query(self):
        from unittest.mock import patch
        from broader_vids.discovery.sources import YouTubeDiscovery

        identifier = self.create()
        run = self.store.get(identifier)
        prepare_plan(
            self.store, run, lambda: self.store.save(run), lambda: 100, self.model
        )
        run["state"]["plan"] = [
            next(task for task in run["state"]["plan"] if not task["new_topic"])
        ]
        first = dict(run["state"]["plan"][0])
        self.store.save(run)
        calls = []
        original = YouTubeDiscovery.search

        def search(provider, query, *args, **kwargs):
            items, cursor = original(provider, query, *args, **kwargs)
            calls.append(query)
            if len(calls) == 1:
                items = [{**item, "title": "Unrelated result"} for item in items]
            return items, cursor

        with patch.object(YouTubeDiscovery, "search", search):
            result = execute_discovery(
                self.store,
                identifier,
                FixtureHttp,
                model_runner=lambda *a: self.fail("Extra model call"),
            )
        task = result["state"]["plan"][0]
        self.assertEqual(calls[:2], [first["query"], first["fallback_query"]])
        self.assertTrue(task["query_simplified"])
        self.assertEqual(task["node_id"], first["node_id"])

    def test_new_round_model_is_pinned_and_feedback_changes_only_the_next_snapshot(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            identifier = create_recommendation(
                store,
                "youtube",
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = store.get(identifier)
            freeze_learning(store, run)
            before = round_learning(store, run)
            save_detail(
                store,
                example(1)["canonical_id"],
                "topic_interest",
                "low",
                target="topology",
            )
            current = ensure_model(store)
            self.assertNotEqual(before["id"], current["id"])
            freeze_learning(store, run)
            self.assertEqual(before, round_learning(store, run))
            self.assertIsNotNone(
                store.db.execute(
                    "SELECT 1 FROM learning_models WHERE id=?", (before["id"],)
                ).fetchone()
            )
