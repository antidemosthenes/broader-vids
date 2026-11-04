import builtins
import os
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.cli.arguments import parser
from broader_vids.cli.startup import prepare_recommendation
from broader_vids.discovery.service import create_discovery
from broader_vids.evidence.service import create_evidence
from broader_vids.preferences import set_profile
from broader_vids.recommendations.batch import create_batch, _validate_current_batch
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from broader_vids.token_usage import report
from broader_vids.worker_context import resolve_worker_settings, worker_runner


class WorkerHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(self.temporary.name)
        self.addCleanup(self.store.close)
        set_profile(self.store, ["mathematics"])

    def create(self, host="codex"):
        return self.store.get(
            create_recommendation(
                self.store,
                "youtube",
                assessment_model="claude-fixture",
                reasoning_effort="medium",
                host=host,
            )
        )

    def test_codex_default_never_imports_claude_and_keeps_existing_settings(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "broader_vids.claude_worker":
                raise AssertionError("Claude module reached the Codex path")
            return original(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=guarded),
            patch.dict(
                os.environ,
                {
                    "BROADER_VIDS_CLAUDE_MODEL": "claude-fixture",
                    "BROADER_VIDS_CLAUDE_EFFORT": "high",
                },
            ),
        ):
            self.assertEqual(
                resolve_worker_settings("codex-fixture", "low"),
                ("codex-fixture", "low"),
            )
            run = self.create()
            self.assertNotIn("worker_host", run["settings"])
            runner = worker_runner(run["settings"])
            self.assertEqual(runner.func.__name__, "run_codex")
            self.assertEqual(parser().parse_args(["doctor"]).host, "codex")

    def test_evidence_cache_separates_providers_without_changing_codex_identity(self):
        options = {"assessment_model": "claude-fixture", "reasoning_effort": "medium"}
        url = "https://www.youtube.com/watch?v=imYQJOgUx7Y"
        codex = self.store.get(create_evidence(self.store, url, **options))
        claude = self.store.get(
            create_evidence(self.store, url, host="claude", **options)
        )
        self.assertNotEqual(
            codex["settings"]["cache_key"], claude["settings"]["cache_key"]
        )
        self.assertNotIn("worker_host", codex["settings"])
        self.assertEqual(claude["settings"]["worker_host"], "claude")
        self.assertEqual(create_evidence(self.store, url, **options), codex["id"])
        self.assertEqual(
            create_evidence(self.store, url, host="claude", **options), claude["id"]
        )

    def test_batches_and_discovery_keep_explicit_provider(self):
        identifier = create_batch(
            self.store,
            host="claude",
            assessment_model="claude-fixture",
            reasoning_effort="medium",
        )
        batch = self.store.get(identifier)
        self.assertEqual(batch["settings"]["worker_host"], "claude")
        for child in batch["state"]["children"].values():
            self.assertEqual(self.store.get(child)["settings"]["worker_host"], "claude")
        discovery = create_discovery(
            self.store,
            "youtube",
            host="claude",
            assessment_model="claude-fixture",
            reasoning_effort="medium",
        )
        with self.assertRaisesRegex(ValueError, "another AI host"):
            create_recommendation(
                self.store,
                "youtube",
                discovery,
                assessment_model="codex-fixture",
                reasoning_effort="medium",
            )
        child = self.store.get(next(iter(batch["state"]["children"].values())))
        child["settings"].pop("worker_host")
        with self.store.db:
            self.store.db.execute(
                "UPDATE runs SET settings=json_remove(settings, '$.worker_host') WHERE id=?",
                (child["id"],),
            )
        with self.assertRaisesRegex(ValueError, "inconsistent saved AI hosts"):
            _validate_current_batch(self.store, batch)

    def test_resumed_round_uses_saved_provider_despite_current_host(self):
        for saved_host, invoking_host in (("codex", "claude"), ("claude", "codex")):
            with self.subTest(saved_host=saved_host):
                run = self.create(saved_host)
                args = parser().parse_args(
                    ["--host", invoking_host, "continue", run["id"]]
                )
                with patch(
                    "broader_vids.cli.startup.preflight",
                    return_value={"status": "ready", "elapsed_seconds": 0},
                ) as probe:
                    prepare_recommendation(self.store, args)
                self.assertEqual(
                    probe.call_args.kwargs.get("host", "codex"), saved_host
                )
                self.assertEqual(probe.call_args.kwargs["model"], "claude-fixture")
                self.assertEqual(probe.call_args.kwargs["effort"], "medium")
                self.assertFalse(probe.call_args.kwargs["check_feedback"])

    def test_dispatch_retains_saved_settings_after_environment_changes(self):
        run = self.create("claude")
        with (
            patch.dict(
                os.environ,
                {
                    "BROADER_VIDS_CLAUDE_MODEL": "claude-changed",
                    "BROADER_VIDS_CLAUDE_EFFORT": "high",
                },
            ),
            patch(
                "broader_vids.claude_worker.run_claude",
                return_value=({"fixture": True}, None),
            ) as execute,
        ):
            result = worker_runner(run["settings"])("prompt", "schema", "output", [], 5)
        self.assertEqual(result[0], {"fixture": True})
        self.assertEqual(execute.call_args.kwargs["model"], "claude-fixture")
        self.assertEqual(execute.call_args.kwargs["effort"], "medium")

    def test_unknown_host_creates_no_work(self):
        before = len(self.store.list_runs())
        with self.assertRaisesRegex(ValueError, "codex.*claude"):
            self.create("automatic")
        self.assertEqual(len(self.store.list_runs()), before)

    def test_token_report_adds_host_only_for_claude(self):
        codex = self.create()
        claude = self.create("claude")
        codex_report = report(self.store, codex["id"])["report"]
        claude_report = report(self.store, claude["id"])["report"]
        self.assertNotIn("worker_host", codex_report)
        self.assertEqual(claude_report["worker_host"], "claude")

    def test_claude_cache_matches_model_and_effort_without_changing_codex_policy(self):
        from broader_vids.recommendations.quality import compatible_quality

        settings = {
            "worker_host": "claude",
            "assessment_model": "claude-fixture",
            "reasoning_effort": "medium",
        }
        self.assertTrue(compatible_quality(settings, dict(settings)))
        for key, value in (
            ("assessment_model", "claude-other"),
            ("reasoning_effort", "high"),
            ("worker_host", "codex"),
        ):
            self.assertFalse(compatible_quality(settings, {**settings, key: value}))
        self.assertTrue(
            compatible_quality(
                {"assessment_model": "codex-new"}, {"assessment_model": "codex-old"}
            )
        )
