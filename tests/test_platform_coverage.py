import tempfile
import unittest

from broader_vids.exploration.coverage import (
    allocation_coverage,
    empty_summary,
    export_summary,
    import_summary,
    memory,
    platform_number,
    record_search,
    round_number,
    save_memory,
    validate_summary,
)
from broader_vids.exploration.domains import node_id, nodes, seed_interests
from broader_vids.preferences import set_profile
from broader_vids.storage import Store


class PlatformCoverageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        set_profile(self.store, ["mathematics"])
        seed_interests(self.store)
        self.identifier = next(iter(nodes(self.store)))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_empty_and_platform_counts_are_neutral_and_idempotent(self):
        self.assertEqual(empty_summary()["version"], 2)
        record_search(
            self.store,
            "round",
            self.identifier,
            platform="youtube",
            domain="Mathematics",
            task_id="task-a",
        )
        record_search(
            self.store,
            "round",
            self.identifier,
            platform="youtube",
            domain="Mathematics",
            task_id="task-a",
        )
        value = memory(self.store)
        self.assertEqual(value["usage"][self.identifier]["searches"], 1)
        self.assertEqual(
            value["platform_usage"]["youtube"][self.identifier]["searches"], 1
        )
        self.assertEqual(value["root_usage"]["youtube"]["mathematics"]["searches"], 1)
        self.assertEqual(
            allocation_coverage(self.store, "youtube", ["mathematics"])["mathematics"][
                "searches"
            ],
            1,
        )
        self.assertEqual(
            allocation_coverage(self.store, "bilibili", ["mathematics"])["mathematics"][
                "searches"
            ],
            0,
        )

    def test_v2_roundtrip_and_v1_import_do_not_invent_platform_counts(self):
        record_search(
            self.store,
            "round",
            self.identifier,
            platform="bilibili",
            domain="mathematics",
            task_id="t1",
        )
        summary = export_summary(self.store)
        validate_summary(summary)
        target_dir = tempfile.TemporaryDirectory()
        target = Store(target_dir.name)
        try:
            set_profile(target, ["mathematics"])
            import_summary(target, summary)
            import_summary(target, summary)
            self.assertEqual(
                memory(target)["platform_usage"]["bilibili"][self.identifier][
                    "searches"
                ],
                1,
            )
            self.assertEqual(
                memory(target)["root_profiles"]["youtube"], ["mathematics"]
            )
            v1 = {
                key: summary[key] for key in ("version", "sequence", "nodes", "usage")
            }
            v1["version"] = 1
            import_summary(target, v1)
            self.assertEqual(memory(target)["platform_usage"]["youtube"], {})
        finally:
            target.close()
            target_dir.cleanup()

    def test_invalid_platform_usage_is_rejected(self):
        summary = export_summary(self.store)
        summary["platform_usage"]["youtube"][self.identifier] = {
            "searches": 0,
            "last_used": 1,
            "last_recommended": 0,
        }
        with self.assertRaises(ValueError):
            validate_summary(summary)

    def test_invalid_import_is_atomic(self):
        before = (memory(self.store), nodes(self.store))
        summary = empty_summary()
        summary["platform_usage"]["youtube"]["bad-key"] = {
            "searches": 1,
            "last_used": 0,
            "last_recommended": 0,
        }
        with self.assertRaises(ValueError):
            import_summary(self.store, summary)
        self.assertEqual((memory(self.store), nodes(self.store)), before)

    def test_platform_ordinals_and_v2_import_preserve_relative_age(self):
        platform_number(self.store, "yt-1", "youtube")
        platform_number(self.store, "yt-2", "youtube")
        platform_number(self.store, "yt-3", "youtube")
        self.assertEqual(platform_number(self.store, "bi-1", "bilibili"), 1)
        record_search(
            self.store,
            "bi-1",
            self.identifier,
            platform="bilibili",
            domain="mathematics",
            task_id=self.identifier,
        )
        self.assertEqual(platform_number(self.store, "bi-2", "bilibili"), 2)
        for index in range(3):
            platform_number(self.store, f"yt-later-{index}", "youtube")
        self.assertEqual(platform_number(self.store, "bi-3", "bilibili"), 3)

        summary = export_summary(self.store)
        exported = summary["platform_usage"]["bilibili"][self.identifier]
        self.assertEqual(exported["last_used"], summary["sequence"] - 2)
        target_dir = tempfile.TemporaryDirectory()
        target = Store(target_dir.name)
        try:
            set_profile(target, ["mathematics"])
            import_summary(target, summary)
            first_clock = memory(target)["platform_sequences"]["bilibili"]
            first_cell = memory(target)["platform_usage"]["bilibili"][self.identifier]
            self.assertEqual(first_clock - first_cell["last_used"], 2)

            platform_number(target, "later-bili", "bilibili")
            import_summary(target, summary)
            value = memory(target)
            cell = value["platform_usage"]["bilibili"][self.identifier]
            self.assertEqual(
                value["platform_sequences"]["bilibili"] - cell["last_used"], 3
            )
        finally:
            target.close()
            target_dir.cleanup()

    def test_source_observation_prevents_evicted_round_replay(self):
        source = self.store.create("youtube", {"kind": "discovery"}, {})
        saved = round_number(self.store, "old-owner")
        record_search(
            self.store,
            "old-owner",
            self.identifier,
            saved,
            platform="youtube",
            domain="mathematics",
            task_id=self.identifier,
            source_run=source,
        )
        before = memory(self.store)["platform_usage"]["youtube"][self.identifier][
            "searches"
        ]
        for index in range(64):
            round_number(self.store, f"new-owner-{index}")
        self.assertNotIn("old-owner", memory(self.store)["rounds"])
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO domain_observations VALUES(?,?,?,?)",
                (source, self.identifier, "fixture", "{}"),
            )
        record_search(
            self.store,
            "old-owner",
            self.identifier,
            saved,
            platform="youtube",
            domain="mathematics",
            task_id=self.identifier,
            source_run=source,
        )
        self.assertNotIn("old-owner", memory(self.store)["rounds"])
        self.assertEqual(
            memory(self.store)["platform_usage"]["youtube"][self.identifier][
                "searches"
            ],
            before,
        )

    def test_completed_baselines_compact_and_imported_orphan_roots_are_pruned(self):
        batch = self.store.create("both", {"kind": "recommendation_batch"}, {})
        round_number(self.store, batch)
        value = memory(self.store)
        value["rounds"][batch]["novelty_baseline"] = [str(i) for i in range(1000)]
        save_memory(self.store, value)
        value = memory(self.store)
        self.assertEqual(
            value["rounds"][batch]["expected_platforms"], ["youtube", "bilibili"]
        )
        value["rounds"][batch]["published"] = ["youtube"]
        save_memory(self.store, value)
        self.assertIn("novelty_baseline", memory(self.store)["rounds"][batch])
        value = memory(self.store)
        value["rounds"][batch]["published"].append("bilibili")
        save_memory(self.store, value)
        self.assertNotIn("novelty_baseline", memory(self.store)["rounds"][batch])

        single = self.store.create("youtube", {"kind": "recommendation"}, {})
        round_number(self.store, single)
        value = memory(self.store)
        value["rounds"][single]["novelty_baseline"] = ["active"]
        save_memory(self.store, value)
        self.assertEqual(
            memory(self.store)["rounds"][single]["expected_platforms"], ["youtube"]
        )
        value = memory(self.store)
        value["rounds"][single]["published"] = ["youtube"]
        save_memory(self.store, value)
        self.assertNotIn("novelty_baseline", memory(self.store)["rounds"][single])

        root_id = node_id("shared root")
        for root in ("first root", "second root", "third root"):
            import_summary(
                self.store,
                {
                    "version": 2,
                    "sequence": 0,
                    "nodes": [
                        {
                            "id": root_id,
                            "label": "shared root",
                            "aliases": [],
                            "parents": [],
                            "domains": [root],
                            "depth": 1,
                            "origin": "exploration",
                            "status": "active",
                        }
                    ],
                    "usage": {},
                    "platform_usage": {p: {} for p in ("youtube", "bilibili")},
                    "root_usage": {
                        p: {root: {"searches": 1, "last_used": 0}}
                        for p in ("youtube", "bilibili")
                    },
                },
            )
        roots = memory(self.store)["root_usage"]["youtube"]
        self.assertNotIn("second root", roots)
        self.assertNotIn("third root", roots)
        self.assertLessEqual(len(roots), 1024)

        catalog_nodes = []
        catalog_roots = []
        for index in range(26):
            domains = [f"catalog domain {index} {offset}" for offset in range(40)]
            catalog_roots.extend(domains)
            label = f"catalog node {index}"
            catalog_nodes.append(
                {
                    "id": node_id(label),
                    "label": label,
                    "aliases": [],
                    "parents": [],
                    "domains": domains,
                    "depth": 1,
                    "origin": "exploration",
                    "status": "active",
                }
            )
        summary = {
            "version": 2,
            "sequence": 0,
            "nodes": catalog_nodes,
            "usage": {},
            "platform_usage": {p: {} for p in ("youtube", "bilibili")},
            "root_usage": {
                platform: {
                    root: {"searches": 1, "last_used": 0}
                    for root in catalog_roots[:1024]
                }
                for platform in ("youtube", "bilibili")
            },
        }
        import_summary(self.store, summary)
        self.assertEqual(len(memory(self.store)["root_usage"]["youtube"]), 1024)


if __name__ == "__main__":
    unittest.main()
