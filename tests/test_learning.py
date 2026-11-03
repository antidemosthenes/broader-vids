from concurrent.futures import ThreadPoolExecutor
import json
import math
import random
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.learning.features import (
    capture,
    quality_features,
    quality_prior,
    logit,
)
from broader_vids.learning.inference import fit_logistic, fit_sparse, predict, sigmoid
from broader_vids.learning.service import (
    ensure_model,
    source_data,
    train,
    component_prediction,
    final_score,
)
from broader_vids.learning.calibration import (
    evaluate,
    finalize_ready_cohort,
    start_calibration,
)
from broader_vids.policy import Policy
from broader_vids.preferences import set_profile, user_action, video_states
from broader_vids.storage import Store
from broader_vids.http import BudgetExhausted
from broader_vids.evidence.media import EvidencePending
from test_recommendations import candidate


def example(index=1, creator=None):
    key = f"youtube:{index:010d}A"
    observation = {
        "quality": 75.0,
        "content_quality": 72.5,
        "duration_seconds": 1800,
        "criteria": {
            key: {"status": "assessed", "grade": 3, "confidence": "medium"}
            for key in ("substance", "directness", "originality", "visuals")
        },
    }
    item = {
        "canonical_id": key,
        "platform": "youtube",
        "url": f"https://www.youtube.com/watch?v={index:010d}A",
        "title": f"Development video {index}",
        "domains": ["mathematics"],
        "creator_id": creator or f"creator-{index}",
        "quality_evidence": observation,
        "origins": [],
    }
    return item


