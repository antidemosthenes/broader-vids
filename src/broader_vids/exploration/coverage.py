from contextlib import contextmanager
from functools import wraps
import hashlib
import json

from broader_vids.exploration.domains import node_id, nodes, normalized, put_node
from broader_vids.storage import utc_now


MEMORY_ID = hashlib.sha256(b"broader-vids-exploration-memory-v1").hexdigest()[:32]
MAX_NODES = 1024
COOLDOWN = 3


def memory(store):
    row = store.db.execute("SELECT state FROM runs WHERE id=?", (MEMORY_ID,)).fetchone()
    return (
        json.loads(row[0])
        if row
        else {"version": 1, "sequence": 0, "usage": {}, "rounds": {}}
    )


def save_memory(store, value):
    now = utc_now()
    store.db.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,state=excluded.state",
        (
            MEMORY_ID,
            "both",
            now,
            now,
            "completed",
            "",
            json.dumps({"kind": "exploration_memory"}),
            json.dumps(value),
        ),
    )


@contextmanager
def transaction(store):
    if store.db.in_transaction:
        yield
    else:
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            yield


def atomic_update(function):
    @wraps(function)
    def update(store, *args, **kwargs):
        with transaction(store):
            return function(store, *args, **kwargs)

    return update


@atomic_update
def round_number(store, owner):
    value = memory(store)
    if owner not in value["rounds"]:
        value["sequence"] += 1
        value["rounds"][owner] = {
            "number": value["sequence"],
            "platforms": {},
            "published": [],
        }
        value["rounds"] = dict(list(value["rounds"].items())[-64:])
        save_memory(store, value)
    return value["rounds"][owner]["number"]


@atomic_update
def reserve(store, owner, platform, identifiers, *, relaxations=()):
    number = round_number(store, owner)
    value = memory(store)
    value["rounds"][owner]["platforms"][platform] = list(identifiers)
    value["rounds"][owner].setdefault("relaxations", {})[platform] = list(relaxations)
    save_memory(store, value)
    return number


@atomic_update
def record_search(store, owner, identifier, saved_round=None):
    value = memory(store)
    number = value["rounds"].get(owner, {}).get("number") or saved_round
    if number is None:
        number = round_number(store, owner)
        value = memory(store)
    cell = value["usage"].setdefault(
        identifier, {"searches": 0, "last_used": 0, "last_recommended": 0}
    )
    cell.update(
        searches=min(1_000_000_000, cell["searches"] + 1),
        last_used=max(cell["last_used"], number, value["sequence"]),
    )
    save_memory(store, value)


@atomic_update
def record_recommendations(store, run):
    discovery = run["state"].get("discovery_run")
    if not discovery:
        return
    plan = store.get(discovery)["state"].get("exploration", {})
    if plan.get("policy_version", 1) < 2:
        return
    owner = plan.get("coverage_owner", discovery)
    value = memory(store)
    number = max(plan.get("coverage_round", value["sequence"]), value["sequence"])
    group = value["rounds"].get(owner)
    known = nodes(store)
    for item in run["state"].get("selected", []):
        for origin in item.get("origins", []):
            identifier = origin.get("node_id")
            if identifier in known:
                cell = value["usage"].setdefault(
                    identifier, {"searches": 0, "last_used": 0, "last_recommended": 0}
                )
                cell["last_recommended"] = max(cell["last_recommended"], number)
    if group is not None and run["platform"] not in group["published"]:
        group["published"].append(run["platform"])
    save_memory(store, value)


def export_summary(store):
    value, current = memory(store), nodes(store)
    chosen = set()
    ordered = sorted(
        current,
        key=lambda key: (
            value["usage"].get(key, {}).get("last_used", 0),
            value["usage"].get(key, {}).get("searches", 0),
            key,
        ),
        reverse=True,
    )
    for key in ordered:
        family = {key, *current[key].get("ancestors", [])}
        if len(chosen | family) <= MAX_NODES:
            chosen.update(family)
    fields = (
        "id",
        "label",
        "aliases",
        "parents",
        "domains",
        "depth",
        "origin",
        "status",
    )
    summary = {
        "version": 1,
        "sequence": value["sequence"],
        "nodes": [
            {name: current[key][name] for name in fields}
            for key in sorted(chosen)
            if key in current
        ],
        "usage": {key: cell for key, cell in value["usage"].items() if key in chosen},
    }
    validate_summary(summary)
    return summary


