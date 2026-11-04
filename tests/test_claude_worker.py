import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.evidence.media import EvidencePending
from broader_vids.claude_worker import auth_check, resolve_settings, run_claude
from broader_vids.preflight_checks import inspect as inspect_preflight


class ClaudeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.schema = self.root / "schema.json"
        self.schema.write_text(
            json.dumps(
                {"type": "object", "properties": {"score": {"type": "integer"}}}
            ),
            encoding="utf-8",
        )
        self.output = self.root / "result.json"

    def test_resolve_settings_uses_only_explicit_claude_values(self):
        self.assertEqual(
            resolve_settings(
                environ={
                    "BROADER_VIDS_CLAUDE_MODEL": "claude-sonnet-4-6",
                    "BROADER_VIDS_CLAUDE_EFFORT": "xhigh",
                    "CODEX_THREAD_ID": "ignored",
                }
            ),
            ("claude-sonnet-4-6", "xhigh"),
        )
        with self.assertRaisesRegex(ValueError, "explicit supported Claude model"):
            resolve_settings(environ={"CODEX_THREAD_ID": "ignored"})
        with self.assertRaises(ValueError):
            resolve_settings("gpt-5", "high", environ={})
        with self.assertRaises(ValueError):
            resolve_settings("claude-opus-4-6", "ultra", environ={})

    def test_multimodal_payload_command_and_aggregate_usage(self):
        image = self.root / "frame.jpg"
        image.write_bytes(b"fixture-jpeg")
        scorecard = {"score": 4}
        result_event = {
            "type": "result",
            "subtype": "success",
            "structured_output": scorecard,
            "usage": {
                "input_tokens": 80,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 5,
                "output_tokens": 12,
            },
        }
        assistant_event = {
            "type": "assistant",
            "uuid": "event-1",
            "message": {"usage": {"input_tokens": 999, "output_tokens": 999}},
        }
        commands = []

        def execute(command, **kwargs):
            commands.append((command, kwargs))
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(assistant_event) + "\n" + json.dumps(result_event),
                "",
            )

        with (
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_API_KEY": "fixture-key",
                    "ANTHROPIC_BASE_URL": "https://fixture.invalid",
                    "CLAUDECODE": "nested-session",
                    "CLAUDE_CODE_EFFORT_LEVEL": "low",
                },
            ),
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch("broader_vids.claude_worker.run_bounded", side_effect=execute),
        ):
            value, usage = run_claude(
                "fixture prompt",
                self.schema,
                self.output,
                [image],
                30,
                model="claude-sonnet-4-6",
                effort="medium",
            )
        self.assertEqual(value, scorecard)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), scorecard)
        self.assertEqual(
            usage, {"input_tokens": 105, "output_tokens": 12, "cached_input_tokens": 20}
        )
        command, options = commands[0]
        for flag in (
            "--input-format",
            "--output-format",
            "--json-schema",
            "--model",
            "--effort",
            "--no-session-persistence",
            "--safe-mode",
            "--tools",
        ):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--model") + 1], "claude-sonnet-4-6")
        self.assertEqual(command[command.index("--effort") + 1], "medium")
        self.assertEqual(options["cwd"], self.root)
        self.assertEqual(options["env"]["ANTHROPIC_API_KEY"], "fixture-key")
        self.assertEqual(
            options["env"]["ANTHROPIC_BASE_URL"], "https://fixture.invalid"
        )
        self.assertNotIn("CLAUDECODE", options["env"])
        self.assertEqual(options["env"]["ANTHROPIC_MODEL"], "claude-sonnet-4-6")
        self.assertEqual(options["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        payload = json.loads(options["input"])
        self.assertEqual(
            payload["message"]["content"][0], {"type": "text", "text": "fixture prompt"}
        )
        attached = payload["message"]["content"][1]["source"]
        self.assertEqual(attached["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(attached["data"]), b"fixture-jpeg")

    def test_timeout_cancellation_and_provider_failures_are_run_errors(self):
        image = self.root / "frame.webp"
        image.write_bytes(b"fixture")
        for error, category in (
            (subprocess.TimeoutExpired(["claude"], 1), "timeout"),
            (InterruptedError("cancel"), "cancelled"),
        ):
            with (
                self.subTest(category=category),
                patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
                patch("broader_vids.claude_worker.run_bounded", side_effect=error),
            ):
                with self.assertRaises(EvidencePending) as caught:
                    run_claude(
                        "prompt",
                        self.schema,
                        self.output,
                        [image],
                        1,
                        model="claude-opus-4-6",
                        effort="high",
                    )
                self.assertEqual(caught.exception.category, category)
        stream = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "authentication failed",
                },
            }
        )
        with (
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch(
                "broader_vids.claude_worker.run_bounded",
                return_value=subprocess.CompletedProcess(
                    ["claude"], 1, stream, "secret diagnostic"
                ),
            ),
        ):
            with self.assertRaises(EvidencePending) as caught:
                run_claude(
                    "prompt",
                    self.schema,
                    self.output,
                    [image],
                    1,
                    model="claude-opus-4-6",
                    effort="high",
                )
        self.assertEqual(caught.exception.scope, "run")
        self.assertNotIn("secret diagnostic", str(caught.exception))

    def test_interrupted_stream_reports_deduplicated_usage(self):
        image = self.root / "frame.jpg"
        image.write_bytes(b"fixture")
        assistant = {
            "type": "assistant",
            "uuid": "event-1",
            "message": {
                "id": "message-1",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                    "output_tokens": 3,
                },
            },
        }
        duplicate = {
            **assistant,
            "uuid": "event-2",
            "message": {
                **assistant["message"],
                "usage": {**assistant["message"]["usage"], "output_tokens": 5},
            },
        }
        stream = json.dumps(assistant) + "\n" + json.dumps(duplicate)
        error = subprocess.TimeoutExpired(["claude"], 1, output=stream)
        with (
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch("broader_vids.claude_worker.run_bounded", side_effect=error),
            patch("broader_vids.token_usage.capture") as capture,
        ):
            with self.assertRaises(EvidencePending):
                run_claude(
                    "prompt",
                    self.schema,
                    self.output,
                    [image],
                    1,
                    model="claude-opus-4-6",
                    effort="high",
                )
        capture.assert_called_once_with(
            {"input_tokens": 13, "output_tokens": 5, "cached_input_tokens": 2}
        )

    def test_auth_check_uses_cli_status_without_returning_native_output(self):
        with (
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch(
                "broader_vids.claude_worker.run_bounded",
                return_value=subprocess.CompletedProcess(
                    ["claude"], 0, '{"loggedIn":true,"authMethod":"claude.ai"}', ""
                ),
            ) as execute,
        ):
            self.assertEqual(auth_check(), {"status": "available", "remedy": None})
        self.assertEqual(execute.call_args.args[0][1:], ["auth", "status", "--json"])
        with patch("broader_vids.claude_worker.shutil.which", return_value=None):
            self.assertEqual(auth_check()["status"], "missing")

    def test_auth_readiness_statuses_match_preflight_contract(self):
        environment = {"required": {}, "conditional": {}, "optional": {}}
        with (
            patch(
                "broader_vids.cli.environment.environment_report",
                return_value=environment,
            ),
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch(
                "broader_vids.claude_worker.run_bounded",
                return_value=subprocess.CompletedProcess(
                    ["claude"], 0, '{"loggedIn":true}', ""
                ),
            ),
        ):
            report = inspect_preflight(
                Path(self.root), "youtube", "fixture", False, host="claude"
            )
        self.assertEqual(report["checks"]["claude_sign_in"]["status"], "available")

        for returncode, stdout, expected in (
            (1, '{"loggedIn":false}', "missing"),
            (0, "not-json", "unverified"),
        ):
            with (
                self.subTest(expected=expected),
                patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
                patch(
                    "broader_vids.claude_worker.run_bounded",
                    return_value=subprocess.CompletedProcess(
                        ["claude"], returncode, stdout, ""
                    ),
                ),
            ):
                self.assertEqual(auth_check()["status"], expected)

    def test_windows_npm_cli_uses_node_without_invoking_cmd_wrapper(self):
        from broader_vids.claude_worker import command

        npm = self.root / "npm"
        wrapper = npm / "claude.cmd"
        cli = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("fixture", encoding="utf-8")
        node = self.root / "node.exe"
        with (
            patch("broader_vids.claude_worker._WINDOWS", True),
            patch(
                "broader_vids.claude_worker.shutil.which",
                side_effect=lambda name: str(wrapper)
                if name == "claude"
                else str(node),
            ),
            patch(
                "broader_vids.claude_worker.run_bounded",
                return_value=subprocess.CompletedProcess(
                    ["node"], 0, '{"loggedIn":true}', ""
                ),
            ) as execute,
        ):
            self.assertEqual(command(), [str(node), str(cli)])
            self.assertEqual(auth_check()["status"], "available")
        self.assertEqual(
            execute.call_args.args[0], [str(node), str(cli), "auth", "status", "--json"]
        )

        with (
            patch("broader_vids.claude_worker._WINDOWS", True),
            patch(
                "broader_vids.claude_worker.shutil.which",
                side_effect=lambda name: str(wrapper) if name == "claude" else None,
            ),
        ):
            self.assertIsNone(command())
        with (
            patch("broader_vids.claude_worker._WINDOWS", True),
            patch(
                "broader_vids.claude_worker.shutil.which",
                return_value=str(self.root / "claude.exe"),
            ),
        ):
            self.assertEqual(command(), [str(self.root / "claude.exe")])

    def test_provider_result_error_is_classified_without_disclosing_text(self):
        from broader_vids.claude_worker import _stream_result

        event = {
            "type": "result",
            "subtype": "error_api_error",
            "errors": ["Rate limit exceeded: fixture-only detail"],
        }
        _, _, has_error, provider_error = _stream_result(json.dumps(event))
        self.assertTrue(has_error)
        self.assertTrue(provider_error)

    def test_stream_input_and_usage_fields_are_bounded_and_strict(self):
        from broader_vids.claude_worker import _message, _stream_result, _usage_from

        with self.assertRaisesRegex(EvidencePending, "10 MiB"):
            _message("x" * (10 * 1024 * 1024 + 1), [])
        self.assertIsNone(_usage_from({"input_tokens": 4}))
        self.assertIsNone(_usage_from({"input_tokens": "4", "output_tokens": 2}))
        self.assertIsNone(
            _usage_from(
                {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cache_read_input_tokens": "bad",
                }
            )
        )
        scorecard, _, has_error, _ = _stream_result(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "structured_output": {"score": 1},
                }
            )
        )
        self.assertEqual(scorecard, {"score": 1})
        self.assertTrue(has_error)

    def test_image_size_is_bounded_before_subprocess_launch(self):
        image = self.root / "frame.gif"
        image.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        with (
            patch("broader_vids.claude_worker.shutil.which", return_value="claude"),
            patch("broader_vids.claude_worker.run_bounded") as execute,
        ):
            with self.assertRaisesRegex(EvidencePending, "oversized frame"):
                run_claude(
                    "prompt",
                    self.schema,
                    self.output,
                    [image],
                    1,
                    model="claude-haiku-4-5",
                    effort="low",
                )
        execute.assert_not_called()

    def test_cancelled_or_expired_request_never_starts_a_process(self):
        cancelled = threading.Event()
        cancelled.set()
        for expired in (False, True):
            with (
                self.subTest(expired=expired),
                patch(
                    "broader_vids.claude_worker.command",
                    return_value=["claude-fixture"],
                ),
                patch("broader_vids.claude_worker.time.monotonic", side_effect=[0, 10]),
                patch("broader_vids.claude_worker.run_bounded") as spawn,
            ):
                with self.assertRaises(EvidencePending) as failure:
                    run_claude(
                        "fixture",
                        self.schema,
                        self.output,
                        [],
                        5,
                        model="claude-fixture",
                        effort="low",
                        cancelled=None if expired else cancelled,
                    )
                self.assertEqual(
                    failure.exception.category, "timeout" if expired else "cancelled"
                )
                self.assertTrue(failure.exception.retryable)
                spawn.assert_not_called()

    def test_terminal_success_recovers_an_earlier_error(self):
        from broader_vids.claude_worker import _stream_result

        error = {
            "type": "error",
            "error": {"type": "authentication_error", "message": "fixture"},
        }
        success = {
            "type": "result",
            "subtype": "success",
            "structured_output": {"score": 4},
        }
        stream = json.dumps(error) + "\n" + json.dumps(success)
        with (
            patch(
                "broader_vids.claude_worker.command", return_value=["claude-fixture"]
            ),
            patch(
                "broader_vids.claude_worker.run_bounded",
                return_value=subprocess.CompletedProcess([], 0, stream, ""),
            ),
        ):
            card, _ = run_claude(
                "fixture",
                self.schema,
                self.output,
                [],
                5,
                model="claude-fixture",
                effort="low",
            )
        self.assertEqual(card, {"score": 4})
        failed = {"type": "result", "subtype": "error_during_execution"}
        self.assertEqual(
            _stream_result(json.dumps(error) + "\n" + json.dumps(failed))[2:],
            (True, True),
        )

    def test_growing_or_missing_frame_never_starts_a_process(self):
        from broader_vids.claude_worker import _message
        from types import SimpleNamespace

        frame = self.root / "frame.jpg"
        frame.write_bytes(b"12345")
        with (
            patch("broader_vids.claude_worker._MAX_IMAGE_BYTES", 4),
            patch.object(Path, "stat", return_value=SimpleNamespace(st_size=1)),
        ):
            with self.assertRaisesRegex(EvidencePending, "oversized frame"):
                _message("fixture", [frame])
        with (
            patch(
                "broader_vids.claude_worker.command", return_value=["claude-fixture"]
            ),
            patch("broader_vids.claude_worker.run_bounded") as spawn,
        ):
            with self.assertRaises(FileNotFoundError):
                run_claude(
                    "fixture",
                    self.schema,
                    self.output,
                    [self.root / "missing.jpg"],
                    5,
                    model="claude-fixture",
                    effort="low",
                )
            spawn.assert_not_called()
