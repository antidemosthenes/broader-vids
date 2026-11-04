from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from broader_vids.cli import main


class CliTests(unittest.TestCase):
    def test_core_workflow_from_a_fresh_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for command, expected in [
                (["profile", "set", "--interest", "math"], "interests"),
                (["profile", "show"], "interests"),
                (["cleanup"], "dry_run"),
                (["cleanup", "--apply"], "files"),
            ]:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--data-dir", directory, *command]), 0)
                self.assertIn(expected, json.loads(output.getvalue()))

    def test_fresh_profile_can_start_recommendations_without_pilot_or_calibration(self):
        from unittest.mock import patch
        from contextlib import closing
        from broader_vids.storage import Store
        from broader_vids.preferences import set_profile

        for platform in ("youtube", "both"):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
            ):
                with closing(Store(directory)) as store:
                    set_profile(store, ["mathematics"])
                target = (
                    "broader_vids.cli.app.execute_recommendation"
                    if platform == "youtube"
                    else "broader_vids.recommendations.batch.execute_batch"
                )
                output = io.StringIO()
                from broader_vids.onboarding import configure, complete

                with patch(
                    "broader_vids.preflight.external_checks",
                    return_value={
                        "checks": {"tools": {"status": "available"}},
                        "warnings": {},
                    },
                ):
                    with closing(Store(directory)) as store:
                        configure(store, platform)
                        self.assertEqual(
                            complete(store, model="fixture-model", effort="low")[
                                "status"
                            ],
                            "ready",
                        )
                with (
                    patch(
                        "broader_vids.preflight.external_checks",
                        return_value={
                            "checks": {"tools": {"status": "available"}},
                            "warnings": {},
                        },
                    ),
                    patch(target) as execute,
                    redirect_stdout(output),
                    redirect_stderr(io.StringIO()),
                ):
                    result = main(
                        [
                            "--data-dir",
                            directory,
                            "recommend",
                            platform,
                            "--model",
                            "fixture-model",
                            "--effort",
                            "low",
                            "--max-assessments",
                            "0",
                        ]
                    )
                self.assertEqual(result, 2)
                self.assertEqual(execute.call_count, 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], "pending")
                with closing(Store(directory)) as store:
                    self.assertEqual(
                        store.db.execute(
                            "SELECT COUNT(*) FROM calibration_items"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertFalse(list(store.iter_runs(kind="discovery")))

    def test_bundled_preset_preview_and_apply_preserve_history_and_current_format(self):
        from contextlib import closing
        from broader_vids.storage import Store
        from broader_vids.preferences import get_profile, set_profile, user_action
        from broader_vids.learning.presets import starter_preferences
        from broader_vids.learning.transfer import export_preferences

        preset = starter_preferences()
        with tempfile.TemporaryDirectory() as directory:
            with closing(Store(directory)) as store:
                set_profile(store, ["ecology"])
                user_action(store, "youtube:0000000001A", "watched")
                before = store.db.total_changes
                from broader_vids.learning.transfer import preview_import

                preview_import(store, preset)
                self.assertEqual(store.db.total_changes, before)
            for arguments in (
                ["profile", "preset", "starter"],
                ["profile", "preset", "starter", "--apply"],
            ):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--data-dir", directory, *arguments]), 0)
                result = json.loads(output.getvalue())
                self.assertTrue(result.get("compatible") or result.get("imported"))
            with closing(Store(directory)) as store:
                restored = export_preferences(store)
                self.assertEqual(restored["parameters"], preset["parameters"])
                self.assertEqual(
                    get_profile(store)["interests"], preset["preferences"]["interests"]
                )
                self.assertEqual(restored["watched_video_ids"], ["youtube:0000000001A"])
                self.assertEqual(
                    len(list((Path(directory) / "backups").glob("*.json"))), 1
                )

    def test_fresh_profile_uses_untrained_priors_without_the_preset(self):
        from contextlib import closing
        from broader_vids.storage import Store
        from broader_vids.preferences import set_profile
        from broader_vids.learning.transfer import export_preferences

        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["ecology"])
            bundle = export_preferences(store)
            self.assertTrue(
                all(
                    model["observations"] == 0
                    for model in bundle["parameters"]["models"].values()
                )
            )
            self.assertEqual(
                bundle["parameters"]["structured"]["quality"]["observations"], 0
            )
            self.assertTrue(
                all(
                    not groups
                    for groups in bundle["parameters"]["structured"]["groups"].values()
                )
            )
            self.assertEqual(bundle["watched_video_ids"], [])
