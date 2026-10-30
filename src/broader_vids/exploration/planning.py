import hashlib
import json
import random
import subprocess
import time

from broader_vids.discovery.languages import QueryLanguageError
from broader_vids.evidence.assessment import run_codex
from broader_vids.evidence.media import EvidencePending, public_error
from broader_vids.exploration.domains import (
    is_related,
    nodes,
    put_node,
    seed_interests,
    select_existing,
)
from broader_vids.exploration.proposals import (
    proposal_prompt,
    proposal_schema,
    validate_response,
)
from broader_vids.http import BudgetExhausted
from broader_vids.preferences import get_profile
from broader_vids.storage import utc_now


POLICY_ID = hashlib.sha256(b"broader-vids-hierarchical-exploration-v1").hexdigest()


def new_plan(store, seed=None):
    profile = seed_interests(store)
    seed = random.SystemRandom().getrandbits(63) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Exploration seed must be an integer from zero to 2^63-1.")
    selected = select_existing(store, profile, seed)
    recent = [
        {"node_id": row["node_id"], **json.loads(row["payload"])}
        for row in store.db.execute(
            "SELECT node_id,payload FROM domain_observations ORDER BY created_at DESC,rowid DESC LIMIT 24"
        )
    ]
    return {
        "policy_version": 1,
        "seed": seed,
        "profile": profile,
        "selected": selected,
        "existing_target": 12,
        "new_target": 5,
        "new_nodes": [],
        "queries": {},
        "recent": recent,
        "attempts": [],
        "rejections": [],
        "ready": False,
    }


