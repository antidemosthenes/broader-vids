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

            page.route("**/api/action", reject)
            checkbox.uncheck()
            page.get_by_text("Fixture write rejected", exact=True).wait_for()
            self.assertTrue(checkbox.is_checked())
            page.unroute("**/api/action", reject)
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

    def test_sync_busy_state_does_not_block_other_views(self):
        with dashboard_fixture() as (page, _):
            nav = page.get_by_role("navigation")
            nav.get_by_role("button", name="Exclusions", exact=True).click()
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            delayed = []
            page.route("**/api/action", lambda route: delayed.append(route))
            page.get_by_role("button", name="Sync blacklist", exact=True).click()
            page.get_by_role("button", name="Syncing…", exact=True).wait_for()
            self.assertTrue(
                page.get_by_role("button", name="Syncing…", exact=True).is_disabled()
            )
            page.get_by_role("tab", name="YouTube", exact=True).click()
            nav.get_by_role("button", name="Recommendations", exact=True).click()
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("draft during sync")
            delayed[0].fulfill(status=400, json={"error": "Blacklist interrupted"})
            nav.get_by_role("button", name="Exclusions", exact=True).click()
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            page.get_by_text("Blacklist interrupted", exact=True).wait_for()
            self.assertTrue(
                page.get_by_role(
                    "button", name="Sync blacklist", exact=True
                ).is_enabled()
            )
            nav.get_by_role("button", name="Recommendations", exact=True).click()
            page.get_by_role("tab", name="YouTube", exact=True).click()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "draft during sync",
            )