class InferenceTests(unittest.TestCase):
    def test_deadline_context_stops_learning_and_is_released_after_failure(self):
        from broader_vids.learning.inference import work_scope

        def stop():
            raise BudgetExhausted("Fixture deadline")

        with self.assertRaises(BudgetExhausted), work_scope(stop):
            fit_logistic([{"name": "bias", "mean": 0, "sd": 1}], [])
        self.assertEqual(
            fit_logistic([{"name": "bias", "mean": 0, "sd": 1}], [])["observations"], 0
        )

    def test_laplace_matches_numerical_integration_for_a_small_posterior(self):
        specs = [{"name": "bias", "mean": 0.0, "sd": 1.3}]
        rows = [{"x": {"bias": 1}, "y": int(i < 28), "offset": 0.2} for i in range(40)]
        model = fit_logistic(specs, rows)
        grid = [-8 + i * 0.002 for i in range(8001)]
        weights = [
            math.exp(
                -0.5 * (v / 1.3) ** 2
                + 28 * (v + 0.2)
                - 40 * math.log1p(math.exp(v + 0.2))
            )
            for v in grid
        ]
        total = sum(weights)
        mean = sum(v * w for v, w in zip(grid, weights)) / total
        variance = sum((v - mean) ** 2 * w for v, w in zip(grid, weights)) / total
        probability = sum(sigmoid(v + 0.2) * w for v, w in zip(grid, weights)) / total
        self.assertLess(abs(model["mean"][0] - mean), 0.04)
        self.assertLess(abs(model["covariance"][0][0] / variance - 1), 0.08)
        self.assertLess(
            abs(predict(model, {"bias": 1}, 0.2)["probability"] - probability), 0.01
        )

    def test_parameters_recover_truth_and_variance_contracts_with_more_coverage(self):
        truth = [-0.4, 1.2, -0.7]
        specs = [{"name": key, "mean": 2.0, "sd": 0.4} for key in ("bias", "a", "b")]
        errors, variances = [], []
        for n in (5, 50, 1000):
            rows = []
            for a in (-1, 1):
                for b in (-1, 1):
                    yes = round(n * sigmoid(truth[0] + truth[1] * a + truth[2] * b))
                    rows.extend(
                        {"x": {"bias": 1, "a": a, "b": b}, "y": int(i < yes)}
                        for i in range(n)
                    )
            model = fit_logistic(specs, rows)
            errors.append(
                math.sqrt(sum((v - t) ** 2 for v, t in zip(model["mean"], truth)))
            )
            variances.append(sum(model["covariance"][i][i] for i in range(3)))
            self.assertTrue(model["identifiable_on_observed_design"])
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])
        self.assertLess(errors[-1], 0.09)
        self.assertLess(variances[-1], variances[0] / 20)

    def test_repeated_uninformative_context_does_not_identify_unseen_preferences(self):
        model = fit_logistic(
            [
                {"name": "seen", "mean": 0, "sd": 1},
                {"name": "unseen", "mean": 0, "sd": 1},
            ],
            [{"x": {"seen": 1}, "y": i % 2} for i in range(1000)],
        )
        self.assertEqual(model["information_rank"], 1)
        self.assertFalse(model["identifiable_on_observed_design"])
        self.assertEqual(model["covariance"][1][1], 1)

    def test_a_wrong_context_dependent_prior_can_be_cancelled_as_data_grows(self):
        specs = [
            {"name": name, "mean": 0, "sd": 0.5}
            for name in ("bias", "interest", "prior_logit")
        ]
        rows = []
        for interest in (-1, 1):
            yes = round(1000 * sigmoid(-0.4 + interest))
            for prior in (-2, -0.3, 1.5):
                rows.extend(
                    {
                        "x": {"bias": 1, "interest": interest, "prior_logit": prior},
                        "offset": prior,
                        "y": int(i < yes),
                    }
                    for i in range(1000)
                )
        model = fit_logistic(specs, rows)
        self.assertLess(abs(model["mean"][2] + 1), 0.02)
        for interest in (-1, 1):
            estimates = [
                predict(
                    model,
                    {"bias": 1, "interest": interest, "prior_logit": prior},
                    prior,
                )["probability"]
                for prior in (-2, -0.3, 1.5)
            ]
            self.assertLess(max(estimates) - min(estimates), 0.01)

    def test_sparse_and_dense_solutions_and_uncertainty_agree(self):
        rng = random.Random(12)
        specs = [{"name": f"x{i}", "mean": 0, "sd": 0.7} for i in range(12)]
        rows = [
            {
                "x": {f"x{i}": rng.choice((-1, 0, 1)) for i in range(12)},
                "y": rng.randrange(2),
            }
            for _ in range(100)
        ]
        dense, sparse = fit_logistic(specs, rows), fit_sparse(specs, rows)
        for a, b in zip(dense["mean"], sparse["mean"]):
            self.assertAlmostEqual(a, b, places=5)
        for key in ("probability", "logit_variance"):
            self.assertAlmostEqual(
                predict(dense, rows[0]["x"])[key],
                predict(sparse, rows[0]["x"])[key],
                places=6,
            )

    def test_many_features_are_retained_without_a_fixed_dictionary_cutoff(self):
        specs = [{"name": f"x{i}", "mean": 0, "sd": 0.5} for i in range(401)]
        rows = [{"x": {"x0": 1, f"x{i}": 1}, "y": i % 2} for i in range(1, 401)]
        model = fit_logistic(specs, rows)
        self.assertEqual(model["parameter_count"], 401)
        self.assertEqual(model["observations"], 400)
        self.assertIsNone(model["covariance"])
        self.assertTrue(
            math.isfinite(predict(model, {"x0": 1, "x400": 1})["probability"])
        )

    def test_persistent_artificial_evidence_noise_does_not_bias_the_point_limit(self):
        features = capture(example())
        x = quality_features(features)
        specs = [{"name": key, "mean": 0, "sd": 0.5} for key in x]
        model = fit_logistic(
            specs,
            [
                {"x": x, "offset": logit(quality_prior(features)), "y": int(i < 3200)}
                for i in range(4000)
            ],
        )
        result = component_prediction(
            features, {"interests": ["mathematics"]}, model, "quality"
        )
        self.assertLess(abs(result["probability"] - 0.8), 0.003)
        self.assertLess(
            result["evidence_sensitivity_interval90"][0], result["interval90"][0]
        )

    def test_overall_feedback_can_overcome_a_wrong_prior_without_a_blend_floor(self):
        features = capture(example())
        data = {
            "profile": {"revision": 1, "interests": ["mathematics"]},
            "ratings": [
                {
                    "dimension": "overall",
                    "split": "train",
                    "features": features,
                    "vote": "down",
                    "action_id": i,
                }
                for i in range(1500)
            ],
        }
        snapshot = {"id": "fixture", **train(data)}
        score, _, details = final_score(
            features,
            snapshot,
            {
                "quality": 75,
                "interest": 75,
                "engagement": 50,
                "recency": 50,
                "diversity": 100,
            },
            Policy().weights,
        )
        self.assertLess(score, 5)
        self.assertLess(details["overall_adjustment"], -50)
        self.assertEqual(snapshot["models"]["quality"]["observations"], 0)
        self.assertEqual(snapshot["models"]["interest"]["observations"], 0)

    def test_learned_approval_cannot_override_quality_or_duration_exclusions(self):
        from broader_vids.recommendations.ranking import rank_slate

        items = [example(i) for i in range(1, 4)]
        observations = {
            item["canonical_id"]: dict(item["quality_evidence"]) for item in items
        }
        observations[items[1]["canonical_id"]]["quality"] = 40
        observations[items[2]["canonical_id"]]["duration_seconds"] = 7200
        for item in items:
            item["engagement_score"] = {"score": 50}
        snapshot = {
            "id": "fixture",
            **train(
                {
                    "profile": {"revision": 1, "interests": ["mathematics"]},
                    "ratings": [],
                }
            ),
        }
        result = rank_slate(
            items,
            observations,
            snapshot["profile"],
            Policy().weights,
            learning=snapshot,
        )
        self.assertEqual(
            [item["canonical_id"] for item in result], [items[0]["canonical_id"]]
        )

    def test_slate_reuses_predictions_when_only_other_creators_are_selected(self):
        from broader_vids.recommendations.ranking import rank_slate
        from broader_vids.learning.service import score_components

        items = [example(i) for i in range(1, 31)]
        observations = {
            item["canonical_id"]: item["quality_evidence"] for item in items
        }
        for item in items:
            item["engagement_score"] = {"score": 50}
        snapshot = {
            "id": "fixture",
            **train(
                {
                    "profile": {"revision": 1, "interests": ["mathematics"]},
                    "ratings": [],
                }
            ),
        }
        with patch(
            "broader_vids.learning.service.score_components", wraps=score_components
        ) as computed:
            result = rank_slate(
                items,
                observations,
                snapshot["profile"],
                Policy().weights,
                learning=snapshot,
            )
        self.assertEqual(computed.call_count, 30)
        self.assertEqual(
            [item["canonical_id"] for item in result],
            sorted(observations, reverse=True)[:10],
        )


class LearningStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(self.store, ["math"])
        for i in range(1, 32):
            item = example(i)
            self.store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,0,?)",
                (item["canonical_id"], "youtube", json.dumps(item), "fixture"),
            )
        self.store.db.commit()

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def cohort(self):
        for i in range(1, 31):
            item = example(i)
            payload = {
                "item": {
                    k: item[k] for k in ("canonical_id", "platform", "url", "title")
                },
                "features": capture(item),
                "after_action_id": 0,
                "created_at": "2026-10-26T00:00:00+00:00",
            }
            self.store.db.execute(
                "INSERT INTO calibration_items(canonical_id,cohort,split,payload) VALUES(?,?,?,?)",
                (
                    item["canonical_id"],
                    "fixture-cohort",
                    "holdout" if i > 20 else "train",
                    json.dumps(payload),
                ),
            )
        self.store.db.commit()

    def test_reason_channels_revise_independently_and_watch_is_not_a_label(self):
        key = example()["canonical_id"]
        user_action(self.store, key, "up", kind="quality")
        user_action(self.store, key, "down", kind="interest")
        user_action(self.store, key, "up", kind="overall")
        model = ensure_model(self.store)
        self.assertEqual(
            {k: v["observations"] for k, v in model["models"].items()},
            {"quality": 1, "interest": 1, "overall": 1},
        )
        user_action(self.store, key, "clear", kind="quality")
        user_action(self.store, key, "watched")
        state = video_states(self.store, identifiers=[key])[key]
        self.assertEqual(set(state["ratings"]), {"interest", "overall"})
        self.assertTrue(state["watched"])
        self.assertEqual(
            ensure_model(self.store)["models"]["quality"]["observations"], 0
        )

    def test_model_epoch_survives_json_caching(self):
        from broader_vids.learning.service import change_epoch

        self.cohort()
        ensure_model(self.store)
        self.assertEqual(ensure_model(self.store)["epoch"], change_epoch(self.store))

    def publication_fixture(self):
        from broader_vids.recommendations.service import create_recommendation

        key = create_recommendation(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(key)
        items = [example(i) for i in range(1, 21)]
        for item in items:
            item["engagement_score"] = {"score": 50}
        observation = {**example()["quality_evidence"], "evidence_run": "a" * 32}
        run["state"].update(
            phase="assess",
            shortlist=items,
            evidence={item["canonical_id"]: observation for item in items},
        )
        self.store.save(run)
        return run, observation

    def test_posterior_scoring_runs_without_the_publication_write_lock(self):
        from broader_vids.recommendations.service import _publish
        from broader_vids.recommendations.ranking import rank_slate

        run, observation = self.publication_fixture()

        def outside(*args, **kwargs):
            self.assertFalse(self.store.db.in_transaction)
            other = Store(self.directory.name)
            try:
                other.db.execute("BEGIN IMMEDIATE")
                other.db.rollback()
            finally:
                other.close()
            return rank_slate(*args, **kwargs)

        with (
            patch(
                "broader_vids.recommendations.service.cached_quality",
                return_value=observation,
            ),
            patch(
                "broader_vids.recommendations.service.rank_slate", side_effect=outside
            ),
        ):
            _publish(self.store, run)
        self.assertEqual(len(run["state"]["selected"]), 10)

    def test_actions_during_scoring_abort_publication_without_losing_the_action(self):
        from broader_vids.recommendations.service import _publish
        from broader_vids.recommendations.ranking import rank_slate

        run, observation = self.publication_fixture()

        def changed(*args, **kwargs):
            other = Store(self.directory.name)
            try:
                user_action(other, example()["canonical_id"], "watched")
            finally:
                other.close()
            return rank_slate(*args, **kwargs)

        with (
            patch(
                "broader_vids.recommendations.service.cached_quality",
                return_value=observation,
            ),
            patch(
                "broader_vids.recommendations.service.rank_slate", side_effect=changed
            ),
            self.assertRaises(EvidencePending),
        ):
            _publish(self.store, run)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM recommendation_exposures"
            ).fetchone()[0],
            0,
        )
        self.assertTrue(
            video_states(self.store, identifiers=[example()["canonical_id"]])[
                example()["canonical_id"]
            ]["watched"]
        )

    def test_publication_checks_the_deadline_before_fitting(self):
        from broader_vids.recommendations.service import _publish

        run, _ = self.publication_fixture()
        with self.assertRaises(BudgetExhausted):
            _publish(self.store, run, lambda: 0)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM learning_models").fetchone()[0],
            0,
        )

    def test_revisions_do_not_manufacture_infinite_independent_observations(self):
        key = example()["canonical_id"]
        for i in range(12):
            user_action(self.store, key, "up" if i % 2 else "down", kind="interest")
        self.assertEqual(
            ensure_model(self.store)["models"]["interest"]["observations"], 1
        )
        self.assertEqual(len(source_data(self.store)["ratings"]), 1)

    def test_unfinished_holdout_reservations_expire_without_discarding_feedback(self):
        self.cohort()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i in range(32, 92):
                user_action(
                    self.store, example(i)["canonical_id"], "up", kind="interest"
                )
        ensure_model(self.store)
        self.assertTrue(finalize_ready_cohort(self.store))
        report = evaluate(self.store)
        self.assertEqual(report["release_reason"], "finite_holdout_reservation_expired")
        self.assertFalse(report["phase_four_complete"])
        user_action(self.store, example(25)["canonical_id"], "down", kind="interest")
        self.assertEqual(
            ensure_model(self.store)["models"]["interest"]["observations"], 61
        )

    def test_feature_capture_does_not_hold_the_feedback_write_transaction(self):
        from broader_vids.learning.service import capture_rating

        def outside(local, key):
            self.assertFalse(local.db.in_transaction)
            return capture_rating(local, key)

        with patch("broader_vids.learning.service.capture_rating", side_effect=outside):
            user_action(self.store, example()["canonical_id"], "up", kind="quality")

    def test_invalid_evidence_does_not_discard_the_users_explicit_vote(self):
        item = example()
        item["quality_evidence"]["evidence_run"] = "a" * 32
        self.store.db.execute(
            "UPDATE video_state SET payload=? WHERE canonical_id=?",
            (json.dumps(item), item["canonical_id"]),
        )
        self.store.db.commit()
        user_action(self.store, item["canonical_id"], "down", kind="quality")
        self.assertEqual(
            video_states(self.store, identifiers=[item["canonical_id"]])[
                item["canonical_id"]
            ]["ratings"]["quality"]["vote"],
            "down",
        )
        model = ensure_model(self.store)
        self.assertEqual(model["models"]["quality"]["observations"], 0)
        self.assertEqual(model["excluded"]["missing_quality_evidence"], 1)

    def test_feedback_features_are_frozen_and_fitting_is_idempotent(self):
        key = example()["canonical_id"]
        user_action(self.store, key, "down", kind="quality")
        before = ensure_model(self.store)
        item = example()
        item["quality_evidence"]["content_quality"] = 10
        self.store.db.execute(
            "UPDATE video_state SET payload=? WHERE canonical_id=?",
            (json.dumps(item), key),
        )
        self.store.db.commit()
        self.assertEqual(ensure_model(self.store), before)

    def test_held_out_labels_and_their_creator_peers_never_train_before_release(self):
        self.cohort()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i in range(1, 31):
                user_action(
                    self.store,
                    example(i)["canonical_id"],
                    "up" if i % 2 else "down",
                    kind="interest",
                )
        initial = ensure_model(self.store)
        self.assertEqual(initial["models"]["interest"]["observations"], 20)
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(
                self.store, example(25)["canonical_id"], "down", kind="interest"
            )
        updated = ensure_model(self.store)
        self.assertEqual(
            initial["models"]["interest"]["mean"], updated["models"]["interest"]["mean"]
        )
        peer = example(31, creator="creator-25")
        self.store.db.execute(
            "UPDATE video_state SET payload=? WHERE canonical_id=?",
            (json.dumps(peer), peer["canonical_id"]),
        )
        self.store.db.commit()
        user_action(self.store, peer["canonical_id"], "up", kind="interest")
        self.assertEqual(
            ensure_model(self.store)["models"]["interest"]["observations"], 20
        )

    def test_evaluation_is_frozen_before_release_and_revisions_invalidate_it(self):
        self.cohort()
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            for i in range(1, 31):
                for channel in ("interest", "quality"):
                    user_action(
                        self.store,
                        example(i)["canonical_id"],
                        "up" if i % 2 else "down",
                        kind=channel,
                    )
        initial = ensure_model(self.store)
        self.assertEqual(initial["models"]["quality"]["observations"], 20)
        self.assertTrue(finalize_ready_cohort(self.store))
        current = ensure_model(self.store)
        self.assertEqual(current["models"]["quality"]["observations"], 30)
        frozen = evaluate(self.store)
        self.assertEqual(frozen["snapshot_id"], initial["id"])
        self.assertEqual(frozen["held_out"]["quality"]["n"], 10)
        self.assertEqual(frozen["validation_status"], "frozen_before_holdout_release")
        user_action(self.store, example(25)["canonical_id"], "clear", kind="quality")
        self.assertFalse(evaluate(self.store)["phase_four_complete"])

    def test_concurrent_feedback_and_a_rebuild_preserve_every_latest_label(self):
        barrier = threading.Barrier(4)

        def vote(index):
            local = Store(self.directory.name)
            try:
                barrier.wait(3)
                user_action(
                    local, example(index)["canonical_id"], "up", kind="interest"
                )
            finally:
                local.close()

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(vote, i) for i in range(1, 5)]
            for future in futures:
                future.result()
        self.assertEqual(
            ensure_model(self.store)["models"]["interest"]["observations"], 4
        )

    def test_a_feedback_change_during_fitting_retries_without_publishing_stale_parameters(
        self,
    ):
        with patch("broader_vids.learning.service.refresh_after_feedback"):
            user_action(self.store, example(1)["canonical_id"], "up", kind="interest")
        original = train
        calls = []

        def changed(data):
            if not calls:
                calls.append(True)
                other = Store(self.directory.name)
                try:
                    with patch("broader_vids.learning.service.refresh_after_feedback"):
                        user_action(
                            other, example(2)["canonical_id"], "down", kind="interest"
                        )
                finally:
                    other.close()
            return original(data)

        with patch("broader_vids.learning.service.train", side_effect=changed):
            model = ensure_model(self.store)
        self.assertEqual(model["models"]["interest"]["observations"], 2)


class CalibrationSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(self.store, ["math"])
        self.observations = {}
        for platform in ("youtube", "bilibili"):
            items = [candidate(i, platform) for i in range(1, 31)]
            self.store.create(
                platform,
                {"kind": "recommendation"},
                {"shortlist": items, "buffer": items},
            )
            for item in items:
                key = self.store.create(
                    platform,
                    {
                        "kind": "evidence",
                        "identity": {"canonical_id": item["canonical_id"]},
                    },
                    {"assessments": {"augmented": {}}},
                )
                self.observations[key] = {
                    **example()["quality_evidence"],
                    "evidence_run": key,
                }

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_cohort_is_balanced_persistent_and_has_no_creator_leakage(self):
        with patch(
            "broader_vids.learning.calibration.cached_quality",
            side_effect=lambda store, key: self.observations[key],
        ):
            rows = start_calibration(self.store)
        self.assertEqual(len(rows), 30)
        self.assertEqual(sum(r["item"]["platform"] == "bilibili" for r in rows), 15)
        groups = {
            split: {
                r["features"]["platform"] + ":" + r["features"]["creator"]
                for r in rows
                if r["split"] == split
            }
            for split in ("train", "holdout")
        }
        self.assertFalse(groups["train"] & groups["holdout"])
        with patch(
            "broader_vids.learning.calibration.cached_quality",
            side_effect=AssertionError("Saved cohort was regenerated"),
        ):
            self.assertEqual(start_calibration(self.store, seed=999), rows)

    def test_insufficient_evidence_does_not_create_a_partial_cohort(self):
        with (
            patch(
                "broader_vids.learning.calibration.cached_quality", return_value=None
            ),
            self.assertRaises(ValueError),
        ):
            start_calibration(self.store)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM calibration_items").fetchone()[
                0
            ],
            0,
        )

    def test_latest_translation_tags_override_historical_candidate_metadata(self):
        run = next(self.store.iter_runs(kind="recommendation", platform="bilibili"))
        run["state"]["buffer"] = run["state"]["shortlist"][:15]
        run["state"]["shortlist"] = run["state"]["shortlist"][:15]
        self.store.save(run)
        key = run["state"]["shortlist"][0]["canonical_id"]
        self.store.db.execute(
            "INSERT INTO engagement_snapshots VALUES(?,?)",
            (key, json.dumps({"tags": ["视频翻译"], "tags_status": "observed"})),
        )
        self.store.db.commit()
        with (
            patch(
                "broader_vids.learning.calibration.cached_quality",
                side_effect=lambda store, key: self.observations[key],
            ),
            self.assertRaisesRegex(ValueError, "found 14"),
        ):
            start_calibration(self.store)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM calibration_items").fetchone()[
                0
            ],
            0,
        )
