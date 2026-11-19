from contextlib import closing, contextmanager
import errno
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from broader_vids.identity import identify
from broader_vids.processes import run_bounded


_HOST = "ntfy.sh"
_TOPIC_URL = f"https://{_HOST}/"
_TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")
_RESERVED = {
    "account",
    "app",
    "docs",
    "file",
    "login",
    "metrics",
    "settings",
    "signup",
    "static",
    "v1",
}
_BODY_LIMIT = 4096
_RECENT_LIMIT = 3
_TIMEOUT = 5
_DELIVERY_WINDOW = 10
_RETRYABLE = {"pending", "failed", "uncertain"}
_PLATFORMS = {"youtube": "YouTube", "bilibili": "Bilibili"}


def _normalize_topic(value):
    if isinstance(value, str):
        if value == "":
            return ""
        topic = value.removeprefix(_TOPIC_URL)
        if _TOPIC.fullmatch(topic) and topic not in _RESERVED:
            return topic
    raise ValueError("Use an ntfy.sh topic name or its exact HTTPS topic URL.")


def _configuration(store):
    if store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ntfy_settings'"
    ).fetchone():
        return store.db.execute(
            "SELECT enabled, topic FROM ntfy_settings WHERE id=1"
        ).fetchone()
    return None


_RECENT_RUNS = """
    SELECT run_id FROM ntfy_deliveries WHERE platform=?
    ORDER BY published_at DESC, run_id DESC LIMIT ?
"""


def _recent_deliveries(store, platform):
    rows = store.db.execute(
        f"""
        SELECT d.run_id, d.platform, d.published_at,
               COALESCE(SUM(p.status='sent'),0) AS sent_parts, COUNT(p.part) AS total_parts,
               CASE
                   WHEN COUNT(p.part)=0 THEN 'failed'
                   WHEN MAX(p.status='canceled') THEN 'canceled'
                   WHEN MAX(p.status='sending') THEN 'sending'
                   WHEN MAX(p.status='uncertain') THEN 'uncertain'
                   WHEN MAX(p.status='failed') THEN 'failed'
                   WHEN MAX(p.status='pending') THEN 'pending'
                   ELSE 'sent'
               END AS status
        FROM ntfy_deliveries d LEFT JOIN ntfy_delivery_parts p ON d.run_id=p.run_id
        WHERE d.run_id IN ({_RECENT_RUNS})
        GROUP BY d.run_id
        ORDER BY d.published_at DESC, d.run_id DESC
        """,
        (platform, _RECENT_LIMIT),
    )
    return [dict(row) for row in rows]


def settings(store):
    config = _configuration(store)
    if config is None:
        return {"enabled": False, "topic": "", "deliveries": []}

    deliveries = []
    for platform in _PLATFORMS:
        recent = _recent_deliveries(store, platform)
        if any(delivery["status"] == "sending" for delivery in recent):
            with _delivery_lock(store, platform) as acquired:
                if acquired:
                    recent = _recent_deliveries(store, platform)
                    for delivery in recent:
                        if delivery["status"] == "sending":
                            delivery["status"] = "uncertain"
        for delivery in recent:
            delivery["can_retry"] = bool(
                config["enabled"]
                and delivery["total_parts"]
                and delivery["status"] in _RETRYABLE
            )
        deliveries.extend(recent)
    return {
        "enabled": bool(config["enabled"]),
        "topic": config["topic"],
        "deliveries": deliveries,
    }


def configure(store, enabled, topic):
    if type(enabled) is not bool:
        raise ValueError("The notification setting must be true or false.")
    if topic is not None:
        topic = _normalize_topic(topic)

    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute("""
            CREATE TABLE IF NOT EXISTS ntfy_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                topic TEXT NOT NULL
            )
        """)
        store.db.execute("""
            CREATE TABLE IF NOT EXISTS ntfy_deliveries (
                run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                published_at TEXT NOT NULL
            )
        """)
        store.db.execute("""
            CREATE INDEX IF NOT EXISTS ntfy_latest
            ON ntfy_deliveries(platform,published_at DESC,run_id DESC)
        """)
        store.db.execute("""
            CREATE TABLE IF NOT EXISTS ntfy_delivery_parts (
                run_id TEXT NOT NULL REFERENCES ntfy_deliveries(run_id) ON DELETE CASCADE,
                part INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','uncertain','canceled')),
                PRIMARY KEY(run_id,part)
            )
        """)
        config = _configuration(store)
        previous_topic = config["topic"] if config else ""
        if topic is None:
            topic = previous_topic
        if enabled and not topic:
            raise ValueError("Choose an ntfy.sh topic before enabling notifications.")
        store.db.execute(
            "INSERT INTO ntfy_settings VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,topic=excluded.topic",
            (enabled, topic),
        )
        if not enabled or topic != previous_topic:
            store.db.execute("""
                UPDATE ntfy_delivery_parts SET status='canceled', title='', body=''
                WHERE status NOT IN ('sent','canceled')
            """)
    return settings(store)


