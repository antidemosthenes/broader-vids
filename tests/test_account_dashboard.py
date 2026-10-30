from pathlib import Path
import unittest
from unittest.mock import patch

from test_dashboard_feedback import (
    dashboard_fixture,
    open_first,
    sync_playwright,
    settle_layout,
)


class FakeAccountSwitch:
    def __init__(self, root):
        self.value = {"status": "idle", "active": False, "can_start": True}
        self.calls = []

    def status(self):
        return self.value.copy()

    def start(self):
        self.calls.append("start")
        self.value = {
            "id": "fixture",
            "status": "awaiting_sign_in",
            "active": True,
            "can_start": False,
            "message": "Sign in in the new window.",
        }
        return self.status()

    def confirm(self):
        self.calls.append("confirm")
        self.value.update(
            status="completed",
            active=False,
            can_start=True,
            message="Account switched.",
        )
        return self.status()

    def cancel(self):
        self.calls.append("cancel")
        self.value.update(
            status="cancelled",
            active=False,
            can_start=True,
            message="Switch cancelled.",
        )
        return self.status()

    def close(self):
        self.calls.append("close")


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class AccountDashboardTests(unittest.TestCase):
    def test_switch_controls_keep_drafts_and_require_local_action_token(self):
        controllers = []

        def controller(root):
            value = FakeAccountSwitch(root)
            controllers.append(value)
            return value

        with patch(
            "broader_vids.evidence.account_switch.AccountSwitch", side_effect=controller
        ):
            with dashboard_fixture() as (page, _):
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.request.post(
                    page.url + "api/bilibili/account", data={"operation": "start"}
                )
                self.assertEqual(response.status, 403)
                inspector = open_first(page)
                topic = inspector.get_by_role(
                    "group", name="Specific topic", exact=True
                )
                topic.get_by_label("Topic", exact=True).fill(
                    "Draft survives account switch"
                )
                topic.get_by_role("radio", name="Low", exact=True).check()
                nav = page.get_by_role("navigation")
                nav.get_by_role("button", name="Exclusions", exact=True).click()
                self.assertEqual(
                    page.get_by_role(
                        "button", name="Switch account", exact=True
                    ).count(),
                    0,
                )
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                switch = page.get_by_role("button", name="Switch account", exact=True)
                switch.click()
                confirm = page.get_by_role("button", name="I've signed in", exact=True)
                confirm.wait_for()
                self.assertTrue(
                    page.get_by_role(
                        "button", name="Sync blacklist", exact=True
                    ).is_disabled()
                )
                page.get_by_role("button", name="Cancel switch", exact=True).click()
                page.get_by_text("Switch cancelled.", exact=True).wait_for()
                switch.click()
                confirm.click()
                page.get_by_text("Account switched.", exact=True).wait_for()
                nav.get_by_role("button", name="Recommendations", exact=True).click()
                page.get_by_role("tab", name="YouTube", exact=True).click()
                self.assertEqual(
                    topic.get_by_label("Topic", exact=True).input_value(),
                    "Draft survives account switch",
                )
                self.assertTrue(
                    topic.get_by_role("radio", name="Low", exact=True).is_checked()
                )
                nav.get_by_role("button", name="Exclusions", exact=True).click()
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                output = Path(".cache/account-switch-ui")
                output.mkdir(parents=True, exist_ok=True)
                for theme in ("light", "dark"):
                    page.emulate_media(color_scheme=theme)
                    for name, width, height in (
                        ("desktop", 1440, 900),
                        ("mobile", 390, 844),
                    ):
                        page.set_viewport_size({"width": width, "height": height})
                        settle_layout(
                            page,
                            path=str(output / f"{name}-{theme}.png"),
                            full_page=True,
                        )
                        self.assertTrue(
                            page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                        )
                self.assertEqual(
                    controllers[0].calls, ["start", "cancel", "start", "confirm"]
                )
                self.assertFalse(errors)
            self.assertEqual(controllers[0].calls[-1], "close")

    def test_failed_switch_status_enables_retry_and_preserves_rating_draft(self):
        with patch(
            "broader_vids.evidence.account_switch.AccountSwitch", FakeAccountSwitch
        ):
            with dashboard_fixture() as (page, _):
                inspector = open_first(page)
                topic = inspector.get_by_role(
                    "group", name="Specific topic", exact=True
                )
                topic.get_by_label("Topic", exact=True).fill("Unsaved failure test")
                page.get_by_role("navigation").get_by_role(
                    "button", name="Exclusions", exact=True
                ).click()
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                switch = page.get_by_role("button", name="Switch account", exact=True)
                switch.click()
                page.get_by_role("button", name="I've signed in", exact=True).wait_for()
                page.route(
                    "**/api/bilibili/account",
                    lambda route: route.fulfill(
                        json={
                            "id": "fixture",
                            "status": "failed",
                            "active": False,
                            "can_start": True,
                            "message": "Sign-in timed out. Retry the switch.",
                        }
                    )
                    if route.request.method == "GET"
                    else route.continue_(),
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.get_by_text(
                    "Sign-in timed out. Retry the switch.", exact=True
                ).wait_for()
                self.assertTrue(switch.is_enabled())
                self.assertTrue(
                    page.get_by_role(
                        "button", name="Sync blacklist", exact=True
                    ).is_enabled()
                )
                self.assertFalse(
                    page.get_by_role(
                        "button", name="Cancel switch", exact=True
                    ).is_visible()
                )
                switch.click()
                page.get_by_role("button", name="I've signed in", exact=True).wait_for()
                page.get_by_role("navigation").get_by_role(
                    "button", name="Recommendations", exact=True
                ).click()
                page.get_by_role("tab", name="YouTube", exact=True).click()
                self.assertEqual(
                    topic.get_by_label("Topic", exact=True).input_value(),
                    "Unsaved failure test",
                )

    def test_stalled_requests_time_out_and_failure_checks_still_apply(self):
        with dashboard_fixture() as (page, _):
            stalled = []
            page.route("**/api/stalled", lambda route: stalled.append(route))
            message = page.evaluate("""async () => {
                const {get} = await import('/static/api.js');
                try { await get('/api/stalled', {timeoutMs: 100}); }
                catch(error) { return error.message; }
            }""")
            self.assertIn("timed out", message)
            for route in stalled:
                route.abort()
            page.route(
                "**/api/failed",
                lambda route: route.fulfill(
                    json={"status": "failed", "error": "Fixture operation failed"}
                ),
            )
            value = page.evaluate("""async () => {
                const {get} = await import('/static/api.js');
                let error;
                try { await get('/api/failed'); } catch(value) { error = value.message; }
                const status = await get('/api/failed', {allowFailedStatus: true});
                return {error, status: status.status};
            }""")
            self.assertEqual(
                value, {"error": "Fixture operation failed", "status": "failed"}
            )
            page.route(
                "**/api/forbidden",
                lambda route: route.fulfill(
                    status=403, json={"error": "Forbidden fixture"}
                ),
            )
            message = page.evaluate("""async () => {
                const {get} = await import('/static/api.js');
                try { await get('/api/forbidden', {allowFailedStatus: true}); }
                catch(error) { return error.message; }
            }""")
            self.assertEqual(message, "Forbidden fixture")
