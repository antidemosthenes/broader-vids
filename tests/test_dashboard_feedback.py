import re
from contextlib import closing, contextmanager
import atexit
import json
import os
import sys
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    expect = None
    sync_playwright = None

from broader_vids.learning.features import capture
from broader_vids.policy import Policy
from broader_vids.preferences import set_profile
from broader_vids.storage import Store
from broader_vids.web.server import make_server
from test_learning import example


_playwright = None
_browser = None


def _dashboard_browser():
    global _playwright, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    _stop_dashboard_browser()
    _playwright = sync_playwright().start()
    try:
        selected = os.environ.get("BROADER_VIDS_TEST_BROWSER") or (
            "msedge" if sys.platform == "win32" else "chromium"
        )
        if selected not in {"chromium", "msedge"}:
            raise ValueError("Choose chromium or msedge for BROADER_VIDS_TEST_BROWSER.")
        _browser = _playwright.chromium.launch(
            headless=True,
            **({"channel": selected} if selected != "chromium" else {}),
        )
    except BaseException:
        _playwright.stop()
        _playwright = None
        raise
    return _browser


def _stop_dashboard_browser():
    global _playwright, _browser
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        finally:
            _playwright = None


if sync_playwright:
    atexit.register(_stop_dashboard_browser)


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
                            url=f"https://www.bilibili.com/video/BV1Z6H56oEcQ?p={index}",
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
                    if index in (3, 4):
                        item["ranking_adjustments"] = {
                            "version": 2,
                            "score_before_duration_penalty": 85,
                            "short_video_penalty": 5,
                            "final_score": 80,
                        }
                        if index == 3:
                            item["ranking_adjustments"].update(
                                score_before_duration_penalty=100,
                                course_series_penalty=10,
                                course_series={
                                    "part_count": 20,
                                    "total_duration_seconds": 93600,
                                    "status": "recorded_course",
                                    "confidence": "high",
                                    "rationale": "Recorded course lessons.",
                                },
                                portrait_video_penalty=5,
                                video_dimensions={"width": 360, "height": 640},
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
                        "published_at": None if provisional else "2026-11-19T12:00:00Z",
                        "phase": "assess" if provisional else "published",
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
            context = _dashboard_browser().new_context(
                viewport={"width": 1440, "height": 900}
            )
            try:
                page = context.new_page()
                page.set_default_timeout(8000)
                startup_errors = []

                def record_startup_error(message):
                    if len(startup_errors) < 8:
                        startup_errors.append(str(message)[:1000])

                page.on("pageerror", record_startup_error)
                page.on(
                    "requestfailed",
                    lambda request: record_startup_error(
                        f"{request.url}: {request.failure}"
                    ),
                )
                page.goto(
                    f"http://127.0.0.1:{server.server_port}",
                    wait_until="domcontentloaded",
                )
                try:
                    page.locator(".video-row").first.wait_for()
                except Exception as error:
                    error.add_note(
                        "Dashboard startup errors: " + json.dumps(startup_errors)
                    )
                    raise
                yield page, directory
            finally:
                context.close()
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


def saved(group):
    button = group.get_by_role("button", name="Save", exact=True)
    button.click()
    expect(button).to_have_accessible_description(re.compile("^Saved rating for "))
    expect(button).to_be_enabled()
    expect(group.locator(".field-status")).to_be_empty()


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class DashboardFeedbackTests(unittest.TestCase):
    def test_appearance_follows_system_until_overridden_and_remembers_choice(self):
        with dashboard_fixture() as (page, _):
            page.emulate_media(color_scheme="dark", reduced_motion="reduce")
            root = page.locator("html")
            toggle = page.locator("#appearance-toggle")
            expect(toggle).to_have_accessible_name("Use light theme")
            dark = root.evaluate("(node) => getComputedStyle(node).backgroundColor")
            topic = open_first(page).get_by_label("Topic", exact=True)
            page.mouse.move(0, 0)
            selected = page.locator(".video-row.selected")
            rate = selected.get_by_role("button", name="Rate", exact=True)
            expect(rate).to_have_css("border-top-color", "rgba(0, 0, 0, 0)")
            row_fill = selected.evaluate(
                "(item) => getComputedStyle(item).backgroundColor"
            )
            expect(rate).not_to_have_css("background-color", row_fill)
            topic.fill("Draft during a theme switch")
            toggle.press("Space")
            expect(root).to_have_attribute("data-theme", "light")
            expect(root).not_to_have_css("background-color", dark)
            expect(topic).to_have_value("Draft during a theme switch")
            expect(toggle).to_have_accessible_name("Use dark theme")

            peer = page.context.new_page()
            try:
                pending = []
                peer.route(
                    "**/static/dashboard.js", lambda route: pending.append(route)
                )
                peer.goto(page.url, wait_until="commit")
                expect(peer.locator("html")).to_have_attribute("data-theme", "light")
                peer.evaluate(
                    """window.addEventListener("storage", event => {
                        window.themeStorageEvent = event.newValue;
                    }, {once: true})"""
                )
                page.emulate_media(color_scheme="light")
                toggle.click()
                expect(root).to_have_css("background-color", dark)
                peer.wait_for_function("window.themeStorageEvent === 'dark'")
                self.assertEqual(len(pending), 1)
                pending.pop().continue_()
                expect(peer.locator("#appearance-toggle")).to_have_accessible_name(
                    "Use light theme"
                )
                expect(peer.locator("html")).to_have_attribute("data-theme", "dark")
                toggle.click()
                expect(peer.locator("html")).to_have_attribute("data-theme", "light")
                toggle.click()
                page.reload(wait_until="domcontentloaded")
                expect(root).to_have_css("background-color", dark)
                expect(toggle).to_have_accessible_name("Use light theme")
            finally:
                peer.close()

            page.add_init_script(
                """Object.defineProperty(window, "localStorage", {
                    get() { throw new DOMException("Storage is unavailable", "SecurityError"); }
                });"""
            )
            page.reload(wait_until="domcontentloaded")
            expect(toggle).to_have_accessible_name("Use dark theme")
            toggle.click()
            expect(root).to_have_css("background-color", dark)

    def test_manual_level_and_independent_drafts_survive_navigation_save_and_clear(
        self,
    ):
        with dashboard_fixture(provisional=True) as (page, directory):
            page.emulate_media(
                color_scheme="light",
                contrast="no-preference",
                forced_colors="none",
                reduced_motion="reduce",
            )
            self.assertTrue(page.locator("#round-notice").is_visible())
            self.assertEqual(page.locator(".video-row").count(), 10)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            inspector = open_first(page)
            badge = page.locator(".video-row").first.locator(".row-review-state")
            expect(badge).to_have_text("0/4 rated")
            expect(
                page.locator(".video-row").first.get_by_role(
                    "button", name="Rate", exact=True
                )
            ).to_have_accessible_description(
                "0/4 rated. Missing ratings for Specific topic, Depth for this video, Video quality, Broader subject."
            )
            depth = inspector.get_by_role(
                "group", name="Depth for this video", exact=True
            )
            topic = inspector.get_by_role("group", name="Specific topic", exact=True)
            topic_save = topic.get_by_role("button", name="Save", exact=True)
            expect(topic_save).to_have_accessible_description(
                "No saved rating for Specific topic."
            )
            unrated_fill = topic_save.evaluate(
                "(button) => getComputedStyle(button).backgroundColor"
            )
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
            expect(badge).to_have_text("0/4 rated")
            saved(depth)
            expect(badge).to_have_text("1/4 rated")
            expect(inspector.locator(".rating-progress")).to_have_count(0)
            expect(topic.get_by_role("button", name="Save", exact=True)).to_have_class(
                re.compile("unrated")
            )
            page.wait_for_function(
                "() => document.querySelector('#inspector-body select').value === 'advanced'"
            )
            self.assertTrue(
                topic.get_by_role("radio", name="Low", exact=True).is_checked()
            )
            saved(topic)
            expect(badge).to_have_text("2/4 rated")
            page.mouse.move(0, 0)
            expect(topic_save).not_to_have_css("background-color", unrated_fill)
            expect(topic.get_by_role("radio", name="Low", exact=True)).to_be_checked()
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
            saved(depth)
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
            badge = page.locator(".video-row").first.locator(".row-review-state")
            expect(badge).to_have_text("1/4 rated")
            for label in ("Video quality", "Broader subject"):
                group = inspector.get_by_role("group", name=label, exact=True)
                group.get_by_role("radio", name="High", exact=True).check()
                saved(group)
            expect(badge).to_have_text("3/4 rated")
            topic.get_by_role("radio", name="Low", exact=True).check()
            saved(topic)
            expect(badge).to_have_text("Reviewed")
            row = page.locator(".video-row").first
            row.get_by_role("button", name="Watched ✓", exact=True).wait_for()
            row.get_by_role("button", name="Watched ✓", exact=True).click()
            row.get_by_role("button", name="Mark watched", exact=True).wait_for()
            page.reload(wait_until="domcontentloaded")
            row = page.locator(".video-row").first
            expect(row.locator(".row-review-state")).to_have_text("Reviewed")
            row.get_by_role("button", name="Mark watched", exact=True).wait_for()
            inspector = open_first(page)
            expect(inspector.locator(".rating-progress")).to_have_count(0)
            expect(topic.locator(".field-status")).to_be_empty()
            expect(topic_save).to_have_accessible_description(
                "Saved rating for Specific topic."
            )
            quality = inspector.get_by_role("group", name="Video quality", exact=True)
            quality.get_by_role("button", name="Clear", exact=True).click()
            expect(badge).to_have_text("3/4 rated")
            expect(quality.get_by_role("button", name="Save", exact=True)).to_have_css(
                "background-color", unrated_fill
            )
            self.assertFalse(errors, errors)

    def test_navigation_motion_replaces_old_animation_and_respects_reduced_motion(
        self,
    ):
        with dashboard_fixture() as (page, _):
            page.emulate_media(reduced_motion="no-preference")
            result = page.evaluate(
                """() => {
                    document.querySelector('[data-view="calibration"]').click();
                    const list = document.querySelector("#list-pane").getAnimations()[0];
                    const subtitleHidden = document.querySelector("#page-subtitle").hidden;
                    document.querySelector('[data-view="exclusions"]').click();
                    const pageMotion = document.querySelector("#management").getAnimations()[0];
                    document.querySelector("#exclusions-tags-tab").click();
                    const panel = document.querySelector("#exclusion-rules");
                    return [
                        subtitleHidden,
                        list?.playState,
                        pageMotion?.playState,
                        panel.getAnimations()[0]?.playState
                    ];
                }"""
            )
            self.assertEqual(result, [True, "idle", "idle", "running"])
            expect(page.get_by_role("tabpanel", name="Tags")).to_be_visible()
            page.emulate_media(reduced_motion="reduce")
            self.assertEqual(
                page.evaluate(
                    """() => {
                        document.querySelector("#exclusions-creators-tab").click();
                        return document.querySelector("#exclusion-rules").getAnimations().length;
                    }"""
                ),
                0,
            )
            expect(page.get_by_role("tabpanel", name="Creators")).to_be_visible()

    def test_mobile_dialog_details_keyboard_and_layout(self):
        with dashboard_fixture() as (page, _):
            page.emulate_media(reduced_motion="no-preference")
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
            details = page.locator("#details-dialog")
            expect(details).to_have_css("animation-name", "panel-enter")
            page.emulate_media(reduced_motion="reduce")
            expect(details).to_have_css("animation-name", "none")
            expect(
                details.get_by_role(
                    "row", name="Short-video deduction −5.00 points", exact=True
                )
            ).to_be_visible()
            expect(
                page.locator("#details-dialog").get_by_role(
                    "row", name="Portrait video deduction −5.00 points", exact=True
                )
            ).to_be_visible()
            expect(
                page.locator("#details-dialog").get_by_role(
                    "row", name="Video dimensions 360 × 640", exact=True
                )
            ).to_be_visible()
            expect(
                page.locator("#details-dialog").get_by_role(
                    "row", name="Course series deduction −10.00 points", exact=True
                )
            ).to_be_visible()
            expect(
                page.locator("#details-dialog").get_by_role(
                    "row", name="Series length 20 parts · 26.00 hours", exact=True
                )
            ).to_be_visible()
            expect(
                page.locator("#details-dialog").get_by_role(
                    "row", name="Final score 80.00 / 100", exact=True
                )
            ).to_be_visible()
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
            expect(
                page.locator("#mobile-review-body").get_by_role(
                    "row", name="Short-video deduction −5.00 points", exact=True
                )
            ).to_be_visible()
            page.set_viewport_size({"width": 844, "height": 390})
            expect(page.locator("html")).not_to_have_attribute("data-mobile", "")
            expect(page.locator("#mobile-review-title")).to_have_text("Score details")
            expect(page.locator(".video-row.selected .rate-button")).to_have_attribute(
                "aria-expanded", "true"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("html")).to_have_attribute("data-mobile", "")
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
            expect(page.locator(".video-row.selected .rate-button")).to_have_attribute(
                "aria-expanded", "false"
            )
            page.emulate_media(forced_colors="active", reduced_motion="reduce")
            expect(page.locator(".video-row.selected .video-title")).to_have_css(
                "forced-color-adjust", "auto"
            )
            active_tab = page.get_by_role("tab", selected=True)
            active_tab.press("Tab")
            page.keyboard.press("Shift+Tab")
            expect(active_tab).to_be_focused()
            expect(active_tab).to_have_css("outline-style", "solid")
            expect(active_tab).to_have_css("outline-offset", "2px")
            page.evaluate("document.documentElement.style.fontFamily = 'monospace'")
            actions = page.locator(".row-actions").first
            for width in (390, 320):
                if width == 320:
                    page.set_viewport_size({"width": width, "height": 844})
                    page.evaluate("document.documentElement.style.fontSize = '200%'")
                self.assertLessEqual(page.evaluate("document.body.scrollWidth"), width)
                self.assertTrue(
                    page.locator("#list-pane").evaluate(
                        "(pane) => pane.scrollWidth <= pane.clientWidth"
                    )
                )
                for name in ("Mark watched", "Dismiss", "Rate"):
                    expect(
                        actions.get_by_role("button", name=name, exact=True)
                    ).to_be_visible()
                self.assertTrue(
                    actions.evaluate(
                        """(actions) => actions.scrollWidth <= actions.clientWidth
                        && [...actions.children].every(button =>
                            button.scrollWidth <= button.clientWidth
                            && button.scrollHeight <= button.clientHeight)"""
                    ),
                    f"Recommendation actions overflow at {width}px",
                )
            page.evaluate("document.documentElement.style.fontFamily = ''")
            page.locator(".video-row").first.get_by_role(
                "button", name="Rate", exact=True
            ).click()
            expect(page.locator("#review-dialog")).to_be_visible()

            page.keyboard.press("Escape")
            page.evaluate("document.documentElement.style.fontSize = ''")
            page.set_viewport_size({"width": 1440, "height": 200})
            inspector = open_first(page)
            topic_input = inspector.get_by_label("Topic", exact=True)
            topic_input.fill("A draft in a short desktop window")
            expect(topic_input).to_have_value("A draft in a short desktop window")

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
            saved(quality)
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
            page.emulate_media(reduced_motion="reduce", forced_colors="none")
            open_first(page)
            row = page.locator(".video-row").first
            watched = row.locator("button[aria-pressed]")
            expect(watched).to_have_class(re.compile(r"\blist-action\b"))
            resting_style = """(button) => {
                const {color, backgroundColor} = getComputedStyle(button);
                return [color, backgroundColor, button.offsetWidth];
            }"""
            before = watched.evaluate(resting_style)
            watched.click()
            expect(watched).to_have_attribute("aria-pressed", "true")
            expect(watched).to_have_text("Watched ✓")
            page.mouse.move(0, 0)
            self.assertEqual(watched.evaluate(resting_style), before)
            page.wait_for_function(
                "() => !document.querySelector('.row-actions button:disabled')"
            )
            dismiss = row.get_by_role("button", name="Dismiss", exact=True)
            restore = row.get_by_role("button", name="Restore", exact=True)
            expect(dismiss).to_have_class(re.compile(r"\blist-action\b"))
            dismiss.click()
            expect(restore).to_have_class(re.compile(r"\blist-action\b"))
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
            saved(quality)
            self.assertEqual(len(reads), 1)
            pending[0].fulfill(json=stale)
            page.wait_for_timeout(50)
            self.assertEqual(len(reads), 2)
            self.assertTrue(
                quality.get_by_role("radio", name="High", exact=True).is_checked()
            )
            expect(quality.locator(".field-status")).to_be_empty()
            expect(
                quality.get_by_role("button", name="Save", exact=True)
            ).to_have_accessible_description("Saved rating for Video quality.")

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
                for (const id of ['unused-video', 'draft-video']) {
                    state.inspectorScrolls.set(id, 123);
                    for (const key of [`${id}|topic_interest`, `video:${id}`, `creator:${id}`])
                        state.notes.set(key, {text:'Saved'});
                }
            }""")
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("""async () => {
                const {state} = await import('/static/state.js');
                return !state.data.videos['unused-video'] && !!state.data.videos['draft-video']
                    && !state.inspectorScrolls.has('unused-video')
                    && !state.notes.has('unused-video|topic_interest')
                    && !state.notes.has('video:unused-video')
                    && !state.notes.has('creator:unused-video')
                    && state.inspectorScrolls.get('draft-video') === 123
                    && state.notes.has('draft-video|topic_interest');
            }""")
            self.assertEqual(
                topic.get_by_label("Topic", exact=True).input_value(), "Keep my draft"
            )
