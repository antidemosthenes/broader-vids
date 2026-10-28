import json
import re

from broader_vids.preferences import canonical_identity, get_profile
from broader_vids.storage import utc_now


def creator_id(platform, value):
    if platform == "bilibili" and type(value) in (int, str):
        value = str(value).strip()
        if re.fullmatch(r"[0-9]{1,20}", value) and int(value) > 0:
            return str(int(value))
    if platform == "youtube" and isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", value):
            return value
    raise ValueError("Use a YouTube channel ID or a positive Bilibili account ID.")


def identity(item):
    metadata = item.get("engagement") or {}
    for value in (metadata.get("creator_id"), item.get("creator_id")):
        try:
            return creator_id(item.get("platform"), value)
        except ValueError:
            continue
    return None


def enrich(item, metadata):
    previous = identity(item)
    item["engagement"] = metadata
    if metadata.get("identity_verified") and metadata.get("creator_id"):
        try:
            item["creator_id"] = creator_id(item["platform"], metadata["creator_id"])
        except ValueError:
            return item
        item["creator_name"] = metadata.get("creator_name") or (
            item.get("creator_name", "") if previous == item["creator_id"] else ""
        )
    return item


def creator_url(platform, identifier):
    return (
        f"https://www.youtube.com/channel/{identifier}"
        if platform == "youtube"
        else f"https://space.bilibili.com/{identifier}"
    )


def display_name(store, platform, identifier, name=""):
    if isinstance(name, str) and name.strip() and name.strip() != identifier:
        return name.strip()
    row = store.db.execute(
        "SELECT payload FROM discovery_registry WHERE platform=? AND kind='creator' AND item_key=?",
        (platform, identifier),
    ).fetchone()
    cached = json.loads(row[0]).get("name") if row else None
    return cached.strip() if isinstance(cached, str) else ""


def video_creator(store, identifier):
    key = (
        canonical_identity(identifier)["canonical_id"]
        if identifier.startswith("https:")
        else identifier
    )
    row = store.db.execute(
        "SELECT payload FROM video_state WHERE canonical_id=?", (key,)
    ).fetchone()
    if row:
        item = json.loads(row[0])
    else:
        calibration = store.db.execute(
            "SELECT payload FROM calibration_items WHERE canonical_id=?", (key,)
        ).fetchone()
        if calibration:
            payload = json.loads(calibration[0])
            item = {
                **payload["item"],
                "platform": payload["features"]["platform"],
                "creator_id": payload["features"].get("creator"),
            }
        else:
            item = canonical_identity(identifier)
    row = store.db.execute(
        "SELECT payload FROM engagement_snapshots WHERE canonical_id=?", (key,)
    ).fetchone()
    if row:
        enrich(item, json.loads(row[0]))
    if identity(item) is None:
        calibration = store.db.execute(
            "SELECT payload FROM calibration_items WHERE canonical_id=?", (key,)
        ).fetchone()
        if calibration:
            item["creator_id"] = json.loads(calibration[0])["features"].get("creator")
    value = identity(item)
    return {
        "platform": item["platform"],
        "creator_id": value,
        "name": display_name(store, item["platform"], value, item.get("creator_name")),
        "url": creator_url(item["platform"], value) if value else None,
    }


def snapshot(store, platform):
    policy = store.db.execute(
        "SELECT revision,account_id FROM creator_policy WHERE id=1"
    ).fetchone()
    rows = store.db.execute(
        "SELECT creator_id FROM creator_exclusions WHERE platform=? AND (source='manual' OR account_id=?)",
        (platform, policy["account_id"]),
    )
    return {
        "version": 1,
        "revision": policy["revision"],
        "account_id": policy["account_id"] if platform == "bilibili" else "",
        "excluded_ids": sorted({row[0] for row in rows}),
    }


def excluded(item, policy):
    value = identity(item)
    return bool(value and value in (policy or {}).get("excluded_ids", []))


