from contextlib import contextmanager
from functools import wraps
import hashlib
import json

from broader_vids.exploration.domains import node_id, nodes, normalized, put_node
from broader_vids.storage import utc_now


MEMORY_ID = hashlib.sha256(b"broader-vids-exploration-memory-v1").hexdigest()[:32]
MAX_NODES = 1024
COOLDOWN = 3
PLATFORMS = ("youtube", "bilibili")
MAX_COUNTER = 1_000_000_000


def empty_summary():
    return {
        "version": 2,
        "sequence": 0,
        "nodes": [],
        "usage": {},
        "platform_usage": {p: {} for p in PLATFORMS},
        "root_usage": {p: {} for p in PLATFORMS},
    }


def _empty_memory():
    value = empty_summary()
    value.pop("nodes")
    value["rounds"] = {}
    return _ensure_memory(value)


def memory(store):
    row = store.db.execute("SELECT state FROM runs WHERE id=?", (MEMORY_ID,)).fetchone()
    return _ensure_memory(json.loads(row[0])) if row else _empty_memory()


def _ensure_memory(value):
    value.setdefault("version", 1)
    value.setdefault("sequence", 0)
    value.setdefault("usage", {})
    value.setdefault("rounds", {})
    value.setdefault("platform_usage", {})
    value.setdefault("root_usage", {})
    value.setdefault("platform_sequences", {})
    value.setdefault("platform_imports", {})
    for platform in PLATFORMS:
        value["platform_usage"].setdefault(platform, {})
        value["root_usage"].setdefault(platform, {})
        if platform not in value["platform_sequences"]:
            value["platform_sequences"][platform] = max(
                (
                    max(cell.get("last_used", 0), cell.get("last_recommended", 0))
                    for cell in value["platform_usage"][platform].values()
                ),
                default=0,
            )
        for group in value["rounds"].values():
            group.setdefault("platform_numbers", {})
    return value


def _compact_round_baselines(store, value):
    for owner, group in value["rounds"].items():
        if "novelty_baseline" not in group:
            continue
        expected = group.get("expected_platforms")
        if expected is None:
            try:
                run = store.get(owner)
            except ValueError:
                run = None
            if run:
                if run["settings"].get("kind") == "recommendation_batch":
                    expected = list(PLATFORMS)
                elif run["platform"] in PLATFORMS:
                    expected = [run["platform"]]
                if expected is not None:
                    group["expected_platforms"] = list(expected)
        published = set(group.get("published", []))
        if expected is not None and set(expected) <= published:
            group.pop("novelty_baseline", None)
        elif expected is None and set(PLATFORMS) <= published:
            group.pop("novelty_baseline", None)


def _prune_root_usage(store, value):
    current = nodes(store)
    roots = {
        normalized(domain)
        for node in current.values()
        for domain in [node["label"], *node["domains"]]
    }
    from broader_vids.preferences import get_profile

    profile = get_profile(store)
    interests = (
        {normalized(name) for name in profile["interests"]} if profile else set()
    )
    for platform in PLATFORMS:
        usage = value["root_usage"][platform]
        retained = [key for key in usage if key in roots]
        retained.sort(
            key=lambda key: (
                key in interests,
                usage[key].get("last_used", 0),
                usage[key].get("searches", 0),
                key,
            ),
            reverse=True,
        )
        value["root_usage"][platform] = {
            key: usage[key] for key in retained[:MAX_NODES]
        }


def save_memory(store, value):
    _compact_round_baselines(store, value)
    if any(len(value["root_usage"][platform]) > MAX_NODES for platform in PLATFORMS):
        _prune_root_usage(store, value)
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
def platform_number(store, owner, platform):
    if platform not in PLATFORMS:
        raise ValueError("Unsupported exploration platform.")
    round_number(store, owner)
    value = memory(store)
    group = value["rounds"][owner]
    numbers = group.setdefault("platform_numbers", {})
    if platform not in numbers:
        value["platform_sequences"][platform] += 1
        numbers[platform] = value["platform_sequences"][platform]
        save_memory(store, value)
    return numbers[platform]


@atomic_update
def reserve(store, owner, platform, identifiers, *, relaxations=()):
    number = round_number(store, owner)
    value = memory(store)
    value["rounds"][owner]["platforms"][platform] = list(identifiers)
    value["rounds"][owner].setdefault("relaxations", {})[platform] = list(relaxations)
    save_memory(store, value)
    return number


