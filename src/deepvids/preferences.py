import hashlib
import json

from deepvids.identity import identify
from deepvids.storage import utc_now


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
        revision = previous["revision"] + 1 if previous else 1
        payload = {
            "interests": interests,
            "updated_at": utc_now(),
            "learning": "explicit_onboarding_only",
        }
        with store.db:
            store.db.execute(
                "INSERT INTO user_profile VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (revision, json.dumps(payload)),
            )
    return get_profile(store)


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
        WHERE a.canonical_id=v.canonical_id AND a.action='feedback' ORDER BY a.id DESC LIMIT 1) AS feedback_payload
        FROM video_state v"""
        + (" WHERE " + " AND ".join(conditions) if conditions else ""),
        args,
    )
    result = {}
    for row in rows:
        item = {**dict(row), "payload": json.loads(row["payload"])}
        vote = item.pop("feedback_payload")
        item["feedback"] = json.loads(vote) if vote else None
        item["pending"] = bool(
            item["recommended"] and not item["watched"] and not item["dismissed"]
        )
        result[item["canonical_id"]] = item
    return result


def pending_videos(store, platform):
    return [
        v["payload"] for v in video_states(store, platform).values() if v["pending"]
    ]


def user_action(store, identifier, action, reason="", kind="overall"):
    identity = canonical_identity(identifier)
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
        store.db.execute(
            "INSERT INTO user_actions(canonical_id,created_at,action,payload) VALUES(?,?,?,?)",
            (key, utc_now(), action if field else "feedback", json.dumps(payload)),
        )
    return video_states(store, identity["platform"])[key]
