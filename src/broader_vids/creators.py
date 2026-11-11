import json
import re

from broader_vids.preferences import canonical_identity, get_profile
from broader_vids.storage import utc_now
from broader_vids.tag_rules import tag_rules


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
    return _display_name(name, identifier, row[0] if row else None)


def _display_name(name, identifier, payload):
    if isinstance(name, str) and name.strip() and name.strip() != identifier:
        return name.strip()
    return _cached_name(payload)


def _cached_name(payload):
    cached = json.loads(payload).get("name") if payload is not None else None
    return cached.strip() if isinstance(cached, str) else ""


def _video_key(identifier):
    return (
        canonical_identity(identifier)["canonical_id"]
        if identifier.startswith("https:")
        else identifier
    )


def _fetch_creator_rows(store, keys):
    result = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        requested = ",".join("(?)" for _ in chunk)
        query = f"""WITH requested(canonical_id) AS (VALUES {requested})
            SELECT requested.canonical_id, video_state.payload AS video_payload,
                calibration_items.payload AS calibration_payload,
                engagement_snapshots.payload AS engagement_payload
            FROM requested
            LEFT JOIN video_state USING(canonical_id)
            LEFT JOIN calibration_items USING(canonical_id)
            LEFT JOIN engagement_snapshots USING(canonical_id)"""
        for row in store.db.execute(query, chunk):
            result[row["canonical_id"]] = dict(row)
    return result


def _fetch_cached_names(store, pairs):
    result = {}
    grouped = {}
    for platform, identifier in pairs:
        grouped.setdefault(platform, set()).add(identifier)
    for platform, identifiers in grouped.items():
        values = sorted(identifiers)
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            query = (
                "SELECT item_key,payload FROM discovery_registry "
                "WHERE platform=? AND kind='creator' AND item_key IN ("
                + ",".join("?" for _ in chunk)
                + ")"
            )
            result.update(
                ((platform, row["item_key"]), row["payload"])
                for row in store.db.execute(query, (platform, *chunk))
            )
    return result


def video_creators(store, identifiers, *, state_items=None, snapshots=None):
    keys_by_input = {}
    for identifier in identifiers:
        key = _video_key(identifier)
        keys_by_input.setdefault(key, identifier)
    keys = list(keys_by_input)
    if not keys:
        return {}

    state_items = state_items or {}
    video_items = dict(state_items)
    engagement = dict(snapshots or {})
    fetched = {}
    needs_lookup = [key for key in keys if key not in state_items or snapshots is None]
    if needs_lookup:
        fetched = _fetch_creator_rows(store, needs_lookup)
        for key, row in fetched.items():
            if key not in video_items and row["video_payload"] is not None:
                video_items[key] = json.loads(row["video_payload"])
            if snapshots is None and row["engagement_payload"] is not None:
                engagement[key] = json.loads(row["engagement_payload"])

    calibrations = {
        key: row["calibration_payload"]
        for key, row in fetched.items()
        if row["calibration_payload"] is not None
    }
    items = {}
    for key, identifier in keys_by_input.items():
        if key in video_items:
            item = dict(video_items[key])
        elif key in calibrations:
            payload = json.loads(calibrations[key])
            item = {
                **payload["item"],
                "platform": payload["features"]["platform"],
                "creator_id": payload["features"].get("creator"),
            }
        else:
            item = canonical_identity(identifier)
        if key in engagement:
            enrich(item, engagement[key])
        items[key] = item

    unresolved = [key for key, item in items.items() if identity(item) is None]
    missing_calibrations = [key for key in unresolved if key not in calibrations]
    calibrations.update(
        (key, row["calibration_payload"])
        for key, row in _fetch_creator_rows(store, missing_calibrations).items()
        if row["calibration_payload"] is not None
    )
    for key in unresolved:
        payload = calibrations.get(key)
        if payload:
            items[key]["creator_id"] = json.loads(payload)["features"].get("creator")

    values = {key: identity(item) for key, item in items.items()}
    name_lookups = []
    for key, item in items.items():
        identifier = values[key]
        name = item.get("creator_name")
        if identifier and not (
            isinstance(name, str) and name.strip() and name.strip() != identifier
        ):
            name_lookups.append((item["platform"], identifier))
    cached_names = _fetch_cached_names(store, name_lookups)

    result = {}
    for key, item in items.items():
        value = values[key]
        name = item.get("creator_name")
        if isinstance(name, str) and name.strip() and name.strip() != value:
            resolved_name = name.strip()
        else:
            resolved_name = _cached_name(cached_names.get((item["platform"], value)))
        result[key] = {
            "platform": item["platform"],
            "creator_id": value,
            "name": resolved_name,
            "url": creator_url(item["platform"], value) if value else None,
        }
    return result


def video_creator(store, identifier):
    key = _video_key(identifier)
    return video_creators(store, [identifier])[key]


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
        """SELECT exclusions.*, discovery_registry.payload AS cached_payload
        FROM creator_exclusions AS exclusions
        LEFT JOIN discovery_registry ON discovery_registry.platform=exclusions.platform
            AND discovery_registry.kind='creator'
            AND discovery_registry.item_key=exclusions.creator_id
        WHERE exclusions.source='manual' OR exclusions.account_id=?
        ORDER BY exclusions.platform,exclusions.creator_id,exclusions.source""",
        (policy["account_id"],),
    ):
        entry = creators[row["platform"]].setdefault(
            row["creator_id"],
            {
                "creator_id": row["creator_id"],
                "name": _display_name(
                    row["name"], row["creator_id"], row["cached_payload"]
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
    tags = {platform: tag_rules(profile, platform) for platform in creators}
    return {
        "revision": policy["revision"],
        "tag_revision": profile.get("revision", 0),
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


def checkbox_state(creator, rules, index=None):
    entry = (
        index[creator["platform"]].get(creator["creator_id"])
        if index is not None
        else next(
            (
                entry
                for entry in rules["creators"][creator["platform"]]
                if entry["creator_id"] == creator["creator_id"]
            ),
            None,
        )
    )
    return {
        **creator,
        "excluded": bool(entry),
        "imported": bool(entry and entry["imported"]),
    }


def checkbox_index(rules):
    return {
        platform: {
            entry["creator_id"]: entry
            for entry in reversed(rules["creators"][platform])
        }
        for platform in rules["creators"]
    }


def policy_summary(policy):
    return {key: policy[key] for key in ("version", "revision") if key in policy} | {
        "excluded_count": len(policy.get("excluded_ids", []))
    }


def initialize_round(store, run, remaining):
    if run["settings"].get("creator_policy_version") != 1:
        raise ValueError(
            "Unsupported creator policy; start a new recommendation round."
        )
    if "creator_rules" in run["state"]:
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