@atomic_update
def allocation_coverage(store, platform, roots):
    if platform not in PLATFORMS:
        raise ValueError("Unsupported exploration platform.")
    value = memory(store)
    usage = value["root_usage"][platform]
    roots = list(dict.fromkeys(roots))
    existing = [usage[root] for root in roots if root in usage]
    baseline = {
        "searches": min((cell["searches"] for cell in existing), default=0),
        "last_used": min((cell["last_used"] for cell in existing), default=0),
    }
    changed = False
    for root in roots:
        if root not in usage:
            usage[root] = dict(baseline)
            changed = True
    if changed:
        save_memory(store, value)
    return {root: dict(usage.get(root, baseline)) for root in roots}


@atomic_update
def record_search(
    store,
    owner,
    identifier,
    saved_round=None,
    *,
    platform=None,
    domain=None,
    task_id=None,
    source_run=None,
):
    if (
        source_run is not None
        and store.db.execute(
            "SELECT 1 FROM domain_observations WHERE run_id=? AND node_id=?",
            (source_run, identifier),
        ).fetchone()
    ):
        return
    if platform is not None:
        if platform not in PLATFORMS:
            raise ValueError("Unsupported exploration platform.")
        platform_ordinal = platform_number(store, owner, platform)
    value = memory(store)
    number = value["rounds"].get(owner, {}).get("number") or saved_round
    if number is None:
        number = round_number(store, owner)
        value = memory(store)
    cell = value["usage"].setdefault(
        identifier, {"searches": 0, "last_used": 0, "last_recommended": 0}
    )
    should_count = True
    if platform is not None:
        group = value["rounds"].setdefault(
            owner, {"number": number, "platforms": {}, "published": []}
        )
        accounted = group.setdefault("accounted", [])
        key = f"{platform}:{task_id if task_id is not None else identifier}"
        should_count = key not in accounted
        if should_count:
            accounted.append(key)
            del accounted[:-256]
            branch = value["platform_usage"][platform].setdefault(
                identifier, {"searches": 0, "last_used": 0, "last_recommended": 0}
            )
            branch["searches"] = min(MAX_COUNTER, branch["searches"] + 1)
            branch["last_used"] = max(branch["last_used"], platform_ordinal)
            if domain:
                root = normalized(domain)
                root_cell = value["root_usage"][platform].setdefault(
                    root, {"searches": 0, "last_used": 0}
                )
                root_cell["searches"] = min(MAX_COUNTER, root_cell["searches"] + 1)
                root_cell["last_used"] = max(
                    root_cell["last_used"], number, value["sequence"]
                )
    if should_count:
        cell.update(
            searches=min(MAX_COUNTER, cell["searches"] + 1),
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
    branch_mode = plan.get("rotation_version") == 2 and run.get("platform") in PLATFORMS
    platform_ordinal = (
        platform_number(store, owner, run["platform"]) if branch_mode else None
    )
    value = memory(store)
    number = max(plan.get("coverage_round", value["sequence"]), value["sequence"])
    group = value["rounds"].get(owner)
    known = nodes(store)
    for item in run["state"].get("selected", []):
        for origin in item.get("origins", []):
            identifier = origin.get("node_id")
            if identifier in known:
                counters = (
                    value["platform_usage"][run["platform"]]
                    if branch_mode
                    else value["usage"]
                )
                cell = counters.setdefault(
                    identifier, {"searches": 0, "last_used": 0, "last_recommended": 0}
                )
                cell["last_recommended"] = max(
                    cell["last_recommended"],
                    platform_ordinal if branch_mode else number,
                )
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
    live_roots = {
        normalized(domain)
        for key in chosen
        if key in current
        for domain in [current[key]["label"], *current[key]["domains"]]
    }
    summary = {
        "version": 2,
        "sequence": value["sequence"],
        "nodes": [
            {name: current[key][name] for name in fields}
            for key in sorted(chosen)
            if key in current
        ],
        "usage": {key: cell for key, cell in value["usage"].items() if key in chosen},
        "platform_usage": {
            platform: {
                key: {
                    **cell,
                    **{
                        name: value["sequence"]
                        - (value["platform_sequences"][platform] - cell[name])
                        if cell[name]
                        else 0
                        for name in ("last_used", "last_recommended")
                    },
                }
                for key, cell in value["platform_usage"][platform].items()
                if key in chosen
            }
            for platform in PLATFORMS
        },
        "root_usage": {
            platform: {
                key: value["root_usage"][platform][key]
                for key in sorted(
                    (key for key in value["root_usage"][platform] if key in live_roots),
                    key=lambda key: (
                        value["root_usage"][platform][key]["last_used"],
                        value["root_usage"][platform][key]["searches"],
                        key,
                    ),
                    reverse=True,
                )[:MAX_NODES]
            }
            for platform in PLATFORMS
        },
    }
    validate_summary(summary)
    return summary


def validate_summary(summary):
    if (
        not isinstance(summary, dict)
        or summary.get("version") not in (1, 2)
        or set(summary)
        != (
            {"version", "sequence", "nodes", "usage"}
            if summary.get("version") == 1
            else {
                "version",
                "sequence",
                "nodes",
                "usage",
                "platform_usage",
                "root_usage",
            }
        )
        or type(summary["version"]) is not int
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
    if summary["version"] == 2:
        summary_roots = {
            normalized(domain)
            for node in summary["nodes"]
            for domain in [node["label"], *node["domains"]]
        }
        for field, cell_fields in (
            ("platform_usage", {"searches", "last_used", "last_recommended"}),
            ("root_usage", {"searches", "last_used"}),
        ):
            branches = summary[field]
            if not isinstance(branches, dict) or set(branches) != set(PLATFORMS):
                raise ValueError("Invalid platform exploration coverage.")
            for platform, entries in branches.items():
                if not isinstance(entries, dict) or len(entries) > MAX_NODES:
                    raise ValueError(
                        "Exploration coverage exceeds its supported limits."
                    )
                for key, cell in entries.items():
                    canonical = (
                        key in current
                        if field == "platform_usage"
                        else isinstance(key, str)
                        and key == normalized(key)
                        and label(key)
                        and key in summary_roots
                    )
                    if (
                        not canonical
                        or not isinstance(cell, dict)
                        or set(cell) != cell_fields
                        or not all(integer(n) for n in cell.values())
                        or max(
                            cell.get("last_used", 0), cell.get("last_recommended", 0)
                        )
                        > summary["sequence"]
                    ):
                        raise ValueError(
                            "Invalid platform exploration coverage counters."
                        )


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
    for entries in local["root_usage"].values():
        for cell in entries.values():
            if cell["last_used"]:
                cell["last_used"] += shift
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
    platform_offsets = {}
    if summary["version"] == 2:
        platform_imports = local["platform_imports"]
        for platform in PLATFORMS:
            previous_clock = local["platform_sequences"][platform]
            new_clock = max(previous_clock, summary["sequence"])
            clock_shift = new_clock - previous_clock
            if clock_shift:
                for cell in local["platform_usage"][platform].values():
                    for name in ("last_used", "last_recommended"):
                        if cell[name]:
                            cell[name] += clock_shift
                for group in local["rounds"].values():
                    if group.get("platform_numbers", {}).get(platform):
                        group["platform_numbers"][platform] += clock_shift
                for offsets in platform_imports.values():
                    if platform in offsets:
                        offsets[platform] += clock_shift
            local["platform_sequences"][platform] = new_clock
            platform_offsets[platform] = platform_imports.get(checksum, {}).get(
                platform, new_clock - summary["sequence"]
            )
        platform_imports[checksum] = platform_offsets
        local["platform_imports"] = dict(list(platform_imports.items())[-32:])
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
    if summary["version"] == 2:
        for field, names in (
            ("platform_usage", ("searches", "last_used", "last_recommended")),
            ("root_usage", ("searches", "last_used")),
        ):
            for platform, entries in summary[field].items():
                for key, cell in entries.items():
                    target = local[field][platform].setdefault(
                        key, {name: 0 for name in names}
                    )
                    for name in names:
                        restored = cell[name]
                        if name != "searches" and restored:
                            age_offset = (
                                platform_offsets[platform]
                                if field == "platform_usage"
                                else offset
                            )
                            restored += age_offset
                        target[name] = max(target[name], restored)
    from broader_vids.preferences import get_profile

    profile = get_profile(store)
    if profile:
        normalized_interests = sorted(normalized(name) for name in profile["interests"])
        local["root_profiles"] = {
            platform: list(normalized_interests) for platform in PLATFORMS
        }
    _prune_root_usage(store, local)
    save_memory(store, local)
