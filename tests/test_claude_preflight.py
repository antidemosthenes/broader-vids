import builtins
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from broader_vids.cli.environment import environment_report
from broader_vids.onboarding import configure, complete, next_steps
from broader_vids.preferences import set_profile
from broader_vids.preflight import external_checks
from broader_vids.preflight_checks import inspect
from broader_vids.storage import Store


class ClaudeEnvironmentTests(unittest.TestCase):
    def test_claude_report_requires_claude_cli_without_running_codex(self):
        versions = []

        def which(name):
            return {
                "uv": "/tools/uv",
                "node": "/tools/node",
                "codex": "/tools/codex",
            }.get(name)

        def version(executable, argument):
            versions.append(executable)
            if executable == "/tools/uv":
                return "uv 0.9.0"
            if executable == "/tools/node":
                return "v22.0.0"
            return "ffmpeg version fixture"

        with (
            patch("broader_vids.cli.environment.shutil.which", side_effect=which),
            patch(
                "broader_vids.cli.environment.importlib.util.find_spec",
                side_effect=lambda name: object()
                if name in {"yt_dlp", "PIL", "opencc"}
                else None,
            ),
            patch(
                "broader_vids.cli.environment.ffmpeg_executable",
                return_value="/tools/ffmpeg",
            ),
            patch(
                "broader_vids.cli.environment.executable_version", side_effect=version
            ),
            patch("broader_vids.cli.environment.browser_channel", return_value=None),
        ):
            report = environment_report("unused", host="claude")

        self.assertIn("claude", report["required"])
        self.assertEqual(report["required"]["claude"]["status"], "missing")
        self.assertNotIn("codex", report["required"])
        self.assertNotIn("/tools/codex", versions)
        self.assertIn("claude_sign_in", report["manual_checks"])

    def test_unknown_host_fails_clearly(self):
        with self.assertRaisesRegex(ValueError, "codex.*claude"):
            environment_report("unused", host="automatic")


class ClaudePreflightTests(unittest.TestCase):
    def environment(self):
        return {
            "required": {"node": {"status": "available"}},
            "conditional": {},
            "optional": {"ffprobe": {"status": "missing"}},
        }

    def test_codex_check_does_not_import_or_run_claude_auth(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "broader_vids.claude_worker":
                raise AssertionError("Claude auth was imported for Codex")
            return original_import(name, *args, **kwargs)

        with (
            patch(
                "broader_vids.preflight_checks.environment_report",
                return_value=self.environment(),
            ),
            patch(
                "broader_vids.preflight_checks.chatgpt_check",
                return_value={"status": "available"},
            ) as chatgpt,
            patch("builtins.__import__", side_effect=guarded_import),
        ):
            report = inspect(Path("unused"), "youtube", "token", False)
        self.assertEqual(report["checks"]["chatgpt_sign_in"]["status"], "available")
        chatgpt.assert_called_once_with()

    def test_claude_check_uses_lazy_auth_check_and_host_environment(self):
        auth = Mock(return_value={"status": "available", "remedy": "Sign in"})
        module = type("ClaudeWorker", (), {"auth_check": staticmethod(auth)})
        imported = []
        original_import = builtins.__import__

        def tracking_import(name, *args, **kwargs):
            if name == "broader_vids.claude_worker":
                imported.append(name)
                return module
            return original_import(name, *args, **kwargs)

        with (
            patch(
                "broader_vids.preflight_checks.environment_report",
                return_value=self.environment(),
            ) as report_environment,
            patch("broader_vids.preflight_checks.chatgpt_check") as chatgpt,
            patch("builtins.__import__", side_effect=tracking_import),
        ):
            report = inspect(Path("unused"), "youtube", "token", False, host="claude")
        report_environment.assert_called_once_with(Path("unused"), host="claude")
        self.assertEqual(imported, ["broader_vids.claude_worker"])
        auth.assert_called_once_with()
        chatgpt.assert_not_called()
        self.assertEqual(report["checks"]["claude_sign_in"]["status"], "available")

    def test_external_checks_keep_codex_argv_and_append_claude_host(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps({"checks": {"probe": {"status": "available"}}, "warnings": {}}),
            "",
        )
        with patch("broader_vids.preflight.run_bounded", return_value=result) as run:
            external_checks(Path("unused"), "youtube", timeout=2)
        codex_args = run.call_args.args[0]
        self.assertEqual(
            codex_args[-4:], ["unused", "youtube", codex_args[-2], "native"]
        )
        self.assertEqual(len(codex_args), 7)

        with patch("broader_vids.preflight.run_bounded", return_value=result) as run:
            external_checks(Path("unused"), "youtube", timeout=2, host="claude")
        self.assertEqual(run.call_args.args[0][-1], "claude")

    def test_codex_next_steps_remain_exact_and_claude_uses_slash_skills(self):
        codex = next_steps()
        self.assertEqual(codex["recommendations"]["skill_invocation"], "$broader-vids")
        self.assertEqual(
            codex["dashboard"]["skill_invocation"], "$broader-vids-dashboard"
        )
        claude = next_steps(host="claude")
        self.assertEqual(
            claude["recommendations"]["skill_invocation"], "/broader-vids:broader-vids"
        )
        self.assertEqual(
            claude["dashboard"]["skill_invocation"],
            "/broader-vids:broader-vids-dashboard",
        )

    def test_claude_completion_passes_explicit_model_and_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                set_profile(store, ["mathematics"])
                configure(store, "youtube")
                settings = Mock(return_value=("claude-fixture", "high"))
                checks = {
                    "checks": {"tools": {"status": "available"}},
                    "warnings": {},
                }
                with (
                    patch("broader_vids.preflight.resolve_worker_settings", settings),
                    patch(
                        "broader_vids.preflight.external_checks", return_value=checks
                    ),
                ):
                    report = complete(
                        store,
                        host="claude",
                        model="claude-fixture",
                        effort="high",
                    )
                self.assertEqual(report["status"], "ready")
                settings.assert_called_once_with(
                    "claude-fixture", "high", host="claude"
                )
                self.assertEqual(
                    report["next_steps"]["recommendations"]["skill_invocation"],
                    "/broader-vids:broader-vids",
                )
                self.assertEqual(report["model"], "claude-fixture")
                self.assertEqual(report["effort"], "high")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
