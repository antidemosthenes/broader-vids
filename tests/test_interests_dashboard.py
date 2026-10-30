from contextlib import closing
import copy
import json
from pathlib import Path
import tempfile
import unittest

from broader_vids.preferences import (
    get_profile,
    set_profile,
    update_tag_rule,
    platform_lock,
    ProfileConflict,
)
from broader_vids.learning.baseline import current_baseline
from broader_vids.learning.transfer import export_preferences, import_preferences
from broader_vids.storage import Store
from test_preference_transfer import seed, preserved
from test_dashboard_feedback import (
    dashboard_fixture,
    open_first,
    sync_playwright,
    settle_layout,
)


class InterestProfileTests(unittest.TestCase):
    def test_changes_preserve_calibration_rules_history_and_reject_stale_or_busy_writes(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            seed(store)
            import_preferences(store, export_preferences(store))
            update_tag_rule(store, "bilibili", "易语言")
            baseline = copy.deepcopy(current_baseline(store))
            before = preserved(store)
            old = get_profile(store)
            result = set_profile(
                store,
                [" Math ", "math", "travel", "计算机科学"],
                expected_revision=old["revision"],
            )
            self.assertEqual(
                result["interests"], ["mathematics", "travel", "计算机科学"]
            )
            self.assertEqual(result["revision"], old["revision"] + 1)
            self.assertEqual(result["excluded_tags"]["bilibili"], ["易语言"])
            self.assertEqual(current_baseline(store), baseline)
            self.assertEqual(preserved(store), before)
            self.assertEqual(
                set_profile(
                    store, result["interests"], expected_revision=result["revision"]
                ),
                result,
            )
            with self.assertRaises(ProfileConflict):
                set_profile(store, ["games"], expected_revision=old["revision"])
            for invalid in ([], [""], ["a" * 81], ["ß" * 80], ["math"] * 41):
                with (
                    self.subTest(invalid_size=len(invalid)),
                    self.assertRaises(ValueError),
                ):
                    set_profile(store, invalid, expected_revision=result["revision"])
            with closing(Store(directory)) as other:
                for platform in ("youtube", "bilibili"):
                    with (
                        other.lease(platform_lock(platform)),
                        self.assertRaisesRegex(ValueError, "Finish"),
                    ):
                        set_profile(
                            store, ["games"], expected_revision=result["revision"]
                        )
                    with (
                        store.lease(platform_lock("youtube")),
                        store.lease(platform_lock("bilibili")),
                    ):
                        pass
            self.assertEqual(get_profile(store), result)
            self.assertEqual(preserved(store), before)

    def test_first_profile_can_be_created_with_revision_zero(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            self.assertIsNone(get_profile(store))
            with self.assertRaises(ValueError):
                set_profile(store, ["math"], expected_revision=True)
            result = set_profile(store, ["math"], expected_revision=0)
            self.assertEqual(result["revision"], 1)
            self.assertEqual(result["interests"], ["mathematics"])


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class InterestDashboardTests(unittest.TestCase):
    def open_editor(self, page):
        page.get_by_role("button", name="Preferences", exact=True).click()
        dialog = page.get_by_role("region", name="Preferences", exact=True)
        page.wait_for_function(
            "() => !document.getElementById('interests-input').disabled && !document.getElementById('interests-save').disabled"
        )
        return dialog

    def test_save_failures_navigation_and_visual_layout_keep_drafts_and_history(self):
        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("Unsaved video topic")
            topic.get_by_role("radio", name="Low", exact=True).check()
            with closing(Store(directory)) as store:
                before = preserved(store)
            dialog = self.open_editor(page)
            field = dialog.get_by_label("Your interests", exact=True)
            self.assertEqual(field.input_value(), "mathematics")
            self.assertEqual(
                dialog.locator("#preferences-file").evaluate(
                    "node => node.files.length"
                ),
                0,
            )
            field.fill("Math\ntravel\n计算机科学")
            page.keyboard.press("Escape")
            self.assertTrue(
                page.get_by_role("button", name="Preferences", exact=True).evaluate(
                    "(node) => node === document.activeElement"
                )
            )
            dialog = self.open_editor(page)
            self.assertEqual(field.input_value(), "Math\ntravel\n计算机科学")

            def fail_save(route):
                if route.request.method == "POST":
                    route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps({"error": "Fixture save failure"}),
                    )
                else:
                    route.continue_()

            page.route("**/api/interests", fail_save)
            dialog.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_function(
                "() => document.getElementById('interests-status').textContent.includes('Fixture save failure')"
            )
            self.assertEqual(field.input_value(), "Math\ntravel\n计算机科学")
            page.unroute("**/api/interests", fail_save)
            dialog.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_function(
                "() => document.getElementById('interests-status').textContent.startsWith('Saved.')"
            )
            self.assertEqual(field.input_value(), "mathematics\ntravel\n计算机科学")
            output = Path(__file__).resolve().parents[1] / ".cache/interests-ui"
            output.mkdir(parents=True, exist_ok=True)
            for theme, width, zoom in (
                ("light", 1440, "100%"),
                ("dark", 430, "100%"),
                ("light", 720, "200%"),
                ("dark", 430, "200%"),
            ):
                page.emulate_media(color_scheme=theme, reduced_motion="reduce")
                page.set_viewport_size({"width": width, "height": 900})
                page.evaluate(
                    "(size) => document.documentElement.style.fontSize = size", zoom
                )
                settle_layout(page, path=str(output / f"{theme}-{width}-{zoom}.png"))
                box = dialog.bounding_box()
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width + 1)
                self.assertLessEqual(box["height"], 900)
                if width <= 720:
                    self.assertLess(box["x"], width * 0.1)
                    self.assertGreater(box["width"], width * 0.85)
                    self.assertTrue(
                        page.evaluate(
                            "document.documentElement.hasAttribute('data-mobile')"
                        )
                    )
                    self.assertTrue(
                        page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        )
                    )
            page.set_viewport_size({"width": 1440, "height": 900})
            page.evaluate("document.documentElement.style.fontSize = ''")
            page.get_by_role("button", name="Back", exact=True).click()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "Unsaved video topic",
            )
            self.assertTrue(
                topic.get_by_role("radio", name="Low", exact=True).is_checked()
            )
            with closing(Store(directory)) as store:
                self.assertEqual(
                    get_profile(store)["interests"],
                    ["mathematics", "travel", "计算机科学"],
                )
                self.assertEqual(preserved(store), before)

    def test_stale_editor_requires_reload_and_api_checks_revision_token_and_unicode_limits(
        self,
    ):
        with dashboard_fixture() as (page, directory):
            dialog = self.open_editor(page)
            field = dialog.get_by_label("Your interests", exact=True)
            field.fill("mathematics\nphysics")
            with closing(Store(directory)) as store:
                set_profile(store, ["travel"])
            dialog.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_function(
                "() => document.getElementById('interests-status').textContent.includes('another window')"
            )
            self.assertEqual(field.input_value(), "mathematics\nphysics")
            dialog.get_by_role(
                "button", name="Reload saved interests", exact=True
            ).click()
            page.wait_for_function(
                "() => !document.getElementById('interests-input').disabled"
            )
            self.assertEqual(field.input_value(), "travel")
            field.fill("travel\nphysics")
            with closing(Store(directory)) as store:
                update_tag_rule(store, "bilibili", "视频翻译", remove=True)
            dialog.get_by_role("button", name="Save", exact=True).click()
            dialog.locator("#interests-status").filter(has_text="Saved.").wait_for()
            self.assertEqual(field.input_value(), "travel\nphysics")
            with closing(Store(directory)) as store:
                self.assertEqual(
                    get_profile(store)["disabled_builtin_tags"]["bilibili"],
                    ["视频翻译"],
                )
            token = page.locator('meta[name="broader-vids-token"]').get_attribute(
                "content"
            )
            url = page.url.rstrip("/") + "/api/interests"
            profile = page.request.get(url).json()
            self.assertEqual(
                page.request.post(
                    url, data={"interests": ["music"], "revision": profile["revision"]}
                ).status,
                403,
            )
            headers = {"X-Broader-Vids-Token": token}
            for value in (
                {"interests": ["music"]},
                {"interests": ["music"], "revision": True},
                {"interests": [], "revision": profile["revision"]},
            ):
                self.assertEqual(
                    page.request.post(url, data=value, headers=headers).status, 400
                )
            interests = [
                str(index) + "科" * (80 - len(str(index))) for index in range(40)
            ]
            response = page.request.post(
                url,
                data={"interests": interests, "revision": profile["revision"]},
                headers=headers,
            )
            self.assertEqual(response.status, 200, response.text())
            self.assertEqual(response.json()["interests"], interests)
            stale = page.request.post(
                url,
                data={"interests": ["music"], "revision": profile["revision"]},
                headers=headers,
            )
            self.assertEqual(stale.status, 409)
            self.assertEqual(page.request.get(url).json()["interests"], interests)
