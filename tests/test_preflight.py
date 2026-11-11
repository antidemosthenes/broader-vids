from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from broader_vids.cli import main
from broader_vids.cli.arguments import parser
from broader_vids.cli.startup import prepare_recommendation, startup_lock
from broader_vids.onboarding import configure, complete, status
from broader_vids.preferences import get_profile, set_profile, user_action
from broader_vids.preflight import (
    preflight,
    feedback_summary,
    external_checks,
    DIMENSIONS,
)
from broader_vids.preflight_checks import bilibili_check, chatgpt_check, inspect
from broader_vids.recommendations.batch import create_batch
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store, utc_now
from broader_vids.time_budget import effective_limit


AVAILABLE = {
    "checks": {"tools": {"status": "available"}},
    "warnings": {"ffprobe": {"status": "missing"}},
}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        self.checks = patch(
            "broader_vids.preflight.external_checks", return_value=AVAILABLE
        )
        self.probe = self.checks.start()
        self.addCleanup(self.checks.stop)
        set_profile(self.store, ["mathematics"])

    def ready(self, platform="youtube"):
        configure(self.store, platform)
        report = complete(self.store, model="fixture-model", effort="low")
        self.assertEqual(report["status"], "ready")

    def check(self, platform=None, **options):
        return preflight(
            self.store, platform, model="fixture-model", effort="low", **options
        )

    def video(self, key, *, exposed=True):
        user_action(self.store, key, "unwatched")
        if exposed:
            run = self.store.create(key.split(":")[0], {"kind": "recommendation"}, {})
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO recommendation_exposures VALUES(?,?,1,?)",
                    (run, key, utc_now()),
                )
        return key

    def rating(self, key, dimension):
        with self.store.db:
            action = self.store.db.execute(
                "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,?,?,?)",
                (key, utc_now(), "feedback", "{}"),
            )
            self.store.db.execute(
                "INSERT OR REPLACE INTO feedback_details VALUES(?,?,?,?)",
                (
                    key,
                    dimension,
                    action.lastrowid,
                    json.dumps(
                        {
                            "value": "suitable" if dimension == "depth" else "high",
                            "target": "mathematics",
                        }
                    ),
                ),
            )

    def invoke(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(["--data-dir", self.directory.name, *arguments])
        return code, json.loads(output.getvalue())

    def test_existing_transaction_is_preserved_without_probes(self):
        self.ready()
        self.probe.reset_mock()
        self.store.db.execute("UPDATE user_profile SET revision=revision+1")
        revision = get_profile(self.store)["revision"]
        report = self.check()
        self.assertEqual(report["checks"]["profile"]["status"], "busy")
        self.assertTrue(self.store.db.in_transaction)
        self.assertEqual(get_profile(self.store)["revision"], revision)
        self.probe.assert_not_called()
        self.store.db.rollback()

    def test_setup_questions_cover_fresh_profile_choices_without_side_effects(self):
        with self.store.db:
            self.store.db.execute("DELETE FROM user_profile")
        before = self.store.db.total_changes
        code, report = self.invoke("onboarding", "questions")
        self.assertEqual(code, 0)
        self.assertTrue(report["requires_explicit_answer"])
        questions = report["questions"]
        self.assertEqual(
            [o["value"] for o in questions["platform"]["options"]],
            ["youtube", "bilibili", "both"],
        )
        self.assertEqual(
            [o["value"] for o in questions["preference_source"]["options"]],
            ["preset", "manual", "import"],
        )
        self.assertNotIn("existing_preferences", questions)
        self.assertEqual(questions["import_path"]["input"], "text")
        self.assertEqual(questions["interests"]["input"], "text")
        self.assertEqual(
            [o["value"] for o in questions["bilibili_signin"]["options"]],
            ["signed_in", "trouble", "waiting"],
        )
        self.assertEqual(questions["bilibili_trouble"]["input"], "text")
        self.assertEqual(self.store.db.total_changes, before)
        self.assertFalse((self.store.root / "onboarding.json").exists())
        self.assertEqual(self.store.list_runs(), [])
        self.probe.assert_not_called()

    def test_setup_questions_preserve_existing_preferences_and_platform(self):
        configure(self.store, "both")
        previous = (self.store.root / "onboarding.json").read_bytes()
        profile = get_profile(self.store)
        code, report = self.invoke("onboarding", "questions")
        self.assertEqual(code, 0)
        self.assertEqual(report["saved_platform"], "both")
        self.assertEqual(
            report["questions"]["existing_preferences"]["options"][0],
            {"value": "keep", "label": "Keep saved preferences"},
        )
        self.assertEqual((self.store.root / "onboarding.json").read_bytes(), previous)
        self.assertEqual(get_profile(self.store), profile)
        self.probe.assert_not_called()

    def test_fresh_and_interrupted_setup_block_without_run_or_optional_install(self):
        profile = get_profile(self.store)
        self.assertEqual(self.check()["status"], "setup_required")
        configure(self.store, "both")
        self.assertEqual(self.check()["status"], "setup_required")
        self.probe.assert_not_called()
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(get_profile(self.store), profile)
        self.assertIsNone(status(self.store.root)["completed_at"])

    def test_completion_reports_exact_profile_and_actionable_next_steps(self):
        configure(self.store, "youtube")
        launcher = str(
            self.store.root / "plugin with 'quote'" / "bin" / "broader-vids.cjs"
        )
        with patch.dict("os.environ", {"BROADER_VIDS_LAUNCHER": launcher}):
            report = complete(self.store, model="fixture-model", effort="low")
        self.assertEqual(report["self_check"], "passed")
        self.assertEqual(report["data_directory"], str(self.store.root))
        steps = report["next_steps"]
        self.assertEqual(set(steps), {"dashboard", "recommendations", "reset", "clean"})
        for name, action in steps.items():
            expected = (
                "broader-vids" if name == "recommendations" else "broader-vids-" + name
            )
            self.assertEqual(action["skill_invocation"], "$" + expected)
            self.assertTrue(action["request"].startswith("Use Broader Vids"))
            self.assertNotIn("terminal_argv", action)
            self.assertNotIn("powershell_command", action)
            self.assertNotIn(launcher, json.dumps(action))
        message = report["next_steps_message"]
        for action in steps.values():
            self.assertIn(action["description"], message)
            self.assertIn(action["request"], message)
            self.assertIn(action["skill_invocation"], message)
        self.assertIn("Import/Export", message)
        self.assertIn("Run setup before reuse.", message)
        self.assertNotIn(launcher, message)
        self.assertEqual(self.store.list_runs(), [])
        self.assertFalse((self.store.root / ".dashboard").exists())

    def test_platform_defaults_completion_and_changes_preserve_learning_profile(self):
        profile = get_profile(self.store)
        for platform in ("youtube", "bilibili", "both"):
            self.ready(platform)
            self.assertEqual(self.check()["platform"], platform)
            self.assertEqual(self.check()["status"], "ready")
            self.assertEqual(self.check()["warnings"]["ffprobe"]["status"], "missing")
        saved = status(self.store.root)
        configure(self.store, "both")
        self.assertEqual(status(self.store.root), saved)
        configure(self.store, "youtube")
        self.assertIsNone(status(self.store.root)["completed_at"])
        self.assertEqual(get_profile(self.store), profile)
        self.assertEqual(list(self.store.root.glob("*.tmp")), [])

    def test_failed_completion_does_not_mark_setup_or_reset_existing_profile(self):
        configure(self.store, "bilibili")
        self.probe.return_value = {
            "checks": {"bilibili_access": {"status": "missing", "remedy": "Sign in"}},
            "warnings": {},
        }
        report = complete(self.store, model="fixture-model", effort="low")
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("self_check", report)
        self.assertNotIn("next_steps", report)
        self.assertIsNone(status(self.store.root)["completed_at"])
        self.assertEqual(get_profile(self.store)["interests"], ["mathematics"])
        self.assertEqual(self.store.list_runs(), [])

    def test_missing_feedback_counts_history_duplicates_partials_and_clears(self):
        self.ready("both")
        youtube = self.video("youtube:0000000001A")
        bili = self.video("bilibili:BV1Z6H56oEcQ:p1")
        self.video(youtube)
        self.video("youtube:0000000002A", exposed=False)
        user_action(self.store, youtube, "watched")
        for dimension in DIMENSIONS[:-1]:
            self.rating(youtube, dimension)
        report = self.check()
        self.assertEqual(report["status"], "feedback_required")
        self.assertEqual(report["feedback"]["total"], 2)
        self.assertEqual(report["feedback"]["platforms"]["youtube"]["partial"], 1)
        self.assertEqual(report["feedback"]["platforms"]["bilibili"]["no_ratings"], 1)
        user_action(self.store, bili, "dismiss")
        self.assertEqual(feedback_summary(self.store, "both")["total"], 1)
        user_action(self.store, bili, "restore")
        self.assertEqual(feedback_summary(self.store, "both")["total"], 2)
        self.rating(youtube, "quality_level")
        self.assertEqual(feedback_summary(self.store, "youtube")["total"], 0)
        with self.store.db:
            self.store.db.execute(
                "DELETE FROM feedback_details WHERE canonical_id=? AND dimension='depth'",
                (youtube,),
            )
        self.assertEqual(
            feedback_summary(self.store, "youtube")["platforms"]["youtube"][
                "missing_dimensions"
            ]["depth"],
            1,
        )
        self.assertEqual(
            self.check("youtube", allow_missing_feedback=True)["status"], "ready"
        )
        self.assertEqual(self.check("youtube")["status"], "feedback_required")

    def test_cli_blocked_attempts_and_feedback_choice_create_no_new_work(self):
        self.assertEqual(self.invoke("recommend")[1]["status"], "setup_required")
        self.ready()
        self.video("youtube:0000000001A")
        before = self.store.list_runs()
        code, report = self.invoke(
            "recommend", "--model", "fixture-model", "--effort", "low"
        )
        self.assertEqual((code, report["status"]), (2, "feedback_required"))
        self.assertEqual(self.store.list_runs(), before)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0
        )
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM feedback_details").fetchone()[
                0
            ],
            0,
        )
        self.probe.return_value = {
            "checks": {"ffmpeg": {"status": "unusable", "remedy": "Repair"}},
            "warnings": {},
        }
        self.assertEqual(
            self.invoke(
                "recommend",
                "--allow-missing-feedback",
                "--model",
                "fixture-model",
                "--effort",
                "low",
            )[1]["status"],
            "blocked",
        )
        self.assertEqual(self.store.list_runs(), before)

    def test_cli_default_bypass_and_automatic_bilibili_access_do_not_change_default(
        self,
    ):
        self.ready("youtube")
        self.video("bilibili:BV1Z6H56oEcQ:p1")
        with patch("broader_vids.recommendations.batch.execute_batch") as execute:
            code, report = self.invoke(
                "recommend",
                "both",
                "--allow-missing-feedback",
                "--model",
                "fixture-model",
                "--effort",
                "low",
                "--max-assessments",
                "0",
            )
        self.assertEqual(code, 2)
        self.assertEqual(execute.call_count, 1)
        batch = self.store.get(report["run_id"])
        self.assertGreater(batch["elapsed"], 0)
        children = [self.store.get(key) for key in batch["state"]["children"].values()]
        self.assertTrue(
            next(child for child in children if child["platform"] == "bilibili")[
                "settings"
            ]["use_browser"]
        )
        self.assertEqual(status(self.store.root)["default_platform"], "youtube")
        self.assertEqual(self.check("both")["status"], "feedback_required")

    def test_omitted_platform_starts_saved_default_without_changing_it(self):
        self.ready("youtube")
        with patch("broader_vids.cli.app.execute_recommendation") as execute:
            code, report = self.invoke(
                "recommend",
                "--model",
                "fixture-model",
                "--effort",
                "low",
                "--max-assessments",
                "0",
            )
        self.assertEqual(code, 2)
        self.assertEqual(report["platform"], "youtube")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(status(self.store.root)["default_platform"], "youtube")

    def test_startup_reservation_blocks_reentrant_round_before_creation(self):
        self.ready("youtube")
        nested = {}
        actual_preflight = preflight

        def reenter(*args, **kwargs):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(io.StringIO()):
                nested["code"] = main(
                    [
                        "--data-dir",
                        self.directory.name,
                        "recommend",
                        "youtube",
                        "--model",
                        "fixture-model",
                        "--effort",
                        "low",
                    ]
                )
            nested["report"] = json.loads(output.getvalue())
            return actual_preflight(*args, **kwargs)

        with (
            patch("broader_vids.cli.startup.preflight", side_effect=reenter),
            patch("broader_vids.cli.app.execute_recommendation"),
        ):
            _, report = self.invoke(
                "recommend",
                "youtube",
                "--model",
                "fixture-model",
                "--effort",
                "low",
                "--max-assessments",
                "0",
            )

        self.assertEqual(nested["code"], 2)
        self.assertEqual(nested["report"]["status"], "blocked")
        self.assertEqual(nested["report"]["checks"]["active_run"]["status"], "busy")
        self.assertEqual(report["platform"], "youtube")
        self.assertEqual(len(self.store.list_runs()), 1)
        self.assertFalse((self.store.root / f"{startup_lock('youtube')}.lock").exists())

    def test_resume_uses_saved_settings_ignores_feedback_and_charges_once(self):
        self.video("youtube:0000000001A")
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="saved-model",
            reasoning_effort="high",
        )
        args = parser().parse_args(["continue", identifier])
        before = self.store.get(identifier)
        report = prepare_recommendation(self.store, args)
        after = self.store.get(identifier)
        self.assertEqual(report["status"], "ready")
        self.assertEqual((report["model"], report["effort"]), ("saved-model", "high"))
        self.assertEqual(report["feedback"]["total"], 0)
        self.assertEqual(after["settings"], before["settings"])
        self.assertAlmostEqual(after["elapsed"], report["elapsed_seconds"])
        self.assertEqual(len(self.store.events(identifier)), 1)
        self.assertIsNone(status(self.store.root)["completed_at"])

    def test_interrupted_run_preflight_is_charged_once_after_crash_recovery(self):
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="saved-model",
            reasoning_effort="high",
        )
        run = self.store.get(identifier)
        run["elapsed"] = 25
        run["state"]["active_attempt"] = {"started_wall": 100, "prior_elapsed": 5}
        self.store.save(run)
        wall = [120]

        def ready(*args, **kwargs):
            wall[0] += 2
            return {"status": "ready", "elapsed_seconds": 2}

        with (
            patch(
                "broader_vids.recommendations.timing.time.time",
                side_effect=lambda: wall[0],
            ),
            patch("broader_vids.cli.startup.preflight", side_effect=ready),
        ):
            report = prepare_recommendation(
                self.store, parser().parse_args(["continue", identifier])
            )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(self.store.get(identifier)["elapsed"], 27)
        self.assertNotIn("active_attempt", self.store.get(identifier)["state"])

    def test_batch_resume_retains_children_settings_and_rejects_child_continuation(
        self,
    ):
        identifier = create_batch(
            self.store, assessment_model="saved-model", reasoning_effort="medium"
        )
        before = self.store.get(identifier)
        report = prepare_recommendation(
            self.store, parser().parse_args(["continue", identifier])
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["model"], "saved-model")
        for child in before["state"]["children"].values():
            with self.assertRaisesRegex(ValueError, "Continue batch"):
                prepare_recommendation(
                    self.store, parser().parse_args(["continue", child])
                )
        self.assertEqual(before["settings"], self.store.get(identifier)["settings"])

    def test_active_run_is_busy_and_keeps_lock_and_allowance(self):
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        with self.store.lease(identifier):
            report = prepare_recommendation(
                self.store, parser().parse_args(["continue", identifier])
            )
            self.assertEqual(report["checks"]["active_run"]["status"], "busy")
            self.assertTrue((self.store.root / f"{identifier}.lock").exists())
        self.probe.assert_not_called()
        self.assertEqual(self.store.get(identifier)["elapsed"], 0)

    def test_unlimited_rounds_resume_beyond_thirty_minutes(self):
        self.assertIsNone(parser().parse_args(["recommend"]).max_seconds)
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="saved-model",
            reasoning_effort="high",
        )
        run = self.store.get(identifier)
        self.assertIsNone(run["settings"]["max_seconds"])
        run["elapsed"] = 2400
        self.store.save(run)
        report = prepare_recommendation(
            self.store, parser().parse_args(["continue", identifier])
        )
        self.assertEqual(report["status"], "ready")
        self.assertTrue(
            math.isinf(effective_limit(self.store.get(identifier)["settings"]))
        )
        self.assertGreaterEqual(self.store.get(identifier)["elapsed"], 2400)
        self.assertLessEqual(self.probe.call_args.kwargs["timeout"], 30)
        json.dumps(self.store.get(identifier), allow_nan=False)

    def test_resume_recovers_crash_time_before_any_network_check(self):
        identifier = create_recommendation(
            self.store,
            "youtube",
            max_seconds=100,
            assessment_model="saved-model",
            reasoning_effort="high",
        )
        run = self.store.get(identifier)
        run["state"]["active_attempt"] = {"started_wall": 0, "prior_elapsed": 0}
        self.store.save(run)
        report = prepare_recommendation(
            self.store, parser().parse_args(["continue", identifier])
        )
        self.assertEqual(report["status"], "blocked")
        self.probe.assert_not_called()
        self.assertEqual(
            self.store.get(identifier)["elapsed"], run["settings"]["max_seconds"]
        )

    def test_resume_never_fills_missing_saved_model_from_current_chat(self):
        identifier = create_recommendation(
            self.store,
            "youtube",
            assessment_model="saved-model",
            reasoning_effort="high",
        )
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=json_remove(settings,'$.assessment_model') WHERE id=?",
                (identifier,),
            )
        report = prepare_recommendation(
            self.store, parser().parse_args(["continue", identifier])
        )
        self.assertEqual(report["checks"]["model_settings"]["status"], "unverified")
        self.probe.assert_not_called()

    def test_short_new_allowance_cannot_start_after_checks_exhaust_it(self):
        self.ready()
        with patch(
            "broader_vids.cli.startup.preflight",
            return_value={"status": "ready", "elapsed_seconds": 1},
        ):
            report = prepare_recommendation(
                self.store,
                parser().parse_args(["recommend", "youtube", "--max-seconds", "0.5"]),
            )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.store.list_runs(), [])

    def test_import_preserves_onboarding_and_export_excludes_it(self):
        from broader_vids.learning.transfer import (
            export_preferences,
            import_preferences,
        )

        self.ready()
        before = status(self.store.root)
        bundle = export_preferences(self.store)
        self.assertNotIn("onboarding", json.dumps(bundle))
        self.assertNotIn("default_platform", json.dumps(bundle))
        import_preferences(self.store, bundle)
        self.assertEqual(status(self.store.root), before)
        self.assertEqual(self.check()["status"], "ready")

    def test_missing_model_or_requirements_block_without_losing_completion(self):
        self.ready()
        saved = status(self.store.root)
        with patch(
            "broader_vids.preflight.resolve_worker_settings",
            side_effect=ValueError("Pass host settings"),
        ):
            self.assertEqual(
                self.check()["checks"]["model_settings"]["status"], "unverified"
            )
        self.probe.return_value = {
            "checks": {"ffmpeg": {"status": "missing"}},
            "warnings": {},
        }
        self.assertEqual(self.check()["status"], "blocked")
        self.assertEqual(status(self.store.root), saved)
        self.assertEqual(self.store.list_runs(), [])

    def test_chat_completion_renders_the_entire_guide_and_keeps_blockers_structured(
        self,
    ):
        configure(self.store, "both")
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(
                [
                    "--data-dir",
                    self.directory.name,
                    "onboarding",
                    "complete",
                    "--format",
                    "chat",
                    "--skill-namespace",
                    "broader-vids",
                    "--model",
                    "fixture-model",
                    "--effort",
                    "low",
                    "--asr-model",
                    "base",
                ]
            )
        message = output.getvalue().strip()
        self.assertEqual(code, 0)
        self.assertIn("Self-check: passed.", message)
        self.assertIn("YouTube and Bilibili", message)
        self.assertIn(str(self.store.root), message)
        self.assertIn("ffprobe: missing", message)
        self.assertIn("Import/Export", message)
        report = complete(
            self.store,
            model="fixture-model",
            effort="low",
            asr_model="base",
            skill_namespace="broader-vids",
        )
        self.assertEqual(message, report["next_steps_message"])
        for action in report["next_steps"].values():
            self.assertIn(action["description"], message)
            self.assertIn(action["request"], message)
            self.assertIn("`$broader-vids:" + action["skill_name"] + "`", message)
        self.assertEqual(self.store.list_runs(), [])
        self.assertFalse((self.store.root / ".dashboard").exists())
        configure(self.store, "youtube")
        self.probe.return_value = {
            "checks": {"ffmpeg": {"status": "missing"}},
            "warnings": {},
        }
        code, blocked = self.invoke(
            "onboarding",
            "complete",
            "--format",
            "chat",
            "--model",
            "fixture-model",
            "--effort",
            "low",
        )
        self.assertEqual((code, blocked["status"]), (2, "blocked"))
        self.assertNotIn("next_steps_message", blocked)
        self.assertIsNone(status(self.store.root)["completed_at"])
        self.probe.reset_mock()
        with self.assertRaises(ValueError):
            complete(
                self.store,
                model="fixture-model",
                effort="low",
                skill_namespace="invalid:namespace",
            )
        self.probe.assert_not_called()


