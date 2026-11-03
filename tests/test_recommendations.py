import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from broader_vids.web.server import make_server
from broader_vids.recommendations.engagement import (
    parse_bilibili,
    parse_youtube,
    rate_scores,
)
from broader_vids.http import AccessPaused
from broader_vids.preferences import (
    pending_videos,
    platform_lock,
    set_profile,
    user_action,
    video_states,
)
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
    publish_recommendation,
)
from broader_vids.recommendations.ranking import rank_slate, screen
from broader_vids.recommendations.reports import (
    write_recommendation_report,
    recommendation_report,
)
from broader_vids.storage import Store, utc_now


def candidate(n, platform="youtube"):
    video_id = f"{n:010d}A" if platform == "youtube" else "BV1" + str(n + 111111111)
    identifier = (
        f"youtube:{video_id}" if platform == "youtube" else f"bilibili:{video_id}:p1"
    )
    domain = "mathematics" if n % 2 else "conservation"
    return {
        "canonical_id": identifier,
        "platform": platform,
        "video_id": video_id,
        "part_index": 1,
        "url": f"https://www.youtube.com/watch?v={video_id}"
        if platform == "youtube"
        else f"https://www.bilibili.com/video/{video_id}?p=1",
        "creator_id": f"creator{n % 40}",
        "title": f"Example {n}",
        "duration_seconds": 300,
        "origins": [{"family_id": f"family{n % 10}", "domains": [domain]}],
        "engagement": {
            "views": 10000,
            "likes": 100 + n * 10,
            "coins": 20 + n,
            "favorites": 40 + n * 2,
        },
    }


def quality(identifier):
    return {
        "evidence_run": identifier,
        "quality": 80,
        "duration_seconds": 300,
        "summary": "Fixture assessment",
        "unknowns": ["Asset provenance"],
    }


class EngagementTests(unittest.TestCase):
    def test_unrecognized_count_shapes_pause_instead_of_crashing(self):
        with self.assertRaises(AccessPaused):
            parse_bilibili(
                {"code": 0, "data": {"bvid": "BV1111111111", "pages": None}},
                "BV1111111111",
            )
        with self.assertRaises(AccessPaused):
            parse_youtube(
                'ytInitialPlayerResponse = {"videoDetails":null}', "0000000001A"
            )

    def test_shrinkage_missing_and_platform_specific_metrics(self):
        small, large, missing = candidate(1), candidate(2), candidate(3)
        small["engagement"] = {"views": 10, "likes": 1}
        large["engagement"] = {"views": 100000, "likes": 5000}
        missing["engagement"] = {"views": 0, "likes": 10}
        scores = rate_scores([small, large, missing])
        self.assertGreater(
            scores[large["canonical_id"]]["score"],
            scores[small["canonical_id"]]["score"],
        )
        self.assertEqual(scores[missing["canonical_id"]]["score"], 50)
        bili = candidate(4, "bilibili")
        self.assertEqual(
            set(rate_scores([bili])[bili["canonical_id"]]["features"]),
            {"likes", "coins", "favorites"},
        )

    def test_metadata_checks_identity_and_preserves_archive_scope(self):
        value = {
            "code": 0,
            "data": {
                "bvid": "BV1111111111",
                "stat": {"view": 100, "like": 4, "coin": 2, "favorite": 3},
                "pages": [{"page": 2, "cid": 123, "duration": 100}],
            },
        }
        result = parse_bilibili(value, "BV1111111111")
        self.assertEqual(
            (result["likes"], result["coins"], result["favorites"], result["scope"]),
            (4, 2, 3, "archive"),
        )
        with self.assertRaises(AccessPaused):
            parse_bilibili(value, "BV1222222222")
        player = {
            "videoDetails": {
                "videoId": "0000000001A",
                "viewCount": "100",
                "lengthSeconds": "30",
            }
        }
        initial = {
            "contents": {
                "toggleButtonRenderer": {
                    "defaultIcon": {"iconType": "LIKE"},
                    "defaultText": {
                        "accessibility": {"accessibilityData": {"label": "1,234 likes"}}
                    },
                }
            }
        }
        body = (
            "var ytInitialPlayerResponse = "
            + json.dumps(player)
            + "; var ytInitialData = "
            + json.dumps(initial)
        )
        self.assertEqual(parse_youtube(body, "0000000001A")["likes"], 1234)


