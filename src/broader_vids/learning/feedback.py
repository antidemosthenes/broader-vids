import json

from broader_vids.exploration.domains import normalized
from broader_vids.preferences import (
    canonical_identity,
    current_recommendation_id,
    video_states,
    video_payload,
)
from broader_vids.storage import utc_now
from broader_vids.learning.topic_labels import readable_labels


CHOICES = {
    "topic_interest": ("low", "medium", "high"),
    "domain_interest": ("low", "medium", "high"),
    "quality_level": ("low", "medium", "high"),
    "depth": ("deeper", "suitable", "lighter"),
}
LEVELS = ("unknown", "introductory", "intermediate", "advanced")


def superseded_channels(rows):
    result = {}
    for row in rows:
        channels = result.setdefault(row["id"], set())
        channels.update(
            ("quality",)
            if row["dimension"] == "quality_level"
            else ("interest", "overall")
        )
    return result


def details(store, identifiers=None):
    if identifiers == []:
        return {}
    query, args = "SELECT * FROM feedback_details", []
    if identifiers is not None:
        query += " WHERE canonical_id IN (" + ",".join("?" for _ in identifiers) + ")"
        args = identifiers
    result = {}
    for row in store.db.execute(query, args):
        value = json.loads(row["payload"])
        result.setdefault(row["canonical_id"], {})[row["dimension"]] = {
            k: v for k, v in value.items() if k != "features"
        }
    return result


def context_from_features(features):
    return {
        "topics": readable_labels(features.get("content_topics", []), 3),
        "domains": readable_labels(features.get("content_domains", []), 4),
        "audience_level": features.get("audience_level") or "unknown",
        "audience_assessment": features.get("audience_assessment"),
        "topic_source": features.get("topic_source", "unlabeled"),
        "topic_labels_are_suggestions": True,
    }


def mark_reviewed_watched(store, identifier, *, displayed=False):
    store.db.execute(
        """UPDATE video_state SET watched=1, updated_at=?
        WHERE canonical_id=? AND (recommended=1 OR ?) AND watched=0
        AND (SELECT COUNT(*) FROM feedback_details WHERE canonical_id=?)=4
        AND COALESCE((SELECT action FROM user_actions WHERE canonical_id=?
            AND action IN ('watched','unwatched') ORDER BY id DESC LIMIT 1),'') != 'unwatched'""",
        (utc_now(), identifier, int(displayed), identifier, identifier),
    )


def reconcile_current_reviews(store):
    name = "reviewed_watched_v1"
    table_exists = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dashboard_migrations'"
    ).fetchone()
    if (
        table_exists
        and store.db.execute(
            "SELECT 1 FROM dashboard_migrations WHERE name=?", (name,)
        ).fetchone()
    ):
        return
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS dashboard_migrations (name TEXT PRIMARY KEY)"
        )
        if store.db.execute(
            "SELECT 1 FROM dashboard_migrations WHERE name=?", (name,)
        ).fetchone():
            return
        for platform in ("youtube", "bilibili"):
            run_id = current_recommendation_id(store, platform)
            if not run_id:
                continue
            rows = store.db.execute(
                """SELECT json_extract(item.value,'$.canonical_id')
                FROM runs r, json_each(r.state, CASE
                    WHEN COALESCE(json_array_length(r.state,'$.selected'),0)>0
                    THEN '$.selected' ELSE '$.preview' END) item WHERE r.id=?""",
                (run_id,),
            ).fetchall()
            for row in rows:
                if row[0]:
                    mark_reviewed_watched(store, row[0], displayed=True)
        store.db.execute("INSERT INTO dashboard_migrations VALUES(?)", (name,))


def save_detail(
    store,
    identifier,
    dimension,
    value,
    *,
    target="",
    level=None,
    fit_model=True,
):
    infer_level = level is None
    if infer_level:
        level = "unknown"
    identity = canonical_identity(identifier)
    if (
        not isinstance(dimension, str)
        or dimension not in CHOICES
        or not isinstance(value, str)
        or value not in (*CHOICES[dimension], "clear")
        or not isinstance(target, str)
        or len(target) > 120
        or not isinstance(level, str)
        or level not in LEVELS
    ):
        raise ValueError(
            "Supply a supported feedback dimension, choice, level and bounded text."
        )
    target = normalized(target)
    if value != "clear" and dimension != "quality_level" and not target:
        raise ValueError(
            "Choose or enter the specific topic or broad subject this feedback concerns."
        )
    if dimension == "quality_level":
        target = ""
    from broader_vids.learning.service import capture_rating, refresh_after_feedback

    key = identity["canonical_id"]
    item = video_payload(store, key)
    features = capture_rating(store, key, item=item) if value != "clear" else None
    if dimension == "depth" and infer_level and features:
        inferred = features.get("audience_level")
        level = inferred if inferred in LEVELS else "unknown"
    if features and dimension in {"topic_interest", "depth"}:
        features = {
            **features,
            "topics": sorted(set(features.get("topics", [])) | {target}),
        }
    if features and dimension == "domain_interest":
        features = {
            **features,
            "domains": sorted(set(features.get("domains", [])) | {target}),
        }
    if features and dimension == "depth":
        features = {**features, "audience_level": level}
    payload = {"value": value, "target": target, "level": level}
    if dimension == "depth":
        payload["level_source"] = (
            "user" if not infer_level else "ai" if level != "unknown" else "pending"
        )
    if features:
        payload["features"] = features
    item_payload = json.dumps(item)
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(
            "INSERT OR IGNORE INTO video_state VALUES(?,?,?,0,0,0,?)",
            (
                key,
                identity["platform"],
                item_payload,
                utc_now(),
            ),
        )
        cursor = store.db.execute(
            "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,?,?,?)",
            (key, utc_now(), "feedback", json.dumps({"kind": dimension, **payload})),
        )
        if dimension in {"topic_interest", "depth"} and value != "clear":
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
        if value == "clear":
            store.db.execute(
                "DELETE FROM feedback_details WHERE canonical_id=? AND dimension=?",
                (key, dimension),
            )
        else:
            store.db.execute(
                "INSERT INTO feedback_details VALUES(?,?,?,?) ON CONFLICT(canonical_id,dimension) DO UPDATE SET action_id=excluded.action_id,payload=excluded.payload",
                (key, dimension, cursor.lastrowid, json.dumps(payload)),
            )
        if value != "clear" and store.db.execute(
            "SELECT COUNT(*) FROM feedback_details WHERE canonical_id=?", (key,)
        ).fetchone()[0] == len(CHOICES):
            mark_reviewed_watched(store, key)
            state = store.db.execute(
                "SELECT watched, recommended FROM video_state WHERE canonical_id=?",
                (key,),
            ).fetchone()
            if not state["watched"] and not state["recommended"]:
                current_id = current_recommendation_id(store, identity["platform"])
                displayed = bool(
                    current_id
                    and store.db.execute(
                        """SELECT 1 FROM runs r, json_each(r.state, CASE
                            WHEN COALESCE(json_array_length(r.state,'$.selected'),0)>0
                            THEN '$.selected' ELSE '$.preview' END) item
                        WHERE r.id=? AND json_extract(item.value,'$.canonical_id')=? LIMIT 1""",
                        (current_id, key),
                    ).fetchone()
                )
                if displayed:
                    mark_reviewed_watched(store, key, displayed=True)
    refresh_after_feedback(store, fit_model=fit_model)
    saved = details(store, [key])
    return {
        "ratings": video_states(store, identifiers=[key])[key]["ratings"],
        "details": saved.get(key, {}),
    }
