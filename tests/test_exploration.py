import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.discovery.service import (
    create_discovery,
    execute_discovery,
    discovery_report,
)
from broader_vids.evidence.media import EvidencePending
from broader_vids.exploration.domains import (
    nodes,
    put_node,
    seed_interests,
    select_existing,
)
from broader_vids.exploration.proposals import validate_response
from broader_vids.preferences import get_profile, set_profile
from broader_vids.recommendations.ranking import related
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
)
from broader_vids.storage import Store
from test_discovery import FixtureHttp


def proposal(label, parents=None):
    return {
        "label": label,
        "aliases": [],
        "parents": parents or [],
        "novelty_reason": f"Explore the distinct concept {label} beyond recent coverage.",
        "queries": {"youtube": f"{label} explanation", "bilibili": f"{label} 原理"},
        "anchors": ["Topic"],
    }


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.profile = set_profile(self.store, ["math", "physics", "music"])
        self.calls = []

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def model(self, prompt, schema, output, images, timeout):
        context = json.loads(prompt.split("\n", 1)[1])
        self.calls.append(context)
        self.assertEqual(images, [])
        self.assertLessEqual(timeout, 120)
        self.assertTrue(Path(schema).is_file())
        return {
            "existing_queries": [
                {
                    "node_id": n["id"],
                    "queries": proposal(n["label"])["queries"],
                    "anchors": ["Topic"],
                }
                for n in context["selected"]
            ],
            "proposals": [
                proposal(f"New concept {i}") for i in range(context["needed"])
            ],
        }, {"input_tokens": 100, "output_tokens": 50}

    def test_cold_start_search_activation_and_preferences_are_independent(self):
        original = get_profile(self.store)
        identifier = create_discovery(
            self.store,
            "youtube",
            seed=42,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=self.model
        )
        state = run["state"]["exploration"]
        self.assertTrue(state["ready"])
        self.assertEqual(len(state["selected"]), 3)
        self.assertEqual(state["existing_shortfall"], 9)
        self.assertEqual(len(state["new_nodes"]), 5)
        self.assertEqual(state["new_shortfall"], 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(nodes(self.store)), 8)
        self.assertTrue(
            all(n["status"] == "active" for n in nodes(self.store).values())
        )
        self.assertEqual(get_profile(self.store), original)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM domain_observations"
            ).fetchone()[0],
            8,
        )
        self.assertEqual(
            discovery_report(self.store, identifier)["planning_model_calls"], 1
        )
        execute_discovery(self.store, identifier, FixtureHttp, model_runner=self.model)
        self.assertEqual(len(self.calls), 1)

    def test_continuation_keeps_selected_topics_plan_and_attempt_allowance(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            seed=4,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        FixtureHttp.fail_once = True
        try:
            first = execute_discovery(
                self.store, identifier, FixtureHttp, model_runner=self.model
            )
            self.assertEqual(first["status"], "paused")
            snapshot = first["state"]["plan"]
            second = execute_discovery(
                self.store, identifier, FixtureHttp, model_runner=self.model
            )
            self.assertEqual(second["state"]["plan"], snapshot)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(second["state"]["phase"], "finished")
        finally:
            FixtureHttp.fail_once = False

    def test_duplicate_aliases_rejected_and_refinements_inherit_familiarity(self):
        seed_interests(self.store)
        current = nodes(self.store)
        math = next(n for n in current.values() if n["label"] == "mathematics")
        response = {
            "existing_queries": [],
            "proposals": [proposal("math"), proposal("Topology", [math["id"]])],
        }
        _, accepted, rejected = validate_response(response, current, [], False, 5)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 2)
        _, accepted, _ = validate_response(response, current, [], True, 5)
        topology = accepted[0]
        self.assertEqual(topology["depth"], 2)
        self.assertEqual(topology["domains"], ["mathematics"])
        self.assertTrue(related({"domains": topology["domains"]}, self.profile))
        topology["status"] = "active"
        with self.store.db:
            put_node(self.store, topology)
        current = nodes(self.store)
        music = next(n for n in current.values() if n["label"] == "music")
        response["proposals"] = [
            proposal("Fourier topology", [topology["id"], music["id"]])
        ]
        _, added, _ = validate_response(response, current, [], True, 5)
        self.assertEqual(added[0]["depth"], 3)
        self.assertEqual(set(added[0]["domains"]), {"mathematics", "music"})
        self.assertIn(math["id"], added[0]["ancestors"])

    def test_failed_generation_is_bounded_and_does_not_request_the_platform(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            max_planning_calls=1,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )

        def failed(*args):
            raise EvidencePending("Fixture model unavailable")

        first = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=failed
        )
        second = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=self.model
        )
        self.assertEqual(first["status"], "paused")
        self.assertEqual(second["status"], "budget_exhausted")
        self.assertEqual(self.store.request_count(identifier), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(nodes(self.store)), 3)

    def test_bounded_second_attempt_refines_locally_and_records_shortfalls(self):
        def refinement(prompt, schema, output, images, timeout):
            context = json.loads(prompt.split("\n", 1)[1])
            response, usage = self.model(prompt, schema, output, images, timeout)
            response["proposals"] = (
                []
                if not context["allow_refinements"]
                else [proposal("Topology", [context["selected"][0]["id"]])]
            )
            return response, usage

        identifier = create_discovery(
            self.store,
            "bilibili",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=refinement
        )
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(run["state"]["exploration"]["new_shortfall"], 4)
        node = next(n for n in nodes(self.store).values() if n["label"] == "Topology")
        self.assertEqual(node["depth"], 2)
        self.assertEqual(node["status"], "active")

    def test_empty_successful_search_does_not_activate_a_proposal(self):
        def no_match(*args):
            response, usage = self.model(*args)
            for node in response["proposals"]:
                node["anchors"] = ["absent-specialized-term"]
            return response, usage

        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        execute_discovery(self.store, identifier, FixtureHttp, model_runner=no_match)
        self.assertEqual(
            sum(n["status"] == "active" for n in nodes(self.store).values()), 3
        )
        self.assertEqual(
            sum(n["status"] == "unconfirmed" for n in nodes(self.store).values()), 5
        )

    def test_selection_is_reproducible_preserves_interests_and_avoids_ancestors(self):
        seed_interests(self.store)
        current = nodes(self.store)
        math = next(n for n in current.values() if n["label"] == "mathematics")
        candidates = [
            proposal(f"Topic {i}", [math["id"]] if i < 10 else []) for i in range(25)
        ]
        for offset in range(0, 25, 5):
            _, added, _ = validate_response(
                {"existing_queries": [], "proposals": candidates[offset : offset + 5]},
                current,
                [],
                True,
                5,
            )
            with self.store.db:
                for node in added:
                    node["status"] = "active"
                    put_node(self.store, node)
        first = select_existing(self.store, self.profile, 3)
        second = select_existing(self.store, self.profile, 3)
        self.assertEqual(first, second)
        ids = {n["id"] for n in first}
        self.assertTrue(all(not ids.intersection(n["ancestors"]) for n in first))
        self.assertEqual(len(ids), len(first))
        self.assertGreaterEqual(
            sum(
                bool(set(n["domains"]) & set(self.profile["interests"])) for n in first
            ),
            6,
        )

    def test_profile_change_cannot_silently_rewrite_a_checkpoint(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        selected = self.store.get(identifier)["state"]["exploration"]["selected"]
        set_profile(self.store, ["travel"])
        result = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=self.model
        )
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.calls, [])
        self.assertEqual(result["state"]["exploration"]["selected"], selected)

    def test_curated_mode_remains_explicit_and_does_not_invoke_the_model(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            curated=True,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        execute_discovery(
            self.store,
            identifier,
            FixtureHttp,
            model_runner=lambda *a: self.fail("Model called"),
        )
        self.assertEqual(len(self.store.get(identifier)["state"]["plan"]), 17)
        self.assertNotIn("exploration", self.store.get(identifier)["state"])

    def test_missing_existing_queries_can_use_the_remaining_attempt(self):
        def incomplete(*args):
            response, usage = self.model(*args)
            if len(self.calls) == 1:
                response["existing_queries"] = []
            return response, usage

        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = execute_discovery(
            self.store, identifier, FixtureHttp, model_runner=incomplete
        )
        self.assertEqual(run["state"]["phase"], "finished")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(run["state"]["exploration"]["new_nodes"]), 5)

    def test_reusing_a_finished_buffer_does_not_charge_historical_planning(self):
        identifier = create_discovery(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        execute_discovery(self.store, identifier, FixtureHttp, model_runner=self.model)
        with patch("broader_vids.recommendations.service.enrich_buffer"):
            run = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    identifier,
                    max_assessments=0,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(run["state"]["planning_model_calls"], 0)
        self.assertEqual(len(self.calls), 1)

    def test_recommendations_charge_planning_calls_to_the_shared_allowance(self):
        def discovery(store, identifier, **kwargs):
            return execute_discovery(
                store, identifier, FixtureHttp, model_runner=self.model, **kwargs
            )

        with (
            patch(
                "broader_vids.recommendations.service.execute_discovery",
                side_effect=discovery,
            ),
            patch("broader_vids.recommendations.service.enrich_buffer") as enrich,
        ):
            run = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    max_assessments=0,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(self.calls, [])
        self.assertEqual(run["status"], "paused")
        self.assertFalse(enrich.called)
        self.assertEqual(self.store.request_count(run["state"]["discovery_run"]), 0)

    def test_planning_consumes_the_last_model_call_before_video_analysis(self):
        def discovery(store, identifier, **kwargs):
            return execute_discovery(
                store, identifier, FixtureHttp, model_runner=self.model, **kwargs
            )

        def enrich(store, run, *args):
            run["state"]["shortlist"] = run["state"]["candidate_pool"][:1]
            run["state"]["phase"] = "assess"

        with (
            patch(
                "broader_vids.recommendations.service.execute_discovery",
                side_effect=discovery,
            ),
            patch(
                "broader_vids.recommendations.service.enrich_buffer", side_effect=enrich
            ),
        ):
            run = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    max_assessments=1,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(run["state"]["planning_model_calls"], 1)
        self.assertEqual(run["state"]["model_calls"], 0)
        self.assertEqual(run["status"], "paused")
        self.assertIn("model-call budget", run["reason"])


if __name__ == "__main__":
    unittest.main()
