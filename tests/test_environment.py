from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.cli import main
from broader_vids.evidence.media import EvidencePending


class EnvironmentCheckTests(unittest.TestCase):
    def check(self, version, ffmpeg_error=None, missing_tool=None):
        def executable_version(executable, argument):
            if argument == "--version":
                return version
            if ffmpeg_error:
                raise ffmpeg_error
            return "ffmpeg version fixture"

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "broader_vids.cli.environment.shutil.which",
                side_effect=lambda name: None
                if name in {"ffprobe", missing_tool}
                else f"/tools/{name}",
            ),
            patch(
                "broader_vids.cli.environment.importlib.util.find_spec",
                side_effect=lambda name: object()
                if name in {"yt_dlp", "PIL"}
                else None,
            ),
            patch(
                "broader_vids.cli.environment.ffmpeg_executable",
                return_value="/bundled/ffmpeg",
            ),
            patch(
                "broader_vids.cli.environment.executable_version",
                side_effect=executable_version,
            ),
            patch("broader_vids.cli.environment.browser_channel", return_value=None),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(["--data-dir", directory, "doctor"])
            return status, json.loads(output.getvalue())

    def test_optional_and_conditional_omissions_do_not_fail_required_local_check(self):
        status, report = self.check("v22.1.0")
        self.assertEqual(status, 0)
        self.assertTrue(report["local_requirements_met"])
        self.assertEqual(report["missing_required"], [])
        self.assertEqual(report["required"]["ffmpeg"]["status"], "available")
        self.assertEqual(report["optional"]["ffprobe"]["status"], "missing")
        self.assertEqual(report["optional"]["faster_whisper"]["status"], "missing")
        self.assertEqual(report["conditional"]["playwright"]["status"], "missing")
        self.assertEqual(report["conditional"]["browser"]["status"], "not_checked")
        self.assertEqual(
            report["manual_checks"]["chatgpt_sign_in"]["status"], "not_checked"
        )
        self.assertNotIn("tools", report)

    def test_old_node_and_unusable_ffmpeg_fail_with_actionable_required_results(self):
        status, report = self.check("v18.20.0", OSError("Fixture executable failure"))
        self.assertEqual(status, 2)
        self.assertFalse(report["local_requirements_met"])
        self.assertEqual(report["missing_required"], ["node", "ffmpeg"])
        self.assertEqual(report["required"]["node"]["status"], "unsupported")
        self.assertEqual(report["required"]["ffmpeg"]["status"], "unusable")
        self.assertIn("setup --evidence", report["required"]["ffmpeg"]["remedy"])

    def test_missing_required_tool_or_bundled_executable_is_not_reported_ready(self):
        status, report = self.check("v22.1.0", missing_tool="codex")
        self.assertEqual(status, 2)
        self.assertIn("codex", report["missing_required"])
        with (
            patch("broader_vids.cli.environment.shutil.which", return_value="/tool"),
            patch(
                "broader_vids.cli.environment.importlib.util.find_spec",
                return_value=object(),
            ),
            patch(
                "broader_vids.cli.environment.executable_version",
                return_value="v22.1.0",
            ),
            patch(
                "broader_vids.cli.environment.ffmpeg_executable",
                side_effect=EvidencePending("No executable"),
            ),
            patch(
                "broader_vids.cli.environment.browser_channel", return_value="msedge"
            ),
        ):
            from broader_vids.cli.environment import environment_report

            report = environment_report("unused-profile")
        self.assertIn("ffmpeg", report["missing_required"])
        self.assertEqual(report["required"]["ffmpeg"]["status"], "missing")
