from contextlib import closing
import unittest

from broader_vids.storage import Store
from test_dashboard_feedback import (
    dashboard_fixture,
    open_first,
    saved,
    sync_playwright,
)


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class CreatorDashboardTests(unittest.TestCase):
    def test_creator_changes_do_not_overlap_across_navigation_and_keep_drafts(self):
        with dashboard_fixture() as (page, _):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("Draft across creator update")
            checkbox = inspector.get_by_role(
                "checkbox", name="Exclude this creator from future rounds"
            )
            checkbox.check()
            page.wait_for_function(
                "() => { const input = document.querySelector('.creator-rule input'); return input.checked && !input.disabled; }"
            )

            pending = []

            def hold_creator_update(route):
                if route.request.post_data_json["action"] == "creator_rule":
                    pending.append(route)
                else:
                    route.continue_()

            page.route("**/api/action", hold_creator_update)
            try:
                with page.expect_request("**/api/action"):
                    checkbox.uncheck()
                self.assertTrue(checkbox.is_disabled())
                page.get_by_role("navigation").get_by_role(
                    "button", name="Exclusions", exact=True
                ).click()
                remove = page.get_by_role("button", name="Remove", exact=True)
                self.assertTrue(remove.is_disabled())
                self.assertEqual(len(pending), 1)
                pending.pop().continue_()
                page.get_by_text(
                    "No creator exclusions on YouTube.", exact=True
                ).wait_for()
            finally:
                for route in pending:
                    route.abort()
                page.unroute("**/api/action", hold_creator_update)

            page.get_by_role("navigation").get_by_role(
                "button", name="Recommendations", exact=True
            ).click()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "Draft across creator update",
            )

    def test_exclusions_share_state_preserve_drafts_and_restore_failed_updates(self):
        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("corrected topic")
            topic.get_by_role("radio", name="Low", exact=True).check()
            checkbox = inspector.get_by_role(
                "checkbox", name="Exclude this creator from future rounds"
            )
            checkbox.check()
            page.wait_for_function(
                "() => !document.querySelector('.creator-rule input').disabled"
            )
            self.assertTrue(checkbox.is_checked())
            self.assertEqual(page.locator(".video-row").count(), 10)
            page.get_by_role("navigation").get_by_role(
                "button", name="Calibration", exact=True
            ).click()
            open_first(page)
            self.assertTrue(checkbox.is_checked())
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "corrected topic"
            )

            def reject(route):
                route.fulfill(status=400, json={"error": "Fixture write rejected"})

            pending = []

            def hold_rejection(route):
                pending.append(route)

            page.route("**/api/action", hold_rejection)
            try:
                with page.expect_request("**/api/action"):
                    checkbox.uncheck()
                self.assertFalse(checkbox.is_checked())
                self.assertTrue(checkbox.is_disabled())
                reject(pending.pop())
                page.get_by_text("Fixture write rejected", exact=True).wait_for()
                self.assertTrue(checkbox.is_checked())
                self.assertTrue(checkbox.is_enabled())
            finally:
                for route in pending:
                    route.abort()
                page.unroute("**/api/action", hold_rejection)
            nav = page.get_by_role("navigation")
            nav.get_by_role("button", name="Exclusions", exact=True).click()
            page.get_by_role("button", name="Remove", exact=True).click()
            page.get_by_text("No creator exclusions on YouTube.", exact=True).wait_for()
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            page.get_by_role("tab", name="Tags", exact=True).click()
            tag = page.get_by_label("Tag to exclude on Bilibili", exact=True)
            tag.fill("易语言")
            page.get_by_role("button", name="Add tag", exact=True).click()
            page.wait_for_function(
                "() => document.querySelector('.tag-form input').value === ''"
            )
            page.get_by_text("易语言", exact=True).wait_for()
            tag.fill("Keep this draft")
            page.route("**/api/action", reject)
            page.get_by_role("button", name="Add tag", exact=True).click()
            page.locator("#management").get_by_text(
                "Fixture write rejected", exact=True
            ).wait_for()
            self.assertEqual(tag.input_value(), "Keep this draft")
            page.unroute("**/api/action", reject)
            nav.get_by_role("button", name="Recommendations", exact=True).click()
            page.get_by_role("tab", name="YouTube", exact=True).click()
            self.assertFalse(checkbox.is_checked())
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "corrected topic"
            )
            saved(page, topic)
            with closing(Store(directory)) as store:
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM feedback_details"
                    ).fetchone()[0],
                    1,
                )
            rules = page.request.get(page.url + "api/exclusions").json()
            self.assertEqual(rules["tags"]["youtube"], [])
            self.assertTrue(
                any(rule["tag"] == "易语言" for rule in rules["tags"]["bilibili"])
            )
