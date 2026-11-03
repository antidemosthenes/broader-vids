from contextlib import closing, redirect_stdout
import io
import json
import re
from pathlib import Path
import tempfile
import unittest

from broader_vids.cli import main
from broader_vids.learning.transfer import export_preferences
from broader_vids.learning.feedback import save_detail
from broader_vids.storage import Store
from test_dashboard_feedback import dashboard_fixture, open_first, sync_playwright
from test_preference_transfer import preserved


class PreferenceCliTests(unittest.TestCase):
    def test_export_preview_and_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "my preferences.json"
            for args, key in (
                (["profile", "set", "--interest", "math"], "interests"),
                (["profile", "export", str(path)], "exported"),
                (["profile", "import", str(path)], "compatible"),
                (["profile", "import", str(path), "--apply"], "imported"),
            ):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--data-dir", directory, *args]), 0)
                self.assertTrue(json.loads(output.getvalue())[key])
            self.assertNotIn("canonical_id", path.read_text(encoding="utf-8"))


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class PreferenceDashboardTests(unittest.TestCase):
    def test_updated_assets_keep_an_older_running_dashboard_usable(self):
        with dashboard_fixture() as (page, directory):

            def older_page(route):
                response = route.fetch()
                markup = re.sub(
                    r'<section\s+id="preferences-page".*</section>',
                    "",
                    response.text(),
                    flags=re.S,
                )
                markup = re.sub(
                    r'<button\s+id="preferences-open".*?</button>',
                    "",
                    markup,
                    flags=re.S,
                )
                route.fulfill(response=response, body=markup)

            page.route(page.url, older_page)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(
                "**/static/preferences.js",
                lambda route: route.fulfill(status=404, body="Not found"),
            )
            page.reload(wait_until="domcontentloaded")
            page.locator(".video-row").first.wait_for()
            self.assertEqual(page.locator(".video-row").count(), 10)
            self.assertEqual(errors, [])

    def test_export_import_preserve_lists_and_unsaved_rating_drafts(self):
        with dashboard_fixture() as (page, directory):
            row = page.locator(".video-row").first
            watched_id = row.get_attribute("data-id")
            row.get_by_role("button", name="Mark watched", exact=True).click()
            row.get_by_role("button", name="Watched ✓", exact=True).wait_for()
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("unsaved subject")
            topic.get_by_role("radio", name="Low", exact=True).check()
            page.get_by_role("button", name="Preferences", exact=True).click()
            dialog = page.get_by_role("region", name="Preferences", exact=True)
            with page.expect_download() as download:
                dialog.get_by_role(
                    "button", name="Export preferences", exact=True
                ).click()
            file_text = Path(download.value.path()).read_text(encoding="utf-8")
            bundle = json.loads(file_text)
            self.assertEqual(bundle["watched_video_ids"], [watched_id])
            page.wait_for_function(
                "() => !document.getElementById('preferences-file').disabled"
            )
            for marker in (
                "canonical_id",
                "recommendation",
                "features",
                "video_topics",
            ):
                self.assertNotIn(marker, json.dumps(bundle))
            with closing(Store(directory)) as store:
                before = preserved(store)
            with page.expect_file_chooser() as chooser:
                dialog.get_by_role("button", name="Choose file", exact=True).click()
            chooser.value.set_files(
                {
                    "name": "preferences.json",
                    "mimeType": "application/json",
                    "buffer": file_text.encode(),
                }
            )
            dialog.get_by_text(
                "File is compatible. Check the summary before importing.",
                exact=True,
            ).wait_for()
            self.assertEqual(
                dialog.get_by_role("button", name="Review file", exact=True).count(), 0
            )
            self.assertEqual(
                dialog.locator("#preferences-filename").inner_text(), "preferences.json"
            )
            self.assertIn(
                "Watched video IDs", dialog.locator("#preferences-summary").inner_text()
            )
            self.assertIn(
                "1 (1 YouTube, 0 Bilibili)",
                dialog.locator("#preferences-summary").inner_text(),
            )
            dialog.get_by_role("button", name="Import preferences", exact=True).click()
            dialog.locator("#preferences-status").filter(
                has_text="Preferences imported."
            ).wait_for()
            with closing(Store(directory)) as store:
                self.assertEqual(preserved(store), before)
            page.keyboard.press("Escape")
            self.assertTrue(
                page.get_by_role("button", name="Preferences", exact=True).evaluate(
                    "e=>e===document.activeElement"
                )
            )
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "unsaved subject"
            )
            self.assertTrue(
                topic.get_by_role("radio", name="Low", exact=True).is_checked()
            )
            self.assertEqual(page.locator(".video-row").count(), 10)

    def test_manual_creator_exclusion_roundtrip_is_visible_after_import(self):
        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            checkbox = inspector.get_by_role(
                "checkbox", name="Exclude this creator from future rounds"
            )
            checkbox.check()
            page.wait_for_function(
                "() => { const input = document.querySelector('.creator-rule input'); return input.checked && !input.disabled; }"
            )
            page.get_by_role("button", name="Preferences", exact=True).click()
            preferences = page.get_by_role("region", name="Preferences", exact=True)
            page.wait_for_function(
                "() => !document.getElementById('preferences-file').disabled"
            )
            with page.expect_download() as download:
                preferences.get_by_role(
                    "button", name="Export preferences", exact=True
                ).click()
            file_text = Path(download.value.path()).read_text(encoding="utf-8")

            page.get_by_role("button", name="Back", exact=True).click()
            page.get_by_role("navigation").get_by_role(
                "button", name="Exclusions", exact=True
            ).click()
            management = page.locator("#management")
            management.get_by_role("button", name="Remove", exact=True).click()
            page.get_by_text("No creator exclusions on YouTube.", exact=True).wait_for()

            page.get_by_role("button", name="Preferences", exact=True).click()
            preferences = page.get_by_role("region", name="Preferences", exact=True)
            preferences.get_by_label("Preference file", exact=True).set_input_files(
                {
                    "name": "preferences.json",
                    "mimeType": "application/json",
                    "buffer": file_text.encode(),
                }
            )
            preferences.get_by_text(
                "File is compatible. Check the summary before importing.", exact=True
            ).wait_for()
            summary = preferences.locator("#preferences-summary").inner_text()
            self.assertIn("Manual creator exclusions", summary)
            self.assertIn("1 (1 YouTube, 0 Bilibili)", summary)
            preferences.get_by_role(
                "button", name="Import preferences", exact=True
            ).click()
            preferences.locator("#preferences-status").filter(
                has_text="Manual creator lists replaced with 1 exclusion"
            ).wait_for()

            page.get_by_role("button", name="Back", exact=True).click()
            page.get_by_role("navigation").get_by_role(
                "button", name="Exclusions", exact=True
            ).click()
            self.assertEqual(management.locator(".management-row").count(), 1)
            creator_id = management.locator(".management-row .small-text").inner_text()
            page.get_by_role("navigation").get_by_role(
                "button", name="Recommendations", exact=True
            ).click()
            restored = open_first(page).get_by_role(
                "checkbox", name="Exclude this creator from future rounds"
            )
            self.assertTrue(restored.is_checked())
            with closing(Store(directory)) as store:
                saved = store.db.execute(
                    "SELECT source,creator_id FROM creator_exclusions"
                ).fetchall()
                self.assertEqual(
                    [(row["source"], row["creator_id"]) for row in saved],
                    [("manual", creator_id)],
                )

    def test_imported_interests_are_editable_on_the_same_page_and_operations_are_serial(
        self,
    ):
        from broader_vids.learning.baseline import current_baseline
        from broader_vids.preferences import get_profile, set_profile

        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("retained video draft")
            page.get_by_role("button", name="Preferences", exact=True).click()
            settings = page.get_by_role("region", name="Preferences", exact=True)
            field = settings.get_by_label("Your interests", exact=True)
            page.wait_for_function(
                "() => !document.getElementById('interests-save').disabled"
            )
            field.fill("games")
            self.assertTrue(
                settings.get_by_role(
                    "button", name="Export preferences", exact=True
                ).is_disabled()
            )
            with closing(Store(directory)) as store:
                before = preserved(store)
            with (
                tempfile.TemporaryDirectory() as source,
                closing(Store(source)) as store,
            ):
                set_profile(store, ["music", "travel"])
                bundle = export_preferences(store)
            settings.get_by_label("Preference file", exact=True).set_input_files(
                {
                    "name": "preferences.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps(bundle).encode(),
                }
            )
            settings.get_by_text(
                "File is compatible. Check the summary before importing.", exact=True
            ).wait_for()
            self.assertEqual(field.input_value(), "games")
            self.assertTrue(settings.locator("#preferences-draft-warning").is_visible())
            pending = []

            def hold(route):
                pending.append(route)

            page.route("**/api/preferences/import", hold)
            try:
                with page.expect_request("**/api/preferences/import"):
                    settings.get_by_role(
                        "button", name="Import preferences", exact=True
                    ).click()
                self.assertTrue(field.is_disabled())
                self.assertTrue(
                    settings.get_by_role(
                        "button", name="Save", exact=True
                    ).is_disabled()
                )
                page.get_by_role("button", name="Back", exact=True).click()
                self.assertEqual(
                    topic.get_by_label("Topic", exact=True).input_value(),
                    "retained video draft",
                )
                page.get_by_role("button", name="Preferences", exact=True).click()
                self.assertTrue(field.is_disabled())
                pending[0].continue_()
                settings.locator("#preferences-status").filter(
                    has_text="Preferences imported."
                ).wait_for()
            finally:
                for route in pending[1:]:
                    route.abort()
                page.unroute("**/api/preferences/import", hold)
            self.assertEqual(field.input_value(), "music\ntravel")
            self.assertTrue(
                settings.get_by_role(
                    "button", name="Export preferences", exact=True
                ).is_enabled()
            )
            self.assertTrue(settings.locator("#preferences-preview").is_hidden())
            with closing(Store(directory)) as store:
                baseline = current_baseline(store)
                self.assertEqual(get_profile(store)["interests"], ["music", "travel"])
                self.assertEqual(preserved(store), before)
            field.fill("music\ntravel\nphysics")
            settings.get_by_role("button", name="Save", exact=True).click()
            settings.locator("#interests-status").filter(has_text="Saved.").wait_for()
            with closing(Store(directory)) as store:
                self.assertEqual(current_baseline(store), baseline)
                self.assertEqual(
                    get_profile(store)["interests"], ["music", "travel", "physics"]
                )
                self.assertEqual(preserved(store), before)
            with page.expect_download() as download:
                settings.get_by_role(
                    "button", name="Export preferences", exact=True
                ).click()
            exported = json.loads(
                Path(download.value.path()).read_text(encoding="utf-8")
            )
            self.assertEqual(
                exported["preferences"]["interests"], ["music", "travel", "physics"]
            )
            page.get_by_role("button", name="Back", exact=True).click()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "retained video draft",
            )

    def test_preview_security_stale_save_and_failed_request(self):
        with dashboard_fixture() as (page, directory):
            with closing(Store(directory)) as store:
                bundle = export_preferences(store)
            token = page.locator('meta[name="broader-vids-token"]').get_attribute(
                "content"
            )
            endpoint = page.url + "api/preferences/preview"
            for headers in (
                {},
                {"X-Broader-Vids-Token": token, "Origin": "https://example.com"},
            ):
                response = page.request.post(
                    endpoint, headers=headers, data={"file_text": json.dumps(bundle)}
                )
                self.assertEqual(response.status, 403)
            response = page.request.post(
                endpoint,
                headers={"X-Broader-Vids-Token": token},
                data={"file_text": '{"format":1,"format":2}'},
            )
            self.assertEqual(response.status, 400)
            page.get_by_role("button", name="Preferences", exact=True).click()
            dialog = page.get_by_role("region", name="Preferences", exact=True)
            field = dialog.get_by_label("Preference file", exact=True)
            page.wait_for_function(
                "() => !document.getElementById('preferences-file').disabled"
            )
            field.set_input_files(
                {
                    "name": "preferences.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps(bundle).encode(),
                }
            )
            dialog.get_by_text(
                "File is compatible. Check the summary before importing.",
                exact=True,
            ).wait_for()
            with closing(Store(directory)) as store:
                save_detail(
                    store,
                    "youtube:0000000001A",
                    "topic_interest",
                    "high",
                    target="topology",
                )
            apply = dialog.get_by_role("button", name="Import preferences", exact=True)
            apply.click()
            dialog.get_by_text(
                "Preferences changed since you chose this file. Check the summary, then select Import preferences again.",
                exact=True,
            ).wait_for()
            self.assertEqual(field.evaluate("e=>e.files.length"), 1)
            page.route(
                "**/api/preferences/import",
                lambda route: route.fulfill(
                    status=400, json={"error": "Fixture import rejected"}
                ),
            )
            apply.click()
            dialog.get_by_text("Fixture import rejected", exact=True).wait_for()
            self.assertTrue(apply.is_enabled())
            self.assertEqual(field.evaluate("e=>e.files.length"), 1)

    def test_closing_transfer_releases_selected_file_without_losing_rating_drafts(self):
        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("unsaved topic")
            page.get_by_role("button", name="Preferences", exact=True).click()
            dialog = page.get_by_role("region", name="Preferences", exact=True)
            with closing(Store(directory)) as store:
                raw = json.dumps(export_preferences(store)).encode()
            field = dialog.get_by_label("Preference file", exact=True)
            page.wait_for_function(
                "() => !document.getElementById('preferences-file').disabled"
            )
            field.set_input_files(
                {
                    "name": "preferences.json",
                    "mimeType": "application/json",
                    "buffer": raw,
                }
            )
            dialog.get_by_text(
                "File is compatible. Check the summary before importing.", exact=True
            ).wait_for()
            page.get_by_role("button", name="Back", exact=True).click()
            page.get_by_role("button", name="Preferences", exact=True).click()
            self.assertEqual(field.evaluate("e=>e.files.length"), 0)
            self.assertTrue(dialog.locator("#preferences-preview").is_hidden())
            self.assertEqual(dialog.locator("#preferences-status").inner_text(), "")
            page.keyboard.press("Escape")
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "unsaved topic"
            )
