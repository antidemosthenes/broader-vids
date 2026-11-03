from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from broader_vids.storage import Store


class StorageTests(unittest.TestCase):
    def test_obsolete_and_unversioned_profiles_are_rejected_without_changes(self):
        import hashlib

        for version in (0, 1, 5, 10, 11, 999):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as directory,
            ):
                database = Path(directory) / "broader-vids.sqlite3"
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("CREATE TABLE historical(value TEXT)")
                    connection.execute("INSERT INTO historical VALUES('keep')")
                    connection.execute(f"PRAGMA user_version={version}")
                    connection.commit()
                before = hashlib.sha256(database.read_bytes()).hexdigest()
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    Store(directory)
                self.assertEqual(
                    hashlib.sha256(database.read_bytes()).hexdigest(), before
                )
                self.assertFalse(
                    (Path(directory) / ".broader-vids-profile.json").exists()
                )
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("BEGIN EXCLUSIVE")
                    self.assertEqual(
                        connection.execute("SELECT value FROM historical").fetchone()[
                            0
                        ],
                        "keep",
                    )
                    connection.rollback()

    def test_shared_data_selection_and_override_precedence(self):
        from unittest.mock import patch
        from broader_vids.storage import data_root

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"LOCALAPPDATA": directory}, clear=True),
        ):
            default = Path(directory) / "broader-vids"
            default.mkdir()
            selected = Path(directory) / "chosen profile"
            choice = default / "launcher.json"
            choice.write_text(
                json.dumps({"version": 1, "data_directory": str(selected)}),
                encoding="utf-8",
            )
            self.assertEqual(data_root(), selected)
            self.assertEqual(data_root(directory), Path(directory))
            with patch.dict("os.environ", {"BROADER_VIDS_DATA_DIR": directory}):
                self.assertEqual(data_root(), Path(directory))
            choice.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                data_root()
            self.assertEqual(data_root(directory), Path(directory))
            choice.unlink()
            self.assertEqual(data_root(), default)

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
