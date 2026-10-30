from contextlib import closing
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.depth import integrate
from broader_vids.learning.feedback import save_detail, details
from broader_vids.learning.inference import fit_logistic, fit_sparse, predict
from broader_vids.learning.posterior import portable_model, restored_model
from broader_vids.learning.service import (
    ensure_model,
    source_data,
    train,
    digest,
    score_components,
)
from broader_vids.learning.transfer import (
    export_preferences,
    import_preferences,
    parse_bundle,
    preview_import,
    write_export,
)
from broader_vids.preferences import (
    set_profile,
    get_profile,
    update_tag_rule,
    user_action,
)
from broader_vids.storage import Store
from broader_vids.creators import set_exclusion
from test_learning import example
from broader_vids.learning.features import capture


def seed(store):
    set_profile(store, ["mathematics", "physics"])
    for i in range(1, 5):
        item = example(i)
        item["quality_evidence"].update(
            topics={"specific": ["topology"], "broad": ["mathematics"]},
            audience_level="intermediate",
        )
        store.db.execute(
            "INSERT INTO video_state VALUES(?,?,?,0,0,1,'fixture')",
            (item["canonical_id"], "youtube", json.dumps(item)),
        )
    store.db.commit()
    save_detail(
        store, example(1)["canonical_id"], "topic_interest", "high", target="topology"
    )
    save_detail(store, example(2)["canonical_id"], "quality_level", "high")
    save_detail(
        store,
        example(3)["canonical_id"],
        "depth",
        "deeper",
        target="topology",
        level="intermediate",
    )
    save_detail(
        store,
        example(4)["canonical_id"],
        "domain_interest",
        "high",
        target="mathematics",
    )


def preserved(store):
    tables = (
        "feedback_details",
        "feedback_ratings",
        "calibration_items",
        "video_state",
        "user_actions",
        "creator_exclusions",
        "creator_sync",
        "creator_policy",
        "recommendation_exposures",
    )
    names = {
        r[0]
        for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        name: [
            tuple(row)
            for row in store.db.execute(f"SELECT * FROM {name} ORDER BY rowid")
        ]
        for name in tables
        if name in names
    }


class PosteriorTransferTests(unittest.TestCase):
    def test_full_prior_correlations_affect_map_and_predictive_uncertainty(self):
        prior = {
            "names": ["a", "b"],
            "mean": [0.2, -0.3],
            "observations": 10,
            "covariance": [[1.0, 0.6], [0.6, 2.0]],
        }
        specs = [{"name": name, "mean": 0, "sd": 0.35} for name in prior["names"]]
        restored = fit_logistic(specs, [], posterior=prior)
        self.assertEqual(portable_model(restored), prior)
        rows = [{"x": {"a": 1}, "y": 1} for _ in range(12)]
        model = fit_logistic(specs, rows, posterior=prior)
        a, b = model["mean"]
        self.assertAlmostEqual(
            b - prior["mean"][1], 0.6 * (a - prior["mean"][0]), places=7
        )
        self.assertAlmostEqual((a - 0.2), 12 * (1 - 1 / (1 + math.exp(-a))), places=6)
        self.assertGreater(
            predict(model, {"a": 1})["probability"],
            predict(restored, {"a": 1})["probability"],
        )
        self.assertLess(model["covariance"][1][1], 2)
        self.assertEqual(model["observations"], 22)

    def test_sparse_export_removes_observation_rows_and_preserves_prediction(self):
        specs = [{"name": f"x{i}", "mean": 0, "sd": 0.7} for i in range(100)]
        rows = [{"x": {"x0": 1, "x4": -0.3}, "y": i % 3 == 0} for i in range(30)]
        model = fit_sparse(specs, rows)
        portable = portable_model(model)
        self.assertEqual(set(portable), {"names", "mean", "observations", "precision"})
        restored = restored_model(portable)
        features = {"x0": 1, "x4": 0.4, "x9": 0.8}
        for key in ("probability", "logit_mean", "logit_variance"):
            self.assertAlmostEqual(
                predict(model, features)[key],
                predict(restored, features)[key],
                places=8,
            )

    def test_weighted_depth_counts_equal_individual_likelihoods(self):
        expanded = [("introductory", "deeper")] * 9 + [("advanced", "suitable")] * 4
        counted = [("introductory", "deeper", 9), ("advanced", "suitable", 4)]
        a, b = integrate(expanded), integrate(counted)
        self.assertAlmostEqual(a["mean"], b["mean"], places=10)
        self.assertAlmostEqual(a["sd"], b["sd"], places=10)
        for level in a["predictions"]:
            for x, y in zip(a["predictions"][level], b["predictions"][level]):
                self.assertAlmostEqual(x, y, places=10)


