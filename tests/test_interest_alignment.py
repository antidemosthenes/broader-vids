import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.http import BudgetExhausted
from broader_vids.learning.alignment import (
    ALIGNMENT_VERSION,
    aligned_items,
    fingerprint,
    prepare_alignment,
)
from broader_vids.learning.features import capture, interest_prior, affinity_features
from broader_vids.learning.scoped import group_key, interest_prediction
from broader_vids.storage import Store
from broader_vids.token_usage import report
from test_recommendations import candidate


class InterestAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def create_run(self, records, *, interests=None, overrides=None):
        interests = interests or ["crime", "math"]
        items, evidence = [], {}
        for index, topic, broad, domains in records:
            item = candidate(index)
            item.update(
                title=topic,
                domains=domains,
                origins=[{"family_id": f"family-{index}", "domains": domains}],
            )
            items.append(item)
            evidence[item["canonical_id"]] = {
                "quality": 75,
                "duration_seconds": 900,
                "evidence_run": f"evidence-{index}",
                "summary": f"Evidence about {topic}.",
                "topics": {"specific": [topic], "broad": [broad]},
            }
        settings = {
            "kind": "recommendation",
            "interest_alignment_version": ALIGNMENT_VERSION,
            "worker_host": "codex",
            "assessment_model": "fixture-model",
            "reasoning_effort": "low",
            "max_assessments": 52,
            "profile": {"interests": interests},
            **(overrides or {}),
        }
        state = {
            "shortlist": items,
            "evidence": evidence,
            "model_calls": 0,
            "alignment_model_calls": 0,
            "planning_model_calls": 0,
        }
        run_id = self.store.create("youtube", settings, state)
        return self.store.get(run_id)

    def prepare(self, run, runner, remaining=lambda: 30):
        prepare_alignment(
            self.store,
            run,
            lambda: self.store.save(run),
            remaining,
            runner=runner,
        )

    @staticmethod
    def response_for(prompt, *, confidence="high"):
        request = json.loads(prompt.split("\n", 1)[1])
        videos = request["videos"]
        output = []
        for video in videos:
            is_crime = "art crime" in video["summary"].lower()
            output.append(
                {
                    "canonical_id": video["canonical_id"],
                    "matches": ["crime"] if is_crime else [],
                    "confidence": confidence,
                    "rationale": "The assessed content supports this match.",
                }
            )
        return {"videos": output}

    def test_content_alignment_preserves_evidence_and_scoped_precedence(self):
        run = self.create_run(
            [
                (1, "Art crime investigation", "Art crime", ["crime"]),
                (2, "Art history overview", "Art history", ["art history"]),
                (3, "Art history without broad label", "", ["crime"]),
            ]
        )
        calls = []

        def runner(prompt, *_args):
            calls.append(prompt)
            return self.response_for(prompt), {
                "input_tokens": 120,
                "output_tokens": 20,
                "cached_input_tokens": 0,
            }

        self.prepare(run, runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(run["state"]["model_calls"], 0)
        self.assertEqual(run["state"]["alignment_model_calls"], 1)
        self.assertEqual(
            run["state"]["interest_alignment"]["records"]["youtube:0000000001A"][
                "matches"
            ],
            ["crime"],
        )

        items = aligned_items(run, run["state"]["shortlist"])
        aligned = capture(items[0], run["state"]["evidence"][items[0]["canonical_id"]])
        unrelated = capture(
            items[1], run["state"]["evidence"][items[1]["canonical_id"]]
        )
        self.assertEqual(aligned["domains"], ["crime"])
        self.assertEqual(aligned["content_domains"], ["art crime"])
        self.assertEqual(interest_prior(aligned, run["settings"]["profile"]), 0.75)
        self.assertEqual(interest_prior(unrelated, run["settings"]["profile"]), 0.35)

        uncertain = capture(
            items[2], run["state"]["evidence"][items[2]["canonical_id"]]
        )
        self.assertEqual(uncertain["domains"], [])
        self.assertEqual(interest_prior(uncertain, run["settings"]["profile"]), 0.5)

        self.assertIn("domain:crime", affinity_features(aligned))
        self.assertIn("domain:art crime", affinity_features(aligned))
        raw_subject = interest_prediction(
            aligned,
            0.75,
            {"groups": {"domain_interest": {group_key("art crime"): {"mean": 0.1}}}},
        )
        self.assertEqual(raw_subject["probability"], 0.1)

        video_id = aligned["canonical_id"]
        prediction = interest_prediction(
            aligned,
            0.5,
            {
                "groups": {
                    "domain_interest": {group_key("crime"): {"mean": 0.8}},
                    "topic_interest": {
                        group_key("art crime investigation"): {"mean": 0.2}
                    },
                    "depth": {
                        group_key("art crime investigation", "intermediate"): {
                            "mean": 0.1,
                            "observations": 1,
                        }
                    },
                },
                "video_levels": {video_id: "intermediate"},
                "depth_models": {},
            },
        )
        self.assertEqual(prediction["scope"], "topic_interest")
        self.assertEqual(prediction["matches"], ["art crime investigation"])
        self.assertAlmostEqual(prediction["depth"]["suitability"], 0.1)
        self.assertAlmostEqual(prediction["probability"], 0.0270270270)

        usage = next(
            stage
            for stage in report(self.store, run["id"])["report"]["stages"]
            if stage["key"] == "alignment"
        )
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["input_tokens"], 120)
        self.assertEqual(usage["output_tokens"], 20)

    def test_subject_relevance_does_not_require_a_confirmed_crime(self):
        run = self.create_run(
            [
                (
                    1,
                    "Suspected animal poisoning investigation",
                    "Community safety",
                    ["animals"],
                ),
                (2, "Pet care with a brief poisoning warning", "Pet care", ["crime"]),
                (
                    3,
                    "Unsolved murder investigation",
                    "Historical investigations",
                    ["history"],
                ),
            ]
        )
        keys = [item["canonical_id"] for item in run["state"]["shortlist"]]
        run["state"]["evidence"][keys[0]]["summary"] = (
            "Investigates suspected animal poisoning with surveillance and testing requests. The allegation remains unverified."
        )
        evidence = copy.deepcopy(run["state"]["evidence"])
        calls = []

        def runner(prompt, *_args):
            instructions, raw = prompt.split("\n", 1)
            self.assertIn("A confirmed offense is not required.", instructions)
            self.assertIn(
                "A pet-care video that briefly mentions possible poisoning need not match crime.",
                instructions,
            )
            self.assertIn(
                "Confidence measures topic relevance, not the probability that an allegation is true.",
                instructions,
            )
            self.assertIn(
                "Preserve uncertainty about allegations in the rationale.", instructions
            )
            self.assertEqual(len(json.loads(raw)["videos"]), 3)
            calls.append(True)
            return {
                "videos": [
                    {
                        "canonical_id": key,
                        "matches": ["crime"] if index != 1 else [],
                        "confidence": "high",
                        "rationale": rationale,
                    }
                    for index, (key, rationale) in enumerate(
                        zip(
                            keys,
                            [
                                "The central subject is a suspected poisoning investigation. The allegation remains unverified.",
                                "Animal care is central. Possible poisoning is mentioned only briefly.",
                                "The central subject is an unresolved murder investigation.",
                            ],
                        )
                    )
                ]
            }, None

        self.prepare(run, runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(run["state"]["evidence"], evidence)
        items = aligned_items(run, run["state"]["shortlist"])
        matches = [
            capture(item, evidence[item["canonical_id"]])["domains"] for item in items
        ]
        self.assertEqual(matches, [["crime"], ["pet care"], ["crime"]])
        self.assertEqual(run["state"]["model_calls"], 0)
        self.assertEqual(run["state"]["alignment_model_calls"], 1)
        self.assertEqual(
            list((self.store.root / "recommendations" / run["id"]).glob("alignment-*")),
            [],
        )

    def test_partial_output_retries_only_unresolved_and_low_confidence_is_ignored(self):
        run = self.create_run(
            [
                (1, "Art crime one", "Art crime", ["crime"]),
                (2, "Art crime two", "Art crime", ["crime"]),
                (3, "Art history", "Art history", ["art history"]),
            ]
        )
        keys = [item["canonical_id"] for item in run["state"]["shortlist"]]
        prompts = []

        def runner(prompt, *_args):
            prompts.append(prompt)
            request = json.loads(prompt.split("\n", 1)[1])
            pending = request["videos"]
            if len(prompts) == 1:
                response = {
                    "videos": [
                        {
                            "canonical_id": keys[0],
                            "matches": ["crime"],
                            "confidence": "high",
                            "rationale": "Clear content match.",
                        },
                        {
                            "canonical_id": keys[1],
                            "matches": ["not an interest"],
                            "confidence": "high",
                            "rationale": "Invalid interest must be retried.",
                        },
                        {
                            "canonical_id": "youtube:9999999999A",
                            "matches": [],
                            "confidence": "high",
                            "rationale": "Unknown identifier is ignored.",
                        },
                    ]
                }
            else:
                self.assertEqual(
                    {video["canonical_id"] for video in pending}, set(keys[1:])
                )
                response = {
                    "videos": [
                        {
                            "canonical_id": keys[1],
                            "matches": ["crime"],
                            "confidence": "high",
                            "rationale": "Clear content match.",
                        },
                        {
                            "canonical_id": keys[2],
                            "matches": ["crime"],
                            "confidence": "low",
                            "rationale": "Uncertain match is not applied.",
                        },
                    ]
                }
            return response, None

        self.prepare(run, runner)
        alignment = run["state"]["interest_alignment"]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(alignment["attempts"][0]["status"], "partial")
        self.assertEqual(alignment["attempts"][1]["status"], "completed")
        self.assertEqual(alignment["records"][keys[0]]["matches"], ["crime"])
        self.assertEqual(alignment["records"][keys[1]]["matches"], ["crime"])
        self.assertEqual(alignment["records"][keys[2]]["matches"], [])
        self.assertEqual(alignment["records"][keys[2]]["confidence"], "low")
        self.assertEqual(run["state"]["alignment_warnings"][0]["canonical_id"], keys[2])
        low_confidence_item = aligned_items(run, run["state"]["shortlist"])[2]
        low_confidence_features = capture(
            low_confidence_item, run["state"]["evidence"][keys[2]]
        )
        self.assertEqual(low_confidence_features["domains"], ["art history"])
        self.assertEqual(
            interest_prior(low_confidence_features, run["settings"]["profile"]), 0.35
        )

        exhausted = self.create_run(
            [(4, "Art crime unresolved", "Art crime", ["crime"])]
        )
        retry_calls = []

        def invalid_runner(prompt, *_args):
            retry_calls.append(prompt)
            request = json.loads(prompt.split("\n", 1)[1])
            key = request["videos"][0]["canonical_id"]
            return {
                "videos": [
                    {
                        "canonical_id": key,
                        "matches": ["not an interest"],
                        "confidence": "high",
                        "rationale": "Invalid interest remains unresolved.",
                    }
                ]
            }, None

        self.prepare(exhausted, invalid_runner)
        attempts = exhausted["state"]["interest_alignment"]["attempts"]
        self.assertEqual(len(retry_calls), 4)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(attempts[-1]["number"], 4)
        self.assertNotIn(
            "youtube:0000000004A",
            exhausted["state"]["interest_alignment"]["records"],
        )

    def test_cache_reuses_exact_fingerprint_and_invalidates_all_input_changes(self):
        changes = (
            (
                "mapping_policy",
                lambda run: run["settings"].update(interest_alignment_version=1),
            ),
            (
                "interests",
                lambda run: run["settings"]["profile"].update(
                    interests=["crime", "art"]
                ),
            ),
            ("host", lambda run: run["settings"].update(worker_host="claude")),
            (
                "model",
                lambda run: run["settings"].update(assessment_model="another-model"),
            ),
            (
                "evidence",
                lambda run: run["state"]["evidence"]["youtube:0000000001A"].update(
                    evidence_run="new-evidence"
                ),
            ),
        )
        for label, mutate in changes:
            with self.subTest(input=label):
                run = self.create_run([(1, "Art crime", "Art crime", ["crime"])])
                calls = []

                def runner(prompt, *_args):
                    calls.append(prompt)
                    return self.response_for(prompt), None

                self.prepare(run, runner)
                self.prepare(run, runner)
                self.assertEqual(len(calls), 1)
                mutate(run)
                self.prepare(run, runner)
                self.assertEqual(len(calls), 2)
                if label == "mapping_policy":
                    key = run["state"]["shortlist"][0]["canonical_id"]
                    self.assertEqual(
                        fingerprint(
                            run["state"]["shortlist"][0],
                            run["state"]["evidence"][key],
                            run["settings"],
                        ),
                        "98cf449453a9775f7fa4017983f0b382053b4e7d51feac306642832028f7a608",
                    )
                    self.assertNotIn("A confirmed offense is not required.", calls[-1])
                    self.prepare(run, runner)
                    self.assertEqual(len(calls), 2)
                    run["settings"]["interest_alignment_version"] = 999
                    with self.assertRaisesRegex(
                        ValueError, "unsupported interest-mapping policy"
                    ):
                        self.prepare(run, runner)
                    self.assertEqual(len(calls), 2)

    def test_manual_publication_prepares_alignment_and_resumes_alignment_phase(self):
        from broader_vids.preferences import set_profile
        from broader_vids.recommendations.service import (
            create_recommendation,
            publish_recommendation,
        )

        set_profile(self.store, ["crime", "mathematics"])
        for phase, budget in (("assess", 52), ("alignment", 52), ("alignment", 0)):
            with self.subTest(phase=phase, budget=budget):
                rid = create_recommendation(
                    self.store,
                    "youtube",
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                    max_assessments=budget,
                )
                run = self.store.get(rid)
                self.assertEqual(
                    run["settings"]["interest_alignment_version"],
                    ALIGNMENT_VERSION,
                )
                item = candidate(1)
                item.update(title="Art crime", engagement_score={"score": 50})
                key = item["canonical_id"]
                observation = {
                    "canonical_id": key,
                    "quality": 75,
                    "duration_seconds": 900,
                    "evidence_run": "fixture-evidence",
                    "summary": "An art crime investigation.",
                    "topics": {"specific": ["Art theft"], "broad": ["Art crime"]},
                }
                run["state"].update(
                    phase=phase,
                    shortlist=[item],
                    evidence={key: observation},
                    assessment_index=1,
                )
                self.store.save(run)
                calls = []

                def runner(prompt, *_args):
                    calls.append(prompt)
                    return self.response_for(prompt), {
                        "input_tokens": 120,
                        "output_tokens": 20,
                        "cached_input_tokens": 0,
                    }

                with patch(
                    "broader_vids.recommendations.service.cached_quality",
                    return_value=observation,
                ):
                    published = publish_recommendation(
                        self.store, rid, allow_incomplete=True, alignment_runner=runner
                    )
                selected = published["state"]["selected"][0]
                self.assertEqual(len(calls), int(budget > 0))
                self.assertTrue(selected["content_interest_only"])
                if budget:
                    self.assertEqual(
                        selected["interest_alignment"]["matches"], ["crime"]
                    )
                    saved = json.loads(
                        self.store.db.execute(
                            "SELECT payload FROM video_state WHERE canonical_id=?",
                            (key,),
                        ).fetchone()[0]
                    )
                    self.assertEqual(saved["interest_alignment"]["matches"], ["crime"])
                else:
                    self.assertNotIn("interest_alignment", selected)
                    self.assertTrue(published["state"]["alignment_warnings"])

    def test_expired_deadline_does_not_call_runner_or_leave_temp_files(self):
        run = self.create_run([(1, "Art crime", "Art crime", ["crime"])])
        calls = []

        def runner(*_args):
            calls.append(True)
            raise AssertionError("Runner must not be called after deadline")

        with self.assertRaises(BudgetExhausted):
            self.prepare(run, runner, remaining=lambda: 0)
        self.assertEqual(calls, [])
        self.assertEqual(run["state"]["alignment_model_calls"], 0)
        self.assertEqual(run["state"]["interest_alignment"]["attempts"], [])
        recommendation_dir = self.store.root / "recommendations" / run["id"]
        self.assertEqual(list(recommendation_dir.glob("alignment-*")), [])


if __name__ == "__main__":
    unittest.main()
