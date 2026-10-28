from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from broader_vids.preferences import (
    set_profile,
    user_action,
    video_states,
    update_tag_rule,
)
from broader_vids.learning.feedback import save_detail
from broader_vids.learning.service import ensure_model
from broader_vids.storage import Store


class StorageTests(unittest.TestCase):
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

    def test_schema_ten_upgrade_removes_comments_and_preserves_ratings_and_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            key = "youtube:0000000001A"
            store = Store(directory)
            set_profile(store, ["math"])
            update_tag_rule(store, "bilibili", "易语言")
            save_detail(
                store, key, "depth", "deeper", target="topology", level="advanced"
            )
            before = ensure_model(store)
            with store.db:
                store.db.execute(
                    "UPDATE feedback_details SET payload=json_set(payload,'$.note','obsolete text')"
                )
                store.db.execute(
                    "UPDATE user_actions SET payload=json_set(payload,'$.note','obsolete text') WHERE action='feedback'"
                )
                store.db.execute(
                    "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,'fixture','comment',?)",
                    (key, json.dumps({"note": "obsolete text"})),
                )
                job = store.create(
                    "both",
                    {"kind": "feedback_interpretation"},
                    {"comments": ["obsolete text"], "proposals": []},
                    commit=False,
                )
                store.db.execute("PRAGMA user_version=10")
            store.close()
            with closing(Store(directory)) as store:
                self.assertEqual(
                    store.db.execute("PRAGMA user_version").fetchone()[0], 12
                )
                self.assertEqual(
                    store.db.execute(
                        "SELECT COUNT(*) FROM user_actions WHERE action='comment'"
                    ).fetchone()[0],
                    0,
                )
                payload = json.loads(
                    store.db.execute("SELECT payload FROM feedback_details").fetchone()[
                        0
                    ]
                )
                self.assertNotIn("note", payload)
                self.assertEqual(
                    (payload["value"], payload["level"], payload["level_source"]),
                    ("deeper", "advanced", "user"),
                )
                self.assertEqual(
                    set_profile(store, ["math"])["excluded_tags"],
                    {"bilibili": ["易语言"]},
                )
                self.assertEqual(store.get(job)["state"], {"retired": True})
                after = ensure_model(store)
                self.assertEqual(before["models"], after["models"])
                self.assertEqual(before["structured"], after["structured"])
                self.assertEqual(
                    store.db.execute("PRAGMA foreign_key_check").fetchall(), []
                )
            with closing(
                sqlite3.connect(Path(directory) / "broader-vids.before-v12.sqlite3")
            ) as backup:
                self.assertEqual(
                    backup.execute("PRAGMA user_version").fetchone()[0], 10
                )

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
                    sqlite3.connect(Path(directory) / "broader-vids.before-v12.sqlite3")
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
                    store.db.execute("PRAGMA user_version").fetchone()[0], 12
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
                    sqlite3.connect(Path(directory) / "broader-vids.before-v12.sqlite3")
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
