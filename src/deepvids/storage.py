from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def data_root(override=None):
    if override:
        return Path(override).expanduser().resolve()
    if os.environ.get("DEEPVIDS_DATA_DIR"):
        return Path(os.environ["DEEPVIDS_DATA_DIR"]).expanduser().resolve()
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / ".local" / "share"
    return base / "deepvids"


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "deepvids.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise ValueError(f"Unsupported database version {version}; use a compatible Deepvids release.")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            BEGIN;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, platform TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                settings TEXT NOT NULL, state TEXT NOT NULL, elapsed REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                created_at TEXT NOT NULL, endpoint TEXT NOT NULL, outcome TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS catalogs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, platform TEXT NOT NULL,
                scope TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                manifest TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS catalog_items (
                catalog_id TEXT NOT NULL REFERENCES catalogs(id), ordinal INTEGER NOT NULL,
                video_id TEXT NOT NULL, part_index INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (catalog_id, ordinal), UNIQUE (catalog_id, video_id, part_index)
            );
            CREATE TABLE IF NOT EXISTS catalog_draws (
                id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL REFERENCES catalogs(id),
                seed INTEGER NOT NULL, sample_size INTEGER NOT NULL, created_at TEXT NOT NULL,
                ordinals TEXT NOT NULL
            );
            PRAGMA user_version=2;
            COMMIT;
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def lease(self, run_id):
        self.validate_id(run_id)
        lock = self.root / f"{run_id}.lock"
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError:
            raise ValueError(f"Run {run_id} is locked; verify no worker is active before removing {lock}.") from None
        try:
            handle.write(str(os.getpid()))
            handle.close()
            yield
        finally:
            lock.unlink(missing_ok=True)

    def create(self, platform, settings, state):
        run_id = uuid.uuid4().hex
        now = utc_now()
        self.db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, platform, now, now, "pending", "", json.dumps(settings), json.dumps(state), 0))
        self.db.commit()
        return run_id

    def get(self, run_id):
        self.validate_id(run_id)
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown run: {run_id}")
        result = dict(row)
        for key in ("settings", "state"):
            result[key] = json.loads(result[key])
        return result

    @staticmethod
    def validate_id(run_id):
        if not isinstance(run_id, str) or re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
            raise ValueError("Run IDs must contain exactly 32 lowercase hexadecimal characters.")

    def list_runs(self):
        return [self.get(r[0]) for r in self.db.execute("SELECT id FROM runs ORDER BY created_at")]

    def save(self, run, event=None, additional=()):
        with self.db:
            self.db.execute("UPDATE runs SET updated_at=?,status=?,reason=?,state=?,elapsed=? WHERE id=?",
                            (utc_now(), run["status"], run["reason"], json.dumps(run["state"]), run["elapsed"], run["id"]))
            for event in ([event] if event else []) + list(additional):
                self.db.execute("INSERT INTO events (run_id,created_at,kind,payload) VALUES (?,?,?,?)",
                                (run["id"], utc_now(), event[0], json.dumps(event[1])))

    def events(self, run_id):
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in self.db.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]

    def request_count(self, run_id):
        return self.db.execute("SELECT COUNT(*) FROM requests WHERE run_id=?", (run_id,)).fetchone()[0]

    def begin_request(self, run_id, endpoint):
        cursor = self.db.execute("INSERT INTO requests (run_id,created_at,endpoint,outcome) VALUES (?,?,?,?)",
                                 (run_id, utc_now(), endpoint, "unresolved"))
        self.db.commit()
        return cursor.lastrowid

    def end_request(self, request_id, outcome, detail=""):
        self.db.execute("UPDATE requests SET outcome=?,detail=? WHERE id=?", (outcome, detail, request_id))
        self.db.commit()

    def requests(self, run_id):
        return [dict(r) for r in self.db.execute("SELECT * FROM requests WHERE run_id=? ORDER BY id", (run_id,))]

