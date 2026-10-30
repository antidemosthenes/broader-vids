from contextlib import closing
from pathlib import Path
import unittest

from broader_vids.recommendations.service import create_recommendation
from broader_vids.storage import Store
from broader_vids.token_usage import capture, tracked_call
from test_dashboard_feedback import (
    dashboard_fixture,
    open_first,
    sync_playwright,
    settle_layout,
)


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class TokenUsageDashboardTests(unittest.TestCase):
    def test_paused_failed_and_empty_reports_preserve_drafts_and_dialog_navigation(
        self,
    ):
        with dashboard_fixture() as (page, directory):
            with closing(Store(directory)) as store:
                owner = create_recommendation(
                    store,
                    "youtube",
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
                tracked_call(
                    store,
                    owner,
                    owner,
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
                    tracked_call(store, owner, owner, "assessment", 1, failure)
                tracked_call(store, owner, owner, "assessment", 2, lambda: ({}, None))
                run = store.get(owner)
                run["status"] = "paused"
                store.save(run)
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("Unchanged token-report draft")
            button = page.get_by_role("button", name="Token usage", exact=True)
            button.click()
            dialog = page.get_by_role("dialog", name="Token usage", exact=True)
            dialog.get_by_text("Reported tokens", exact=True).wait_for()
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
            output = Path(".cache/token-usage-ui")
            output.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                page.emulate_media(color_scheme=theme, reduced_motion="reduce")
                for name, width, height, font in (
                    ("desktop", 1440, 1000, 15),
                    ("mobile", 390, 844, 15),
                    ("text-zoom", 1440, 1000, 30),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate(
                        '(size)=>document.documentElement.style.fontSize=size+"px"',
                        font,
                    )
                    settle_layout(
                        page, path=str(output / f"{name}-{theme}.png"), full_page=True
                    )
                    self.assertTrue(
                        dialog.evaluate("(node)=>node.scrollWidth<=node.clientWidth")
                    )
            page.evaluate('document.documentElement.style.fontSize="15px"')
            page.set_viewport_size({"width": 1440, "height": 900})
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