def _entry(position, title, url):
    title = " ".join(title.splitlines()).encode("utf-8", "replace").decode("utf-8")
    prefix = f"{position}. "
    suffix = f"\n{url}"
    available = _BODY_LIMIT - len((prefix + suffix).encode("utf-8"))
    encoded = title.encode("utf-8")
    if len(encoded) > available:
        ellipsis = "…"
        title = (
            encoded[: available - len(ellipsis.encode("utf-8"))].decode(
                "utf-8", "ignore"
            )
            + ellipsis
        )
    return prefix + title + suffix


def _message_parts(run):
    state = run["state"]
    bodies = []
    current = []
    size = 0
    for position, item in enumerate(state["selected"], 1):
        identity = identify(item["url"])
        if (
            identity["platform"] != run["platform"]
            or identity["canonical_id"] != item["canonical_id"]
        ):
            raise ValueError("Published video identity does not match its URL.")
        entry = _entry(position, item["title"], identity["url"])
        entry_size = len(entry.encode("utf-8"))
        if current and size + 2 + entry_size > _BODY_LIMIT:
            bodies.append("\n\n".join(current))
            current = []
            size = 0
        size += entry_size + (2 if current else 0)
        current.append(entry)
    bodies.append("\n\n".join(current))
    date = state["published_at"][:10]
    platform = _PLATFORMS[run["platform"]]
    completeness = "Partial" if run["status"] == "partial" else "Full"
    title = f"Broader Vids - {platform} - {date} - {completeness}"
    if len(bodies) == 1:
        return [(title, bodies[0])]
    return [
        (f"{title} ({part}/{len(bodies)})", body) for part, body in enumerate(bodies, 1)
    ]


def enqueue(store, run):
    state = run["state"]
    if state["phase"] != "published" or not state.get("selected"):
        return
    config = _configuration(store)
    if config is None or not config["enabled"]:
        return

    inserted = store.db.execute(
        "INSERT INTO ntfy_deliveries VALUES (?,?,?) ON CONFLICT(run_id) DO NOTHING",
        (run["id"], run["platform"], state["published_at"]),
    ).rowcount
    if not inserted:
        return
    store.db.execute(
        f"""
        DELETE FROM ntfy_delivery_parts
        WHERE run_id NOT IN ({_RECENT_RUNS})
          AND EXISTS (
              SELECT 1 FROM ntfy_deliveries d
              WHERE d.run_id=ntfy_delivery_parts.run_id AND d.platform=?
          )
        """,
        (run["platform"], _RECENT_LIMIT, run["platform"]),
    )
    try:
        parts = _message_parts(run)
    except (AttributeError, KeyError, TypeError, ValueError):
        return

    store.db.executemany(
        "INSERT INTO ntfy_delivery_parts (run_id,part,title,body,status) VALUES (?,?,?,?,'pending')",
        ((run["id"], part, title, body) for part, (title, body) in enumerate(parts, 1)),
    )


