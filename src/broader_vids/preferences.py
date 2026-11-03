from contextlib import ExitStack
import hashlib
import json

from broader_vids.identity import identify
from broader_vids.storage import utc_now
from broader_vids.tag_rules import BUILTIN_TAGS, normalize_tag


ALIASES = {
    "eat": "food",
    "eating": "food",
    "game": "games",
    "computer science": "computer-science",
    "computing": "computer-science",
    "math": "mathematics",
}


def platform_lock(platform):
    return hashlib.sha256(f"recommendations:{platform}".encode()).hexdigest()[:32]


def get_profile(store):
    row = store.db.execute(
        "SELECT revision,payload FROM user_profile WHERE id=1"
    ).fetchone()
    return {"revision": row["revision"], **json.loads(row["payload"])} if row else None


class ProfileConflict(ValueError):
    pass


def set_profile(store, interests, *, expected_revision=None):
    if (
        not isinstance(interests, list)
        or not interests
        or len(interests) > 40
        or any(
            not isinstance(s, str) or not 1 <= len(s.strip()) <= 80 for s in interests
        )
    ):
        raise ValueError("Enter 1–40 interests, each with 1–80 characters.")
    interests = list(
        dict.fromkeys(
            ALIASES.get(s.strip().casefold(), s.strip().casefold()) for s in interests
        )
    )
    if any(len(name) > 80 for name in interests):
        raise ValueError("Each normalized interest must contain at most 80 characters.")
    if expected_revision is not None and (
        type(expected_revision) is not int or expected_revision < 0
    ):
        raise ValueError("Load the saved interests before saving changes.")
    with ExitStack() as leases:
        try:
            for platform in ("youtube", "bilibili"):
                leases.enter_context(store.lease(platform_lock(platform)))
        except ValueError:
            raise ValueError(
                "Recommendation work still uses this profile. Finish that work before changing interests."
            ) from None
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            current = get_profile(store)
            if expected_revision is not None and expected_revision != (
                current or {}
            ).get("revision", 0):
                raise ProfileConflict(
                    "Saved interests changed in another window. Reload saved interests before saving again."
                )
            if current and current["interests"] == interests:
                return current
            revision = current["revision"] + 1 if current else 1
            payload = {
                **{k: v for k, v in (current or {}).items() if k != "revision"},
                "interests": interests,
                "updated_at": utc_now(),
                "learning": "explicit_onboarding_only",
            }
            store.db.execute(
                "INSERT INTO user_profile VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (revision, json.dumps(payload)),
            )
            return {"revision": revision, **payload}


def update_tag_rule(store, platform, tag, *, remove=False):
    if (
        platform not in ("youtube", "bilibili")
        or not isinstance(tag, str)
        or not 1 <= len(tag.strip()) <= 120
    ):
        raise ValueError("A tag rule requires a platform and bounded tag name.")
    tag = tag.strip()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        profile = get_profile(store)
        if not profile:
            raise ValueError("Set interests before adding a tag rule.")
        rules = {k: list(v) for k, v in profile.get("excluded_tags", {}).items()}
        disabled = {
            k: list(v) for k, v in profile.get("disabled_builtin_tags", {}).items()
        }
        current = rules.setdefault(platform, [])
        previous = list(current)
        previous_disabled = list(disabled.get(platform, []))
        key = normalize_tag(tag)
        builtin = next(
            (
                value
                for value in BUILTIN_TAGS.get(platform, ())
                if normalize_tag(value) == key
            ),
            None,
        )
        if builtin:
            current[:] = [value for value in current if normalize_tag(value) != key]
            inactive = disabled.setdefault(platform, [])
            inactive[:] = [value for value in inactive if normalize_tag(value) != key]
            if remove:
                inactive.append(builtin)
        elif remove:
            current[:] = [value for value in current if normalize_tag(value) != key]
        elif not any(normalize_tag(value) == key for value in current):
            current.append(tag)
        if current == previous and disabled.get(platform, []) == previous_disabled:
            return profile.get("excluded_tags", {})
        payload = {k: v for k, v in profile.items() if k != "revision"}
        payload.update(excluded_tags=rules, updated_at=utc_now())
        if disabled or "disabled_builtin_tags" in profile:
            payload["disabled_builtin_tags"] = disabled
        store.db.execute(
            "UPDATE user_profile SET revision=?,payload=? WHERE id=1",
            (profile["revision"] + 1, json.dumps(payload)),
        )
    return rules


def canonical_identity(value):
    if not isinstance(value, str):
        raise ValueError("A video identifier must be a string.")
    if value.startswith("https:"):
        return identify(value)
    pieces = value.split(":")
    if len(pieces) == 2 and pieces[0] == "youtube":
        return identify(f"https://www.youtube.com/watch?v={pieces[1]}")
    if len(pieces) == 3 and pieces[0] == "bilibili" and pieces[2].startswith("p"):
        return identify(f"https://www.bilibili.com/video/{pieces[1]}?p={pieces[2][1:]}")
    raise ValueError("Use a canonical video URL or canonical platform/video/part ID.")


def current_recommendation_id(store, platform):
    query = "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='recommendation' AND platform=?"
    order = " ORDER BY created_at DESC, id DESC LIMIT 1"
    row = store.db.execute(
        query + " AND (COALESCE(json_array_length(state,'$.selected'),0)>0"
        " OR COALESCE(json_array_length(state,'$.preview'),0)>0)" + order,
        (platform,),
    ).fetchone()
    if row is None:
        row = store.db.execute(query + order, (platform,)).fetchone()
    return row[0] if row else None