class AccessCheckTests(unittest.TestCase):
    def test_bilibili_verified_expired_malformed_and_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(bilibili_check(root, "token")["status"], "missing")
            profile = root / "access/bilibili/profile"
            profile.mkdir(parents=True)
            browser = Mock()
            browser.request.get.return_value.ok = True
            with patch(
                "broader_vids.preflight_checks.bilibili_browser",
                side_effect=lambda *args, **kwargs: nullcontext(browser),
            ):
                for value, expected in (
                    ({"code": 0, "data": {"isLogin": True, "mid": 42}}, "available"),
                    ({"code": 0, "data": {"isLogin": False}}, "missing"),
                    ({"code": 0, "data": {}}, "unverified"),
                    ([], "unverified"),
                ):
                    browser.request.get.return_value.json.return_value = value
                    self.assertEqual(bilibili_check(root, "token")["status"], expected)
            lock = profile.parent / "browser.lock"
            lock.write_text("another-owner", encoding="utf-8")
            self.assertEqual(bilibili_check(root, "token")["status"], "busy")
            self.assertEqual(lock.read_text(), "another-owner")

    def test_browser_launch_failures_and_request_failures_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "access/bilibili/profile").mkdir(parents=True)
            with patch(
                "broader_vids.preflight_checks.bilibili_browser",
                side_effect=OSError("Browser missing"),
            ):
                self.assertEqual(bilibili_check(root, "token")["status"], "unusable")
            browser = Mock()
            browser.request.get.side_effect = OSError("Offline")
            with patch(
                "broader_vids.preflight_checks.bilibili_browser",
                return_value=nullcontext(browser),
            ):
                self.assertEqual(bilibili_check(root, "token")["status"], "unverified")

    def test_chatgpt_only_login_and_timeout_without_credential_exposure(self):
        with (
            patch("broader_vids.preflight_checks.shutil.which", return_value="codex"),
            patch("broader_vids.preflight_checks.run_bounded") as run,
        ):
            for text, expected in (
                ("Logged in using ChatGPT", "available"),
                ("Logged in using an API key", "missing"),
            ):
                run.return_value = subprocess.CompletedProcess([], 0, "", text)
                self.assertEqual(chatgpt_check()["status"], expected)
            run.side_effect = subprocess.TimeoutExpired("codex", 5)
            self.assertEqual(chatgpt_check()["status"], "unverified")
            self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])

    def test_platform_specific_and_requested_asr_requirements(self):
        environment = {
            "required": {"node": {"status": "available"}},
            "conditional": {"playwright": {"status": "missing"}},
            "optional": {
                "faster_whisper": {"status": "missing"},
                "ffprobe": {"status": "missing"},
            },
        }
        with (
            patch(
                "broader_vids.preflight_checks.environment_report",
                return_value=environment,
            ),
            patch(
                "broader_vids.preflight_checks.chatgpt_check",
                return_value={"status": "available"},
            ),
            patch("broader_vids.preflight_checks.bilibili_check") as browser,
        ):
            youtube = inspect(Path("unused"), "youtube", "token", False)
            self.assertNotIn("playwright", youtube["checks"])
            self.assertNotIn("faster_whisper", youtube["checks"])
            both = inspect(Path("unused"), "both", "token", True)
            self.assertEqual(both["checks"]["playwright"]["status"], "missing")
            self.assertEqual(both["checks"]["faster_whisper"]["status"], "missing")
            browser.assert_not_called()

    def test_broken_installed_media_module_is_unusable(self):
        environment = {
            "required": {"PIL": {"status": "available", "remedy": "Repair evidence"}},
            "conditional": {},
            "optional": {},
        }
        with (
            patch(
                "broader_vids.preflight_checks.environment_report",
                return_value=environment,
            ),
            patch(
                "broader_vids.preflight_checks.chatgpt_check",
                return_value={"status": "available"},
            ),
            patch(
                "broader_vids.preflight_checks.importlib.import_module",
                side_effect=ImportError("broken"),
            ),
        ):
            self.assertEqual(
                inspect(Path("unused"), "youtube", "token", False)["checks"]["PIL"][
                    "status"
                ],
                "unusable",
            )

    def test_timeout_cleans_only_owned_browser_lock_and_returns_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "access/bilibili/browser.lock"
            lock.parent.mkdir(parents=True)

            def timeout(command, **kwargs):
                lock.write_text(command[-2], encoding="utf-8")
                raise subprocess.TimeoutExpired(command, 1)

            with patch("broader_vids.preflight.run_bounded", side_effect=timeout):
                report = external_checks(root, "bilibili", timeout=1)
            self.assertEqual(
                report["checks"]["automated_checks"]["status"], "unverified"
            )
            self.assertFalse(lock.exists())
            lock.write_text("another-owner", encoding="utf-8")
            with patch(
                "broader_vids.preflight.run_bounded",
                side_effect=subprocess.TimeoutExpired("probe", 1),
            ):
                external_checks(root, "bilibili", timeout=1)
            self.assertEqual(lock.read_text(), "another-owner")

    def test_pending_session_recovery_blocks_access_with_correct_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "access/bilibili/switch-commit.json"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}")
            with patch("broader_vids.preflight_checks.bilibili_browser") as browser:
                report = bilibili_check(root, "fixture")
            self.assertEqual(report["status"], "busy")
            self.assertIn("Finish recovery", report["remedy"])
            browser.assert_not_called()
