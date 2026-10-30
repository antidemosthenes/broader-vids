from contextlib import closing, contextmanager
import json
import os
import sys
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from broader_vids.learning.features import capture
from broader_vids.policy import Policy
from broader_vids.preferences import set_profile
from broader_vids.storage import Store
from broader_vids.web.server import make_server
from test_learning import example


@contextmanager
def dashboard_fixture(*, provisional=False):
    with tempfile.TemporaryDirectory() as directory:
        with closing(Store(directory)) as store:
            set_profile(store, ["mathematics"])
            for platform in ("youtube", "bilibili"):
                items = []
                for index in range(1, 13):
                    item = example(index, "UC" + "a" * 22)
                    item["quality_evidence"].update(
                        topics={
                            "specific": ["functional analysis"],
                            "broad": ["mathematics"],
                        },
                        audience_level="intermediate",
                    )
                    if platform == "bilibili":
                        item.update(
                            canonical_id=f"bilibili:BV1Z6H56oEcQ:p{index}",
                            platform=platform,
                            creator_id="1234",
                            url="https://www.bilibili.com/video/BV1Z6H56oEcQ",
                        )
                    item.update(
                        score=80,
                        creator_name="Example creator",
                        content_topics=["functional analysis"],
                        audience_level="intermediate",
                        related=True,
                        carryover=False,
                        engagement_score={"score": 50},
                    )
                    items.append(item)
                    if not provisional:
                        store.db.execute(
                            "INSERT INTO video_state VALUES(?,?,?,0,0,1,'fixture')",
                            (item["canonical_id"], platform, json.dumps(item)),
                        )
                    if index == 1:
                        payload = {
                            "item": {
                                k: item[k]
                                for k in ("canonical_id", "platform", "title", "url")
                            },
                            "features": capture(item),
                            "created_at": "fixture",
                            "after_action_id": 0,
                        }
                        store.db.execute(
                            "INSERT INTO calibration_items VALUES(?,'fixture','train',?,0)",
                            (item["canonical_id"], json.dumps(payload)),
                        )
                store.db.commit()
                rid = store.create(
                    platform,
                    {"kind": "recommendation", "weights": Policy().weights},
                    {
                        "selected": [] if provisional else items[:10],
                        "preview": items[:10] if provisional else [],
                        "published_at": None if provisional else "fixture",
                        "shortlist": items,
                        "evidence": {
                            item["canonical_id"]: {"quality": 75} for item in items
                        },
                        "issues": [],
                    },
                )
                run = store.get(rid)
                run["status"] = "paused" if provisional else "completed"
                store.save(run)
        server = make_server(directory, 0)
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
        )
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    **({"channel": "msedge"} if sys.platform == "win32" else {}),
                )
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    page.set_default_timeout(8000)
                    page.goto(
                        f"http://127.0.0.1:{server.server_port}",
                        wait_until="domcontentloaded",
                    )
                    page.locator(".video-row").first.wait_for()
                    yield page, directory
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def settle_layout(page, *, path, **options):
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    if os.environ.get("BROADER_VIDS_TEST_SCREENSHOTS") == "1":
        page.screenshot(path=path, **options)


def open_first(page):
    page.locator(".video-row").first.get_by_role(
        "button", name="Rate", exact=True
    ).click()
    return page.locator("#inspector-body")


