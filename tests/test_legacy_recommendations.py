from contextlib import closing
import json
import tempfile
import unittest
from unittest.mock import patch

from broader_vids.policy import Policy
from broader_vids.preferences import set_profile
from broader_vids.recommendations.ranking import rank_slate, screen
from broader_vids.recommendations.reports import write_recommendation_report
from broader_vids.recommendations.service import (
    create_recommendation,
    enrich_buffer,
    execute_recommendation,
)
from broader_vids.storage import Store, utc_now
from test_recommendations import candidate, quality


class LegacyRecommendationTests(unittest.TestCase):
    def test_saved_policy_survives_discovery_enrichment_and_assessment_resumes(self):
        for phase in ("discovery", "enrich", "assess"):
            for version in (None, 2):
                with (
                    self.subTest(phase=phase, version=version),
                    tempfile.TemporaryDirectory() as directory,
                    closing(Store(directory)) as store,
                ):
                    set_profile(store, ["mathematics"])
                    items = [candidate(i) for i in range(1, 101)]
                    items[0]["engagement"]["likes"] = None
                    source = store.create(
                        "youtube",
                        {"kind": "discovery"},
                        {
                            "phase": "finished",
                            "candidates": {i["canonical_id"]: i for i in items},
                        },
                    )
                    for item in items:
                        store.create(
                            "youtube",
                            {
                                "kind": "evidence",
                                "identity": {"canonical_id": item["canonical_id"]},
                            },
                            {},
                        )
                    pending = items[0]
                    with store.db:
                        store.db.execute(
                            "INSERT INTO video_state VALUES(?,?,?,0,0,1,?)",
                            (
                                pending["canonical_id"],
                                "youtube",
                                json.dumps(pending),
                                utc_now(),
                            ),
                        )
                    identifier = create_recommendation(
                        store,
                        "youtube",
                        source,
                        assessment_model="fixture-model",
                        reasoning_effort="low",
                    )
                    run = store.get(identifier)
                    if version is None:
                        run["settings"].pop("screening_policy_version")
                    else:
                        run["settings"]["screening_policy_version"] = version
                    with store.db:
                        store.db.execute(
                            "UPDATE runs SET settings=? WHERE id=?",
                            (json.dumps(run["settings"]), identifier),
                        )
                    if phase != "discovery":
                        run["state"].update(phase="enrich", buffer=items[1:])
                    store.save(run)
                    with (
                        patch(
                            "broader_vids.recommendations.service.collect_snapshot",
                            side_effect=lambda h, i: {
                                **i["engagement"],
                                "observed_at": utc_now(),
                            },
                        ),
                        patch(
                            "broader_vids.recommendations.service.cached_quality",
                            side_effect=lambda s, i: quality(i),
                        ),
                    ):
                        if phase == "assess":
                            enrich_buffer(
                                store, run, None, lambda: 100, lambda: store.save(run)
                            )
                        result = execute_recommendation(store, identifier)
                    self.assertEqual(result["status"], "completed", result["reason"])
                    shortlist = result["state"]["shortlist"]
                    self.assertEqual(len(shortlist), 50)
                    self.assertIn(
                        pending["canonical_id"], [i["canonical_id"] for i in shortlist]
                    )
                    self.assertEqual(sum(i["carryover"] for i in shortlist), 1)
                    self.assertGreaterEqual(
                        sum(not i["related"] for i in result["state"]["selected"]),
                        2,
                    )
                    digest = write_recommendation_report(store, identifier).read_text(
                        encoding="utf-8"
                    )
                    self.assertIn("Pending videos compete with new candidates", digest)
                    self.assertNotIn("keep their next-run inspection slots", digest)

    def test_legacy_duration_and_creator_caps_do_not_change_new_rounds(self):
        items = [candidate(i) for i in range(1, 101)]
        for item in items:
            item["creator_id"] = "same-creator"
            item["duration_seconds"] = 30
            item["engagement"]["likes"] = None
        profile = {"interests": ["mathematics"]}
        old, _ = screen(items, [], {}, profile, policy_version=2)
        new, _ = screen(items, [], {}, profile)
        self.assertEqual(len(old), 3)
        self.assertEqual(new, [])
        observations = {
            i["canonical_id"]: {**quality("fixture"), "duration_seconds": 30}
            for i in old
        }
        self.assertEqual(
            len(
                rank_slate(
                    old, observations, profile, Policy().weights, policy_version=2
                )
            ),
            3,
        )
        self.assertEqual(rank_slate(old, observations, profile, Policy().weights), [])
        with self.assertRaisesRegex(ValueError, "guaranteed pending"):
            screen(items, items[:51], {}, profile, policy_version=2)
