from contextlib import closing
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.learning.depth import integrate, fit_depth
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
from broader_vids.creators import set_exclusion, snapshot as creator_snapshot
from broader_vids.blacklist import sync_blacklist
from test_creator_exclusions import Browser
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
        fitted = fit_depth(
            [
                {
                    "dimension": "depth",
                    "split": "train",
                    "target": "topology",
                    "level": level,
                    "value": response,
                }
                for level, response in expanded
            ]
        )["topology"]
        self.assertEqual(fitted["counts"], [[9, 0, 0], [0, 0, 0], [0, 4, 0]])
        self.assertAlmostEqual(fitted["mean"], a["mean"], places=10)
        self.assertAlmostEqual(fitted["sd"], a["sd"], places=10)
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
                with self.assertRaisesRegex(ValueError, "changed during import"):
                    import_preferences(store, bundle)
            self.assertNotIn("baseline", source_data(store))
            self.assertEqual(
                details(store)[example(1)["canonical_id"]]["topic_interest"]["value"],
                "low",
            )
            self.assertEqual(
                list((store.root / "backups").glob("preferences-*.json")), []
            )

            def change_manual_rule(data):
                with closing(Store(directory)) as other:
                    set_exclusion(
                        other, "bilibili", "123", True, "Concurrent manual rule"
                    )
                return train(data)

            with patch(
                "broader_vids.learning.service.train", side_effect=change_manual_rule
            ):
                with self.assertRaisesRegex(ValueError, "changed during import"):
                    import_preferences(store, bundle)
            self.assertEqual(
                creator_snapshot(store, "bilibili")["excluded_ids"], ["123"]
            )
            self.assertNotIn("baseline", source_data(store))
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
                retained = {
                    k: v
                    for k, v in preserved(target).items()
                    if k not in {"creator_exclusions", "creator_policy"}
                }
                self.assertEqual(
                    retained,
                    {
                        k: v
                        for k, v in local_before.items()
                        if k not in {"creator_exclusions", "creator_policy"}
                    },
                )
                self.assertEqual(
                    creator_snapshot(target, "youtube")["excluded_ids"], []
                )
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

    def test_manual_creator_rules_replace_and_survive_blacklist_sync(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with closing(Store(a)) as source, closing(Store(b)) as target:
                set_profile(source, ["mathematics"])
                set_exclusion(
                    source, "youtube", "UC" + "a" * 22, True, "Source creator"
                )
                set_exclusion(source, "bilibili", "3", True, "Manual overlap")
                sync_blacklist(source, context=Browser({1: [3, 9]}, total=2))
                bundle = parse_bundle(json.dumps(export_preferences(source)))
                self.assertEqual(bundle["version"], 4)
                self.assertEqual(
                    bundle["creator_exclusions"]["bilibili"],
                    [{"creator_id": "3", "name": "Manual overlap"}],
                )
                self.assertNotIn("account_id", json.dumps(bundle))
                self.assertNotIn("bilibili_blacklist", json.dumps(bundle))
                set_profile(target, ["physics"])
                set_exclusion(target, "youtube", "UC" + "b" * 22, True, "Local creator")
                set_exclusion(target, "bilibili", "4", True, "Local Bilibili creator")
                sync_blacklist(target, context=Browser({1: [3, 5]}, total=2))
                preview = preview_import(target, bundle)
                self.assertEqual(preview["creator_exclusions"], 2)
                self.assertEqual(
                    preview["creator_exclusions_by_platform"],
                    {"youtube": 1, "bilibili": 1},
                )
                sync_blacklist(target, context=Browser({1: [3, 6]}, total=2))
                result = import_preferences(
                    target, bundle, expected_revision=preview["revision"]
                )
                self.assertEqual(result["creator_exclusions"], 2)
                self.assertEqual(result["creator_exclusions_mode"], "replace")
                self.assertEqual(
                    creator_snapshot(target, "youtube")["excluded_ids"],
                    ["UC" + "a" * 22],
                )
                self.assertEqual(
                    creator_snapshot(target, "bilibili")["excluded_ids"],
                    ["3", "6"],
                )
                backup = parse_bundle(
                    Path(result["backup_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    backup["creator_exclusions"]["bilibili"],
                    [{"creator_id": "4", "name": "Local Bilibili creator"}],
                )
                revision = creator_snapshot(target, "bilibili")["revision"]
                import_preferences(target, bundle)
                self.assertEqual(
                    creator_snapshot(target, "bilibili")["revision"], revision
                )
                sync_blacklist(target, context=Browser({1: [8]}, total=1))
                self.assertEqual(
                    creator_snapshot(target, "bilibili")["excluded_ids"],
                    ["3", "8"],
                )
                self.assertEqual(
                    export_preferences(target)["parameters"], bundle["parameters"]
                )
                for version in (1, 2, 3):
                    legacy = copy.deepcopy(bundle)
                    legacy["version"] = version
                    legacy.pop("creator_exclusions")
                    if version < 3:
                        legacy.pop("exploration_summary")
                    if version < 2:
                        legacy.pop("watched_video_ids")
                    legacy["checksum"] = digest(
                        {k: v for k, v in legacy.items() if k != "checksum"}
                    )
                    before = creator_snapshot(target, "bilibili")
                    with self.assertRaisesRegex(ValueError, "Only preference format 4"):
                        parse_bundle(json.dumps(legacy))
                    self.assertEqual(creator_snapshot(target, "bilibili"), before)
                empty = copy.deepcopy(bundle)
                empty["creator_exclusions"] = {"youtube": [], "bilibili": []}
                empty["checksum"] = digest(
                    {k: v for k, v in empty.items() if k != "checksum"}
                )
                import_preferences(target, parse_bundle(json.dumps(empty)))
                self.assertEqual(
                    creator_snapshot(target, "youtube")["excluded_ids"], []
                )
                self.assertEqual(
                    creator_snapshot(target, "bilibili")["excluded_ids"], ["8"]
                )
                import_preferences(target, backup)
                self.assertEqual(
                    creator_snapshot(target, "youtube")["excluded_ids"],
                    ["UC" + "b" * 22],
                )
                self.assertEqual(
                    creator_snapshot(target, "bilibili")["excluded_ids"], ["4", "8"]
                )

    def test_watched_ids_restore_screening_exclusions_without_restoring_history(self):
        from broader_vids.preferences import video_states, pending_videos
        from broader_vids.recommendations.screening import rejection_reason

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with closing(Store(a)) as source, closing(Store(b)) as target:
                seed(source)
                key = example(1)["canonical_id"]
                bili = "bilibili:BV1Z6H56oEcQ:p2"
                user_action(source, key, "watched")
                user_action(source, bili, "watched")
                bundle = parse_bundle(json.dumps(export_preferences(source)))
                self.assertEqual(bundle["version"], 4)
                self.assertIn("exploration_summary", bundle)
                self.assertEqual(set(bundle["watched_video_ids"]), {key, bili})
                self.assertNotIn(example(2)["canonical_id"], json.dumps(bundle))
                self.assertNotIn("https:", json.dumps(bundle))
                seed(target)
                local_key = example(4)["canonical_id"]
                user_action(target, local_key, "watched")
                user_action(target, key, "dismiss")
                before = preserved(target)
                preview = preview_import(target, bundle)
                self.assertEqual(preview["watched_videos"], 2)
                self.assertEqual(
                    preview["watched_by_platform"], {"youtube": 1, "bilibili": 1}
                )
                result = import_preferences(
                    target, bundle, expected_revision=preview["revision"]
                )
                self.assertEqual(result["watched_videos"], 2)
                states = video_states(target)
                self.assertTrue(states[key]["watched"])
                self.assertTrue(states[key]["dismissed"])
                previous_payload = json.loads(
                    next(row[2] for row in before["video_state"] if row[0] == key)
                )
                self.assertEqual(states[key]["payload"], previous_payload)
                self.assertTrue(states[local_key]["watched"])
                self.assertEqual(
                    (states[bili]["watched"], states[bili]["recommended"]), (1, 0)
                )
                self.assertFalse(
                    any(
                        item["canonical_id"] == bili
                        for item in pending_videos(target, "bilibili")
                    )
                )
                self.assertEqual(
                    rejection_reason({"canonical_id": bili}, states),
                    "watched_or_dismissed",
                )
                after = preserved(target)
                for table in before:
                    if table != "video_state":
                        self.assertEqual(before[table], after[table], table)
                restored = export_preferences(target)
                self.assertEqual(
                    set(restored["watched_video_ids"]), {key, bili, local_key}
                )
                self.assertEqual(bundle["parameters"], restored["parameters"])
                backup = parse_bundle(
                    Path(result["backup_path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(backup["watched_video_ids"], [local_key])
                user_action(target, bili, "unwatched")
                self.assertNotIn(bili, export_preferences(target)["watched_video_ids"])
                import_preferences(target, bundle)
                self.assertTrue(video_states(target)[bili]["watched"])

    def test_obsolete_preferences_are_rejected_and_import_failure_rolls_back(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            key = example(1)["canonical_id"]
            user_action(store, key, "watched")
            bundle = export_preferences(store)
            legacy = copy.deepcopy(bundle)
            legacy["version"] = 1
            legacy.pop("watched_video_ids")
            legacy.pop("exploration_summary")
            legacy.pop("creator_exclusions")
            legacy["checksum"] = digest(
                {k: v for k, v in legacy.items() if k != "checksum"}
            )
            with self.assertRaisesRegex(ValueError, "Only preference format 4"):
                parse_bundle(json.dumps(legacy))
            self.assertEqual(export_preferences(store)["watched_video_ids"], [key])
            bundle["watched_video_ids"].append("bilibili:BV1Z6H56oEcQ:p2")
            bundle["creator_exclusions"]["bilibili"].append(
                {"creator_id": "123", "name": "Imported creator"}
            )
            bundle["checksum"] = digest(
                {k: v for k, v in bundle.items() if k != "checksum"}
            )
            set_exclusion(store, "bilibili", "456", True, "Keep this rule on rollback")
            before = preserved(store)
            baseline = source_data(store)["epoch"]["imported_baseline"]
            backups = sorted((store.root / "backups").glob("*.json"))
            with patch.object(
                store, "create", side_effect=RuntimeError("Fixture commit failure")
            ):
                with self.assertRaisesRegex(RuntimeError, "Fixture commit failure"):
                    import_preferences(store, bundle)
            self.assertEqual(preserved(store), before)
            self.assertEqual(source_data(store)["epoch"]["imported_baseline"], baseline)
            self.assertEqual(sorted((store.root / "backups").glob("*.json")), backups)

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
            revision = preview_import(store, bundle)["revision"]
            set_exclusion(store, "bilibili", "456", True, "New manual exclusion")
            with self.assertRaisesRegex(ValueError, "after the preview"):
                import_preferences(store, bundle, expected_revision=revision)
            before = preserved(store)
            with patch(
                "broader_vids.learning.service.train",
                side_effect=ValueError("fixture rollback"),
            ):
                with self.assertRaisesRegex(ValueError, "fixture rollback"):
                    import_preferences(store, bundle)
            self.assertEqual(preserved(store), before)
            self.assertNotIn("baseline", source_data(store))

    def test_exploration_summary_roundtrip_merges_bounded_usage_idempotently(self):
        from broader_vids.exploration.coverage import memory, record_search
        from broader_vids.exploration.domains import (
            node_id,
            nodes,
            put_node,
            seed_interests,
        )

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with closing(Store(a)) as source, closing(Store(b)) as target:
                seed(source)
                seed_interests(source)
                roots = nodes(source)
                parent = next(
                    node for node in roots.values() if node["label"] == "mathematics"
                )
                source_node = node_id("topology")
                put_node(
                    source,
                    {
                        **parent,
                        "id": source_node,
                        "label": "topology",
                        "parents": [parent["id"]],
                        "ancestors": [parent["id"]],
                        "depth": 2,
                        "origin": "subdivision",
                    },
                )
                record_search(source, "source", source_node)
                record_search(source, "source", source_node)
                source.db.commit()
                bundle = export_preferences(source)
                serialized = json.dumps(bundle)
                self.assertEqual(bundle["version"], 4)
                self.assertGreaterEqual(bundle["exploration_summary"]["sequence"], 1)
                for private in ("queries", "ancestors", "domain_observations"):
                    self.assertNotIn(private, serialized)
                self.assertNotIn("canonical_id", serialized)

                seed(target)
                seed_interests(target)
                local_node = next(iter(nodes(target)))
                record_search(target, "local", local_node)
                record_search(target, "local", local_node)
                record_search(target, "local", local_node)
                target.db.commit()
                preview = preview_import(target, bundle)
                self.assertEqual(
                    preview["exploration_nodes"],
                    len(bundle["exploration_summary"]["nodes"]),
                )
                import_preferences(
                    target, bundle, expected_revision=preview["revision"]
                )
                import_preferences(target, bundle)

                self.assertIn(source_node, nodes(target))
                self.assertIn(local_node, nodes(target))
                usage = memory(target)["usage"]
                self.assertEqual(usage[source_node]["searches"], 2)
                self.assertEqual(usage[local_node]["searches"], 3)

    def test_exploration_import_failure_rolls_back_profile_and_coverage(self):
        from broader_vids.exploration.coverage import (
            import_summary as apply_summary,
            memory,
            record_search,
        )
        from broader_vids.exploration.domains import nodes, seed_interests
        from broader_vids.preferences import video_states

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with closing(Store(a)) as source, closing(Store(b)) as target:
                seed(source)
                seed_interests(source)
                record_search(source, "source", next(iter(nodes(source))))
                watched_id = example(1)["canonical_id"]
                user_action(source, watched_id, "watched")
                source.db.commit()
                bundle = export_preferences(source)
                seed(target)
                seed_interests(target)
                before_profile = get_profile(target)
                before_nodes = nodes(target)
                before_memory = memory(target)
                before_videos = video_states(target)
                with patch(
                    "broader_vids.exploration.coverage.import_summary",
                    side_effect=RuntimeError("coverage import failed"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "coverage import failed"):
                        import_preferences(target, bundle)
                self.assertEqual(get_profile(target), before_profile)
                self.assertEqual(nodes(target), before_nodes)
                self.assertEqual(memory(target), before_memory)
                self.assertEqual(video_states(target), before_videos)
                self.assertEqual(
                    list((target.root / "backups").glob("preferences-*.json")), []
                )

                merged = {"value": False}

                def import_then_fail_deadline(store, summary):
                    apply_summary(store, summary)
                    merged["value"] = True

                from broader_vids.learning import transfer

                original_deadline = transfer._deadline

                def fail_after_merge(deadline):
                    if merged["value"]:
                        raise ValueError(
                            "Preference processing reached its time allowance."
                        )
                    original_deadline(deadline)

                with (
                    patch(
                        "broader_vids.exploration.coverage.import_summary",
                        side_effect=import_then_fail_deadline,
                    ),
                    patch(
                        "broader_vids.learning.transfer._deadline",
                        side_effect=fail_after_merge,
                    ),
                ):
                    with self.assertRaisesRegex(ValueError, "time allowance"):
                        import_preferences(target, bundle)
                self.assertEqual(get_profile(target), before_profile)
                self.assertEqual(nodes(target), before_nodes)
                self.assertEqual(memory(target), before_memory)
                self.assertEqual(video_states(target), before_videos)
                self.assertEqual(
                    list((target.root / "backups").glob("preferences-*.json")), []
                )

    def test_invalid_and_modified_files_are_rejected(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            original = export_preferences(store)
            for mutate in (
                lambda b: b.update(history=[]),
                lambda b: b.update(watched_video_ids="youtube:0000000001A"),
                lambda b: b.update(watched_video_ids=[example(1)["canonical_id"]] * 2),
                lambda b: b.update(
                    watched_video_ids=["https://www.youtube.com/watch?v=0000000001A"]
                ),
                lambda b: b.update(watched_video_ids=["bilibili:BV1Z6H56oEcQ:p01"]),
                lambda b: b.update(watched_video_ids=[None]),
                lambda b: b.update(watched_video_ids=["unknown:123"]),
                lambda b: b.update(watched_video_ids=["youtube:0000000001A"] * 100001),
                lambda b: b.update(creator_exclusions=[]),
                lambda b: b["creator_exclusions"].update(
                    youtube=[{"creator_id": "123", "name": "Invalid channel"}]
                ),
                lambda b: b["creator_exclusions"].update(
                    bilibili=[{"creator_id": "003", "name": "Noncanonical account"}]
                ),
                lambda b: b["creator_exclusions"].update(
                    bilibili=[{"creator_id": "3", "name": "Duplicate"}] * 2
                ),
                lambda b: b["creator_exclusions"].update(
                    bilibili=[{"creator_id": "3", "name": "Too many"}] * 10001
                ),
                lambda b: b["creator_exclusions"].update(
                    bilibili=[{"creator_id": "3", "name": "x" * 301}]
                ),
                lambda b: b["creator_exclusions"].update(
                    bilibili=[
                        {
                            "creator_id": "3",
                            "name": "Untrusted account",
                            "account_id": "99",
                        }
                    ]
                ),
                lambda b: b.update(model_version=0),
                lambda b: b["exploration_summary"].update(usage={"invalid": {}}),
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
