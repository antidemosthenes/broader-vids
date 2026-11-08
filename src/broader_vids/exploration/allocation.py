from collections import Counter
from functools import lru_cache
import random

from broader_vids.exploration.coverage import (
    COOLDOWN,
    allocation_coverage,
    memory,
    reserve,
    round_number,
    platform_number,
    save_memory,
)
from broader_vids.exploration.domains import nodes, normalized, node_id, is_related


@lru_cache(maxsize=64)
def _interest_ids(interests):
    return {node_id(label): normalized(label) for label in interests}


def domain_keys(node, profile):
    family = {node["id"], *node["ancestors"]}
    interests = _interest_ids(tuple(profile["interests"]))
    matches = {interests[key] for key in family & interests.keys()}
    return matches or {normalized(d) for d in node["domains"]}


def available_leaves(current, profile):
    parents = {
        p for n in current.values() if n["status"] == "active" for p in n["parents"]
    }
    retained = {
        ancestor
        for interest in profile["interests"]
        for ancestor in [
            node_id(interest),
            *current.get(node_id(interest), {}).get("ancestors", []),
        ]
    }
    retired = {
        n["id"]
        for n in current.values()
        if n.get("origin") == "onboarding"
        and not is_related(n, profile)
        and n["id"] not in retained
    }
    return [
        n
        for n in current.values()
        if n["status"] == "active"
        and n["id"] not in parents
        and not ({n["id"], *n["ancestors"]} & retired)
    ]


def allocate(store, state, owner, platform):
    current = nodes(store)
    number = round_number(store, owner)
    state["platform_round"] = platform_number(store, owner, platform)
    value = memory(store)
    group = value["rounds"][owner]
    baseline = group.setdefault("novelty_baseline", sorted(current))
    state["novelty_baseline"] = list(baseline)
    cached = state.get(
        "domain_allocations", group.get("root_allocations", {}).get(platform)
    )
    if cached is not None:
        state["domain_allocations"] = list(cached)
        state["coverage_round"] = number
        if not state.get("selected"):
            state["selected"] = [
                current[node_id(root)]
                for root in dict.fromkeys(cached)
                if node_id(root) in current
            ]
        save_memory(store, value)
        return
    leaves = available_leaves(current, state["profile"])
    roots = sorted({d for n in leaves for d in domain_keys(n, state["profile"])})
    interests = {normalized(d) for d in state["profile"]["interests"]}
    familiar = [r for r in roots if r in interests]
    unrelated = [r for r in roots if r not in interests]
    previous = value.setdefault("root_profiles", {}).get(platform)
    counters = value["root_usage"][platform]
    if previous is not None:
        retained = [
            counters[r]["searches"] for r in familiar if r in previous and r in counters
        ]
        minimum = min(retained, default=0)
        for root in interests - set(previous):
            counters[root] = {"searches": minimum, "last_used": 0}
    value["root_profiles"][platform] = sorted(interests)
    save_memory(store, value)
    counts = {}
    for pool in (familiar, unrelated):
        counts.update(allocation_coverage(store, platform, pool))
    rng = random.Random(state["seed"])
    planned, allocations = Counter(), []
    for _ in range(12 if roots else 0):
        if sum(r in interests for r in allocations) < 6 and familiar:
            pool = familiar
        elif familiar and unrelated:
            pool = rng.choices(
                [familiar, unrelated], weights=[3 * len(familiar), len(unrelated)], k=1
            )[0]
        else:
            pool = familiar or unrelated
        minimum = min(counts[r]["searches"] + planned[r] for r in pool)
        root = rng.choice(
            [r for r in pool if counts[r]["searches"] + planned[r] == minimum]
        )
        allocations.append(root)
        planned[root] += 1
    value = memory(store)
    value["rounds"][owner].setdefault("root_allocations", {})[platform] = list(
        allocations
    )
    save_memory(store, value)
    state["domain_allocations"] = allocations
    state["coverage_round"] = number
    representatives = []
    for root in dict.fromkeys(allocations):
        key = node_id(root)
        candidates = [n for n in leaves if root in domain_keys(n, state["profile"])]
        representatives.append(
            current.get(key) or min(candidates, key=lambda n: (n["depth"], n["id"]))
        )
    state["selected"] = representatives


