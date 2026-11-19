from contextlib import closing
from http.client import HTTPConnection
import json
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from broader_vids import notifications
from broader_vids.storage import Store
from broader_vids.web.server import make_server
from test_dashboard_feedback import dashboard_fixture, expect, sync_playwright
from test_learning import example


class NotificationsApiTests(unittest.TestCase):
    def test_authenticates_local_requests_and_keeps_topic_out_of_responses(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            make_server(directory, 0) as server,
        ):
            thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
            )
            thread.start()
            try:
                with closing(HTTPConnection("127.0.0.1", server.server_port)) as client:

                    def request(path, body=None, headers=None):
                        client.request(
                            "GET" if body is None else "POST",
                            path,
                            body=None if body is None else json.dumps(body),
                            headers=headers or {},
                        )
                        response = client.getresponse()
                        return response.status, response.read().decode()

                    status, page = request("/")
                    self.assertEqual(status, 200)
                    token = re.search(
                        r'name="broader-vids-token" content="([^"]+)"', page
                    )[1]
                    authenticated = {"X-Broader-Vids-Token": token}
                    wrong_origin = {**authenticated, "Origin": "https://example.com"}
                    endpoint = "/api/notifications"
                    self.assertEqual(request(endpoint)[0], 404)
                    for path, body in (
                        (endpoint, {}),
                        (endpoint, {"enabled": True, "topic": "fixture-topic"}),
                        (f"{endpoint}/retry", {"run_id": "fixture"}),
                    ):
                        self.assertEqual(request(path, body)[0], 403)
                        self.assertEqual(request(path, body, wrong_origin)[0], 403)

                    topic = "fixture-private-topic"
                    with patch.object(notifications, "_send") as send:
                        status, data = request(
                            endpoint,
                            {"enabled": True, "topic": f"https://ntfy.sh/{topic}"},
                            authenticated,
                        )
                        send.assert_not_called()
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        json.loads(data),
                        {"enabled": True, "has_topic": True, "deliveries": []},
                    )
                    with closing(Store(directory)) as store:
                        self.assertEqual(notifications.settings(store)["topic"], topic)
                    for path, body in (
                        (endpoint, {}),
                        ("/api/state", None),
                        ("/api/preferences/export", {}),
                    ):
                        status, data = request(path, body, authenticated)
                        self.assertEqual(status, 200)
                        self.assertNotIn(topic, data)
            finally:
                server.shutdown()
                thread.join(timeout=3)


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class NotificationsDashboardTests(unittest.TestCase):
    def test_keeps_draft_private_and_retries_only_on_request(self):
        with (
            patch.object(notifications, "_send", notifications._worker),
            patch.object(
                notifications, "_post", side_effect=["failed", "uncertain", "sent"]
            ) as send,
            dashboard_fixture() as (page, directory),
        ):
            page.set_viewport_size({"width": 390, "height": 844})
            page.emulate_media(color_scheme="dark", reduced_motion="reduce")
            open_preferences = page.get_by_role(
                "button", name="Preferences", exact=True
            )
            open_preferences.click()
            panel = page.get_by_role("region", name="Notifications", exact=True)
            toggle = panel.get_by_role(
                "switch", name="Send new recommendations to ntfy"
            )
            field = panel.get_by_label("ntfy topic", exact=True)
            save = panel.get_by_role("button", name="Save notifications")
            expect(toggle).to_be_enabled()
            expect(toggle).not_to_be_checked()
            self.assertTrue(
                panel.evaluate("(panel) => panel.scrollWidth <= panel.clientWidth")
            )
            expect(field).to_be_hidden()

            toggle.press("Space")
            expect(field).to_be_focused()
            invalid = "https://example.com/private-fixture-topic"
            field.fill(invalid)
            save.click()
            expect(panel.get_by_role("status")).to_contain_text("Use an ntfy.sh topic")
            expect(field).to_have_value(invalid)
            expect(save).to_be_focused()
            send.assert_not_called()

            destination = "fixture-private-topic"
            field.fill(destination)
            save.click()
            expect(panel.get_by_role("status")).to_have_text("Notifications saved.")
            expect(toggle).to_be_checked()
            expect(toggle).to_be_focused()
            expect(field).to_have_value("")
            expect(field).to_have_attribute("type", "password")
            expect(field).to_have_attribute("placeholder", "Saved topic")
            send.assert_not_called()
            self.assertNotIn(destination, page.content())

            with closing(Store(directory)) as store:
                run_id = store.create(
                    "youtube",
                    {"kind": "recommendation"},
                    {
                        "phase": "published",
                        "selected": [example(42)],
                        "shortlist": [example(42)],
                        "published_at": "2026-11-19T12:00:00Z",
                        "evidence": {},
                        "issues": [],
                    },
                )
                with store.db:
                    notifications.enqueue(store, store.get(run_id))
                notifications.deliver(store, run_id)
            self.assertEqual(send.call_count, 1)
            page.evaluate('() => document.dispatchEvent(new Event("visibilitychange"))')
            recent = panel.get_by_role("region", name="Recent deliveries")
            retry = recent.get_by_role("button", name=re.compile(r"^Retry YouTube"))
            expect(recent.get_by_text("Delivery failed", exact=True)).to_be_visible()
            retry.click()
            expect(
                recent.get_by_text("Delivery unconfirmed", exact=True)
            ).to_be_visible()
            expect(recent.get_by_text("Retrying may send a duplicate.")).to_be_visible()
            retry.click()
            expect(recent.get_by_text("Sent", exact=True)).to_be_visible()
            expect(retry).to_have_count(0)
            self.assertEqual(send.call_count, 3)
            self.assertTrue(
                all(call.args[0] == destination for call in send.call_args_list)
            )

            toggle.click()
            expect(panel.get_by_role("status")).to_have_text(
                "Notifications turned off."
            )
            with closing(Store(directory)) as store:
                self.assertFalse(notifications.settings(store)["enabled"])
                self.assertEqual(notifications.settings(store)["topic"], destination)
            panel.get_by_role("button", name="Clear saved topic").click()
            expect(panel.get_by_role("status")).to_have_text("Saved topic cleared.")
            expect(field).to_be_hidden()
            expect(toggle).to_be_focused()
            with closing(Store(directory)) as store:
                self.assertEqual(notifications.settings(store)["topic"], "")
            self.assertEqual(send.call_count, 3)
