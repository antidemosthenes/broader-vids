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
                expected_status, expected_exit = (
                    ("completed", 0) if platform == "youtube" else ("paused", 2)
                )

                def finish(store, run_id, **kwargs):
                    run = store.get(run_id)
                    if run["status"] == "completed":
                        return run
                    run["status"] = expected_status
                    if run["platform"] == "youtube":
                        run["state"]["phase"] = "published"
                    store.save(run)
                    return run

                server = {"status": "running", "url": "http://127.0.0.1:9087"}
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
                    patch(target, side_effect=finish) as execute,
                    patch(
                        "broader_vids.web.runtime.dashboard", return_value=server
                    ) as restart,
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
                    self.assertEqual(result, expected_exit)
                    execute.assert_called_once()
                    report = json.loads(output.getvalue())
                    self.assertEqual(report["status"], expected_status)
                    self.assertEqual(report["dashboard"], server)
                    restart.assert_called_once_with(
                        Path(directory).resolve(), "restart", open_browser=False
                    )

                    run_args = ["--data-dir", directory]
                    with redirect_stdout(io.StringIO()) as inspection:
                        self.assertEqual(
                            main([*run_args, "report", report["run_id"]]), 0
                        )
                    self.assertNotIn("dashboard", json.loads(inspection.getvalue()))
                    self.assertEqual(restart.call_count, 1)

                    if platform == "both":
                        restart.side_effect = PermissionError(
                            "Dashboard startup was denied."
                        )
                        with redirect_stdout(io.StringIO()) as resumed:
                            self.assertEqual(
                                main([*run_args, "continue", report["run_id"]]), 2
                            )
                        result = json.loads(resumed.getvalue())
                        self.assertEqual(result["status"], "paused")
                        self.assertEqual(
                            result["dashboard"],
                            {
                                "status": "unavailable",
                                "reason": "Dashboard startup was denied.",
                            },
                        )
                        self.assertTrue(Path(result["digest_path"]).is_file())
                        with closing(Store(directory)) as store:
                            run = store.get(report["run_id"])
                            run["status"] = "completed"
                            store.save(run)

                    if platform == "both":
                        with redirect_stderr(io.StringIO()) as error:
                            self.assertEqual(
                                main([*run_args, "publish", report["run_id"]]), 2
                            )
                        self.assertIn("Not a recommendation run.", error.getvalue())

                    restart.reset_mock()
                    commands = (
                        ("continue", "publish")
                        if platform == "youtube"
                        else ("continue",)
                    )
                    for command in commands:
                        with redirect_stdout(io.StringIO()) as repeated:
                            self.assertEqual(
                                main([*run_args, command, report["run_id"]]), 0
                            )
                        self.assertNotIn("dashboard", json.loads(repeated.getvalue()))
                    restart.assert_not_called()
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
