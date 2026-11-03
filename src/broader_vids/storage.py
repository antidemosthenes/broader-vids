from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
import tempfile


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def data_root(override=None):
    if override:
        return Path(override).expanduser().resolve()
    if os.environ.get("BROADER_VIDS_DATA_DIR"):
        return Path(os.environ["BROADER_VIDS_DATA_DIR"]).expanduser().resolve()
    base = (
        Path(os.environ["LOCALAPPDATA"])
        if os.environ.get("LOCALAPPDATA")
        else (
            Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "Local"
            if sys.platform == "win32"
            else Path.home() / ".local" / "share"
        )
    )
    default = base / "broader-vids"
    selection = default / "launcher.json"
    if selection.exists():
        try:
            value = json.loads(selection.read_text(encoding="utf-8"))
            directory = value.get("data_directory") if isinstance(value, dict) else None
            if (
                value.get("version") != 1
                or not isinstance(directory, str)
                or not Path(directory).is_absolute()
            ):
                raise ValueError("Invalid saved data selection.")
            return Path(directory).resolve()
        except (OSError, ValueError, AttributeError) as error:
            raise ValueError(
                "Invalid saved data selection; use broader-vids data use PATH or data reset through the Node launcher."
            ) from error
    return default


def mark_profile(root):
    target = root / ".broader-vids-profile.json"
    if target.exists():
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump({"format": "broader-vids.profile", "version": 1}, handle)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if (self.root / ".launcher/setup.json").exists():
            raise ValueError(
                "A runtime or data operation holds this profile. Wait for it to finish before using the database."
            )
        if (self.root / ".reset-pending").exists():
            raise ValueError(
                "A reset is unfinished. Complete reset or clean through the Node launcher before using this profile."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "broader-vids.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 12):
                raise ValueError(
                    f"Unsupported database version {version}. This release supports fresh profiles and schema 12. Export preferences with the matching older engine before changing versions."
                )
            self.db.execute("PRAGMA foreign_keys=ON")
            if version == 0:
                if self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone():
                    raise ValueError(
                        "Unsupported unversioned database. Use a new profile directory; existing data was not changed."
                    )
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
                CREATE TABLE IF NOT EXISTS discovery_registry (
                    platform TEXT NOT NULL, kind TEXT NOT NULL, item_key TEXT NOT NULL,
                    payload TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    PRIMARY KEY (platform, kind, item_key)
                );
                CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS video_state (
                    canonical_id TEXT PRIMARY KEY, platform TEXT NOT NULL, payload TEXT NOT NULL,
                    watched INTEGER NOT NULL DEFAULT 0, dismissed INTEGER NOT NULL DEFAULT 0,
                    recommended INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_actions (
                    id INTEGER PRIMARY KEY, canonical_id TEXT NOT NULL REFERENCES video_state(canonical_id),
                    created_at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_exposures (
                    run_id TEXT NOT NULL REFERENCES runs(id), canonical_id TEXT NOT NULL REFERENCES video_state(canonical_id),
                    position INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id,canonical_id)
                );
                CREATE TABLE IF NOT EXISTS engagement_snapshots (
                    canonical_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS user_feedback_lookup ON user_actions(canonical_id,action,id);
                CREATE INDEX IF NOT EXISTS run_request_lookup ON requests(run_id);
                CREATE INDEX IF NOT EXISTS exposure_time_lookup ON recommendation_exposures(created_at);
                CREATE TABLE IF NOT EXISTS domain_nodes (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS domain_observations (
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    node_id TEXT NOT NULL REFERENCES domain_nodes(id),
                    created_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,node_id)
                );
                CREATE INDEX IF NOT EXISTS domain_history_lookup ON domain_observations(node_id,created_at);
                CREATE INDEX IF NOT EXISTS run_kind_platform_lookup ON runs(json_extract(settings,'$.kind'),platform,created_at);
                CREATE INDEX IF NOT EXISTS pending_video_lookup ON video_state(recommended,watched,dismissed,platform);
                CREATE INDEX IF NOT EXISTS evidence_cache_lookup ON runs(json_extract(settings,'$.cache_key'),platform);
                CREATE INDEX IF NOT EXISTS evidence_identity_lookup ON runs(json_extract(settings,'$.identity.canonical_id'),platform);
                CREATE TABLE IF NOT EXISTS feedback_ratings (
                    canonical_id TEXT NOT NULL REFERENCES video_state(canonical_id),
                    dimension TEXT NOT NULL CHECK(dimension IN ('interest','quality','overall')),
                    action_id INTEGER NOT NULL REFERENCES user_actions(id), payload TEXT NOT NULL,
                    PRIMARY KEY(canonical_id,dimension)
                );
                CREATE TABLE IF NOT EXISTS learning_models (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calibration_items (
                    canonical_id TEXT PRIMARY KEY, cohort TEXT NOT NULL,
                    split TEXT NOT NULL CHECK(split IN ('train','holdout')),
                    payload TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS learning_evaluations (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback_details (
                    canonical_id TEXT NOT NULL REFERENCES video_state(canonical_id),
                    dimension TEXT NOT NULL CHECK(dimension IN ('topic_interest','domain_interest','depth','quality_level')),
                    action_id INTEGER NOT NULL REFERENCES user_actions(id), payload TEXT NOT NULL,
                    PRIMARY KEY(canonical_id,dimension)
                );
                CREATE TABLE IF NOT EXISTS feedback_scopes (
                    target TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS creator_exclusions (
                    platform TEXT NOT NULL CHECK(platform IN ('youtube','bilibili')),
                    creator_id TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN ('manual','bilibili_blacklist')),
                    account_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(platform,creator_id,source,account_id)
                );
                CREATE TABLE IF NOT EXISTS creator_sync (
                    account_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS creator_policy (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, account_id TEXT NOT NULL
                );
                INSERT OR IGNORE INTO creator_policy VALUES(1,0,'');
                PRAGMA user_version=12;
                COMMIT;
            """)
            mark_profile(self.root)
        except BaseException:
            try:
                self.db.rollback()
            finally:
                self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def lease(self, run_id, *, owner=None):
        self.validate_id(run_id)
        lock = self.root / f"{run_id}.lock"
        if owner is not None:
            try:
                record = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
            if not isinstance(record, dict) or record.get("token") != owner:
                raise ValueError(
                    "The delegated evidence lease is not owned by this worker."
                )
            yield owner
            return
        try:
            handle = lock.open("x", encoding="utf-8")
        except FileExistsError:
            raise ValueError(
                f"Run {run_id} is locked; verify no worker is active before removing {lock}."
            ) from None
        try:
            token = uuid.uuid4().hex
            handle.write(json.dumps({"pid": os.getpid(), "token": token}))
            handle.close()
            yield token
        finally:
            handle.close()
            try:
                record = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
            if isinstance(record, dict) and record.get("token") == token:
                lock.unlink(missing_ok=True)

    def create(self, platform, settings, state, *, commit=True, run_id=None):
        run_id = uuid.uuid4().hex if run_id is None else run_id
        self.validate_id(run_id)
        now = utc_now()
        self.db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                platform,
                now,
                now,
                "pending",
                "",
                json.dumps(settings),
                json.dumps(state),
                0,
            ),
        )
        if commit:
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
            raise ValueError(
                "Run IDs must contain exactly 32 lowercase hexadecimal characters."
            )

    def list_runs(self, *, kind=None, platform=None):
        return list(self.iter_runs(kind=kind, platform=platform))

    def iter_runs(self, *, kind=None, platform=None, identities=None):
        conditions, args = [], []
        if kind:
            conditions.append("json_extract(settings,'$.kind')=?")
            args.append(kind)
        if platform:
            conditions.append("platform=?")
            args.append(platform)
        if identities is not None:
            if not identities:
                return
            conditions.append(
                "json_extract(settings,'$.identity.canonical_id') IN ("
                + ",".join("?" for _ in identities)
                + ")"
            )
            args.extend(identities)
        for row in self.db.execute(
            "SELECT id FROM runs"
            + (" WHERE " + " AND ".join(conditions) if conditions else "")
            + " ORDER BY created_at",
            args,
        ):
            yield self.get(row[0])

    def save(self, run, event=None, additional=()):
        with self.db:
            self.db.execute(
                "UPDATE runs SET updated_at=?,status=?,reason=?,state=?,elapsed=? WHERE id=?",
                (
                    utc_now(),
                    run["status"],
                    run["reason"],
                    json.dumps(run["state"]),
                    run["elapsed"],
                    run["id"],
                ),
            )
            for event in ([event] if event else []) + list(additional):
                self.db.execute(
                    "INSERT INTO events (run_id,created_at,kind,payload) VALUES (?,?,?,?)",
                    (run["id"], utc_now(), event[0], json.dumps(event[1])),
                )
            if run["settings"].get("kind") == "discovery":
                now = utc_now()
                for kind, values in (
                    ("candidate", run["state"].get("candidates", {})),
                    ("creator", run["state"].get("creators", {})),
                ):
                    self.db.executemany(
                        """INSERT INTO discovery_registry VALUES (?,?,?,?,?,?)
                        ON CONFLICT(platform,kind,item_key) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen""",
                        [
                            (
                                run["platform"],
                                kind,
                                key,
                                json.dumps(value, ensure_ascii=False),
                                now,
                                now,
                            )
                            for key, value in values.items()
                        ],
                    )

    def events(self, run_id):
        return [
            {**dict(r), "payload": json.loads(r["payload"])}
            for r in self.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,)
            )
        ]

    def request_count(self, run_id):
        return self.db.execute(
            "SELECT COUNT(*) FROM requests WHERE run_id=?", (run_id,)
        ).fetchone()[0]

    def begin_request(self, run_id, endpoint):
        cursor = self.db.execute(
            "INSERT INTO requests (run_id,created_at,endpoint,outcome) VALUES (?,?,?,?)",
            (run_id, utc_now(), endpoint, "unresolved"),
        )
        self.db.commit()
        return cursor.lastrowid

    def end_request(self, request_id, outcome, detail=""):
        self.db.execute(
            "UPDATE requests SET outcome=?,detail=? WHERE id=?",
            (outcome, detail, request_id),
        )
        self.db.commit()

    def requests(self, run_id):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM requests WHERE run_id=? ORDER BY id", (run_id,)
            )
        ]
