from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from broader_vids.preferences import get_profile, set_profile, user_action
from broader_vids.storage import Store


class ProfileDataTests(unittest.TestCase):
    def invoke(self, root, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", "broader_vids", "--data-dir", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_source_cli_reset_clears_models_and_history_and_clean_removes_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            with closing(Store(root)) as store:
                set_profile(store, ["mathematics"])
                user_action(store, "youtube:0000000001A", "watched")
            (root / ".broader-vids-profile.json").unlink()
            (root / "onboarding.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "default_platform": "youtube",
                        "completed_at": "fixture",
                    }
                ),
                encoding="utf-8",
            )
            for name in ("runtimes", "models", "access/bilibili/profile"):
                folder = root / name
                folder.mkdir(parents=True)
                (folder / "retained.txt").write_text("keep", encoding="utf-8")
            reset_preview = self.invoke(root, "reset")
            self.assertTrue(reset_preview["dry_run"])
            self.assertFalse((root / ".broader-vids-profile.json").exists())
            report = self.invoke(
                root,
                "reset",
                "--apply",
                "--confirm-path",
                reset_preview["data_directory"],
            )
            self.assertEqual(report["status"], "completed")
            for name in ("runtimes", "models", "access/bilibili/profile"):
                self.assertEqual((root / name / "retained.txt").read_text(), "keep")
            with closing(Store(root)) as store:
                self.assertIsNone(get_profile(store))
                for table in (
                    "user_actions",
                    "learning_models",
                    "runs",
                    "creator_exclusions",
                    "calibration_items",
                ):
                    self.assertEqual(
                        store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                        0,
                    )
            clean_preview = self.invoke(root, "clean")
            self.assertEqual(
                self.invoke(
                    root,
                    "clean",
                    "--apply",
                    "--confirm-path",
                    clean_preview["data_directory"],
                )["status"],
                "completed",
            )
            self.assertFalse(root.exists())
            self.assertEqual(self.invoke(root, "clean")["status"], "absent")
            self.assertFalse(root.exists())

    def test_unfinished_reset_and_maintenance_block_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".reset-pending").mkdir()
            with self.assertRaisesRegex(ValueError, "reset is unfinished"):
                Store(root)
            (root / ".reset-pending").rmdir()
            (root / ".launcher").mkdir()
            (root / ".launcher/setup.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "operation holds"):
                Store(root)
            self.assertFalse((root / "broader-vids.sqlite3").exists())
