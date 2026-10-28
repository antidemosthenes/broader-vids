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
                    r'<dialog\s+id="preferences-dialog".*?</dialog>',
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
                "**/static/transfer.js",
                lambda route: route.fulfill(status=404, body="Not found"),
            )
            page.reload(wait_until="networkidle")
            page.locator(".video-row").first.wait_for()
            self.assertEqual(page.locator(".video-row").count(), 10)
            self.assertEqual(errors, [])

    def test_export_import_preserve_lists_and_unsaved_rating_drafts(self):
        with dashboard_fixture() as (page, directory):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("unsaved subject")
            topic.get_by_role("radio", name="Low", exact=True).check()
            page.get_by_role("button", name="Import/Export", exact=True).click()
            dialog = page.get_by_role("dialog", name="Import/Export", exact=True)
            with page.expect_download() as download:
                dialog.get_by_role(
                    "button", name="Export preferences", exact=True
                ).click()
            file_text = Path(download.value.path()).read_text(encoding="utf-8")
            bundle = json.loads(file_text)
            page.wait_for_function(
                "() => !document.getElementById('preferences-file').disabled"
            )
            for marker in (
                "canonical_id",
                "watched",
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
            dialog.get_by_role("button", name="Import preferences", exact=True).click()
            dialog.locator("#preferences-status").filter(
                has_text="Preferences imported."
            ).wait_for()
            with closing(Store(directory)) as store:
                self.assertEqual(preserved(store), before)
            page.keyboard.press("Escape")
            self.assertTrue(
                page.get_by_role("button", name="Import/Export", exact=True).evaluate(
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
            page.get_by_role("button", name="Import/Export", exact=True).click()
            dialog = page.get_by_role("dialog", name="Import/Export", exact=True)
            field = dialog.get_by_label("Preference file", exact=True)
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
