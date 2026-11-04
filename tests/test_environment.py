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
            if executable == "/tools/uv":
                return "uv 0.9.0"
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
                if name in {"yt_dlp", "PIL", "opencc"}
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

    def test_installed_but_broken_uv_is_not_reported_as_ready(self):
        from broader_vids.cli.environment import environment_report

        def version(executable, argument):
            if executable == "uv":
                raise OSError("Broken executable")
            return "v22.0.0" if executable == "node" else "ffmpeg version fixture"

        with (
            patch(
                "broader_vids.cli.environment.shutil.which",
                side_effect=lambda name: name,
            ),
            patch(
                "broader_vids.cli.environment.executable_version", side_effect=version
            ),
            patch(
                "broader_vids.cli.environment.ffmpeg_executable", return_value="ffmpeg"
            ),
        ):
            report = environment_report("unused")
        self.assertEqual(report["required"]["uv"]["status"], "unusable")
        self.assertEqual(report["required"]["node"]["status"], "available")
        self.assertIn("uv", report["missing_required"])
        self.assertFalse(report["local_requirements_met"])
