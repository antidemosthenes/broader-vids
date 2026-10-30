from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from broader_vids.evidence.account_switch import (
    job_dir,
    prepare_login,
    verified_account,
)
from broader_vids.evidence.service import atomic_json


class AccountLoginTests(unittest.TestCase):
    def test_authentication_requires_valid_signed_in_identity(self):
        context = SimpleNamespace(request=SimpleNamespace(get=lambda *a, **k: response))
        response = SimpleNamespace(ok=True, json=lambda: payload)
        for payload in (
            {"code": True},
            {"code": 0, "data": {}},
            {"code": 0, "data": {"isLogin": True, "mid": 0}},
        ):
            with self.assertRaises(ValueError):
                verified_account(context)
        payload = {
            "code": 0,
            "data": {"isLogin": True, "mid": 200, "uname": "New account"},
        }
        self.assertEqual(
            verified_account(context), {"id": "200", "name": "New account"}
        )

    def test_fresh_profile_confirmation_retry_and_closed_browser_before_result(self):
        with tempfile.TemporaryDirectory() as root:
            identifier = "b" * 32
            job = job_dir(root, identifier)
            job.mkdir(parents=True)
            checks, closed = [], []

            class Page:
                def goto(self, *args, **kwargs):
                    checks.append(args[0])

                def is_closed(self):
                    return False

                def wait_for_timeout(self, value):
                    (job / "confirm.request").write_text("confirm")

            page = Page()
            context = SimpleNamespace(pages=[page])

            @contextmanager
            def browser(*args, **kwargs):
                self.assertTrue(kwargs["visible"])
                self.assertEqual(kwargs["lease_owner"], identifier)
                self.assertEqual(kwargs["profile_path"], job / "profile")
                self.assertFalse(kwargs["profile_path"].exists())
                yield context
                closed.append(True)

            def sync(store, **kwargs):
                self.assertEqual(Path(store.root), job / "rules")
                self.assertIs(kwargs["context"], context)
                with store.db:
                    store.db.execute(
                        "INSERT OR REPLACE INTO creator_exclusions VALUES('bilibili','22','bilibili_blacklist','200','Blocked','fixture')"
                    )
                return {"status": "complete", "account_id": "200"}

            account = {"id": "200", "name": "New account"}
            original = atomic_json

            def write(path, data):
                if path.name == "result.json":
                    self.assertTrue(closed)
                return original(path, data)

            with (
                patch("broader_vids.evidence.account_switch.bilibili_browser", browser),
                patch(
                    "broader_vids.evidence.account_switch.verified_account",
                    side_effect=[
                        ValueError("Not yet"),
                        account,
                        {"id": "300", "name": "Changed during sync"},
                        account,
                        account,
                    ],
                ),
                patch(
                    "broader_vids.evidence.account_switch.sync_blacklist",
                    side_effect=sync,
                ),
                patch(
                    "broader_vids.evidence.account_switch.atomic_json",
                    side_effect=write,
                ),
            ):
                prepare_login(root, identifier, timeout=3)
            result = json.loads((job / "result.json").read_text())
            self.assertEqual(result["account"], account)
            self.assertEqual(
                result["entries"], [{"creator_id": "22", "name": "Blocked"}]
            )
            self.assertEqual(checks, ["https://www.bilibili.com/"])
            self.assertFalse((Path(root) / "access/bilibili/profile").exists())

    def test_closed_window_and_timeout_do_not_publish_session(self):
        with tempfile.TemporaryDirectory() as root:
            identifier = "c" * 32
            job = job_dir(root, identifier)
            job.mkdir(parents=True)
            page = SimpleNamespace(goto=lambda *a, **k: None)
            context = SimpleNamespace(pages=[page])

            @contextmanager
            def browser(*args, **kwargs):
                context.pages = []
                context.new_page = lambda: page
                yield context

            with patch(
                "broader_vids.evidence.account_switch.bilibili_browser", browser
            ):
                with self.assertRaisesRegex(ValueError, "closed"):
                    prepare_login(root, identifier, timeout=3)
                with self.assertRaisesRegex(ValueError, "timed out"):
                    prepare_login(root, identifier, timeout=0)
            self.assertFalse((job / "result.json").exists())
