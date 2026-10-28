from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.cli.arguments import parser
from broader_vids.cli import main
from broader_vids.evidence.assessment import run_codex
from broader_vids.evidence.service import create_evidence
from broader_vids.preferences import set_profile
from broader_vids.recommendations.batch import create_batch
from broader_vids.storage import Store
from contextlib import redirect_stdout
import io
from broader_vids.worker_context import resolve_worker_settings


class WorkerDefaultsTests(unittest.TestCase):
    def test_standalone_commands_accept_and_save_explicit_host_settings(self):
        for command, target in (
            (["discover", "youtube"], "broader_vids.cli.app.execute_discovery"),
            (
                ["evidence", "prepare", "https://www.youtube.com/watch?v=imYQJOgUx7Y"],
                "broader_vids.cli.app.prepare_evidence",
            ),
        ):
            with (
                self.subTest(command=command),
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {}, clear=True),
                patch(target),
                redirect_stdout(io.StringIO()),
            ):
                with closing(Store(directory)) as store:
                    set_profile(store, ["mathematics"])
                self.assertEqual(
                    main(
                        [
                            "--data-dir",
                            directory,
                            *command,
                            "--model",
                            "host-model",
                            "--effort",
                            "medium",
                        ]
                    ),
                    2,
                )
                with closing(Store(directory)) as store:
                    runs = store.list_runs()
                    self.assertEqual(len(runs), 1)
                    settings = store.get(runs[0]["id"])["settings"]
                    self.assertEqual(settings["assessment_model"], "host-model")
                    self.assertEqual(settings["reasoning_effort"], "medium")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        thread = "00000000-0000-0000-0000-000000000001"
        self.session = self.home / "sessions" / ("rollout-" + thread + ".jsonl")
        self.session.parent.mkdir()
        self.select("parent-model", "high")
        env = patch.dict(
            os.environ, {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": thread}
        )
        env.start()
        self.addCleanup(env.stop)

    def select(self, model, effort):
        with self.session.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {"model": model, "effort": effort},
                    }
                )
                + "\n"
            )

    def test_new_runs_inherit_parent_and_resumes_keep_saved_settings(self):
        with closing(Store(self.home / "profile")) as store:
            set_profile(store, ["mathematics"])
            args = parser().parse_args(["recommend", "both"])
            self.assertIsNone(args.effort)
            self.assertIsNone(args.model)
            batch = store.get(create_batch(store))
            for child in batch["state"]["children"].values():
                settings = store.get(child)["settings"]
                self.assertEqual(
                    (settings["assessment_model"], settings["reasoning_effort"]),
                    ("parent-model", "high"),
                )
            url = "https://www.youtube.com/watch?v=imYQJOgUx7Y"
            first = create_evidence(store, url)
            self.select("changed-parent", "medium")
            second = create_evidence(store, url)
            self.assertNotEqual(first, second)
            self.assertEqual(
                store.get(first)["settings"]["assessment_model"], "parent-model"
            )
            self.assertEqual(
                store.get(second)["settings"]["reasoning_effort"], "medium"
            )

    def test_missing_context_does_not_silently_use_global_defaults(self):
        with self.assertRaisesRegex(ValueError, "invoking chat"):
            resolve_worker_settings(environ={})
        self.assertEqual(
            resolve_worker_settings("explicit-model", "xhigh", environ={}),
            ("explicit-model", "xhigh"),
        )

    def test_dispatch_inherits_without_live_model_calls(self):
        output = self.home / "response.json"
        commands = []

        def execute(command, **kwargs):
            commands.append(command)
            if command[1:3] == ["login", "status"]:
                return subprocess.CompletedProcess(
                    command, 0, "Logged in using ChatGPT", ""
                )
            output.write_text(json.dumps({"fixture": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch(
                "broader_vids.evidence.assessment.shutil.which", return_value="codex"
            ),
            patch("broader_vids.evidence.assessment.run_bounded", side_effect=execute),
        ):
            result, _ = run_codex("prompt", self.home / "schema.json", output, [], 10)
        self.assertTrue(result["fixture"])
        command = commands[-1]
        self.assertEqual(command[command.index("--model") + 1], "parent-model")
        self.assertIn('model_reasoning_effort="high"', command)
