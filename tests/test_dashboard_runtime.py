from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids.evidence.service import atomic_json
from broader_vids.storage import Store
from broader_vids.web.runtime import active, dashboard, read_session, session_path
from broader_vids.web.server import make_server


@contextmanager
def local_server():
    with tempfile.TemporaryDirectory() as directory:
        with closing(Store(directory)):
            pass
        server = make_server(directory, 0)
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        thread.start()
        try:
            yield server, Path(directory)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
            if thread.is_alive():
                raise AssertionError("The local server did not stop.")


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
                with patch(
                    "broader_vids.web.runtime.webbrowser.open", return_value=True
                ) as launch:
                    opened = dashboard(root)
                launch.assert_called_once_with(results[0]["url"], new=2)
                self.assertTrue(opened["browser_opened"])
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

    def test_shutdown_waits_for_request_database_cleanup_before_account_close(self):
        entered, release, store_closed, close_done = (
            threading.Event() for _ in range(4)
        )
        errors = []

        class ObservedStore(Store):
            def close(self):
                super().close()
                store_closed.set()

        def held_state(store):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("The fixture request was not released.")
            return {"fixture": True}

        with local_server() as (server, root):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=10
            )
            original_close = server.account_switch.close

            def close_account():
                self.assertTrue(store_closed.is_set())
                original_close()

            def stop_server():
                try:
                    server.server_close()
                except BaseException as error:
                    errors.append(error)
                finally:
                    close_done.set()

            closer = threading.Thread(target=stop_server)
            with (
                patch("broader_vids.web.server.Store", ObservedStore),
                patch(
                    "broader_vids.web.server.dashboard_state", side_effect=held_state
                ),
                patch.object(server.account_switch, "close", side_effect=close_account),
            ):
                try:
                    connection.request("GET", "/api/state")
                    self.assertTrue(entered.wait(timeout=10))
                    server.shutdown()
                    closer.start()
                    self.assertEqual(connection.sock.recv(1), b"")
                    self.assertFalse(close_done.is_set())
                    self.assertFalse(store_closed.is_set())
                finally:
                    release.set()
                    if closer.ident is not None:
                        closer.join(timeout=10)
                    connection.close()
                self.assertFalse(closer.is_alive())
                if errors:
                    raise errors[0]
                self.assertTrue(store_closed.is_set())
                self.assertTrue(close_done.is_set())
                self.assertFalse(server._connections)
                database = root / "broader-vids.sqlite3"
                renamed = database.with_suffix(".closed")
                database.rename(renamed)
                renamed.rename(database)
