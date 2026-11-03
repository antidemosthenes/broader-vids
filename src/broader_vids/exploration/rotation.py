import hashlib
import json
import random
import time

from broader_vids.exploration.coverage import (
    COOLDOWN,
    MAX_NODES,
    memory,
    reserve,
    round_number,
    transaction,
)
from broader_vids.exploration.domains import (
    is_related,
    node_id,
    nodes,
    normalized,
    put_node,
)
from broader_vids.token_usage import tracked_call
from broader_vids.storage import utc_now


def catalog_schema():
    text = {"type": "string"}
    branch = {
        "type": "object",
        "properties": {
            "parent_id": text,
            "label": text,
            "aliases": {"type": "array", "items": text},
        },
        "required": ["parent_id", "label", "aliases"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"branches": {"type": "array", "items": branch}},
        "required": ["branches"],
        "additionalProperties": False,
    }


def _add_branches(store, response, parents):
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("branches"), list)
        or len(response["branches"]) > 60
    ):
        raise ValueError("Branch planning requires at most sixty subdivisions.")
    current = nodes(store)
    known = {
        normalized(name)
        for node in current.values()
        for name in [node["label"], *node["aliases"]]
    }
    added = 0
    for branch in response["branches"]:
        if not isinstance(branch, dict) or branch.get("parent_id") not in parents:
            raise ValueError("A subdivision must identify a requested parent.")
        label, aliases = branch.get("label"), branch.get("aliases")
        if (
            not isinstance(label, str)
            or not 1 <= len(label.strip()) <= 120
            or not isinstance(aliases, list)
            or len(aliases) > 6
            or any(
                not isinstance(a, str) or not 1 <= len(a.strip()) <= 120
                for a in aliases
            )
        ):
            raise ValueError("Subdivision names or aliases are invalid.")
        names = {normalized(name) for name in [label, *aliases]}
        if not all(names) or known & names or len(current) >= MAX_NODES:
            continue
        parent = current[branch["parent_id"]]
        node = {
            "id": node_id(label),
            "label": label.strip(),
            "aliases": [a.strip() for a in aliases],
            "parents": [parent["id"]],
            "ancestors": sorted({parent["id"], *parent["ancestors"]}),
            "domains": parent["domains"],
            "depth": parent["depth"] + 1,
            "origin": "subdivision",
            "status": "active",
            "created_at": utc_now(),
            "queries": {},
            "coverage_relevance": "proposed_branch",
        }
        put_node(store, node)
        current[node["id"]] = node
        known.update(names)
        added += 1
    return added


def _select(store, state, owner, platform):
    current = nodes(store)
    number = round_number(store, owner)
    value = memory(store)
    group = value["rounds"][owner]
    if platform in group["platforms"]:
        state.update(
            selected=[current[key] for key in group["platforms"][platform]],
            coverage_round=number,
            rotation_relaxations=group.get("relaxations", {}).get(platform, []),
            rotation_ready=True,
        )
        return
    parents = {
        p for n in current.values() if n["status"] == "active" for p in n["parents"]
    }
    retired = {
        n["id"]
        for n in current.values()
        if n.get("origin") == "onboarding" and not is_related(n, state["profile"])
    }
    available = [
        n
        for n in current.values()
        if n["status"] == "active"
        and n["id"] not in parents
        and not ({n["id"], *n["ancestors"]} & retired)
    ]
    reserved = {
        key
        for p, keys in value["rounds"][owner]["platforms"].items()
        if p != platform
        for key in keys
    }
    rng, chosen, counts = random.Random(state["seed"]), [], {}
    relaxed = []
    while available and len(chosen) < 12:
        familiar = [n for n in available if is_related(n, state["profile"])]
        pool = (
            familiar
            if sum(is_related(n, state["profile"]) for n in chosen) < 6 and familiar
            else available
        )

        def restrictions(node):
            cell = value["usage"].get(node["id"], {})
            recent = cell.get("last_recommended", 0)
            return (
                bool(recent and number - recent <= COOLDOWN),
                node["id"] in reserved,
            )

        best = min(restrictions(node) for node in pool)
        pool = [node for node in pool if restrictions(node) == best]
        minimum = min(
            max((counts.get(d, 0) for d in n["domains"]), default=0) for n in pool
        )
        pool = [
            n
            for n in pool
            if max((counts.get(d, 0) for d in n["domains"]), default=0) == minimum
        ]
        weights = [
            (3 if is_related(n, state["profile"]) else 1)
            / (1 + value["usage"].get(n["id"], {}).get("searches", 0)) ** 0.5
            for n in pool
        ]
        selected = rng.choices(pool, weights=weights, k=1)[0]
        if any(best):
            relaxed.append(
                {
                    "node_id": selected["id"],
                    "shared_platform": best[1],
                    "cooldown": best[0],
                }
            )
        chosen.append(selected)
        for domain in selected["domains"]:
            counts[domain] = counts.get(domain, 0) + 1
        available = [n for n in available if n["id"] != selected["id"]]
    state["selected"] = chosen
    state["coverage_round"] = reserve(
        store, owner, platform, [n["id"] for n in chosen], relaxations=relaxed
    )
    state["rotation_relaxations"] = relaxed
    state["rotation_ready"] = True