@contextmanager
def _delivery_lock(store, platform, *, wait=0):
    folder = store.root / ".dashboard" / "ntfy"
    folder.mkdir(parents=True, exist_ok=True)
    # The OS releases this lock when the sender exits, including after a crash.
    with (folder / f"{platform}.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            def acquire():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        deadline = time.monotonic() + wait
        while True:
            try:
                acquire()
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield False
                    return
                time.sleep(min(0.05, remaining))
        yield True


def _claim(store, run_id, retry_from):
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        part = store.db.execute(
            """
            SELECT p.part, p.status, p.attempts
            FROM ntfy_delivery_parts p JOIN ntfy_settings s ON s.id=1 AND s.enabled=1
            WHERE p.run_id=? AND p.status!='sent'
            ORDER BY p.part LIMIT 1
        """,
            (run_id,),
        ).fetchone()
        if part is None or part["status"] == "canceled":
            return None
        if retry_from is None:
            if part["status"] != "pending":
                return None
        elif (part["part"], part["attempts"]) != retry_from:
            return None
        store.db.execute(
            "UPDATE ntfy_delivery_parts SET status='sending', attempts=attempts+1 WHERE run_id=? AND part=?",
            (run_id, part["part"]),
        )
        return part


def _post(topic, title, body, *, is_current):
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Title": title,
        "Connection": "close",
    }
    with closing(http.client.HTTPSConnection(_HOST, timeout=_TIMEOUT)) as connection:
        try:
            connection.connect()
        except (OSError, http.client.HTTPException):
            return "failed"
        if not is_current():
            return "canceled"
        try:
            connection.request("POST", f"/{topic}", body.encode("utf-8"), headers)
            with connection.getresponse() as response:
                status = response.status
        except (OSError, http.client.HTTPException):
            return "uncertain"

    if 200 <= status < 300:
        return "sent"
    if 300 <= status < 500:
        return "failed"
    return "uncertain"


def _send(root, run_id, part, attempt):
    try:
        result = run_bounded(
            [sys.executable, "-m", "broader_vids.notifications"],
            timeout=_TIMEOUT,
            input=json.dumps([str(root), run_id, part, attempt]),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "uncertain"
    outcome = result.stdout.strip()
    if result.returncode == 0 and outcome in {
        "sent",
        "failed",
        "uncertain",
        "canceled",
    }:
        return outcome
    return "uncertain"


def _worker(root, run_id, part, attempt):
    database = (Path(root) / "broader-vids.sqlite3").as_uri()
    with closing(sqlite3.connect(f"{database}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row

        def active_part():
            return db.execute(
                """
                SELECT s.topic, p.title, p.body
                FROM ntfy_delivery_parts p JOIN ntfy_settings s ON s.id=1 AND s.enabled=1
                WHERE p.run_id=? AND p.part=? AND p.attempts=? AND p.status='sending'
                """,
                (run_id, part, attempt),
            ).fetchone()

        message = active_part()
        if message is None:
            return "canceled"
        return _post(
            message["topic"],
            message["title"],
            message["body"],
            is_current=lambda: active_part() == message,
        )


def deliver(store, run_id, *, retry=False):
    store.validate_id(run_id)
    config = _configuration(store)
    if config is None or not config["enabled"]:
        return
    outstanding = store.db.execute(
        """
        SELECT d.platform, p.part, p.status, p.attempts
        FROM ntfy_deliveries d JOIN ntfy_delivery_parts p ON p.run_id=d.run_id
        WHERE d.run_id=? AND p.status NOT IN ('sent','canceled')
        ORDER BY p.part LIMIT 1
    """,
        (run_id,),
    ).fetchone()
    if outstanding is None or (not retry and outstanding["status"] != "pending"):
        return

    retry_from = (outstanding["part"], outstanding["attempts"]) if retry else None
    deadline = time.monotonic() + _DELIVERY_WINDOW
    while (remaining := deadline - time.monotonic()) > 0:
        with _delivery_lock(
            store,
            outstanding["platform"],
            wait=0 if outstanding["status"] == "sending" else remaining,
        ) as acquired:
            if not acquired or time.monotonic() >= deadline:
                return
            if (part := _claim(store, run_id, retry_from)) is None:
                return

            attempt = part["attempts"] + 1
            outcome = _send(store.root, run_id, part["part"], attempt)
            settled = outcome in {"sent", "canceled"}
            with store.db:
                acknowledged = store.db.execute(
                    """
                    UPDATE ntfy_delivery_parts
                    SET status=?, title=CASE WHEN ? THEN '' ELSE title END,
                        body=CASE WHEN ? THEN '' ELSE body END
                    WHERE run_id=? AND part=? AND attempts=? AND status='sending'
                """,
                    (outcome, settled, settled, run_id, part["part"], attempt),
                ).rowcount
            if outcome != "sent" or not acknowledged:
                return
            retry_from = None


def deliver_after_publication(store, run_id):
    try:
        deliver(store, run_id)
    except (OSError, sqlite3.Error):
        return


if __name__ == "__main__":
    try:
        outcome = _worker(*json.load(sys.stdin))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        outcome = "uncertain"
    print(outcome)
