from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.evidence.assessment import run_codex
from broader_vids.evidence.media import EvidencePending
from broader_vids.storage import Store, utc_now
from broader_vids.token_usage import (
    capture,
    from_jsonl,
    normalize,
    report,
    tracked_call,
)


class TokenUsageTests(unittest.TestCase):
    def test_parallel_failed_and_unreported_attempts_survive_reopen_without_double_counting(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            with closing(Store(directory)) as store:
                parent = store.create(
                    "both", {"kind": "recommendation_batch"}, {"children": {}}
                )
                ids = [
                    store.create(
                        platform,
                        {"kind": "recommendation", "batch_id": parent},
                        {"model_calls": 0, "planning_model_calls": 0},
                    )
                    for platform in ("youtube", "bilibili")
                ]
                run = store.get(parent)
                run["state"]["children"] = dict(zip(("youtube", "bilibili"), ids))
                store.save(run)

            def work(index):
                with closing(Store(directory)) as store:

                    def model():
                        capture(
                            {
                                "input_tokens": 100,
                                "cached_input_tokens": 60,
                                "output_tokens": 20,
                            }
                        )
                        if index:
                            raise ValueError("Invalid model output")
                        return {}, {
                            "input_tokens": 100,
                            "cached_input_tokens": 60,
                            "output_tokens": 20,
                        }

                    try:
                        tracked_call(
                            store, ids[index], ids[index], "assessment", 1, model
                        )
                    except ValueError:
                        pass

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(work, range(2)))
            with closing(Store(directory)) as store:
                tracked_call(
                    store, ids[0], ids[0], "exploration", 1, lambda: ({}, None)
                )

                def success():
                    return {}, {
                        "input_tokens": 50,
                        "cached_input_tokens": 10,
                        "output_tokens": 10,
                    }

                tracked_call(store, ids[0], ids[0], "assessment", 2, success)
                root = store.get(parent)
                root["status"] = "paused"
                store.save(root)
                result = report(store, parent)["report"]
                self.assertEqual(result["totals"]["total_tokens"], 300)
                self.assertEqual(result["totals"]["cached_input_tokens"], 130)
                self.assertEqual(result["totals"]["calls"], 4)
                self.assertEqual(result["totals"]["reported_calls"], 3)
                self.assertEqual(result["totals"]["unreported_calls"], 1)
                self.assertEqual(
                    sum(
                        row["share_percent"]
                        for row in result["stages"]
                        if row["share_percent"] is not None
                    ),
                    100,
                )
                self.assertEqual(result["status"], "paused")
                self.assertEqual([p["tokens"] for p in result["platforms"]], [180, 120])
                self.assertEqual(report(store, parent)["report"], result)
                from broader_vids.cli import main

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(
                        main(["--data-dir", directory, "usage", parent]), 0
                    )
                self.assertEqual(
                    json.loads(output.getvalue())["report"]["totals"]["total_tokens"],
                    300,
                )

    def test_historical_usage_and_cached_evidence_are_not_imported_into_new_round(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            self.assertEqual(report(store), {"runs": [], "report": None})
            source = store.create(
                "youtube",
                {"kind": "discovery"},
                {
                    "exploration": {
                        "attempts": [
                            {"token_usage": {"input_tokens": 50, "output_tokens": 10}}
                        ]
                    }
                },
            )
            evidence = store.create(
                "youtube",
                {"kind": "evidence"},
                {
                    "attempts": [
                        {
                            "started_at": utc_now(),
                            "status": "completed",
                            "mode": "augmented",
                        }
                    ],
                    "assessments": {
                        "augmented": {
                            "usage": {
                                "token_usage": {
                                    "input_tokens": 200,
                                    "output_tokens": 40,
                                }
                            }
                        }
                    },
                },
            )
            old = store.create(
                "youtube",
                {"kind": "recommendation"},
                {
                    "discovery_run": source,
                    "planning_model_calls": 1,
                    "model_calls": 1,
                    "evidence": {"video": {"evidence_run": evidence}},
                },
            )
            with store.db:
                store.db.execute(
                    "UPDATE runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                    (old,),
                )
            values = report(store, old)["report"]
            self.assertEqual(values["totals"]["total_tokens"], 300)
            self.assertTrue(values["legacy"])
            new = store.create(
                "youtube",
                {"kind": "recommendation", "discovery_initial_planning_calls": 1},
                {
                    "discovery_run": source,
                    "planning_model_calls": 0,
                    "model_calls": 0,
                    "evidence": {"video": {"evidence_run": evidence}},
                },
            )
            self.assertEqual(report(store, new)["report"]["totals"]["total_tokens"], 0)
            self.assertEqual(report(store, new)["report"]["totals"]["calls"], 0)
            for invalid in (
                {"input_tokens": True, "output_tokens": 3},
                {"input_tokens": 3, "output_tokens": -1},
                {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 4},
            ):
                self.assertIsNone(normalize(invalid))
            usage = from_jsonl(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 10,
                                    "cached_input_tokens": 2,
                                    "output_tokens": 4,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 20,
                                    "cached_input_tokens": 3,
                                    "output_tokens": 6,
                                },
                            }
                        ),
                    ]
                )
            )
            self.assertEqual(
                usage,
                {"input_tokens": 30, "cached_input_tokens": 5, "output_tokens": 10},
            )

    def test_failed_invalid_json_and_timeout_keep_usage_emitted_by_codex(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            owner = store.create("youtube", {"kind": "recommendation"}, {})
            usage = {
                "input_tokens": 123,
                "cached_input_tokens": 100,
                "output_tokens": 17,
            }
            line = json.dumps({"type": "turn.completed", "usage": usage})
            output = Path(directory) / "response.json"

            def execute(command, **kwargs):
                if command[1:3] == ["login", "status"]:
                    return subprocess.CompletedProcess(
                        command, 0, "Logged in using ChatGPT", ""
                    )
                if mode == "timeout":
                    raise subprocess.TimeoutExpired(command, 1, output=line)
                if mode == "invalid":
                    output.write_text("invalid JSON", encoding="utf-8")
                return subprocess.CompletedProcess(
                    command, 1 if mode == "failure" else 0, line, ""
                )

            for mode in ("failure", "invalid", "timeout"):
                with (
                    self.subTest(mode=mode),
                    patch(
                        "broader_vids.evidence.assessment.shutil.which",
                        return_value="codex",
                    ),
                    patch(
                        "broader_vids.evidence.assessment.run_bounded",
                        side_effect=execute,
                    ),
                ):
                    with self.assertRaises(
                        (EvidencePending, ValueError, subprocess.TimeoutExpired)
                    ):
                        tracked_call(
                            store,
                            owner,
                            owner,
                            "assessment",
                            mode,
                            run_codex,
                            "prompt",
                            Path(directory) / "schema.json",
                            output,
                            [],
                            30,
                            model="fixture",
                            effort="low",
                        )
            totals = report(store, owner)["report"]["totals"]
            self.assertEqual(totals["total_tokens"], 420)
            self.assertEqual(totals["calls"], 3)
            self.assertEqual(totals["unreported_calls"], 0)
