from contextlib import closing, redirect_stdout
import copy
import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from broader_vids.cli import main
from broader_vids.creators import status
from broader_vids.identity import identify
from broader_vids.learning.service import LEARNING_LOCK
from broader_vids.learning.transfer import export_preferences, import_preferences
from broader_vids.preferences import get_profile, set_profile, update_tag_rule
from broader_vids.recommendations.originality import provenance
from broader_vids.recommendations.parallel import assess_parallel
from broader_vids.recommendations.ranking import screen, rank_slate
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
)
from broader_vids.policy import Policy
from broader_vids.storage import Store
from test_recommendations import candidate, quality
from test_dashboard_feedback import dashboard_fixture, sync_playwright


def translated_item():
    item = candidate(1)
    item.update(identify("https://www.bilibili.com/video/BV1VYEM6tE74"))
    item["engagement"]["tags"] = ["视频翻译"]
    return item


class TagRuleTests(unittest.TestCase):
    def test_default_removal_survives_reopen_interest_edits_and_import_and_can_be_restored(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            with closing(Store(directory)) as store:
                set_profile(store, ["math"])
                update_tag_rule(store, "bilibili", "易语言")
                bundle = export_preferences(store)
                original = copy.deepcopy(get_profile(store))
                rules = status(store)["tags"]["bilibili"]
                self.assertIn(
                    {"tag": "视频翻译", "source": "builtin", "editable": True}, rules
                )
                item = translated_item()
                self.assertTrue(provenance(item, original)["excluded"])
                youtube = {**item, "platform": "youtube"}
                self.assertFalse(provenance(youtube, original)["excluded"])
                update_tag_rule(store, "bilibili", "视频翻译", remove=True)
                removed = copy.deepcopy(get_profile(store))
                self.assertFalse(provenance(item, removed)["excluded"])
                self.assertIn(
                    "translated_video_tag",
                    {signal["kind"] for signal in provenance(item, removed)["signals"]},
                )
                self.assertTrue(provenance(item, original)["excluded"])
                update_tag_rule(store, "bilibili", "视频翻译", remove=True)
                self.assertEqual(get_profile(store), removed)
                import_preferences(store, bundle)
                set_profile(store, ["physics"])
                exported = json.dumps(export_preferences(store), ensure_ascii=False)
                self.assertNotIn("disabled_builtin_tags", exported)
                self.assertNotIn("易语言", exported)
            with closing(Store(directory)) as store:
                self.assertFalse(provenance(item, get_profile(store))["excluded"])
                self.assertFalse(
                    any(
                        rule["tag"] == "视频翻译"
                        for rule in status(store)["tags"]["bilibili"]
                    )
                )
                self.assertTrue(
                    provenance(
                        {**item, "engagement": {"tags": ["易语言"]}}, get_profile(store)
                    )["excluded"]
                )
                update_tag_rule(store, "bilibili", "视频翻译")
                revision = get_profile(store)["revision"]
                update_tag_rule(store, "bilibili", "视频翻译")
                self.assertEqual(get_profile(store)["revision"], revision)
                self.assertTrue(provenance(item, get_profile(store))["excluded"])
                self.assertIn(
                    {"tag": "视频翻译", "source": "builtin", "editable": True},
                    status(store)["tags"]["bilibili"],
                )

    def test_screening_parallel_sequential_and_final_selection_honor_removal(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["math"])
            item = translated_item()
            original = copy.deepcopy(get_profile(store))
            update_tag_rule(store, "bilibili", "视频翻译", remove=True)
            current = get_profile(store)
            self.assertEqual(screen([item], [], {}, original)[0], [])
            shortlist, _ = screen([item], [], {}, current)
            self.assertEqual(len(shortlist), 1)
            item = shortlist[0]
            observations = {item["canonical_id"]: quality("fixture")}
            self.assertEqual(
                len(rank_slate(shortlist, observations, current, Policy().weights)), 1
            )
            self.assertEqual(
                rank_slate(shortlist, observations, original, Policy().weights), []
            )
            for profile, expected in ((original, 0), (current, 1)):
                identifier = create_recommendation(
                    store,
                    "bilibili",
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                )
                run = store.get(identifier)
                run["settings"]["profile"] = profile
                run["state"].update(phase="assess", shortlist=[item])
                worker = Mock(
                    side_effect=lambda root, work, *args: quality(work["evidence_run"])
                )
                assess_parallel(
                    store,
                    run,
                    lambda: store.save(run),
                    lambda: 60,
                    worker=worker,
                    quality_lookup=lambda *args: None,
                )
                self.assertEqual(worker.call_count, expected)
            identifier = create_recommendation(
                store,
                "bilibili",
                workers=1,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = store.get(identifier)
            run["state"].update(phase="assess", shortlist=[item], buffer=[item])
            store.save(run)
            with patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lambda store, key: quality(key),
            ):
                result = execute_recommendation(
                    store,
                    identifier,
                    prepare=Mock(side_effect=AssertionError("Unexpected download")),
                    assess=Mock(side_effect=AssertionError("Unexpected model call")),
                )
            self.assertEqual(len(result["state"]["selected"]), 1, result["reason"])

    def test_terminal_can_remove_and_readd_the_builtin_rule(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(Store(directory)) as store,
        ):
            set_profile(store, ["math"])
            for action, present in (("remove", False), ("add", True)):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(
                        main(
                            [
                                "--data-dir",
                                directory,
                                "exclusions",
                                "tag",
                                action,
                                "bilibili",
                                "视频翻译",
                            ]
                        ),
                        0,
                    )
                rules = json.loads(output.getvalue())["tags"]["bilibili"]
                self.assertEqual(
                    any(rule["tag"] == "视频翻译" for rule in rules), present
                )


@unittest.skipUnless(sync_playwright, "Playwright is not installed")
class TagRuleDashboardTests(unittest.TestCase):
    def test_builtin_tag_is_removable_persistent_and_can_be_added_again(self):
        with dashboard_fixture() as (page, directory):

            def tags():
                page.get_by_role("navigation").get_by_role(
                    "button", name="Exclusions", exact=True
                ).click()
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                page.get_by_role("tab", name="Tags", exact=True).click()
                return page.locator(".management-row").filter(
                    has=page.get_by_text("视频翻译", exact=True)
                )

            row = tags()
            self.assertTrue(row.get_by_text("Built-in", exact=True).is_visible())
            row.get_by_role("button", name="Remove", exact=True).click()
            page.wait_for_function(
                "() => !document.querySelector('#management').textContent.includes('视频翻译')"
            )
            page.reload(wait_until="domcontentloaded")
            page.locator(".video-row").first.wait_for()
            row = tags()
            self.assertEqual(row.count(), 0)
            page.get_by_label("Tag to exclude on Bilibili").fill("视频翻译")
            page.get_by_role("button", name="Add tag", exact=True).click()
            row.get_by_role("button", name="Remove", exact=True).wait_for()
            self.assertTrue(row.get_by_text("Built-in", exact=True).is_visible())
            with closing(Store(directory)) as store:
                self.assertTrue(
                    provenance(translated_item(), get_profile(store))["excluded"]
                )

    def test_tag_updates_do_not_require_fitting_and_serialize_without_blocking_navigation(
        self,
    ):
        with dashboard_fixture() as (page, directory):
            token = page.locator('meta[name="broader-vids-token"]').get_attribute(
                "content"
            )
            with closing(Store(directory)) as store, store.lease(LEARNING_LOCK):
                before = store.db.execute(
                    "SELECT COUNT(*) FROM learning_models"
                ).fetchone()[0]
                response = page.request.post(
                    page.url + "api/action",
                    headers={"X-Broader-Vids-Token": token},
                    data={
                        "action": "remove_tag",
                        "id": "all",
                        "platform": "bilibili",
                        "tag": "视频翻译",
                    },
                )
                self.assertEqual(response.status, 200, response.text())
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM learning_models").fetchone()[
                        0
                    ],
                    before,
                )
                update_tag_rule(store, "bilibili", "视频翻译")
            page.reload(wait_until="domcontentloaded")
            page.locator(".video-row").first.wait_for()
            page.get_by_role("navigation").get_by_role(
                "button", name="Exclusions", exact=True
            ).click()
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            page.get_by_role("tab", name="Tags", exact=True).click()
            row = page.locator(".management-row").filter(
                has=page.get_by_text("视频翻译", exact=True)
            )
            pending = []

            def hold(route):
                pending.append(route)

            page.route("**/api/action", hold)
            try:
                with page.expect_request("**/api/action"):
                    row.get_by_role("button", name="Remove", exact=True).click()
                page.get_by_label("Tag to exclude on Bilibili").fill("Retained draft")
                self.assertTrue(
                    page.get_by_role("button", name="Add tag", exact=True).is_disabled()
                )
                page.get_by_role("tab", name="YouTube", exact=True).click()
                page.get_by_role("tab", name="Tags", exact=True).click()
                self.assertTrue(
                    page.get_by_role("button", name="Add tag", exact=True).is_enabled()
                )
                page.get_by_role("tab", name="Bilibili", exact=True).click()
                self.assertEqual(
                    page.get_by_label("Tag to exclude on Bilibili").input_value(),
                    "Retained draft",
                )
                pending.pop(0).fulfill(
                    status=503, json={"error": "Fixture rule save failure"}
                )
                page.get_by_text("Fixture rule save failure", exact=True).wait_for()
                self.assertTrue(
                    row.get_by_role("button", name="Remove", exact=True).is_enabled()
                )
                self.assertTrue(
                    page.get_by_role("button", name="Add tag", exact=True).is_enabled()
                )
                with closing(Store(directory)) as store:
                    self.assertTrue(
                        provenance(translated_item(), get_profile(store))["excluded"]
                    )
            finally:
                for route in pending:
                    route.abort()
                page.unroute("**/api/action", hold)

    def test_delayed_creator_response_cannot_restore_removed_tag_rules(self):
        with dashboard_fixture() as (page, directory):
            page.get_by_role("tab", name="Bilibili", exact=True).click()
            inspector = page.locator("#inspector-body")
            page.locator(".video-row").first.get_by_role(
                "button", name="Rate", exact=True
            ).click()
            held = []

            def hold_creator(route):
                if route.request.post_data_json["action"] == "creator_rule":
                    held.append((route, route.fetch()))
                else:
                    route.continue_()

            page.route("**/api/action", hold_creator)
            try:
                with page.expect_request("**/api/action"):
                    inspector.get_by_role(
                        "checkbox", name="Exclude this creator from future rounds"
                    ).check()
                page.get_by_role("navigation").get_by_role(
                    "button", name="Exclusions", exact=True
                ).click()
                page.get_by_role("tab", name="Tags", exact=True).click()
                row = page.locator(".management-row").filter(
                    has=page.get_by_text("视频翻译", exact=True)
                )
                row.get_by_role("button", name="Remove", exact=True).click()
                page.wait_for_function(
                    "() => !document.querySelector('#management').textContent.includes('视频翻译')"
                )
                page.route(
                    "**/api/exclusions",
                    lambda route: route.fulfill(
                        status=503, json={"error": "Fixture refresh unavailable"}
                    ),
                )
                route, response = held.pop(0)
                route.fulfill(response=response)
                page.wait_for_function(
                    "async () => !(await import('/static/state.js')).state.busy.size"
                )
                self.assertEqual(row.count(), 0)
                with closing(Store(directory)) as store:
                    self.assertFalse(
                        provenance(translated_item(), get_profile(store))["excluded"]
                    )
            finally:
                for route, response in held:
                    route.abort()
                page.unroute("**/api/action", hold_creator)
