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
                        (f"{endpoint}/test", {}),
                        (
                            f"{endpoint}/send",
                            {"platform": "youtube", "run_id": "a" * 32},
                        ),
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
                    saved = json.loads(data)
                    destination_id = saved.pop("destination_id")
                    self.assertRegex(destination_id, r"^[a-f0-9]{64}$")
                    self.assertEqual(
                        saved,
                        {
                            "enabled": True,
                            "has_topic": True,
                            "deliveries": [],
                            "topic_url": f"https://ntfy.sh/{topic}",
                        },
                    )
                    with closing(Store(directory)) as store:
                        self.assertEqual(notifications.settings(store)["topic"], topic)
                    self.assertTrue(
                        json.loads(request("/api/state")[1])["notifications_active"]
                    )
                    for action, body in (
                        ("test", {"topic": topic}),
                        ("test", {"destination_id": "数" * 64}),
                        ("send", {"platform": [], "run_id": "a" * 32}),
                    ):
                        self.assertEqual(
                            request(f"{endpoint}/{action}", body, authenticated)[0], 400
                        )
                    status, data = request(
                        endpoint,
                        {"enabled": False, "topic": None},
                        authenticated,
                    )
                    self.assertEqual(status, 200)
                    self.assertNotIn("topic_url", json.loads(data))
                    self.assertNotIn("destination_id", json.loads(data))
                    self.assertNotIn(topic, data)
                    status, data = request(
                        f"{endpoint}/test",
                        {"destination_id": destination_id},
                        authenticated,
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(data), {"outcome": "inactive"})
                    for path, body in (
                        ("/api/state", None),
                        ("/api/preferences/export", {}),
                    ):
                        status, data = request(path, body, authenticated)
                        self.assertEqual(status, 200)
                        self.assertNotIn(topic, data)
                        self.assertNotIn(destination_id, data)
            finally:
                server.shutdown()
                thread.join(timeout=3)


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class NotificationsDashboardTests(unittest.TestCase):
    def test_send_uses_selected_published_platform_and_keeps_navigation_usable(self):
        started = threading.Event()
        resume = threading.Event()

        def send(payload, timeout):
            self.assertGreater(timeout, 0)
            title = payload[3]
            if "YouTube" in title:
                started.set()
                if not resume.wait(8):
                    raise AssertionError("The test did not release the send.")
                return "sent"
            return "uncertain"

        with (
            patch.object(notifications, "_send_in_worker", side_effect=send) as worker,
            dashboard_fixture() as (page, directory),
        ):
            page.set_viewport_size({"width": 390, "height": 844})
            page.emulate_media(
                color_scheme="light", forced_colors="none", reduced_motion="reduce"
            )
            button = page.get_by_role("button", name="Send to ntfy", exact=True)
            expect(button).to_be_hidden()
            with closing(Store(directory)) as store:
                notifications.configure(store, True, "fixture-topic")
            page.evaluate('() => document.dispatchEvent(new Event("visibilitychange"))')
            expect(button).to_be_visible()
            expect(button).to_have_css("border-top-color", "rgb(114, 129, 118)")
            try:
                button.press("Enter")
                self.assertTrue(started.wait(3))
                sending = page.get_by_role("button", name="Sending…", exact=True)
                expect(sending).to_be_disabled()
                sending.hover()
                expect(sending).to_have_css("box-shadow", "none")
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                expect(button).to_be_enabled()
                button.click()
                status = page.locator("#send-ntfy-status")
                expect(status).to_contain_text("Delivery could not be confirmed.")
                expect(status).to_contain_text("ntfy confirmed 0 of 1 parts.")
                expect(status).to_contain_text("Sending again may send a duplicate.")
                page.get_by_role("tab", name="YouTube", exact=True).click()
                expect(sending).to_be_disabled()
            finally:
                resume.set()
            expect(status).to_have_text("ntfy accepted the recommendation list.")
            self.assertEqual(worker.call_count, 2)
            self.assertIn("youtube.com/watch", worker.call_args_list[0].args[0][4])
            self.assertIn("bilibili.com/video", worker.call_args_list[1].args[0][4])
            page.evaluate("document.documentElement.style.fontSize = '200%'")
            self.assertTrue(
                page.locator(".list-toolbar").evaluate(
                    "(toolbar) => toolbar.scrollWidth <= toolbar.clientWidth"
                )
            )
            expect(button).to_be_visible()
            page.get_by_role("button", name=re.compile(r"^Earlier unwatched")).click()
            expect(button).to_be_hidden()
            page.get_by_role("button", name="Calibration", exact=True).click()
            expect(button).to_be_hidden()
            with closing(Store(directory)) as store:
                self.assertEqual(notifications.settings(store)["deliveries"], [])

    def test_test_status_tracks_the_displayed_topic(self):
        for scenario in ("completed", "in_flight", "disabled", "same", "newer_action"):
            with self.subTest(scenario=scenario):
                started = threading.Event()
                release = threading.Event()

                def send(payload, timeout):
                    started.set()
                    if scenario != "completed" and not release.wait(8):
                        raise AssertionError("The test did not release the sender.")
                    return "failed" if scenario == "newer_action" else "sent"

                with (
                    dashboard_fixture() as (page, directory),
                    closing(Store(directory)) as store,
                    patch.object(
                        notifications, "_send_in_worker", side_effect=send
                    ) as worker,
                ):
                    notifications.configure(store, True, "topic-a")
                    page.get_by_role("button", name="Preferences", exact=True).click()
                    address = page.locator("#notifications-topic-url")
                    status = page.locator("#notifications-status")
                    field = page.locator("#notifications-topic")
                    test = page.locator("#notifications-test")
                    expect(address).to_have_text("https://ntfy.sh/topic-a")
                    field.fill("unsaved-draft")
                    test.click()
                    self.assertTrue(started.wait(3))
                    try:
                        if scenario == "completed":
                            expect(status).to_have_text(
                                "ntfy accepted the test message."
                            )
                        elif scenario == "newer_action":
                            page.locator("#notifications-clear").click()
                            expect(status).to_have_text("Saved topic cleared.")
                        if scenario not in ("same", "newer_action"):
                            notifications.configure(
                                store, scenario != "disabled", "topic-b"
                            )
                        page.evaluate(
                            '() => document.dispatchEvent(new Event("visibilitychange"))'
                        )
                        if scenario in ("same", "completed", "in_flight"):
                            expected = "topic-a" if scenario == "same" else "topic-b"
                            expect(address).to_have_text(f"https://ntfy.sh/{expected}")
                        else:
                            expect(
                                page.locator("#notifications-active-address")
                            ).to_be_hidden()
                        if scenario in ("completed", "in_flight", "disabled"):
                            expect(status).to_be_empty()
                            expect(field).to_have_value("unsaved-draft")
                    finally:
                        release.set()

                    expect(test).to_have_text("Test")
                    expect(test).to_have_attribute("aria-disabled", "false")
                    if scenario == "same":
                        expect(status).to_have_text("ntfy accepted the test message.")
                        expect(field).to_have_value("unsaved-draft")
                    elif scenario == "newer_action":
                        expect(status).to_have_text("Saved topic cleared.")
                    else:
                        expect(status).to_be_empty()
                    self.assertEqual(worker.call_count, 1)
                    self.assertEqual(worker.call_args.args[0][2], "topic-a")

    def test_changed_topic_is_reported_after_refresh_while_test_is_pending(self):
        with (
            dashboard_fixture() as (page, directory),
            closing(Store(directory)) as store,
            patch.object(notifications, "_send_in_worker") as worker,
        ):
            notifications.configure(store, True, "topic-a")
            page.get_by_role("button", name="Preferences", exact=True).click()
            address = page.locator("#notifications-topic-url")
            expect(address).to_have_text("https://ntfy.sh/topic-a")
            field = page.locator("#notifications-topic")
            field.fill("unsaved-draft")
            page.evaluate("""() => {
                const original = window.fetch.bind(window);
                const gate = new Promise(resolve => window.releaseTest = resolve);
                window.fetch = async (...args) => {
                    if (args[0] === "/api/notifications/test") await gate;
                    return original(...args);
                };
            }""")
            test = page.locator("#notifications-test")
            status = page.locator("#notifications-status")
            test.click()
            expect(test).to_have_text("Testing…")
            notifications.configure(store, True, "topic-b")
            page.evaluate('() => document.dispatchEvent(new Event("visibilitychange"))')
            expect(address).to_have_text("https://ntfy.sh/topic-b")
            expect(test).to_have_text("Test")
            expect(test).to_have_attribute("aria-disabled", "true")
            page.evaluate("() => window.releaseTest()")
            expect(status).to_have_text("The active topic changed. Test it again.")
            expect(test).to_have_attribute("aria-disabled", "false")
            expect(field).to_have_value("unsaved-draft")
            worker.assert_not_called()

    def test_stale_test_and_lost_delivery_responses(self):
        with (
            dashboard_fixture() as (page, directory),
            closing(Store(directory)) as store,
            patch.object(
                notifications, "_send_in_worker", return_value="sent"
            ) as worker,
        ):
            notifications.configure(store, True, "shown-topic")
            page.get_by_role("button", name="Preferences", exact=True).click()
            topic = page.locator("#notifications-topic-url")
            expect(topic).to_have_text("https://ntfy.sh/shown-topic")
            field = page.locator("#notifications-topic")
            field.fill("unsaved-topic")
            test = page.locator("#notifications-test")
            status = page.locator("#notifications-status")

            def change_topic_before_test(route):
                notifications.configure(store, True, "other-topic")
                route.continue_()

            def lose_response(route):
                response = route.fetch()
                try:
                    self.assertEqual(response.json()["outcome"], "sent")
                finally:
                    response.dispose()
                    route.abort()

            def reject_send(route):
                route.fulfill(status=400, json={"error": "Fixture server error."})

            test_steps = iter((change_topic_before_test, lose_response))
            send_steps = iter((lose_response, reject_send))
            page.route(
                "**/api/notifications/test", lambda route: next(test_steps)(route)
            )
            page.route(
                "**/api/notifications/send", lambda route: next(send_steps)(route)
            )
            test.click()
            expect(status).to_have_text("The active topic changed. Test it again.")
            expect(topic).to_have_text("https://ntfy.sh/other-topic")
            expect(field).to_have_value("unsaved-topic")
            worker.assert_not_called()

            uncertain = (
                "Delivery could not be confirmed. Sending again may send a duplicate."
            )
            test.click()
            expect(status).to_have_text(uncertain)
            expect(field).to_have_value("unsaved-topic")
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(worker.call_args.args[0][2], "other-topic")

            page.get_by_role("button", name="Recommendations", exact=True).click()
            send = page.locator("#send-ntfy")
            send_status = page.locator("#send-ntfy-status")
            send.click()
            expect(send_status).to_have_text(uncertain)
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(worker.call_args.args[0][2], "other-topic")

            send.click()
            expect(send_status).to_have_text("Fixture server error.")
            self.assertEqual(worker.call_count, 2)

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
            field = panel.get_by_label("New ntfy topic or URL", exact=True)
            active_address = panel.locator("#notifications-active-address")
            topic_url = panel.locator("#notifications-topic-url")
            save = panel.get_by_role("button", name="Save", exact=True)
            test = panel.get_by_role("button", name="Test", exact=True)
            expect(test).to_be_hidden()
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
            expect(panel.locator("#notifications-status")).to_be_hidden()
            expect(toggle).to_be_checked()
            expect(toggle).to_be_focused()
            expect(field).to_have_value("")
            expect(field).to_have_attribute("type", "text")
            self.assertIsNone(field.get_attribute("placeholder"))
            expect(active_address).to_be_visible()
            expect(topic_url).to_have_text(f"https://ntfy.sh/{destination}")
            send.assert_not_called()
            expect(field).to_have_value("")

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
            topic_url.evaluate(
                "(address) => window.getSelection().selectAllChildren(address)"
            )
            page.evaluate('() => document.dispatchEvent(new Event("visibilitychange"))')
            recent = panel.get_by_role("region", name="Recent deliveries")
            retry = recent.get_by_role("button", name=re.compile(r"^Retry YouTube"))
            expect(recent.get_by_text("Delivery failed", exact=True)).to_be_visible()
            self.assertEqual(
                page.evaluate("window.getSelection().toString()"),
                f"https://ntfy.sh/{destination}",
            )
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

            field.fill("unsaved-replacement")
            with patch.object(
                notifications, "_send_in_worker", return_value="sent"
            ) as manual:
                test.press("Enter")
                expect(panel.get_by_role("status")).to_have_text(
                    "ntfy accepted the test message."
                )
                expect(test).to_be_focused()
                expect(field).to_have_value("unsaved-replacement")
                self.assertEqual(
                    manual.call_args.args[0][2:],
                    [
                        destination,
                        "Broader Vids - Test",
                        "This is a test notification from Broader Vids.",
                    ],
                )
            toggle.click()
            expect(panel.get_by_role("status")).to_have_text(
                "Notifications turned off."
            )
            expect(active_address).to_be_hidden()
            expect(topic_url).to_have_text("")
            with closing(Store(directory)) as store:
                self.assertFalse(notifications.settings(store)["enabled"])
                self.assertEqual(notifications.settings(store)["topic"], destination)
            expect(test).to_be_hidden()
            panel.get_by_role("button", name="Clear", exact=True).click()
            expect(panel.get_by_role("status")).to_have_text("Saved topic cleared.")
            expect(field).to_be_hidden()
            expect(toggle).to_be_focused()
            with closing(Store(directory)) as store:
                self.assertEqual(notifications.settings(store)["topic"], "")
            self.assertEqual(send.call_count, 3)
