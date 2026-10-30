from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import unittest
import webbrowser
from unittest.mock import patch

from broader_vids.evidence.service import atomic_json
from broader_vids.storage import Store
from broader_vids.web.runtime import active, dashboard, read_session, session_path


class DashboardRuntimeTests(unittest.TestCase):
    def test_reuses_one_server_and_stops_only_the_verified_profile(self):
        with tempfile.TemporaryDirectory(prefix="broader-vids space & ") as directory:
            root = Path(directory)
            with closing(Store(root)):
                pass
            original_session = None
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(
                        pool.map(
                            lambda _: dashboard(root, port=0, open_browser=False),
                            range(2),
                        )
                    )
                self.assertEqual(results[0]["url"], results[1]["url"])
                self.assertEqual(results[0]["pid"], results[1]["pid"])
                self.assertNotIn("stop_token", json.dumps(results))
                session = read_session(root)
                original_session = session
                for token in ("wrong", "é"):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", session["port"]
                    )
                    connection.request(
                        "POST",
                        "/api/shutdown",
                        headers={"X-Broader-Vids-Control": token},
                    )
                    self.assertEqual(connection.getresponse().status, 403)
                    connection.close()
                self.assertTrue(active(session))
                fake = {**session, "instance_id": "0" * 32}
                atomic_json(session_path(root), fake)
                self.assertEqual(dashboard(root, "stop")["status"], "stopped")
                self.assertTrue(active(session))
                atomic_json(session_path(root), session)
                with tempfile.TemporaryDirectory() as other:
                    try:
                        second = dashboard(
                            other, port=session["port"], open_browser=False
                        )
                        self.assertNotEqual(second["url"], results[0]["url"])
                        self.assertEqual(dashboard(other, "stop")["status"], "stopped")
                        self.assertTrue(active(session))
                    finally:
                        dashboard(other, "stop")
                self.assertEqual(dashboard(root, "stop")["status"], "stopped")
                self.assertEqual(dashboard(root, "status")["status"], "stopped")
            finally:
                if original_session:
                    atomic_json(session_path(root), original_session)
                dashboard(root, "stop")

    def test_system_browser_launch_and_failures_preserve_the_ready_server(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                ready = dashboard(directory, port=0, open_browser=False)
                for response in (
                    True,
                    False,
                    OSError("Fixture system association failure"),
                    webbrowser.Error("Fixture browser unavailable"),
                ):
                    with self.subTest(response=type(response).__name__):
                        options = (
                            {"side_effect": response}
                            if isinstance(response, Exception)
                            else {"return_value": response}
                        )
                        with patch(
                            "broader_vids.web.runtime.webbrowser.open", **options
                        ) as launch:
                            result = dashboard(directory)
                        launch.assert_called_once_with(ready["url"], new=2)
                        self.assertEqual(result["url"], ready["url"])
                        self.assertEqual(result["pid"], ready["pid"])
                        self.assertEqual(result["browser_opened"], response is True)
                        self.assertEqual(
                            result["browser_status"],
                            "launch_requested"
                            if response is True
                            else "manual_open_required",
                        )
                        self.assertTrue(active(read_session(directory)))
                        if response is not True:
                            self.assertIn("server is ready", result["browser_message"])
                with patch("broader_vids.web.runtime.webbrowser.open") as launch:
                    result = dashboard(directory, open_browser=False)
                launch.assert_not_called()
                self.assertNotIn("browser_status", result)
            finally:
                dashboard(directory, "stop")

    def test_corrupt_registry_and_missing_server_are_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_path(root).parent.mkdir()
            session_path(root).write_text("{broken", encoding="utf-8")
            self.assertEqual(dashboard(root, "status")["status"], "stopped")
            try:
                self.assertEqual(
                    dashboard(root, port=0, open_browser=False)["status"], "running"
                )
            finally:
                dashboard(root, "stop")
            with self.assertRaises(ValueError):
                dashboard(root, port=-1)
