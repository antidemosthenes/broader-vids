from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from deepvids.cli import main
from deepvids.cli.arguments import parser
from deepvids.discovery.queries import load_bank


class CliTests(unittest.TestCase):
    def test_core_workflow_from_a_fresh_state_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for command, expected in [
                (["profile", "set", "--interest", "math"], "interests"),
                (["profile", "show"], "interests"),
                (["gate"], "current_phase"),
                (["cleanup"], "dry_run"),
                (["cleanup", "--apply"], "files"),
            ]:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(["--data-dir", directory, *command]), 0)
                self.assertIn(expected, json.loads(output.getvalue()))

    def test_retired_commands_are_absent_and_malformed_banks_fail_cleanly(self):
        for command in ("catalog", "experiment", "simulate", "probe", "search-control"):
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