class PreferenceTransferTests(unittest.TestCase):
    def test_repeated_import_prunes_only_unreferenced_model_caches(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)
            first = import_preferences(store, bundle)["snapshot_id"]
            with store.db:
                store.db.execute(
                    "INSERT INTO learning_evaluations VALUES(?,?,?)",
                    ("pinned", "fixture", json.dumps({"snapshot_id": first})),
                )
                store.db.execute(
                    "INSERT INTO learning_evaluations VALUES(?,?,?)",
                    ("without_snapshot", "fixture", "{}"),
                )
            second = import_preferences(store, bundle)["snapshot_id"]
            third = import_preferences(store, bundle)["snapshot_id"]
            cached = {
                row[0] for row in store.db.execute("SELECT id FROM learning_models")
            }
            self.assertIn(first, cached)
            self.assertIn(third, cached)
            self.assertNotIn(second, cached)

    def test_cached_export_does_not_refit_and_matches_parameters(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            original = export_preferences(store)
            with patch(
                "broader_vids.learning.service.train",
                side_effect=AssertionError("Unnecessary refit"),
            ):
                cached = export_preferences(store)
            self.assertEqual(cached["parameters"], original["parameters"])

    def test_validation_deadline_leaves_existing_parameters_and_backups_intact(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)
            before = preserved(store)
            with patch(
                "broader_vids.learning.transfer.time.monotonic", side_effect=[0, 21]
            ):
                with self.assertRaisesRegex(ValueError, "time allowance"):
                    import_preferences(store, bundle)
            self.assertEqual(preserved(store), before)
            self.assertEqual(
                list((store.root / "backups").glob("preferences-*.json")), []
            )

    def test_restore_is_prepared_outside_write_transaction_and_has_exact_cache_id(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)

            def prepare(data):
                self.assertFalse(store.db.in_transaction)
                return train(data)

            with patch("broader_vids.learning.service.train", side_effect=prepare):
                result = import_preferences(store, bundle)
            with patch(
                "broader_vids.learning.service.train",
                side_effect=AssertionError("The restored cache was not reused"),
            ):
                restored = ensure_model(store)
            self.assertEqual(restored["id"], result["snapshot_id"])

    def test_feedback_can_save_during_restore_and_prevents_stale_commit(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)

            def prepare(data):
                with closing(Store(directory)) as other:
                    save_detail(
                        other,
                        example(1)["canonical_id"],
                        "topic_interest",
                        "low",
                        target="topology",
                    )
                return train(data)

            with patch("broader_vids.learning.service.train", side_effect=prepare):
                with self.assertRaisesRegex(ValueError, "Feedback changed"):
                    import_preferences(store, bundle)
            self.assertNotIn("baseline", source_data(store))
            self.assertEqual(
                details(store)[example(1)["canonical_id"]]["topic_interest"]["value"],
                "low",
            )
            self.assertEqual(
                list((store.root / "backups").glob("preferences-*.json")), []
            )

    def test_failed_commit_removes_unused_backup_and_rolls_back_profile(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)
            before = preserved(store)
            profile = get_profile(store)
            original = store.create

            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                raise ValueError("Interrupted commit")

            with patch.object(store, "create", side_effect=interrupted):
                with self.assertRaisesRegex(ValueError, "Interrupted commit"):
                    import_preferences(store, bundle)
            self.assertEqual(preserved(store), before)
            self.assertEqual(get_profile(store), profile)
            self.assertNotIn("baseline", source_data(store))
            self.assertEqual(
                list((store.root / "backups").glob("preferences-*.json")), []
            )

    def test_export_publishes_only_complete_data_and_cleans_failed_temporary_files(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            bundle = export_preferences(store)
            destination = Path(directory) / "preferences.json"

            def fail_sync(descriptor):
                self.assertFalse(destination.exists())
                raise OSError("Failed to flush")

            with patch(
                "broader_vids.learning.transfer.os.fsync", side_effect=fail_sync
            ):
                with self.assertRaises(OSError):
                    write_export(destination, bundle)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            destination.write_text("old export", encoding="utf-8")
            with patch(
                "broader_vids.learning.transfer.os.fsync",
                side_effect=OSError("Failed to flush"),
            ):
                with self.assertRaises(OSError):
                    write_export(destination, bundle, overwrite=True)
            self.assertEqual(destination.read_text(), "old export")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_roundtrip_contains_parameters_only_and_retains_local_history(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with closing(Store(a)) as source, closing(Store(b)) as target:
                seed(source)
                before = preserved(source)
                bundle = export_preferences(source)
                self.assertEqual(preserved(source), before)
                raw = json.dumps(bundle)
                for marker in (
                    "canonical_id",
                    "training_actions",
                    "video_topics",
                    "video_domains",
                    "video_levels",
                    "features",
                    "Development video",
                    "http",
                    str(Path(a)),
                ):
                    self.assertNotIn(marker, raw)
                for i in range(1, 5):
                    self.assertNotIn(example(i)["canonical_id"], raw)
                seed(target)
                update_tag_rule(target, "bilibili", "易语言")
                set_exclusion(target, "youtube", "UC" + "a" * 22, True, "Local creator")
                user_action(target, example(1)["canonical_id"], "watched")
                local_before = preserved(target)
                old = train(source_data(source))
                preview = preview_import(target, bundle)
                result = import_preferences(
                    target, parse_bundle(raw), expected_revision=preview["revision"]
                )
                self.assertTrue(result["imported"])
                self.assertEqual(preserved(target), local_before)
                self.assertEqual(
                    get_profile(target)["excluded_tags"]["bilibili"], ["易语言"]
                )
                self.assertTrue(details(target))
                self.assertEqual(source_data(target)["details"], [])
                backup = parse_bundle(
                    Path(result["backup_path"]).read_text(encoding="utf-8")
                )
                self.assertNotIn("canonical_id", json.dumps(backup))
                new = ensure_model(target)
                self.assertEqual(
                    new["structured"]["video_levels"][example(3)["canonical_id"]],
                    "intermediate",
                )
                f = capture(example(999))
                f.update(
                    topics=["topology"],
                    content_topics=["topology"],
                    audience_level="intermediate",
                )
                components = {"interest": 50, "quality": 75}
                x, _ = score_components(
                    f,
                    old["profile"],
                    old["models"],
                    components,
                    structured=old["structured"],
                )
                y, _ = score_components(
                    f,
                    new["profile"],
                    new["models"],
                    components,
                    structured=new["structured"],
                )
                self.assertAlmostEqual(x["interest"], y["interest"], places=10)
                self.assertAlmostEqual(x["quality"], y["quality"], places=10)
                exported_again = export_preferences(target)
                self.assertEqual(bundle["parameters"], exported_again["parameters"])
                self.assertEqual(bundle["preferences"], exported_again["preferences"])

    def test_new_and_edited_feedback_learn_without_recounting_existing_rows(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)
            import_preferences(store, bundle)
            alpha = bundle["parameters"]["structured"]["groups"]["topic_interest"][
                "topology"
            ]["alpha"]
            self.assertEqual(
                ensure_model(store)["structured"]["groups"]["topic_interest"][
                    "topology"
                ]["alpha"],
                alpha,
            )
            save_detail(
                store,
                example(1)["canonical_id"],
                "topic_interest",
                "low",
                target="topology",
            )
            learned = ensure_model(store)["structured"]["groups"]["topic_interest"][
                "topology"
            ]
            self.assertEqual(learned["alpha"], [alpha[0] + 1, *alpha[1:]])
            self.assertEqual(len(source_data(store)["details"]), 1)
            save_detail(
                store,
                example(1)["canonical_id"],
                "topic_interest",
                "medium",
                target="topology",
            )
            self.assertEqual(
                ensure_model(store)["structured"]["groups"]["topic_interest"][
                    "topology"
                ]["alpha"],
                [alpha[0], alpha[1] + 1, alpha[2]],
            )
            save_detail(store, example(1)["canonical_id"], "topic_interest", "clear")
            self.assertEqual(
                ensure_model(store)["structured"]["groups"]["topic_interest"][
                    "topology"
                ]["alpha"],
                alpha,
            )
            save_detail(
                store,
                example(3)["canonical_id"],
                "depth",
                "suitable",
                target="topology",
                level="advanced",
            )
            self.assertEqual(
                ensure_model(store)["structured"]["depth_models"]["topology"][
                    "observations"
                ],
                2,
            )
            save_detail(store, example(2)["canonical_id"], "quality_level", "low")
            self.assertEqual(
                ensure_model(store)["structured"]["quality"]["observations"], 2
            )
            import_preferences(store, bundle)
            self.assertEqual(
                export_preferences(store)["parameters"], bundle["parameters"]
            )

    def test_stale_preview_lock_and_transaction_failure_do_not_replace_parameters(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            bundle = export_preferences(store)
            revision = preview_import(store, bundle)["revision"]
            save_detail(
                store,
                example(1)["canonical_id"],
                "topic_interest",
                "low",
                target="topology",
            )
            with self.assertRaisesRegex(ValueError, "after the preview"):
                import_preferences(store, bundle, expected_revision=revision)
            self.assertEqual(
                list((store.root / "backups").glob("preferences-*.json")), []
            )
            before = preserved(store)
            with patch(
                "broader_vids.learning.service.train",
                side_effect=ValueError("fixture rollback"),
            ):
                with self.assertRaisesRegex(ValueError, "fixture rollback"):
                    import_preferences(store, bundle)
            self.assertEqual(preserved(store), before)
            self.assertNotIn("baseline", source_data(store))

    def test_invalid_and_modified_files_are_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            original = export_preferences(store)
            for mutate in (
                lambda b: b.update(history=[]),
                lambda b: b.update(model_version=0),
                lambda b: b.update(feature_version=True),
                lambda b: b["parameters"]["models"].update(interest=None),
                lambda b: b["parameters"]["models"]["interest"]["covariance"][
                    0
                ].__setitem__(0, -1),
                lambda b: b["parameters"]["models"]["interest"]["mean"].__setitem__(
                    0, float("nan")
                ),
                lambda b: b["parameters"]["models"]["interest"]["mean"].__setitem__(
                    0, 10**500
                ),
            ):
                value = copy.deepcopy(original)
                mutate(value)
                value["checksum"] = digest(
                    {k: v for k, v in value.items() if k != "checksum"}
                )
                with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                    parse_bundle(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "checksum"):
                changed = copy.deepcopy(original)
                changed["preferences"]["interests"] = ["music"]
                parse_bundle(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                parse_bundle('{"format":1,"format":2}')
            with self.assertRaisesRegex(ValueError, "8 MiB"):
                parse_bundle(" " * (8 * 1024 * 1024 + 1))

    def test_file_creation_does_not_overwrite_without_explicit_option(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            bundle = export_preferences(store)
            path = Path(directory) / "我的偏好.json"
            write_export(path, bundle)
            with self.assertRaises(FileExistsError):
                write_export(path, bundle)
            self.assertEqual(parse_bundle(path.read_text(encoding="utf-8")), bundle)
            write_export(path, bundle, overwrite=True)
            self.assertEqual(parse_bundle(path.read_text(encoding="utf-8")), bundle)
            with self.assertRaises(ValueError):
                write_export(store.root / "state.sqlite3", bundle, overwrite=True)
