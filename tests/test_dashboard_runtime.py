from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import webbrowser
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

    def test_assets_reuse_one_connection_and_idle_clients_close_on_shutdown(self):
        with local_server() as (server, _):
            with patch.object(
                server, "get_request", wraps=server.get_request
            ) as accept:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=10
                )
                try:
                    original = None
                    for _ in range(3):
                        for asset in (
                            "/",
                            "/static/dashboard.js",
                            "/static/review.js",
                            "/static/preferences.js",
                        ):
                            connection.request("GET", asset)
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.version, 11)
                            response.read()
                            if original is None:
                                original = connection.sock
                            self.assertIs(connection.sock, original)
                    self.assertEqual(accept.call_count, 1)
                    server.shutdown()
                    server.server_close()
                    self.assertEqual(original.recv(1), b"")
                    self.assertFalse(server._connections)
                finally:
                    connection.close()

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

    def test_rejected_post_framing_closes_connection_without_consuming_later_requests(
        self,
    ):
        import re

        with local_server() as (server, _):
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=10
            )
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                page = response.read().decode()
                token = re.search(r'name="broader-vids-token" content="([^"]+)"', page)[
                    1
                ]
                for headers in (
                    [("Content-Length", "2"), ("Content-Length", "3")],
                    [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
                ):
                    connection.putrequest("POST", "/api/interests")
                    connection.putheader("X-Broader-Vids-Token", token)
                    for name, value in headers:
                        connection.putheader(name, value)
                    connection.endheaders(b"{}")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertEqual(response.getheader("Connection"), "close")
                    self.assertIn("Content-Length", response.read().decode())
                    self.assertIsNone(connection.sock)
            finally:
                connection.close()
