from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from deepvids.preferences import set_profile, user_action, video_states
from deepvids.storage import Store


class StorageTests(unittest.TestCase):
    def test_filtered_iteration_does_not_decode_unrelated_job_states(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                expected = store.create(
                    "youtube",
                    {
                        "kind": "evidence",
                        "identity": {"canonical_id": "youtube:imYQJOgUx7Y"},
                    },
                    {},
                )
                unrelated = store.create(
                    "youtube",
                    {
                        "kind": "evidence",
                        "identity": {"canonical_id": "youtube:another-id"},
                    },
                    {},
                )
                store.db.execute(
                    "UPDATE runs SET state='invalid json' WHERE id=?", (unrelated,)
                )
                store.db.commit()
                self.assertEqual(
                    [
                        run["id"]
                        for run in store.iter_runs(
                            kind="evidence",
                            platform="youtube",
                            identities=["youtube:imYQJOgUx7Y"],
                        )
                    ],
                    [expected],
                )
                self.assertEqual(list(store.iter_runs(identities=[])), [])
            finally:
                store.close()

    def test_schema_five_upgrade_adds_domain_graph_and_keeps_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            profile = set_profile(store, ["math", "physics"])
            store.db.executescript(
                "DROP TABLE domain_observations; DROP TABLE domain_nodes; PRAGMA user_version=5;"
            )
            store.close()
            store = Store(directory)
            try:
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM domain_nodes").fetchone()[0],
                    0,
                )
                self.assertEqual(set_profile(store, ["math", "physics"]), profile)
                with closing(
                    sqlite3.connect(Path(directory) / "deepvids.before-v10.sqlite3")
                ) as backup:
                    self.assertEqual(
                        backup.execute("PRAGMA user_version").fetchone()[0], 5
                    )
            finally:
                store.close()

    def test_upgrade_preserves_jobs_preferences_actions_and_historical_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            identifier = store.create(
                "youtube", {"kind": "discovery"}, {"checkpoint": "preserve"}
            )
            profile = set_profile(store, ["math"])
            user_action(store, "youtube:0000000001A", "up", "Useful", "quality")
            store.db.execute("CREATE TABLE historical_fixture(value TEXT)")
            store.db.execute("INSERT INTO historical_fixture VALUES('preserve')")
            store.db.execute("PRAGMA user_version=4")
            store.db.commit()
            store.close()
            store = Store(directory)
            try:
                self.assertEqual(
                    store.db.execute("PRAGMA user_version").fetchone()[0], 10
                )
                self.assertEqual(
                    store.get(identifier)["state"], {"checkpoint": "preserve"}
                )
                self.assertEqual(
                    store.db.execute("SELECT value FROM historical_fixture").fetchone()[
                        0
                    ],
                    "preserve",
                )
                self.assertEqual(
                    video_states(store)["youtube:0000000001A"]["feedback"]["vote"], "up"
                )
                self.assertEqual(
                    set_profile(store, ["math"])["revision"], profile["revision"]
                )
                with closing(
                    sqlite3.connect(Path(directory) / "deepvids.before-v10.sqlite3")
                ) as backup:
                    self.assertEqual(
                        backup.execute("PRAGMA user_version").fetchone()[0], 4
                    )
            finally:
                store.close()

    def test_opening_current_database_does_not_require_a_write_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Store(directory)
            first.db.execute("BEGIN IMMEDIATE")
            second = Store(directory)
            second.close()
            first.db.rollback()
            first.close()

    def test_future_schema_is_rejected_and_leases_are_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            identifier = store.create("youtube", {}, {})
            with store.lease(identifier):
                with self.assertRaises(ValueError):
                    with store.lease(identifier):
                        self.fail("Duplicate lease")
            with store.lease(identifier):
                pass
            store.db.execute("PRAGMA user_version=999")
            store.db.commit()
            store.close()
            with self.assertRaises(ValueError):
                Store(directory)