def set_exclusion(store, platform, identifier, exclude, name=""):
    identifier = creator_id(platform, identifier)
    if type(exclude) is not bool or not isinstance(name, str) or len(name) > 300:
        raise ValueError(
            "A creator rule requires a boolean choice and a bounded display name."
        )
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if exclude:
            store.db.execute(
                "INSERT INTO creator_exclusions VALUES(?,?, 'manual','',?,?) ON CONFLICT(platform,creator_id,source,account_id) DO UPDATE SET name=excluded.name",
                (platform, identifier, name, utc_now()),
            )
        else:
            store.db.execute(
                "DELETE FROM creator_exclusions WHERE platform=? AND creator_id=? AND source='manual'",
                (platform, identifier),
            )
        store.db.execute("UPDATE creator_policy SET revision=revision+1 WHERE id=1")
    return status(store)


def status(store):
    policy = store.db.execute(
        "SELECT revision,account_id FROM creator_policy WHERE id=1"
    ).fetchone()
    creators = {"youtube": {}, "bilibili": {}}
    for row in store.db.execute(
        "SELECT * FROM creator_exclusions WHERE source='manual' OR account_id=? ORDER BY platform,creator_id,source",
        (policy["account_id"],),
    ):
        entry = creators[row["platform"]].setdefault(
            row["creator_id"],
            {
                "creator_id": row["creator_id"],
                "name": display_name(
                    store, row["platform"], row["creator_id"], row["name"]
                ),
                "url": creator_url(row["platform"], row["creator_id"]),
                "sources": [],
            },
        )
        entry["sources"].append(row["source"])
        entry["imported"] = "bilibili_blacklist" in entry["sources"]
    record = store.db.execute(
        "SELECT payload FROM creator_sync WHERE account_id=?", (policy["account_id"],)
    ).fetchone()
    profile = get_profile(store) or {}
    tags = {
        p: [
            {"tag": tag, "source": "manual", "editable": True}
            for tag in profile.get("excluded_tags", {}).get(p, [])
        ]
        for p in creators
    }
    tags["bilibili"] = [tag for tag in tags["bilibili"] if tag["tag"] != "视频翻译"] + [
        {"tag": "视频翻译", "source": "builtin", "editable": False}
    ]
    return {
        "revision": policy["revision"],
        "creators": {p: list(entries.values()) for p, entries in creators.items()},
        "tags": tags,
        "bilibili_sync": {
            "account_id": policy["account_id"],
            **(
                json.loads(record[0])
                if record
                else {"status": "never_synced", "last_success": None}
            ),
        },
    }


def checkbox_state(creator, rules):
    entry = next(
        (
            entry
            for entry in rules["creators"][creator["platform"]]
            if entry["creator_id"] == creator["creator_id"]
        ),
        None,
    )
    return {
        **creator,
        "excluded": bool(entry),
        "imported": bool(entry and entry["imported"]),
    }


def policy_summary(policy):
    return {key: policy[key] for key in ("version", "revision") if key in policy} | {
        "excluded_count": len(policy.get("excluded_ids", []))
    }


def initialize_round(store, run, remaining):
    if "creator_rules" in run["state"]:
        return
    if run["settings"].get("creator_policy_version") != 1:
        run["state"]["creator_rules"] = {}
        return
    if run["platform"] == "bilibili":
        from broader_vids.blacklist import sync_blacklist

        budget = min(
            100, run["settings"]["max_requests"] - store.request_count(run["id"])
        )
        result = (
            sync_blacklist(
                store,
                max_seconds=min(60, remaining()),
                max_requests=budget,
                run_id=run["id"],
            )
            if budget >= 4
            else {
                "status": "failed",
                "error": "Insufficient request allowance for blacklist sync.",
            }
        )
        run["state"]["blacklist_sync"] = result
        if result["status"] != "complete":
            run["state"]["issues"].append(
                {"stage": "blacklist_sync", "reason": result["error"]}
            )
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        rules = snapshot(store, run["platform"])
        released = []
        for row in store.db.execute(
            "SELECT canonical_id,payload FROM video_state WHERE platform=? AND recommended=1 AND watched=0 AND dismissed=0",
            (run["platform"],),
        ).fetchall():
            item = json.loads(row["payload"])
            metadata = store.db.execute(
                "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                (row["canonical_id"],),
            ).fetchone()
            if metadata:
                enrich(item, json.loads(metadata[0]))
            if excluded(item, rules):
                released.append(row["canonical_id"])
        store.db.executemany(
            "UPDATE video_state SET recommended=0,updated_at=? WHERE canonical_id=?",
            [(utc_now(), key) for key in released],
        )
        run["state"].update(creator_rules=rules, creator_released_pending=released)
        store.save(run)
