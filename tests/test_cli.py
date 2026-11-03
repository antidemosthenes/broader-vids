from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from broader_vids.cli import main
from broader_vids.cli.arguments import parser
from broader_vids.discovery.queries import load_bank


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

    def test_retired_commands_are_absent_and_malformed_banks_fail_cleanly(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["learn", "review-comments"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(
                [
                    "rate",
                    "youtube:0000000001A",
                    "quality_level",
                    "high",
                    "--note",
                    "obsolete",
                ]
            )
        for command in (
            "catalog",
            "experiment",
            "simulate",
            "probe",
            "search-control",
            "gate",
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args([command])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for value in (
                [],
                None,
                {"version": 1, "families": [None]},
                {"version": 1, "families": None},
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_bank(path)
                error = io.StringIO()
                with redirect_stderr(error):
                    self.assertEqual(main(["queries", "--bank", str(path)]), 2)
                self.assertNotIn("Traceback", error.getvalue())

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