def video_payload(store, identifier):
    identity = canonical_identity(identifier)
    key = identity["canonical_id"]
    row = store.db.execute(
        "SELECT payload FROM video_state WHERE canonical_id=?", (key,)
    ).fetchone()
    saved = json.loads(row[0]) if row else {**identity, "title": key}
    current = current_recommendation_id(store, identity["platform"])
    if current:
        row = store.db.execute(
            "SELECT item.value FROM runs r, json_each(r.state, CASE"
            " WHEN COALESCE(json_array_length(r.state,'$.selected'),0)>0"
            " THEN '$.selected' ELSE '$.preview' END) item"
            " WHERE r.id=? AND json_extract(item.value,'$.canonical_id')=?",
            (current, key),
        ).fetchone()
        if row:
            return json.loads(row[0])
    if saved.get("quality_evidence"):
        return saved
    row = store.db.execute(
        "SELECT item.value FROM runs r, json_each(r.state, CASE"
        " WHEN COALESCE(json_array_length(r.state,'$.selected'),0)>0"
        " THEN '$.selected' ELSE '$.preview' END) item"
        " WHERE r.platform=? AND json_extract(r.settings,'$.kind')='recommendation'"
        " AND json_extract(item.value,'$.canonical_id')=?"
        " ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        (identity["platform"], key),
    ).fetchone()
    return json.loads(row[0]) if row else saved


def video_states(store, platform=None, *, identifiers=None):
    if identifiers is not None and not identifiers:
        return {}
    conditions, args = [], []
    if platform:
        conditions.append("v.platform=?")
        args.append(platform)
    if identifiers is not None:
        conditions.append(
            "v.canonical_id IN (" + ",".join("?" for _ in identifiers) + ")"
        )
        args.extend(identifiers)
    rows = store.db.execute(
        """SELECT v.*, (SELECT payload FROM user_actions a
        WHERE a.canonical_id=v.canonical_id AND a.action='feedback'
        AND COALESCE(json_extract(a.payload,'$.kind'),'overall') IN ('interest','quality','overall')
        ORDER BY a.id DESC LIMIT 1) AS feedback_payload
        FROM video_state v"""
        + (" WHERE " + " AND ".join(conditions) if conditions else ""),
        args,
    )
    result = {}
    ratings = {}
    rating_query = "SELECT canonical_id,dimension,payload FROM feedback_ratings"
    if identifiers is not None:
        rating_query += (
            " WHERE canonical_id IN (" + ",".join("?" for _ in identifiers) + ")"
        )
    for row in store.db.execute(rating_query, identifiers or []):
        payload = json.loads(row["payload"])
        ratings.setdefault(row["canonical_id"], {})[row["dimension"]] = {
            k: v for k, v in payload.items() if k != "features"
        }
    for row in rows:
        item = {**dict(row), "payload": json.loads(row["payload"])}
        vote = item.pop("feedback_payload")
        item["feedback"] = (
            {k: v for k, v in json.loads(vote).items() if k != "features"}
            if vote
            else None
        )
        item["ratings"] = ratings.get(item["canonical_id"], {})
        item["pending"] = bool(
            item["recommended"] and not item["watched"] and not item["dismissed"]
        )
        result[item["canonical_id"]] = item
    return result


def pending_videos(store, platform):
    return [
        json.loads(row[0])
        for row in store.db.execute(
            "SELECT payload FROM video_state WHERE platform=? AND recommended=1 AND watched=0 AND dismissed=0",
            (platform,),
        )
    ]


def user_action(store, identifier, action, reason="", kind="overall"):
    identity = canonical_identity(identifier)
    if action in {"up", "down", "clear"} and (get_profile(store) or {}).get(
        "retired_binary_feedback"
    ):
        raise ValueError(
            "Binary votes are retired. Use the separate topic, depth, and quality fields."
        )
    if action not in {
        "watched",
        "unwatched",
        "dismiss",
        "restore",
        "up",
        "down",
        "clear",
    }:
        raise ValueError("Unsupported video action.")
    if (
        not isinstance(kind, str)
        or kind not in {"overall", "interest", "quality"}
        or not isinstance(reason, str)
        or len(reason) > 1000
    ):
        raise ValueError(
            "Feedback requires a supported reason kind and at most 1,000 reason characters."
        )
    key = identity["canonical_id"]
    features = None
    if action in {"up", "down"}:
        from broader_vids.learning.service import capture_rating

        features = capture_rating(store, key)
    item_payload = json.dumps(video_payload(store, key))
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
        field = (
            "watched"
            if action in {"watched", "unwatched"}
            else "dismissed"
            if action in {"dismiss", "restore"}
            else None
        )
        if field:
            store.db.execute(
                f"UPDATE video_state SET {field}=?,updated_at=? WHERE canonical_id=?",
                (int(action in {"watched", "dismiss"}), utc_now(), key),
            )
        payload = (
            {
                "vote": action if action in {"up", "down"} else None,
                "reason": reason,
                "kind": kind,
            }
            if not field
            else {"value": action in {"watched", "dismiss"}}
        )
        if not field and action != "clear":
            payload["features"] = features
        cursor = store.db.execute(
            "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,?,?,?)",
            (key, utc_now(), action if field else "feedback", json.dumps(payload)),
        )
        if not field:
            if action == "clear":
                store.db.execute(
                    "DELETE FROM feedback_ratings WHERE canonical_id=? AND dimension=?",
                    (key, kind),
                )
            else:
                store.db.execute(
                    "INSERT INTO feedback_ratings VALUES(?,?,?,?) ON CONFLICT(canonical_id,dimension) DO UPDATE SET action_id=excluded.action_id,payload=excluded.payload",
                    (key, kind, cursor.lastrowid, json.dumps(payload)),
                )
    if not field:
        from broader_vids.learning.service import refresh_after_feedback

        refresh_after_feedback(store)
    return video_states(store, identifiers=[key])[key]