def validate_summary(summary):
    if (
        not isinstance(summary, dict)
        or set(summary) != {"version", "sequence", "nodes", "usage"}
        or type(summary["version"]) is not int
        or summary["version"] != 1
    ):
        raise ValueError("Invalid exploration coverage summary.")

    def integer(value):
        return type(value) is int and 0 <= value <= 1_000_000_000

    def label(value):
        return (
            isinstance(value, str) and 0 < len(value) <= 120 and value.strip() == value
        )

    if (
        not integer(summary["sequence"])
        or not isinstance(summary["nodes"], list)
        or len(summary["nodes"]) > MAX_NODES
        or not isinstance(summary["usage"], dict)
    ):
        raise ValueError("Exploration coverage exceeds its supported limits.")
    current = {}
    fields = {
        "id",
        "label",
        "aliases",
        "parents",
        "domains",
        "depth",
        "origin",
        "status",
    }
    for node in summary["nodes"]:
        if (
            not isinstance(node, dict)
            or set(node) != fields
            or not label(node["label"])
            or node["id"] != node_id(node["label"])
            or node["id"] in current
        ):
            raise ValueError("Invalid exploration topic identity.")
        for name, maximum in (("aliases", 6), ("parents", 3), ("domains", 40)):
            values = node[name]
            if (
                not isinstance(values, list)
                or len(values) > maximum
                or not all(label(item) for item in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("Invalid exploration topic labels or parents.")
        if (
            not node["domains"]
            or type(node["depth"]) is not int
            or not 1 <= node["depth"] <= 32
            or node["origin"] not in {"onboarding", "exploration", "subdivision"}
            or node["status"] not in {"active", "proposed", "unconfirmed"}
        ):
            raise ValueError("Invalid exploration topic state.")
        current[node["id"]] = node
    complete = {}

    def depth(key, visiting):
        if key in complete:
            return complete[key]
        if key not in current or key in visiting or len(visiting) >= 32:
            raise ValueError(
                "Exploration parents must form a bounded acyclic hierarchy."
            )
        node = current[key]
        result = 1 + max(
            (depth(parent, visiting | {key}) for parent in node["parents"]), default=0
        )
        if node["depth"] != result:
            raise ValueError("Exploration depth disagrees with its parents.")
        complete[key] = result
        return result

    for key in current:
        depth(key, set())
    for key, cell in summary["usage"].items():
        if (
            key not in current
            or not isinstance(cell, dict)
            or set(cell) != {"searches", "last_used", "last_recommended"}
            or not all(integer(n) for n in cell.values())
            or max(cell["last_used"], cell["last_recommended"]) > summary["sequence"]
        ):
            raise ValueError("Invalid exploration coverage counters.")


def _merge_nodes(store, summary):
    validate_summary(summary)
    current = nodes(store)
    incoming = {node["id"]: node for node in summary["nodes"]}
    if len(current.keys() | incoming.keys()) > MAX_NODES:
        raise ValueError("The merged exploration hierarchy would exceed 1,024 topics.")
    names = {
        normalized(name): key
        for key, node in current.items()
        for name in [node["label"], *node["aliases"]]
    }
    added = []

    def insert(key):
        if key in current:
            return
        node = dict(incoming[key])
        for parent in node["parents"]:
            insert(parent)
        for name in [node["label"], *node["aliases"]]:
            if normalized(name) in names and names[normalized(name)] != key:
                raise ValueError(
                    "An imported topic name or alias conflicts with a local topic."
                )
            names[normalized(name)] = key
        depth = 1 + max((current[p]["depth"] for p in node["parents"]), default=0)
        if depth > 32:
            raise ValueError("The merged exploration hierarchy would exceed 32 levels.")
        node.update(
            depth=depth,
            ancestors=sorted(
                {
                    ancestor
                    for p in node["parents"]
                    for ancestor in [p, *current[p].get("ancestors", [])]
                }
            ),
            domains=sorted(
                {domain for p in node["parents"] for domain in current[p]["domains"]}
            )
            if node["parents"]
            else node["domains"],
            created_at=utc_now(),
            queries={},
        )
        current[key] = node
        added.append(node)

    for key in incoming:
        insert(key)
    return added


def validate_merge(store, summary):
    _merge_nodes(store, summary)


@atomic_update
def import_summary(store, summary):
    added = _merge_nodes(store, summary)
    local = memory(store)
    sequence = max(local["sequence"], summary["sequence"])
    shift = sequence - local["sequence"]
    for cell in local["usage"].values():
        for name in ("last_used", "last_recommended"):
            if cell[name]:
                cell[name] += shift
    for group in local["rounds"].values():
        group["number"] += shift
    imports = local.setdefault("imports", {})
    imports.update({key: offset + shift for key, offset in imports.items()})
    checksum = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    offset = imports.pop(checksum, sequence - summary["sequence"])
    imports[checksum] = offset
    local["imports"] = dict(list(imports.items())[-32:])
    local["sequence"] = sequence
    for node in added:
        put_node(store, node)
    for key, cell in summary["usage"].items():
        target = local["usage"].setdefault(
            key, {"searches": 0, "last_used": 0, "last_recommended": 0}
        )
        target["searches"] = max(target["searches"], cell["searches"])
        for name in ("last_used", "last_recommended"):
            restored = cell[name] + offset if cell[name] else 0
            target[name] = max(target[name], restored)
    save_memory(store, local)
