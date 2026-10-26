from collections import Counter
import hashlib
import json
import random
import re
import unicodedata

from deepvids.preferences import ALIASES, get_profile
from deepvids.storage import utc_now


def normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).strip()
    return ALIASES.get(value, value).replace("-", " ")


def node_id(label):
    return hashlib.sha256(normalized(label).encode()).hexdigest()[:24]


def nodes(store):
    return {
        row["id"]: json.loads(row["payload"])
        for row in store.db.execute("SELECT * FROM domain_nodes ORDER BY id")
    }


def put_node(store, node):
    store.db.execute(
        "INSERT INTO domain_nodes VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
        (node["id"], json.dumps(node, ensure_ascii=False)),
    )


def seed_interests(store):
    profile = get_profile(store)
    if not profile:
        raise ValueError("Set an interest profile before hierarchical exploration.")
    with store.db:
        current = nodes(store)
        for interest in profile["interests"]:
            identifier = node_id(interest)
            if identifier in current:
                continue
            node = {
                "id": identifier,
                "label": interest,
                "aliases": [],
                "parents": [],
                "ancestors": [],
                "domains": [interest],
                "depth": 1,
                "origin": "onboarding",
                "status": "active",
                "created_at": utc_now(),
                "queries": {},
            }
            store.db.execute(
                "INSERT OR IGNORE INTO domain_nodes VALUES(?,?)",
                (identifier, json.dumps(node, ensure_ascii=False)),
            )
    return profile


def is_related(node, profile):
    return bool(
        {normalized(d) for d in node["domains"]}
        & {normalized(d) for d in profile["interests"]}
    )


def select_existing(store, profile, seed, limit=12):
    current = nodes(store)
    history = list(
        store.db.execute(
            "SELECT node_id,payload FROM domain_observations ORDER BY created_at DESC,rowid DESC"
        )
    )
    usage = Counter(row["node_id"] for row in history)
    recent = {row["node_id"] for row in history[:24]}
    available = [node for node in current.values() if node["status"] == "active"]
    chosen, rng = [], random.Random(seed)
    while available and len(chosen) < limit:
        familiar = [n for n in available if is_related(n, profile)]
        pool = (
            familiar
            if sum(is_related(n, profile) for n in chosen) < min(6, limit) and familiar
            else available
        )
        weights = [
            (3 if is_related(n, profile) else 1)
            * (0.2 if n["id"] in recent else 1)
            / (1 + usage[n["id"]]) ** 0.5
            for n in pool
        ]
        selected = rng.choices(pool, weights=weights, k=1)[0]
        chosen.append(selected)
        available = [
            n
            for n in available
            if n["id"] != selected["id"]
            and n["id"] not in selected["ancestors"]
            and selected["id"] not in n["ancestors"]
        ]
    return chosen


def map_report(store):
    current = nodes(store)
    return {
        "profile": get_profile(store),
        "nodes": list(current.values()),
        "status_counts": dict(Counter(n["status"] for n in current.values())),
        "observations": store.db.execute(
            "SELECT COUNT(*) FROM domain_observations"
        ).fetchone()[0],
        "preferences_changed_by_exploration": False,
    }
