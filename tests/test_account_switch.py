import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.blacklist import SYNC_LOCK
from broader_vids.creators import snapshot, set_exclusion, status
from broader_vids.evidence.account_switch import (
    AccountSwitch,
    activate_session,
    access_dir,
    job_dir,
    recover_switch,
    SwitchCleanupPending,
)
from broader_vids.evidence.browser import browser_lease
from broader_vids.evidence.service import atomic_json
from broader_vids.preferences import (
    get_profile,
    set_profile,
    user_action,
    platform_lock,
)
from broader_vids.storage import Store


def prepared(account="200", *, failed_sync=False):
    return {
        "account": {"id": account, "name": "New account"},
        "sync": {
            "status": "failed" if failed_sync else "complete",
            "count": 1,
            "last_success": "fixture",
            "error": "Sync interrupted" if failed_sync else None,
        },
        "entries": []
        if failed_sync
        else [{"creator_id": "22", "name": "New blacklist creator"}],
    }


class AccountSwitchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.addCleanup(self.store.close)
        set_profile(self.store, ["mathematics"])
        user_action(self.store, "youtube:0000000001A", "watched")
        set_exclusion(self.store, "bilibili", "11", True, "Manual")
        with self.store.db:
            self.store.db.execute(
                "UPDATE creator_policy SET account_id='100' WHERE id=1"
            )
            self.store.db.execute(
                "INSERT INTO creator_exclusions VALUES('bilibili','33','bilibili_blacklist','100','Old account ban','fixture')"
            )
        self.base = access_dir(self.store.root)
        (self.base / "profile").mkdir(parents=True)
        (self.base / "profile/old-session.txt").write_text(
            "old-session", encoding="utf-8"
        )
        (self.base / "status.json").write_text('{"account_id":"100"}', encoding="utf-8")
        self.identifier = "a" * 32
        job = job_dir(self.store.root, self.identifier)
        (job / "profile").mkdir(parents=True)
        (job / "profile/new-session.txt").write_text("new-session", encoding="utf-8")

    def assert_old(self):
        self.assertEqual(
            (self.base / "profile/old-session.txt").read_text(), "old-session"
        )
        self.assertEqual(status(self.store)["bilibili_sync"]["account_id"], "100")
        self.assertFalse((self.base / "switch-commit.json").exists())

    def test_verified_activation_removes_old_session_and_partitions_rules(self):
        profile = get_profile(self.store)
        actions = list(self.store.db.execute("SELECT * FROM user_actions"))
        result = activate_session(self.store, self.identifier, prepared())
        self.assertEqual(result["account_id"], "200")
        self.assertFalse((self.base / "profile/old-session.txt").exists())
        self.assertEqual(
            (self.base / "profile/new-session.txt").read_text(), "new-session"
        )
        self.assertFalse(job_dir(self.store.root, self.identifier).exists())
        self.assertEqual(
            set(snapshot(self.store, "bilibili")["excluded_ids"]), {"11", "22"}
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM creator_exclusions WHERE account_id='100'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(get_profile(self.store), profile)
        self.assertEqual(
            [tuple(row) for row in self.store.db.execute("SELECT * FROM user_actions")],
            [tuple(row) for row in actions],
        )

    def test_failed_blacklist_keeps_new_accounts_saved_rules_without_old_account_leak(
        self,
    ):
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO creator_exclusions VALUES('bilibili','44','bilibili_blacklist','200','Saved new-account ban','fixture')"
            )
            self.store.db.execute(
                "INSERT INTO creator_sync VALUES('200',?)",
                (
                    json.dumps(
                        {"status": "complete", "last_success": "previous-success"}
                    ),
                ),
            )
        activate_session(self.store, self.identifier, prepared(failed_sync=True))
        self.assertEqual(
            set(snapshot(self.store, "bilibili")["excluded_ids"]), {"11", "44"}
        )
        self.assertEqual(
            status(self.store)["bilibili_sync"]["last_success"], "previous-success"
        )
        self.assertEqual(status(self.store)["bilibili_sync"]["status"], "failed")

    def test_failed_activation_restores_profile_status_and_database(self):
        old = Path.replace
        target = self.base / "status.json"

        def fail_new_status(source, destination):
            if Path(destination) == target and Path(source).suffix == ".tmp":
                raise OSError("Failed status publication")
            return old(source, destination)

        with patch.object(Path, "replace", fail_new_status):
            with self.assertRaises(OSError):
                activate_session(self.store, self.identifier, prepared())
        self.assert_old()
        self.assertEqual(json.loads(target.read_text())["account_id"], "100")

    def test_failed_database_commit_restores_old_session(self):
        result = prepared()
        result["entries"].append({"creator_id": "22", "name": "Duplicate"})
        with self.assertRaisesRegex(ValueError, "repeats"):
            activate_session(self.store, self.identifier, result)
        self.assert_old()
        self.store.db.execute(
            "CREATE TRIGGER reject_switch BEFORE UPDATE ON creator_policy WHEN NEW.account_id='200' BEGIN SELECT RAISE(ABORT,'fixture reject'); END"
        )
        self.store.db.commit()
        with self.assertRaises(Exception):
            activate_session(self.store, self.identifier, prepared())
        self.assert_old()

    def test_journal_recovery_rolls_back_uncommitted_moves(self):
        job = job_dir(self.store.root, self.identifier)
        atomic_json(
            self.base / "switch-commit.json",
            {
                "id": self.identifier,
                "account_id": "200",
                "had_profile": True,
                "had_status": True,
            },
        )
        (self.base / "profile").replace(job / "old-profile")
        (self.base / "status.json").replace(job / "old-status.json")
        (job / "profile").replace(self.base / "profile")
        recover_switch(self.store)
        self.assert_old()

    def test_committed_switch_with_cleanup_failure_can_finish_recovery(self):
        from broader_vids.evidence.account_switch import remove_private

        def fail_cleanup(root, target):
            if Path(target) == job_dir(root, self.identifier):
                raise OSError("Busy old browser files")
            return remove_private(root, target)

        with patch(
            "broader_vids.evidence.account_switch.remove_private",
            side_effect=fail_cleanup,
        ):
            with self.assertRaises(SwitchCleanupPending):
                activate_session(self.store, self.identifier, prepared())
        self.assertTrue((self.base / "switch-commit.json").exists())
        self.assertEqual(status(self.store)["bilibili_sync"]["account_id"], "200")
        with self.assertRaisesRegex(ValueError, "recovery"):
            with browser_lease(self.store.root):
                pass
        recover_switch(self.store)
        self.assertFalse(job_dir(self.store.root, self.identifier).exists())
        self.assertEqual(
            (self.base / "profile/new-session.txt").read_text(), "new-session"
        )

    def test_manager_success_and_cancellation_clean_workers_and_locks(self):
        def success(command, **kwargs):
            job = job_dir(self.store.root, command[-1])
            (job / "profile").mkdir()
            (job / "profile/new-session.txt").write_text(
                "worker-session", encoding="utf-8"
            )
            atomic_json(job / "result.json", prepared())
            return subprocess.CompletedProcess(command, 0, "", "")

        controller = AccountSwitch(self.store.root, runner=success)
        controller.start()
        controller.thread.join(timeout=5)
        self.assertEqual(controller.status()["status"], "completed")
        self.assertEqual(
            (self.base / "profile/new-session.txt").read_text(), "worker-session"
        )
        self.assertFalse(list(self.store.root.glob("*.lock")))
        self.assertFalse((self.base / "browser.lock").exists())
        started = threading.Event()

        def wait_for_cancel(command, *, cancelled, **kwargs):
            atomic_json(
                job_dir(self.store.root, command[-1]) / "progress.json",
                {"status": "awaiting_sign_in", "message": "Waiting"},
            )
            started.set()
            cancelled.wait(timeout=5)
            raise InterruptedError

        controller = AccountSwitch(self.store.root, runner=wait_for_cancel)
        controller.start()
        self.assertTrue(started.wait(timeout=3))
        with self.assertRaises(ValueError):
            controller.start()
        controller.confirm()
        controller.cancel()
        controller.thread.join(timeout=5)
        self.assertEqual(controller.status()["status"], "cancelled")
        self.assertEqual(
            (self.base / "profile/new-session.txt").read_text(), "worker-session"
        )
        self.assertFalse(job_dir(self.store.root, controller.status()["id"]).exists())
        self.assertFalse(list(self.store.root.glob("*.lock")))

    def test_busy_bilibili_work_rejects_start_without_browser_launch(self):
        for key in (platform_lock("bilibili"), SYNC_LOCK):
            with self.store.lease(key):
                controller = AccountSwitch(
                    self.store.root,
                    runner=lambda *a, **k: self.fail("Started a worker while busy"),
                )
                self.assertFalse(controller.status()["can_start"])
                with self.assertRaises(ValueError):
                    controller.start()
        with browser_lease(self.store.root):
            with self.assertRaises(ValueError):
                AccountSwitch(self.store.root).start()

    def test_shutdown_cancels_sign_in_and_removes_temporary_profile(self):
        started = threading.Event()

        def pending(command, *, cancelled, **kwargs):
            job = job_dir(self.store.root, command[-1])
            (job / "profile").mkdir()
            (job / "profile/private-session.txt").write_text("temporary")
            started.set()
            cancelled.wait(timeout=3)
            raise InterruptedError

        controller = AccountSwitch(self.store.root, runner=pending)
        controller.start()
        self.assertTrue(started.wait(timeout=3))
        controller.close()
        self.assertFalse(controller.thread.is_alive())
        self.assertFalse(controller.status()["can_start"])
        with self.assertRaises(ValueError):
            controller.start()
        self.assertEqual(controller.status()["status"], "cancelled")
        self.assertFalse(job_dir(self.store.root, controller.status()["id"]).exists())
        self.assert_old()

    def test_browser_lease_requires_matching_worker_owner(self):
        with browser_lease(self.store.root, token=self.identifier):
            with browser_lease(self.store.root, owner=self.identifier):
                self.assertTrue((self.base / "browser.lock").exists())
            with self.assertRaisesRegex(ValueError, "not owned"):
                with browser_lease(self.store.root, owner="b" * 32):
                    self.fail("An unrelated worker acquired the browser")
        self.assertFalse((self.base / "browser.lock").exists())

    def test_invalid_recovery_journal_preserves_both_session_profiles(self):
        journal = self.base / "switch-commit.json"
        for value in (
            {},
            {
                "id": self.identifier,
                "account_id": "200",
                "had_profile": "true",
                "had_status": True,
            },
            {
                "id": self.identifier,
                "account_id": "invalid",
                "had_profile": True,
                "had_status": True,
            },
        ):
            atomic_json(journal, value)
            with self.assertRaises(ValueError):
                recover_switch(self.store)
            self.assertTrue((self.base / "profile/old-session.txt").exists())
            self.assertTrue(
                (
                    job_dir(self.store.root, self.identifier)
                    / "profile/new-session.txt"
                ).exists()
            )
            self.assertEqual(status(self.store)["bilibili_sync"]["account_id"], "100")
            self.assertTrue(journal.exists())

    def test_invalid_worker_path_reports_failure_without_leaking_thread(self):
        controller = AccountSwitch(
            self.store.root, runner=lambda *a, **k: self.fail("Started unsafe worker")
        )
        with patch(
            "broader_vids.evidence.account_switch.job_dir",
            side_effect=ValueError("Unsafe worker path"),
        ):
            controller.start()
            controller.thread.join(timeout=3)
            self.assertFalse(controller.thread.is_alive())
            self.assertEqual(controller.status()["status"], "failed")
        self.assert_old()
