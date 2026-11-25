from contextlib import closing
import unittest

from broader_vids.recommendations.batch import create_batch
from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from broader_vids.token_usage import capture, tracked_call
from test_dashboard_feedback import (
    dashboard_fixture,
    expect,
    open_first,
    sync_playwright,
)


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class TokenUsageDashboardTests(unittest.TestCase):
    def test_paused_failed_and_empty_reports_preserve_drafts_and_dialog_navigation(
        self,
    ):
        with dashboard_fixture() as (page, directory):
            with closing(Store(directory)) as store:
                owner = create_batch(
                    store,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
                run = store.get(owner)
                youtube = run["state"]["children"]["youtube"]
                bilibili = run["state"]["children"]["bilibili"]
                for identifier, status in zip(
                    (youtube, bilibili), ("completed", "paused")
                ):
                    child = store.get(identifier)
                    child["status"] = status
                    store.save(child)
                tracked_call(
                    store,
                    youtube,
                    youtube,
                    "exploration",
                    1,
                    lambda: (
                        {},
                        {
                            "input_tokens": 80,
                            "cached_input_tokens": 60,
                            "output_tokens": 20,
                        },
                    ),
                )

                def failure():
                    capture(
                        {
                            "input_tokens": 180,
                            "cached_input_tokens": 120,
                            "output_tokens": 20,
                        }
                    )
                    raise ValueError("Invalid output")

                with self.assertRaises(ValueError):
                    tracked_call(store, bilibili, bilibili, "assessment", 1, failure)
                tracked_call(
                    store, bilibili, bilibili, "assessment", 2, lambda: ({}, None)
                )
                run["status"] = "paused"
                store.save(run)
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("Unchanged token-report draft")
            button = page.get_by_role("button", name="Token usage", exact=True)
            button.click()
            dialog = page.get_by_role("dialog", name="Token usage", exact=True)
            dialog.get_by_text("Reported tokens", exact=True).wait_for()
            dialog.get_by_text(
                "No Codex tokens used by this step.", exact=True
            ).first.wait_for()
            expect(dialog.locator(".usage-stage h4")).to_have_text(
                ["Topic exploration", "Quality assessment", "Broad-interest alignment"]
            )
            expect(dialog.get_by_role("heading", name="By platform")).to_be_visible()
            youtube_card, bilibili_card = (
                dialog.locator(".usage-platform").filter(has_text=platform)
                for platform in ("YouTube", "Bilibili")
            )
            expect(youtube_card.get_by_text("100", exact=True)).to_be_visible()
            expect(youtube_card.get_by_text("Completed", exact=True)).to_be_visible()
            expect(bilibili_card.get_by_text("200", exact=True)).to_be_visible()
            expect(bilibili_card.get_by_text("Paused", exact=True)).to_be_visible()
            expect(bilibili_card.get_by_text("1 attempt missing usage")).to_be_visible()
            self.assertIn("2 of 3", dialog.locator(".usage-coverage").inner_text())
            self.assertIn("partial", dialog.locator(".usage-coverage").inner_text())
            self.assertIn(
                "66.7%",
                dialog.locator(".usage-stage")
                .filter(has_text="Quality assessment")
                .inner_text(),
            )
            self.assertIn(
                "33.3%",
                dialog.locator(".usage-stage")
                .filter(has_text="Topic exploration")
                .inner_text(),
            )
            self.assertEqual(
                dialog.locator(".usage-summary dd").first.inner_text(), "300"
            )
            payload = page.request.get(
                page.url + "api/token-usage?run_id=" + owner
            ).json()
            self.assertEqual(payload["report"]["status"], "paused")
            self.assertEqual(
                page.request.get(page.url + "api/token-usage?run_id=invalid").status,
                400,
            )
            page.keyboard.press("Escape")
            self.assertFalse(dialog.is_visible())
            self.assertTrue(button.evaluate("(node)=>node===document.activeElement"))
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "Unchanged token-report draft",
            )
            with closing(Store(directory)) as store:
                run = store.get(owner)
                run["status"] = "failed"
                store.save(run)
            button.click()
            page.wait_for_function(
                "() => document.querySelector('#token-usage-run').selectedOptions[0]?.textContent.includes('failed')"
            )
            self.assertEqual(
                dialog.locator(".usage-summary dd").first.inner_text(), "300"
            )
            dialog.get_by_role("button", name="Close", exact=True).click()
            page.route(
                "**/api/token-usage",
                lambda route: route.fulfill(json={"runs": [], "report": None}),
            )
            page.route(
                "**/api/token-usage?*",
                lambda route: route.fulfill(json={"runs": [], "report": None}),
            )
            button.click()
            dialog.get_by_text(
                "No recommendation rounds yet. Usage will appear here when a round starts.",
                exact=True,
            ).wait_for()

    def test_claude_zero_use_stages_name_claude(self):
        with dashboard_fixture() as (page, directory):
            with closing(Store(directory)) as store:
                owner = create_recommendation(
                    store,
                    "youtube",
                    assessment_model="claude-fixture",
                    reasoning_effort="medium",
                    host="claude",
                )
            open_first(page)
            page.get_by_role("button", name="Token usage", exact=True).click()
            dialog = page.get_by_role("dialog", name="Token usage", exact=True)
            expect(
                dialog.get_by_text("No Claude tokens used by this step.", exact=True)
            ).to_have_count(3)
            dialog.get_by_role("button", name="Close", exact=True).click()
            with closing(Store(directory)) as store:
                tracked_call(
                    store,
                    owner,
                    owner,
                    "screening",
                    1,
                    lambda: (
                        {},
                        {
                            "input_tokens": 20,
                            "output_tokens": 5,
                            "cached_input_tokens": 0,
                        },
                    ),
                )
                tracked_call(store, owner, owner, "preparation", 1, lambda: ({}, None))
            page.get_by_role("button", name="Token usage", exact=True).click()
            stage = dialog.locator(".usage-stage").filter(
                has_text="Search and screening"
            )
            expect(stage).to_contain_text("25 · 100.0%")
            preparation = dialog.locator(".usage-stage").filter(
                has_text="Evidence preparation"
            )
            expect(preparation).to_contain_text("Not reported")
            expect(preparation).to_contain_text("Usage is missing for 1 model attempt.")
            expect(dialog.locator(".usage-stage")).to_have_count(5)