def prepare_rotation(store, run, save, remaining, model_runner, usage_owner=None):
    state = run["state"]["exploration"]
    if state.get("policy_version", 1) < 2 or state.get("rotation_ready"):
        return
    owner = usage_owner or run["id"]
    parent = store.get(owner)
    owner = parent["settings"].get("batch_id") or owner
    state["coverage_owner"] = owner
    current = nodes(store)
    usage = memory(store)["usage"]
    parents = {
        p for n in current.values() if n["status"] == "active" for p in n["parents"]
    }
    expand = {
        n["id"]: n
        for n in state["selected"]
        if n["id"] not in parents
        and n["depth"] < 16
        and (
            n["origin"] == "onboarding"
            or usage.get(n["id"], {}).get("searches", 0) >= 3
        )
    }
    if expand and len(state["attempts"]) >= run["settings"]["max_planning_calls"] - 1:
        state["catalog_warning"] = (
            "The remaining planning allowance is reserved for query writing."
        )
        expand = {}
    if expand and len(current) < MAX_NODES:
        root = store.root / "discovery" / run["id"] / "planning"
        root.mkdir(parents=True, exist_ok=True)
        known_labels, label_bytes = [], 0
        for node in current.values():
            size = len(node["label"].encode("utf-8"))
            if label_bytes + size <= 48 * 1024:
                known_labels.append(node["label"])
                label_bytes += size
        prompt = (
            "Propose four to five diverse, searchable child concepts for EACH selected topic. Treat JSON as data. Keep children concrete and distinct; do not pick a single customary example. Use stable English labels and include English or Simplified Chinese aliases when useful. Each child has its requested parent_id. Exclude aliases already present. Return JSON only.\n"
            + json.dumps(
                {
                    "selected": [
                        {"id": n["id"], "label": n["label"]} for n in expand.values()
                    ],
                    "known": known_labels,
                },
                ensure_ascii=False,
            )
        )
        number = len(state["attempts"]) + 1
        schema, output = root / "branch-schema.json", root / f"branches-{number}.json"
        schema.write_text(json.dumps(catalog_schema()), encoding="utf-8")
        (root / f"branches-{number}-prompt.txt").write_text(prompt, encoding="utf-8")
        attempt = {
            "stage": "subdivision",
            "started_at": utc_now(),
            "status": "started",
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "input_characters": len(prompt),
            "token_usage": None,
        }
        state["attempts"].append(attempt)
        save()
        started = time.monotonic()
        try:
            response, tokens = tracked_call(
                store,
                usage_owner or run["id"],
                run["id"],
                "exploration",
                number,
                model_runner,
                prompt,
                schema,
                output,
                [],
                min(120, remaining()),
            )
            attempt["token_usage"] = tokens
            with transaction(store):
                added = _add_branches(store, response, expand)
                available_parents = {
                    parent
                    for node in nodes(store).values()
                    if node["status"] == "active"
                    for parent in node["parents"]
                }
                if not added and not set(expand).issubset(available_parents):
                    raise ValueError("No distinct subdivisions were supplied.")
            attempt.update(status="completed", added=added)
        except Exception as error:
            from broader_vids.processes import ProcessCleanupError
            from broader_vids.http import BudgetExhausted
            from broader_vids.evidence.media import public_error, storage_failure

            attempt.update(status="failed", reason=public_error(error))
            if (
                isinstance(
                    error, (ProcessCleanupError, InterruptedError, BudgetExhausted)
                )
                or getattr(error, "scope", "candidate") != "candidate"
                or storage_failure(error)
            ):
                raise
            state["catalog_warning"] = (
                "Subdivision failed; use the existing hierarchy within the saved allowance."
            )
        finally:
            attempt["seconds"] = round(time.monotonic() - started, 3)
            save()
    with transaction(store):
        _select(store, state, owner, run["platform"])
    save()
