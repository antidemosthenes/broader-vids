import hashlib
import json

from broader_vids.identity import identify
from broader_vids.storage import utc_now


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


def set_profile(store, interests):
    if (
        not isinstance(interests, list)
        or not interests
        or len(interests) > 40
        or any(
            not isinstance(s, str) or not 1 <= len(s.strip()) <= 80 for s in interests
        )
    ):
        raise ValueError("Supply 1–40 bounded interest names.")
    interests = list(
        dict.fromkeys(
            ALIASES.get(s.strip().casefold(), s.strip().casefold()) for s in interests
        )
    )
    with store.lease(platform_lock("youtube")), store.lease(platform_lock("bilibili")):
        previous = get_profile(store)
        if previous and previous["interests"] == interests:
            return previous
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            current = get_profile(store)
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
    return get_profile(store)


def update_tag_rule(store, platform, tag, *, remove=False):
    if (
        platform not in ("youtube", "bilibili")
        or not isinstance(tag, str)
        or not 1 <= len(tag.strip()) <= 120
    ):
        raise ValueError("A tag rule requires a platform and bounded tag name.")
    tag = tag.strip()
    if platform == "bilibili" and tag == "视频翻译":
        raise ValueError("The built-in translation tag rule cannot be changed.")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        profile = get_profile(store)
        if not profile:
            raise ValueError("Set interests before adding a tag rule.")
        rules = {k: list(v) for k, v in profile.get("excluded_tags", {}).items()}
        current = rules.setdefault(platform, [])
        previous = list(current)
        if remove:
            current[:] = [s for s in current if s.strip().casefold() != tag.casefold()]
        elif not any(s.strip().casefold() == tag.casefold() for s in current):
            current.append(tag)
        if current == previous:
            return profile.get("excluded_tags", {})
        payload = {k: v for k, v in profile.items() if k != "revision"}
        payload.update(excluded_tags=rules, updated_at=utc_now())
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
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute(
            "INSERT OR IGNORE INTO video_state VALUES(?,?,?,0,0,0,?)",
            (
                key,
                identity["platform"],
                json.dumps({**identity, "title": key}),
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
