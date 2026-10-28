import json
import sqlite3

from broader_vids.learning.annotations import latest_annotations
from broader_vids.learning.service import capture_rating, ensure_model, change_epoch
from broader_vids.storage import utc_now


def migrate_binary_feedback(store):
    previous = store.db.execute(
        "SELECT state FROM runs WHERE json_extract(settings,'$.kind')='feedback_migration' AND status='completed' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if previous:
        return json.loads(previous[0])
    rows = [
        dict(row)
        for row in store.db.execute(
            "SELECT * FROM feedback_ratings ORDER BY canonical_id,dimension"
        )
    ]
    annotations = latest_annotations(store)
    missing = {
        row["canonical_id"]
        for row in rows
        if row["dimension"] == "interest"
        and not annotations.get(row["canonical_id"], {})
        .get("topics", {})
        .get("specific")
    }
    if missing:
        raise ValueError(
            f"Label the actual content of {len(missing)} interest-rated videos before migration."
        )
    epoch = change_epoch(store)
    captured = {
        row["canonical_id"]: capture_rating(store, row["canonical_id"]) for row in rows
    }
    backup = store.root / "broader-vids.before-structured-feedback.sqlite3"
    if not backup.exists():
        target = sqlite3.connect(backup)
        try:
            store.db.backup(target)
        finally:
            target.close()
    summary = {
        "mapped": {},
        "unmapped_overall": 0,
        "retired_binary_ratings": len(rows),
        "backup": str(backup),
        "needs_review": True,
    }
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if change_epoch(store) != epoch:
            raise ValueError(
                "Feedback changed during migration. No ratings were removed."
            )
        for row in rows:
            kind = row["dimension"]
            if kind == "overall":
                summary["unmapped_overall"] += 1
                continue
            key = row["canonical_id"]
            dimension = "quality_level" if kind == "quality" else "topic_interest"
            if store.db.execute(
                "SELECT 1 FROM feedback_details WHERE canonical_id=? AND dimension=?",
                (key, dimension),
            ).fetchone():
                continue
            old = json.loads(row["payload"])
            features = captured[key]
            target = (
                "" if kind == "quality" else annotations[key]["topics"]["specific"][0]
            )
            from broader_vids.exploration.domains import normalized

            target = normalized(target)
            payload = {
                "value": "high" if old["vote"] == "up" else "low",
                "target": target,
                "level": "unknown",
                "features": features,
                "origin": "binary_migration",
                "needs_review": True,
            }
            cursor = store.db.execute(
                "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,?,'feedback',?)",
                (key, utc_now(), json.dumps({"kind": dimension, **payload})),
            )
            store.db.execute(
                "INSERT INTO feedback_details VALUES(?,?,?,?)",
                (key, dimension, cursor.lastrowid, json.dumps(payload)),
            )
            if target:
                store.db.execute(
                    "INSERT OR IGNORE INTO feedback_scopes VALUES(?,?)",
                    (
                        target,
                        json.dumps(
                            {
                                "domains": features.get("domains", []),
                                "source_action": cursor.lastrowid,
                            }
                        ),
                    ),
                )
            summary["mapped"][dimension] = summary["mapped"].get(dimension, 0) + 1
        store.db.execute("DELETE FROM feedback_ratings")
        store.db.execute(
            "DELETE FROM user_actions WHERE action='feedback' AND COALESCE(json_extract(payload,'$.kind'),'overall') IN ('interest','quality','overall') AND id NOT IN (SELECT action_id FROM feedback_details)"
        )
        profile = store.db.execute(
            "SELECT revision,payload FROM user_profile WHERE id=1"
        ).fetchone()
        if profile:
            payload = json.loads(profile["payload"])
            payload["retired_binary_feedback"] = True
            store.db.execute(
                "UPDATE user_profile SET revision=?,payload=? WHERE id=1",
                (profile["revision"] + 1, json.dumps(payload)),
            )
        rid = store.create(
            "both", {"kind": "feedback_migration"}, summary, commit=False
        )
        store.db.execute("UPDATE runs SET status='completed' WHERE id=?", (rid,))
    ensure_model(store)
    return summary