def prepare_plan(
    store, run, save, remaining, model_runner=run_codex, *, usage_owner=None
):
    state = run["state"]["exploration"]
    if state["profile"]["revision"] != get_profile(store)["revision"]:
        raise EvidencePending("Interests changed; create a new exploration round.")
    if state["ready"]:
        return
    root = store.root / "discovery" / run["id"] / "planning"
    root.mkdir(parents=True, exist_ok=True)
    current = nodes(store)
    allowance = run["settings"]["max_planning_calls"]
    while len(state["attempts"]) < allowance and (
        len(state["new_nodes"]) < 5
        or {n["id"] for n in state["selected"]} - state["queries"].keys()
    ):
        if remaining() <= 0:
            raise BudgetExhausted("Exploration time budget exhausted.")
        number = len(state["attempts"]) + 1
        recent_empty = sum(
            r.get("new_topic") and r.get("matched", 0) == 0 for r in state["recent"]
        )
        refine = (
            state["attempts"][-1]["allow_refinements"]
            if state["attempts"] and state["attempts"][-1].get("language_error")
            else number > 1 or recent_empty >= 5
        )
        prompt = proposal_prompt(
            current,
            state["selected"],
            state["recent"],
            state["profile"],
            5 - len(state["new_nodes"]),
            refine,
            language_errors=[
                a["reason"] for a in state["attempts"] if a.get("language_error")
            ],
        )
        if len(prompt.encode("utf-8")) > 192 * 1024:
            raise EvidencePending(
                "The domain-map planning context exceeds 192 KiB; review the map before continuing."
            )
        schema = root / "schema.json"
        schema.write_text(json.dumps(proposal_schema()), encoding="utf-8")
        prompt_path = root / f"attempt-{number}-prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        attempt = {
            "started_at": utc_now(),
            "status": "started",
            "allow_refinements": refine,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "input_characters": len(prompt),
            "token_usage": None,
        }
        state["attempts"].append(attempt)
        save()
        started = time.monotonic()
        try:
            from broader_vids.token_usage import tracked_call

            response, usage = tracked_call(
                store,
                usage_owner or run["id"],
                run["id"],
                "exploration",
                number,
                model_runner,
                prompt,
                schema,
                root / f"attempt-{number}.json",
                [],
                min(120, remaining()),
            )
            attempt["token_usage"] = usage
            if get_profile(store)["revision"] != state["profile"]["revision"]:
                raise EvidencePending(
                    "Interests changed during topic generation; create a new round."
                )
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                current = nodes(store)
                updates, additions, rejected = validate_response(
                    response,
                    current,
                    state["selected"],
                    refine,
                    5 - len(state["new_nodes"]),
                )
                state["queries"].update(
                    {
                        key: {"queries": pair, "anchors": anchors}
                        for key, (pair, anchors) in updates.items()
                    }
                )
                state["rejections"].extend(rejected)
                for node in additions:
                    put_node(store, node)
                    current[node["id"]] = node
                    state["new_nodes"].append(node)
                attempt["status"] = "completed"
                save()
        except (
            ValueError,
            OSError,
            EvidencePending,
            subprocess.TimeoutExpired,
        ) as error:
            attempt.update(status="failed", reason=public_error(error))
            if isinstance(error, QueryLanguageError):
                attempt["language_error"] = True
        finally:
            attempt["seconds"] = round(time.monotonic() - started, 3)
            save()
        if attempt["status"] == "failed":
            if (
                attempt.get("language_error")
                and len(state["attempts"]) < allowance
                and remaining() > 0
            ):
                continue
            raise EvidencePending(
                "Exploration generation failed; its attempt is recorded. Continue within the original planning allowance."
            )
    missing = {n["id"] for n in state["selected"]} - state["queries"].keys()
    if missing:
        raise BudgetExhausted(
            "Planning allowance exhausted before queries for all selected concepts were validated."
        )
    tasks = []
    for node in [*state["selected"], *state["new_nodes"]]:
        query = state["queries"].get(node["id"], node)
        tasks.append(
            {
                "family_id": node["id"],
                "node_id": node["id"],
                "topic_label": node["label"],
                "new_topic": node["id"] in {n["id"] for n in state["new_nodes"]},
                "intent": "intersection"
                if len(node["parents"]) > 1
                else "deep"
                if is_related(node, state["profile"])
                else "horizon",
                "domains": node["domains"],
                "topic_ancestors": node["ancestors"],
                "topic_depth": node["depth"],
                "purpose": node.get(
                    "novelty_reason",
                    "Explore an existing concept with a concrete query.",
                ),
                "anchors": [[a] for a in query["anchors"]],
                "query": query["queries"][run["platform"]],
            }
        )
    existing = [t for t in tasks if not t["new_topic"]]
    novel = [t for t in tasks if t["new_topic"]]
    interleaved = []
    while existing or novel:
        for lane in (novel, existing, existing, existing):
            if lane:
                interleaved.append(lane.pop(0))
    run["state"]["plan"] = interleaved
    state.update(
        ready=True,
        existing_shortfall=12 - len(state["selected"]),
        new_shortfall=5 - len(state["new_nodes"]),
        related_queries=sum(
            bool(set(t["domains"]) & set(state["profile"]["interests"])) for t in tasks
        ),
        unrelated_queries=sum(
            not bool(set(t["domains"]) & set(state["profile"]["interests"]))
            for t in tasks
        ),
    )
    run["state"]["phase"] = "bootstrap"
    save()


def observe_query(store, run, task, items, receipt):
    if not task.get("node_id"):
        return
    from broader_vids.discovery.queries import anchor_hits

    request = store.db.execute(
        "SELECT outcome,detail FROM requests WHERE id=? AND run_id=?",
        ((receipt or {}).get("request_id"), run["id"]),
    ).fetchone()
    if (
        not request
        or request["outcome"] != "ok"
        or json.loads(request["detail"]) != receipt
        or receipt.get("transport") != "anonymous_http"
    ):
        raise ValueError("Topic activation requires a successful source receipt.")
    matches = [i["canonical_id"] for i in items[:40] if anchor_hits(i, task)]
    observation = {
        "platform": run["platform"],
        "query": task["query"],
        "new_topic": task["new_topic"],
        "returned": len(items),
        "matched": len(matches),
        "matched_candidates": matches,
        "receipt": receipt,
        "semantic_relevance_verified": False,
    }
    node = nodes(store)[task["node_id"]]
    if matches:
        node.update(status="active", activated_at=node.get("activated_at", utc_now()))
    elif node["status"] != "active":
        node["status"] = "unconfirmed"
    node.setdefault("queries", {})[run["platform"]] = task["query"]
    with store.db:
        put_node(store, node)
        store.db.execute(
            "INSERT OR REPLACE INTO domain_observations VALUES(?,?,?,?)",
            (
                run["id"],
                node["id"],
                utc_now(),
                json.dumps(observation, ensure_ascii=False),
            ),
        )
        store.save(run)