class RecommendationTests(unittest.TestCase):
    def test_paused_round_exposes_a_preview_without_publishing_history(self):
        source = self.source()

        def interrupted(store, run, save, *args, **kwargs):
            for item in run["state"]["shortlist"][:4]:
                rid = next(
                    store.iter_runs(kind="evidence", identities=[item["canonical_id"]])
                )["id"]
                run["state"]["evidence"][item["canonical_id"]] = quality(rid)
            save()
            raise EvidencePending("A candidate needs retry.")

        from broader_vids.evidence.media import EvidencePending

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                side_effect=lambda h, i: {**i["engagement"], "observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.parallel.assess_parallel",
                side_effect=interrupted,
            ),
            patch(
                "broader_vids.recommendations.preview.cached_quality",
                side_effect=lambda s, key: quality(key),
            ),
        ):
            rid = create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = execute_recommendation(self.store, rid)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["state"]["selected"], [])
        self.assertEqual(len(run["state"]["preview"]), 4)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM recommendation_exposures"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(pending_videos(self.store, "youtube"), [])
        self.assertIn(
            "Provisional preview",
            write_recommendation_report(self.store, rid).read_text(encoding="utf-8"),
        )

    def test_malformed_feedback_kind_is_rejected_without_modifying_state(self):
        with self.assertRaises(ValueError):
            user_action(self.store, self.items[0]["canonical_id"], "up", kind=[])
        self.assertEqual(video_states(self.store), {})

    def test_publication_validation_does_not_hold_a_database_write_lock(self):
        source = self.source()
        checked = []

        def observation(store, evidence_id):
            checked.append(not store.db.in_transaction)
            return quality(evidence_id)

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=observation,
            ),
        ):
            result = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    source,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(checked)
        self.assertTrue(all(checked))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.profile = set_profile(self.store, ["math"])
        self.items = [candidate(i + 1) for i in range(100)]
        for index, value in enumerate(self.items):
            value["origins"][0]["domains"] = [
                "mathematics" if index % 10 < 7 or index == 97 else "conservation"
            ]

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def source(self):
        with self.store.db:
            identifier = self.store.create(
                "youtube",
                {"kind": "discovery"},
                {
                    "phase": "finished",
                    "candidates": {i["canonical_id"]: i for i in self.items},
                },
                commit=False,
            )
            for item in self.items:
                self.store.create(
                    "youtube",
                    {
                        "kind": "evidence",
                        "identity": {"canonical_id": item["canonical_id"]},
                    },
                    {},
                    commit=False,
                )
        return identifier

    def insert_pending(self, items):
        with self.store.db:
            for item in items:
                self.store.db.execute(
                    "INSERT INTO video_state VALUES(?,?,?,0,0,1,?)",
                    (
                        item["canonical_id"],
                        item["platform"],
                        json.dumps(item),
                        utc_now(),
                    ),
                )

    def test_screen_selects_global_top35_without_reserved_pending_slots(self):
        shortlist, rejected = screen(self.items, [], {}, self.profile)
        self.assertEqual(len(shortlist), 35)
        self.assertEqual(len(rejected), 65)
        self.assertEqual(
            [i["screening_score"] for i in shortlist],
            sorted((i["screening_score"] for i in shortlist), reverse=True),
        )
        pending = self.items[:50]
        selected, _ = screen(self.items, pending, {}, self.profile)
        self.assertEqual(
            [i["canonical_id"] for i in selected],
            [i["canonical_id"] for i in shortlist],
        )
        self.assertFalse(any(i["carryover"] for i in selected))
        selected, _ = screen(self.items, self.items[:70], {}, self.profile)
        self.assertEqual(len(selected), 35)

    def test_inspection_limit_is_saved_and_old_policy_is_rejected_preflight(self):
        from broader_vids.recommendations.screening import inspection_limit

        source = self.source()
        identifier = create_recommendation(
            self.store,
            "youtube",
            source,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        run = self.store.get(identifier)
        self.assertEqual(run["settings"]["inspection_limit"], 35)
        self.assertEqual(inspection_limit(run["settings"]), 35)
        original_state, original_updated = run["state"], run["updated_at"]
        from broader_vids.cli.arguments import parser
        from broader_vids.cli.startup import prepare_recommendation

        for field, old_value in (
            ("screening_policy_version", 4),
            ("creator_policy_version", 0),
            ("time_policy_version", 1),
        ):
            with self.subTest(field=field):
                settings = {**run["settings"], field: old_value}
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE runs SET settings=? WHERE id=?",
                        (json.dumps(settings), identifier),
                    )
                with self.assertRaisesRegex(ValueError, "unsupported"):
                    execute_recommendation(self.store, identifier)
                with patch("broader_vids.cli.startup.preflight") as probe:
                    with self.assertRaisesRegex(ValueError, "unsupported"):
                        prepare_recommendation(
                            self.store, parser().parse_args(["continue", identifier])
                        )
                    probe.assert_not_called()
                unchanged = self.store.get(identifier)
                self.assertEqual(unchanged["state"], original_state)
                self.assertEqual(unchanged["updated_at"], original_updated)

    def test_recommendation_rejects_a_legacy_discovery_source(self):
        source = self.source()
        discovery = self.store.get(source)
        discovery["state"]["exploration"] = {"policy_version": 1}
        self.store.save(discovery)
        with self.assertRaisesRegex(ValueError, "unsupported exploration policy"):
            create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )

    def test_vote_watch_dismiss_and_revision_are_independent(self):
        self.insert_pending(self.items[:1])
        key = self.items[0]["canonical_id"]
        user_action(self.store, key, "down", "Not my subject", "interest")
        self.assertEqual(len(pending_videos(self.store, "youtube")), 1)
        user_action(self.store, key, "watched")
        self.assertFalse(video_states(self.store)[key]["pending"])
        user_action(self.store, key, "up", "Changed my mind", "quality")
        self.assertTrue(video_states(self.store)[key]["watched"])
        self.assertEqual(video_states(self.store)[key]["feedback"]["vote"], "up")
        user_action(self.store, key, "unwatched")
        user_action(self.store, key, "dismiss")
        self.assertFalse(video_states(self.store)[key]["pending"])
        user_action(self.store, key, "restore")
        user_action(self.store, key, "clear")
        self.assertEqual(len(pending_videos(self.store, "youtube")), 1)
        self.assertIsNone(video_states(self.store)[key]["feedback"]["vote"])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM user_actions").fetchone()[0], 7
        )

    def test_slate_excludes_low_quality_video(self):
        shortlist, _ = screen(self.items, [], {}, self.profile)
        observations = {i["canonical_id"]: quality("fixture") for i in shortlist}
        observations[shortlist[0]["canonical_id"]]["quality"] = 20
        weights = {
            "quality": 0.6,
            "interest": 0.25,
            "engagement": 0.05,
            "recency": 0.05,
            "diversity": 0.05,
        }
        selected = rank_slate(shortlist, observations, self.profile, weights)
        self.assertEqual(len(selected), 10)
        self.assertNotIn(
            shortlist[0]["canonical_id"], {i["canonical_id"] for i in selected}
        )

    def test_cached_full_workflow_and_next_round_carryover(self):
        source = self.source()
        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                side_effect=lambda h, i: {**i["engagement"], "observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lambda s, i: quality(i),
            ),
        ):
            first = create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = execute_recommendation(self.store, first)
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["state"]["model_calls"], 0)
            self.assertEqual(len(pending_videos(self.store, "youtube")), 10)
            self.assertTrue(write_recommendation_report(self.store, first).is_file())
            self.assertEqual(
                execute_recommendation(self.store, first)["state"]["selected"],
                run["state"]["selected"],
            )
            user_action(
                self.store, run["state"]["selected"][0]["canonical_id"], "watched"
            )
            self.items.append(candidate(101))
            source = self.source()
            second = create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            new = execute_recommendation(self.store, second)
            self.assertEqual(sum(i["carryover"] for i in new["state"]["shortlist"]), 9)
            self.assertFalse(
                any(
                    i["canonical_id"] == run["state"]["selected"][0]["canonical_id"]
                    for i in new["state"]["shortlist"]
                )
            )
            self.assertEqual(
                self.store.db.execute(
                    "SELECT COUNT(*) FROM recommendation_exposures"
                ).fetchone()[0],
                20,
            )

    def test_full_pending_queue_does_not_block_new_screening(self):
        source = self.source()
        self.insert_pending(self.items[:50])
        with (
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lambda s, i: quality(i),
            ),
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
        ):
            run = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    source,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(len(run["state"]["buffer"]), 100)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(pending_videos(self.store, "youtube")), 60)

    def test_profile_changes_and_parallel_publication_are_guarded(self):
        source = self.source()
        identifier = create_recommendation(
            self.store,
            "youtube",
            source,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        set_profile(self.store, ["physics"])
        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lambda s, i: quality(i),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "profile changed"):
                execute_recommendation(self.store, identifier)
        self.assertEqual(len(pending_videos(self.store, "youtube")), 0)
        with self.store.lease(platform_lock("youtube")):
            user_action(self.store, self.items[0]["canonical_id"], "watched")
            self.assertTrue(
                video_states(self.store)[self.items[0]["canonical_id"]]["watched"]
            )

    def test_zero_model_budget_preserves_checkpoint_without_publication(self):
        source = self.source()
        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality", return_value=None
            ),
        ):
            run = execute_recommendation(
                self.store,
                create_recommendation(
                    self.store,
                    "youtube",
                    source,
                    max_assessments=0,
                    assessment_model="fixture-model",
                    reasoning_effort="low",
                ),
            )
        self.assertEqual(run["status"], "paused")
        self.assertIsNone(run["state"]["published_at"])

    def test_candidate_failures_retry_three_times_without_blocking_the_sequential_route(
        self,
    ):
        source = self.source()
        blocked = self.items[-1]["canonical_id"]
        calls, evidence_ids = [], []

        def interrupted(store, evidence_id, asr_model, **limits):
            calls.append(limits)
            evidence_ids.append(evidence_id)
            child = store.get(evidence_id)
            child["state"]["retryable_failure"] = True
            child.update(status="pending", reason="Temporary extraction failure")
            store.save(child)
            return child

        def lookup(store, identifier):
            return (
                None
                if store.get(identifier)["settings"]["identity"]["canonical_id"]
                == blocked
                else quality(identifier)
            )

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                side_effect=lambda h, i: {**i["engagement"], "observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lookup,
            ),
        ):
            identifier = create_recommendation(
                self.store,
                "youtube",
                source,
                max_seconds=30,
                max_mib=2,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            run = execute_recommendation(self.store, identifier, prepare=interrupted)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(set(evidence_ids)), 1)
        self.assertEqual(run["state"]["candidate_retry_counts"][blocked], 3)
        self.assertEqual(run["state"]["evidence"][blocked]["status"], "unavailable")
        self.assertEqual(run["state"]["assessment_index"], 35)
        self.assertTrue(all(call["max_seconds"] <= 30 for call in calls))
        self.assertTrue(all(call["max_new_bytes"] == 2 * 1024 * 1024 for call in calls))
        self.assertEqual(len(pending_videos(self.store, "youtube")), 10)

    def test_sequential_unscored_assessment_is_not_retried_as_a_download_failure(self):
        source = self.source()
        blocked = self.items[-1]["canonical_id"]
        prepared_ids = []

        def unavailable(store, identifier, *args, **kwargs):
            prepared_ids.append(identifier)
            child = store.get(identifier)
            child["state"].update(
                phase="prepared",
                assessments={
                    "augmented": {
                        "overall_quality": None,
                        "card": {
                            "overall_rationale": "No transcript establishes the explanation."
                        },
                    }
                },
            )
            store.save(child)
            return child

        def lookup(store, identifier):
            child = store.get(identifier)
            return (
                None
                if child["settings"]["identity"]["canonical_id"] == blocked
                else quality(identifier)
            )

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                side_effect=lambda h, i: {**i["engagement"], "observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lookup,
            ),
        ):
            identifier = create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            result = execute_recommendation(self.store, identifier, prepare=unavailable)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(prepared_ids), 1)
        self.assertEqual(result["state"]["candidate_attempt_counts"][blocked], 1)
        self.assertEqual(
            result["state"]["evidence"][blocked]["failure"]["category"],
            "insufficient_evidence",
        )
        self.assertIn("No transcript", result["state"]["evidence"][blocked]["reason"])
        self.assertEqual(len(result["state"]["selected"]), 10)

    def test_candidate_download_limit_does_not_block_verified_recommendations(self):
        source = self.source()
        blocked = self.items[-1]["canonical_id"]

        def lookup(store, identifier):
            child = store.get(identifier)
            return (
                None
                if child["settings"]["identity"]["canonical_id"] == blocked
                else quality(identifier)
            )

        def unavailable(store, identifier, *args, **kwargs):
            child = store.get(identifier)
            child["state"].update(
                retryable_failure=False,
                failure={
                    "stage": "download",
                    "scope": "candidate",
                    "category": "media_transfer",
                    "retryable": False,
                },
                download_attempts=[
                    {"number": n, "status": "failed", "error": "read timed out"}
                    for n in range(1, 5)
                ],
            )
            child.update(
                status="pending", reason="Candidate download exceeds its allowance."
            )
            store.save(child)
            return child

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                side_effect=lambda h, i: {**i["engagement"], "observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lookup,
            ),
        ):
            identifier = create_recommendation(
                self.store,
                "youtube",
                source,
                assessment_model="fixture-model",
                reasoning_effort="low",
            )
            result = execute_recommendation(self.store, identifier, prepare=unavailable)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["state"]["evidence"][blocked]["status"], "unavailable")
        self.assertTrue(result["state"]["assessment_complete"])
        self.assertEqual(len(result["state"]["selected"]), 10)
        self.assertNotIn(
            blocked, [item["canonical_id"] for item in result["state"]["selected"]]
        )
        report = recommendation_report(self.store, identifier)
        self.assertEqual(len(report["candidate_failures"]), 1)
        failure = report["candidate_failures"][0]
        self.assertEqual(failure["canonical_id"], blocked)
        self.assertEqual(
            (
                failure["status"],
                failure["download_attempts"],
                failure["download_retries"],
            ),
            ("skipped", 4, 3),
        )
        recovered = result["state"]["selected"][0]
        child = self.store.get(recovered["quality_evidence"]["evidence_run"])
        child["state"]["download_attempts"] = [
            {"number": 1, "status": "failed", "error": "read timed out"},
            {"number": 2, "status": "completed"},
            {"number": 1, "status": "failed", "error": "read timed out"},
            {"number": 2, "status": "completed"},
        ]
        self.store.save(child)
        report = recommendation_report(self.store, identifier)
        self.assertEqual(
            {record["status"] for record in report["candidate_failures"]},
            {"recovered", "skipped"},
        )
        recovered_failure = next(
            record
            for record in report["candidate_failures"]
            if record["status"] == "recovered"
        )
        self.assertEqual(recovered_failure["download_retries"], 2)
        digest = write_recommendation_report(self.store, identifier).read_text(
            encoding="utf-8"
        )
        self.assertGreater(
            digest.index("## Candidate failures and retries"), digest.index("10. [")
        )
        self.assertIn("4 download attempts", digest)
        self.assertIn("**Recovered**", digest)
        saved = self.store.get(identifier)
        saved["state"]["cache_hit_ids"] = [recovered["canonical_id"]]
        self.store.save(saved)
        self.assertEqual(
            [
                record["status"]
                for record in recommendation_report(self.store, identifier)[
                    "candidate_failures"
                ]
            ],
            ["skipped"],
        )

    def test_failed_publication_rolls_back_exposures_and_pending_state(self):
        source = self.source()
        self.store.db.execute(
            "CREATE TRIGGER fail_exposure BEFORE INSERT ON recommendation_exposures BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
        )
        self.store.db.commit()
        identifier = create_recommendation(
            self.store,
            "youtube",
            source,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=lambda s, i: quality(i),
            ),
        ):
            with self.assertRaisesRegex(Exception, "fixture failure"):
                execute_recommendation(self.store, identifier)
        self.assertEqual(len(pending_videos(self.store, "youtube")), 0)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM recommendation_exposures"
            ).fetchone()[0],
            0,
        )
        run = self.store.get(identifier)
        self.assertEqual(run["state"]["phase"], "assess")
        self.assertIsNone(run["state"]["published_at"])

    def test_watch_action_during_assessment_is_honored_at_publication(self):
        source = self.source()
        identifier = create_recommendation(
            self.store,
            "youtube",
            source,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        called = []

        def observed(store, evidence_id):
            if not called:
                key = self.store.get(identifier)["state"]["shortlist"][0][
                    "canonical_id"
                ]
                user_action(store, key, "watched")
                called.append(key)
            return quality(evidence_id)

        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality",
                side_effect=observed,
            ),
        ):
            run = execute_recommendation(self.store, identifier)
        self.assertNotIn(
            called[0], {i["canonical_id"] for i in run["state"]["selected"]}
        )
        self.assertEqual(len(run["state"]["selected"]), 10)

    def test_candidate_identity_mismatch_is_not_rendered_or_scored(self):
        self.items[0]["url"] = self.items[1]["url"]
        with self.assertRaisesRegex(ValueError, "same playable video"):
            screen(self.items, [], {}, self.profile)

    def test_partial_publication_requires_explicit_choice_and_stays_partial(self):
        source = self.source()
        identifier = create_recommendation(
            self.store,
            "youtube",
            source,
            max_assessments=0,
            assessment_model="fixture-model",
            reasoning_effort="low",
        )
        with (
            patch(
                "broader_vids.recommendations.service.collect_snapshot",
                return_value={"observed_at": utc_now()},
            ),
            patch(
                "broader_vids.recommendations.service.cached_quality", return_value=None
            ),
        ):
            execute_recommendation(self.store, identifier)
        run = self.store.get(identifier)
        run["state"]["evidence"] = {
            i["canonical_id"]: quality(identifier)
            for i in run["state"]["shortlist"][:10]
        }
        self.store.save(run)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            publish_recommendation(self.store, identifier)
        with patch(
            "broader_vids.recommendations.service.cached_quality",
            side_effect=lambda s, i: quality(i),
        ):
            result = publish_recommendation(
                self.store, identifier, allow_incomplete=True
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["state"]["selected"]), 10)
        self.assertFalse(result["state"]["assessment_complete"])
        self.assertEqual(len(pending_videos(self.store, "youtube")), 10)

    def test_dashboard_rejects_cross_origin_and_persists_valid_action(self):
        server = make_server(self.directory.name, 0)
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
        )
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base) as response:
                page = response.read().decode()
            token = json.loads(
                re.search(r'name="broader-vids-token" content=("[^\"]+")', page)[1]
            )
            data = json.dumps(
                {"id": self.items[0]["canonical_id"], "action": "watched"}
            ).encode()
            request = urllib.request.Request(
                base + "/api/action",
                data=data,
                headers={
                    "X-Broader-Vids-Token": token,
                    "Origin": "https://elsewhere.invalid",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)
            request = urllib.request.Request(
                base + "/api/action",
                data=data,
                headers={"X-Broader-Vids-Token": token, "Origin": base},
            )
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.loads(response.read())["saved"])
            self.assertTrue(
                video_states(self.store)[self.items[0]["canonical_id"]]["watched"]
            )
            for action in ("comment", "review_comments", "apply_proposal"):
                request = urllib.request.Request(
                    base + "/api/action",
                    data=json.dumps({"id": "all", "action": action}).encode(),
                    headers={"X-Broader-Vids-Token": token, "Origin": base},
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