def saved(page, group):
    group.get_by_role("button", name="Save", exact=True).click()
    group.locator(".field-status").filter(has_text="Saved").wait_for()
    page.wait_for_function(
        "() => !document.querySelector('.rating-footer button:disabled:not(.quiet)')"
    )


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class DashboardFeedbackTests(unittest.TestCase):
    def test_manual_level_and_independent_drafts_survive_navigation_save_and_clear(
        self,
    ):
        with dashboard_fixture(provisional=True) as (page, directory):
            self.assertTrue(page.locator("#round-notice").is_visible())
            self.assertEqual(page.locator(".video-row").count(), 10)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            inspector = open_first(page)
            depth = inspector.get_by_role(
                "group", name="Depth for this video", exact=True
            )
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("my corrected topic")
            topic.get_by_role("radio", name="Low", exact=True).check()
            depth.get_by_role("combobox", name="Video level", exact=True).select_option(
                "advanced"
            )
            depth.get_by_role("radio", name="Too basic", exact=True).check()
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            open_first(page)
            self.assertTrue(page.locator("#round-notice").is_visible())
            self.assertEqual(page.locator(".video-row").count(), 10)
            page.get_by_role("tab", name="YouTube", exact=True).click()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "my corrected topic",
            )
            self.assertEqual(
                depth.get_by_role(
                    "combobox", name="Video level", exact=True
                ).input_value(),
                "advanced",
            )
            saved(page, depth)
            page.wait_for_function(
                "() => document.querySelector('#inspector-body select').value === 'advanced'"
            )
            self.assertTrue(
                topic.get_by_role("radio", name="Low", exact=True).is_checked()
            )
            saved(page, topic)
            page.get_by_role("navigation").get_by_role(
                "button", name="Calibration", exact=True
            ).click()
            open_first(page)
            self.assertEqual(
                depth.get_by_role(
                    "combobox", name="Video level", exact=True
                ).input_value(),
                "advanced",
            )
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "my corrected topic",
            )
            depth.get_by_role("combobox", name="Video level", exact=True).select_option(
                "unknown"
            )
            saved(page, depth)
            page.reload(wait_until="domcontentloaded")
            open_first(page)
            self.assertEqual(
                depth.get_by_role(
                    "combobox", name="Video level", exact=True
                ).input_value(),
                "unknown",
            )
            topic.get_by_role("button", name="Clear", exact=True).click()
            page.wait_for_function(
                "() => !document.querySelector('#inspector-body .rating-form:first-of-type input[type=radio]:checked')"
            )
            self.assertTrue(
                depth.get_by_role("radio", name="Too basic", exact=True).is_checked()
            )
            with closing(Store(directory)) as store:
                payload = json.loads(
                    store.db.execute(
                        "SELECT payload FROM feedback_details WHERE dimension='depth'"
                    ).fetchone()[0]
                )
                self.assertEqual(payload["level_source"], "user")
                self.assertEqual(payload["level"], "unknown")
                self.assertEqual(
                    store.db.execute(
                        "SELECT count(*) FROM recommendation_exposures"
                    ).fetchone()[0],
                    0,
                )
                saved_item = json.loads(
                    store.db.execute(
                        "SELECT payload FROM video_state WHERE canonical_id=?",
                        ("youtube:0000000001A",),
                    ).fetchone()[0]
                )
                self.assertEqual(saved_item["title"], "Development video 1")
            self.assertFalse(errors, errors)

    def test_mobile_dialog_details_keyboard_and_layout(self):
        with dashboard_fixture() as (page, _):
            initial = page.locator(".video-row").first.bounding_box()["height"]
            page.locator("#list-pane").evaluate("node => node.scrollTop = 120")
            scroll = page.locator("#list-pane").evaluate("node => node.scrollTop")
            page.locator(".video-row").nth(2).get_by_role(
                "button", name="Rate", exact=True
            ).click()
            self.assertEqual(
                page.locator(".video-row").first.bounding_box()["height"], initial
            )
            page.get_by_role("button", name="Score details", exact=True).click()
            self.assertTrue(page.locator("#details-dialog").is_visible())
            page.keyboard.press("Escape")
            self.assertFalse(page.locator("#details-dialog").is_visible())
            self.assertEqual(
                page.locator("#list-pane").evaluate("node => node.scrollTop"), scroll
            )
            self.assertEqual(
                page.evaluate("document.activeElement.textContent"), "Score details"
            )
            page.locator("#inspector-body").evaluate("node => node.scrollTop=120")
            page.get_by_role("navigation").get_by_role(
                "button", name="Exclusions", exact=True
            ).click()
            page.get_by_role("navigation").get_by_role(
                "button", name="Recommendations", exact=True
            ).click()
            self.assertEqual(
                page.locator("#inspector-body").evaluate("node=>node.scrollTop"), 120
            )
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator(".video-row").nth(2).get_by_role(
                "button", name="Rate", exact=True
            ).click()
            self.assertTrue(page.locator("#review-dialog").is_visible())
            page.locator("#mobile-next").click()
            self.assertIn(
                "Development video 4",
                page.locator("#mobile-review-body .review-title").inner_text(),
            )
            page.locator("#mobile-previous").click()
            page.get_by_role("button", name="Score details", exact=True).click()
            self.assertEqual(
                page.locator("#mobile-review-title").inner_text(), "Score details"
            )
            self.assertFalse(page.locator("#details-dialog").is_visible())
            page.keyboard.press("Escape")
            self.assertEqual(
                page.locator("#mobile-review-title").inner_text(), "Video review"
            )
            self.assertTrue(page.locator("#review-dialog").is_visible())
            page.keyboard.press("Escape")
            self.assertFalse(page.locator("#review-dialog").is_visible())
            self.assertEqual(
                page.evaluate("document.activeElement.textContent"), "Rate"
            )
            self.assertLessEqual(page.evaluate("document.body.scrollWidth"), 390)

    def test_themes_zoom_empty_state_and_readable_sync(self):
        with dashboard_fixture() as (page, _):
            page.route(
                "**/api/exclusions",
                lambda route: route.fulfill(
                    json={
                        "creators": {},
                        "tags": {},
                        "bilibili_sync": {
                            "status": "complete",
                            "account_id": "1234",
                            "last_success": "2026-10-27T22:36:49.873717+00:00",
                        },
                    }
                ),
            )
            page.reload(wait_until="domcontentloaded")
            for theme in ("light", "dark"):
                page.emulate_media(color_scheme=theme, reduced_motion="reduce")
                page.get_by_role("navigation").get_by_role(
                    "button", name="Exclusions", exact=True
                ).click()
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                self.assertEqual(
                    page.locator(".connection-fields dd").nth(0).inner_text(), "Synced"
                )
                self.assertNotIn(
                    "T22:36", page.locator(".connection-fields dd").nth(2).inner_text()
                )
                self.assertIn(
                    "2026",
                    page.locator(".connection-fields dd").nth(2).get_attribute("title"),
                )
                page.evaluate("document.documentElement.style.fontSize='30px'")
                self.assertEqual(page.evaluate("document.body.scrollWidth"), 1440)
                page.evaluate("document.documentElement.style.fontSize='15px'")
            page.route("**/api/calibration", lambda route: route.fulfill(json=[]))
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("navigation").get_by_role(
                "button", name="Calibration", exact=True
            ).click()
            self.assertTrue(
                page.get_by_role("heading", name="Room to learn").is_visible()
            )

    def test_polling_and_inflight_saves_preserve_newer_edits(self):
        with dashboard_fixture() as (page, _):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            quality = inspector.get_by_role("group", name="Video quality", exact=True)
            topic.get_by_label("Topic", exact=True).fill("unsaved narrow topic")
            topic.get_by_role("radio", name="Low", exact=True).check()
            stale = page.request.get(page.url + "api/state").json()
            delayed = []

            def delay_once(route):
                if not delayed:
                    delayed.append(route)
                else:
                    route.continue_()

            page.route("**/api/state", delay_once)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(30)
            self.assertEqual(len(delayed), 1)
            quality.get_by_role("radio", name="High", exact=True).check()
            saved(page, quality)
            delayed[0].fulfill(json=stale)
            page.wait_for_timeout(30)
            self.assertTrue(
                quality.get_by_role("radio", name="High", exact=True).is_checked()
            )
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "unsaved narrow topic",
            )
            page.unroute("**/api/state", delay_once)
            pending = []
            page.route("**/api/action", lambda route: pending.append(route))
            topic.get_by_role("button", name="Save", exact=True).click()
            page.wait_for_timeout(30)
            self.assertEqual(len(pending), 1)
            topic.get_by_label("Topic", exact=True).fill("newer edit while saving")
            pending[0].continue_()
            page.wait_for_function(
                "() => !document.querySelector('.rating-footer .primary:disabled')"
            )
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "newer edit while saving",
            )
            self.assertEqual(topic.locator(".field-status").inner_text(), "Unsaved")
            self.assertEqual(
                page.evaluate("document.activeElement.value"), "newer edit while saving"
            )
            page.unroute("**/api/action")

            def reject(route):
                route.fulfill(status=400, json={"error": "Rating rejected"})

            page.route("**/api/action", reject)
            topic.get_by_role("button", name="Save", exact=True).click()
            topic.get_by_text("Rating rejected", exact=True).wait_for()
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(),
                "newer edit while saving",
            )

    def test_watched_dismiss_restore_and_platform_scroll(self):
        with dashboard_fixture() as (page, directory):
            row = page.locator(".video-row").first
            row.get_by_role("button", name="Mark watched", exact=True).click()
            row.get_by_role("button", name="Watched ✓", exact=True).wait_for()
            page.wait_for_function(
                "() => !document.querySelector('.row-actions button:disabled')"
            )
            row.get_by_role("button", name="Dismiss", exact=True).click()
            row.get_by_role("button", name="Restore", exact=True).wait_for()
            self.assertEqual(page.locator(".video-row").count(), 10)
            page.wait_for_function(
                "() => !document.querySelector('.row-actions button:disabled')"
            )
            row.get_by_role("button", name="Restore", exact=True).click()
            row.get_by_role("button", name="Dismiss", exact=True).wait_for()
            page.locator("#list-pane").evaluate("node => node.scrollTop=160")
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            page.locator("#list-pane").evaluate("node => node.scrollTop=60")
            page.get_by_role("tab", name="YouTube", exact=True).click()
            self.assertEqual(
                page.locator("#list-pane").evaluate("node=>node.scrollTop"), 160
            )
            page.get_by_role("button", name="Earlier unwatched (2)", exact=True).click()
            self.assertEqual(page.locator(".video-row").count(), 2)
            row.get_by_role("button", name="Dismiss", exact=True).click()
            row.get_by_role("button", name="Restore", exact=True).wait_for()
            self.assertEqual(page.locator(".video-row").count(), 2)
            page.wait_for_function(
                "() => !document.querySelector('.row-actions button:disabled')"
            )
            row.get_by_role("button", name="Restore", exact=True).click()
            row.get_by_role("button", name="Dismiss", exact=True).wait_for()
            page.get_by_role("button", name="Current picks", exact=True).click()
            self.assertEqual(page.locator(".video-row").count(), 10)
            with closing(Store(directory)) as store:
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM feedback_details"
                    ).fetchone()[0],
                    0,
                )

    def test_overlapping_refreshes_wait_for_current_read_and_preserve_new_save(self):
        with dashboard_fixture() as (page, _):
            stale = page.request.get(page.url + "api/state").json()
            pending = []
            reads = []

            def delay_first(route):
                reads.append(route)
                if len(reads) == 1:
                    pending.append(route)
                else:
                    route.continue_()

            page.route("**/api/state", delay_first)
            for _ in range(3):
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(50)
            self.assertEqual(len(reads), 1)
            inspector = open_first(page)
            quality = inspector.get_by_role("group", name="Video quality", exact=True)
            quality.get_by_role("radio", name="High", exact=True).check()
            saved(page, quality)
            self.assertEqual(len(reads), 1)
            pending[0].fulfill(json=stale)
            page.wait_for_timeout(50)
            self.assertEqual(len(reads), 2)
            self.assertTrue(
                quality.get_by_role("radio", name="High", exact=True).is_checked()
            )
            self.assertEqual(quality.locator(".field-status").inner_text(), "Saved")

    def test_refresh_releases_unused_records_but_keeps_unsaved_draft_context(self):
        with dashboard_fixture() as (page, _):
            inspector = open_first(page)
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic.get_by_label("Topic", exact=True).fill("Keep my draft")
            page.evaluate("""async () => {
                const {state} = await import('/static/state.js');
                const example = Object.values(state.data.videos)[0];
                state.data.videos['unused-video'] = {...example};
                state.data.videos['draft-video'] = {...example};
                state.drafts.set('draft-video|topic_interest', {value:'low', target:'Saved draft context'});
            }""")
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("""async () => {
                const {state} = await import('/static/state.js');
                return !state.data.videos['unused-video'] && !!state.data.videos['draft-video'];
            }""")
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "Keep my draft"
            )