def expansion_parents(store, state):
    current = nodes(store)
    leaves = available_leaves(current, state["profile"])
    requested = Counter(state["domain_allocations"])
    usage = memory(store).get("platform_usage", {}).get(state["platform"], {})
    result = {}
    for representative in state["selected"]:
        root = normalized(representative["label"])
        if root not in requested:
            root = next(
                (
                    normalized(d)
                    for d in representative["domains"]
                    if normalized(d) in requested
                ),
                root,
            )
        available = [n for n in leaves if root in domain_keys(n, state["profile"])]
        if representative["depth"] < 16 and (
            len(available) < requested[root]
            or (
                representative["origin"] == "onboarding"
                and len(available) == 1
                and available[0]["id"] == representative["id"]
            )
            or (
                len(available) == 1
                and usage.get(available[0]["id"], {}).get("searches", 0) >= 3
            )
        ):
            result[representative["id"]] = representative
    return result


def select_branches(store, state, owner, platform):
    current = nodes(store)
    value = memory(store)
    group = value["rounds"][owner]
    if platform in group["platforms"]:
        state.update(
            selected=[current[key] for key in group["platforms"][platform]],
            rotation_ready=True,
            rotation_relaxations=group.get("relaxations", {}).get(platform, []),
            allocation_domains=group.get("allocation_domains", {}).get(platform, {}),
        )
        return
    leaves = available_leaves(current, state["profile"])
    usage = value.get("platform_usage", {}).get(platform, {})
    roots = value.get("root_usage", {}).get(platform, {})
    number = state.get("platform_round", state["coverage_round"])
    rng = random.Random(state["seed"] ^ 0x5F3759DF)
    chosen, assignments, relaxations = [], {}, []
    planned = Counter()
    for domain in state["domain_allocations"]:
        pool = [n for n in leaves if domain in domain_keys(n, state["profile"])]
        if not pool:
            alternatives = sorted(
                {d for n in leaves for d in domain_keys(n, state["profile"])}
            )
            if not alternatives:
                relaxations.append(
                    {
                        "domain": domain,
                        "capacity": True,
                        "unfilled_slots": len(state["domain_allocations"])
                        - len(chosen),
                    }
                )
                break
            interests = {normalized(d) for d in state["profile"]["interests"]}
            same_lane = [
                d for d in alternatives if (d in interests) == (domain in interests)
            ]
            alternatives = same_lane or alternatives
            minimum = min(
                roots.get(d, {}).get("searches", 0) + planned[d] for d in alternatives
            )
            replacement = rng.choice(
                [
                    d
                    for d in alternatives
                    if roots.get(d, {}).get("searches", 0) + planned[d] == minimum
                ]
            )
            relaxations.append(
                {"domain": domain, "replacement_domain": replacement, "capacity": True}
            )
            domain = replacement
            pool = [n for n in leaves if domain in domain_keys(n, state["profile"])]
        fresh = [
            n
            for n in pool
            if not (
                usage.get(n["id"], {}).get("last_recommended", 0)
                and number - usage[n["id"]]["last_recommended"] <= COOLDOWN
            )
        ]
        if fresh:
            pool = fresh
        else:
            relaxations.append({"domain": domain, "cooldown": True})
        minimum = min(usage.get(n["id"], {}).get("searches", 0) for n in pool)
        node = rng.choice(
            sorted(
                [
                    n
                    for n in pool
                    if usage.get(n["id"], {}).get("searches", 0) == minimum
                ],
                key=lambda n: n["id"],
            )
        )
        chosen.append(node)
        assignments[node["id"]] = domain
        planned[domain] += 1
        leaves = [n for n in leaves if n["id"] != node["id"]]
    reserve(store, owner, platform, [n["id"] for n in chosen], relaxations=relaxations)
    value = memory(store)
    value["rounds"][owner].setdefault("allocation_domains", {})[platform] = assignments
    save_memory(store, value)
    state.update(
        selected=chosen,
        allocation_domains=assignments,
        rotation_relaxations=relaxations,
        rotation_ready=True,
    )
