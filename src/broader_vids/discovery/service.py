from collections import Counter
import html
import json

from broader_vids.discovery.sources import BilibiliDiscovery, YouTubeDiscovery
from broader_vids.http import AccessPaused, BudgetExhausted, PublicHttp
from broader_vids.discovery.queries import anchor_hits, load_bank, query_plan
from broader_vids.storage import utc_now
from broader_vids.evidence.media import EvidencePending
from broader_vids.exploration.planning import (
    POLICY_ID,
    new_plan,
    observe_query,
    prepare_plan,
)
from broader_vids.preferences import get_profile
from broader_vids.creators import policy_summary
from broader_vids.worker_context import (
    host_options,
    host_snapshot,
    worker_runner,
    validate_host,
)


def create_discovery(
    store,
    platform,
    bank_path=None,
    round_number=None,
    max_requests=100,
    max_seconds=600,
    creator_limit=4,
    creator_pages=2,
    *,
    curated=False,
    seed=None,
    max_planning_calls=2,
    creator_rules=None,
    assessment_model=None,
    reasoning_effort=None,
    planning_policy=2,
    host="codex",
):
    if platform not in {"youtube", "bilibili"}:
        raise ValueError("Unsupported discovery platform.")
    if not 1 <= max_requests <= 1000 or not 0 < max_seconds <= 3600:
        raise ValueError(
            "Discovery requires 1–1,000 requests and at most 3,600 seconds."
        )
    if not 1 <= creator_limit <= 10 or not 1 <= creator_pages <= 5:
        raise ValueError("Use 1–10 creators and 1–5 pages per creator.")
    if type(max_planning_calls) is not int or not 0 <= max_planning_calls <= 2:
        raise ValueError("Exploration permits zero to two planning calls per round.")
    if planning_policy != 2:
        raise ValueError("Unsupported exploration policy; start a new discovery round.")
    hierarchical = not curated and bank_path is None and get_profile(store) is not None
    if hierarchical:
        from broader_vids.worker_context import resolve_worker_settings

        assessment_model, reasoning_effort = resolve_worker_settings(
            assessment_model, reasoning_effort, **host_options(host)
        )
    bank = load_bank(bank_path)
    bank_hash = POLICY_ID if hierarchical else bank["sha256"]
    if round_number is None:
        previous = [
            r
            for r in store.list_runs()
            if r["platform"] == platform
            and r["settings"].get("kind") == "discovery"
            and r["settings"].get("bank_sha256") == bank_hash
        ]
        round_number = max((r["settings"]["round"] for r in previous), default=-1) + 1
    if type(round_number) is not int or round_number < 0:
        raise ValueError("Discovery round must be a nonnegative integer.")
    plan = [] if hierarchical else query_plan(bank, platform, round_number)
    settings = {
        "kind": "discovery",
        **host_snapshot(host),
        "assessment_model": assessment_model,
        "reasoning_effort": reasoning_effort,
        "discovery_schema": 2 if hierarchical else 1,
        "mode": "hierarchical" if hierarchical else "curated",
        "bank_sha256": bank_hash,
        "bank_version": 1 if hierarchical else bank["version"],
        "max_planning_calls": max_planning_calls,
        "round": round_number,
        "max_requests": max_requests,
        "max_seconds": max_seconds,
        "creator_limit": creator_limit,
        "creator_pages": creator_pages,
        "buffer_limit": 1000,
        "shortlist_target": 50,
        "creator_cap": 3,
        "creator_rules": creator_rules or {},
    }
    state = {
        "phase": "planning" if hierarchical else "bootstrap",
        "plan": plan,
        "index": 0,
        "creator_index": 0,
        "creator_tasks": [],
        "candidates": {},
        "creators": {},
        "shortlist": [],
        "pages": [],
        "client_version": None,
    }
    if hierarchical:
        state["exploration"] = new_plan(store, seed, policy_version=planning_policy)
    return store.create(platform, settings, state)


def page_context(source, task, receipt):
    return {
        "source": source,
        "family_id": task.get("family_id"),
        "query": task.get("query"),
        "intent": task.get("intent"),
        "domains": task.get("domains", []),
        "anchors": task.get("anchors", []),
        "creator_id": task.get("creator_id"),
        "receipt": receipt,
        **{
            key: task[key]
            for key in (
                "node_id",
                "topic_label",
                "new_topic",
                "topic_ancestors",
                "topic_depth",
            )
            if key in task
        },
    }


def candidate_origin(item, context, rank):
    return {**context, "rank": rank, "anchor_hits": anchor_hits(item, context)}


def merge_page(run, items, source, task, receipt, cursor=None):
    state = run["state"]
    origin_base = page_context(source, task, receipt)
    for rank, item in enumerate(items[:40], 1):
        identifier = item["canonical_id"]
        if (
            identifier not in state["candidates"]
            and len(state["candidates"]) >= run["settings"]["buffer_limit"]
        ):
            continue
        origin = candidate_origin(item, origin_base, rank)
        if identifier not in state["candidates"]:
            state["candidates"][identifier] = {
                **item,
                "first_seen": utc_now(),
                "origins": [],
            }
        record = state["candidates"][identifier]
        if origin not in record["origins"]:
            record["origins"].append(origin)
        creator = item.get("creator_id")
        if creator and item.get("creator_name"):
            entry = state["creators"].setdefault(
                creator,
                {
                    "id": creator,
                    "name": item["creator_name"],
                    "seed_query": task.get("query"),
                    "family_id": task.get("family_id"),
                    "domains": task.get("domains", []),
                    "observed_videos": [],
                    **{
                        key: task[key]
                        for key in (
                            "node_id",
                            "topic_label",
                            "new_topic",
                            "topic_ancestors",
                            "topic_depth",
                        )
                        if key in task
                    },
                },
            )
            if identifier not in entry["observed_videos"]:
                entry["observed_videos"].append(identifier)
    state["pages"].append(
        {
            **origin_base,
            "returned": len(items),
            "continuation": cursor,
            "retained_input": min(len(items), 40),
            "source_page_truncated": len(items) > 40,
            "unique_buffer": len(state["candidates"]),
            "observed_at": utc_now(),
        }
    )


def make_shortlist(run):
    state = run["state"]
    buckets = {}
    for identifier, item in state["candidates"].items():
        duration = item.get("duration_seconds")
        if run["platform"] == "youtube" and duration is not None and duration > 5400:
            continue
        for origin in item["origins"]:
            key = (
                origin["source"],
                origin.get("family_id") or origin.get("creator_id") or "home",
            )
            buckets.setdefault(key, []).append((identifier, origin))
    for bucket in buckets.values():
        bucket.sort(
            key=lambda pair: (-pair[1]["anchor_hits"], pair[1]["rank"], pair[0])
        )
    selected, creator_counts = {}, Counter()
    while len(selected) < run["settings"]["shortlist_target"]:
        progress = False
        for bucket in buckets.values():
            while bucket:
                identifier, origin = bucket.pop(0)
                creator = (
                    state["candidates"][identifier].get("creator_id") or identifier
                )
                if (
                    identifier in selected
                    or creator_counts[creator] >= run["settings"]["creator_cap"]
                ):
                    continue
                selected[identifier] = {
                    "canonical_id": identifier,
                    "selected_for": {
                        k: origin[k]
                        for k in ("source", "family_id", "intent", "creator_id")
                    },
                }
                creator_counts[creator] += 1
                progress = True
                break
            if len(selected) == run["settings"]["shortlist_target"]:
                break
        if not progress:
            break
    return list(selected.values())


def _creator_tasks(run):
    state = run["state"]
    by_family = {}
    for creator in state["creators"].values():
        if str(creator["id"]) in run["settings"].get("creator_rules", {}).get(
            "excluded_ids", []
        ):
            continue
        by_family.setdefault(creator.get("family_id") or "home", []).append(creator)
    result = []
    while len(result) < run["settings"]["creator_limit"] and any(by_family.values()):
        for group in by_family.values():
            if group and len(result) < run["settings"]["creator_limit"]:
                creator = group.pop(0)
                result.append(
                    {
                        "creator_id": creator["id"],
                        "name": creator["name"],
                        "family_id": creator.get("family_id"),
                        "domains": creator["domains"],
                        "query": creator["name"],
                        "cursor": None,
                        "page": 1,
                        **{
                            key: creator[key]
                            for key in (
                                "node_id",
                                "topic_label",
                                "new_topic",
                                "topic_ancestors",
                                "topic_depth",
                            )
                            if key in creator
                        },
                    }
                )
    return result


def execute_discovery(
    store,
    run_id,
    http_factory=PublicHttp,
    *,
    max_seconds=None,
    model_runner=None,
    max_new_planning_calls=None,
    cancel_event=None,
    usage_owner=None,
):
    initial = store.get(run_id)
    if initial["settings"].get("kind") != "discovery":
        raise ValueError("This command requires a discovery run.")
    _validate_discovery_policy(initial)
    with store.lease(run_id):
        run = store.get(run_id)
        _validate_discovery_policy(run)
        if run["status"] == "completed":
            return run
        if max_seconds is not None:
            run["settings"] = {
                **run["settings"],
                "max_seconds": min(
                    run["settings"]["max_seconds"], run["elapsed"] + max_seconds
                ),
            }
        if max_new_planning_calls is not None:
            run["settings"] = {
                **run["settings"],
                "max_planning_calls": min(
                    run["settings"].get("max_planning_calls", 0),
                    len(run["state"].get("exploration", {}).get("attempts", []))
                    + max_new_planning_calls,
                ),
            }
        http = http_factory(store, run, anonymous_session=True)
        http.cancelled = cancel_event
        provider = (
            YouTubeDiscovery(http)
            if run["platform"] == "youtube"
            else BilibiliDiscovery(http)
        )
        state = run["state"]

        def save(event=None):
            run["elapsed"] = http.elapsed()
            state["shortlist"] = make_shortlist(run)
            store.save(run, event)

        run.update(status="running", reason="")
        try:
            http.check()
            if (
                "exploration" in state
                and state["exploration"]["profile"]["revision"]
                != get_profile(store)["revision"]
            ):
                raise EvidencePending(
                    "Interests changed; create a new exploration round."
                )
            if state["phase"] == "planning":
                if model_runner is None:
                    model_runner = worker_runner(run["settings"])
                prepare_plan(
                    store,
                    run,
                    save,
                    lambda: run["settings"]["max_seconds"] - http.elapsed(),
                    usage_owner=usage_owner,
                    **({"model_runner": model_runner} if model_runner else {}),
                )
            if state["phase"] == "bootstrap":
                if run["platform"] == "youtube":
                    state["client_version"] = provider.bootstrap()
                else:
                    items, cursor = provider.home()
                    merge_page(run, items, "home", {}, http.last_receipt, cursor)
                state["phase"] = "queries"
                save(("discovery_bootstrap", {"platform": run["platform"]}))
            while state["index"] < len(state["plan"]):
                task = state["plan"][state["index"]]
                items, cursor = (
                    provider.search(task["query"], state["client_version"])
                    if run["platform"] == "youtube"
                    else provider.search(task["query"])
                )
                if (
                    state.get("exploration", {}).get("policy_version", 1) >= 2
                    and task.get("fallback_query")
                    and task["fallback_query"] != task["query"]
                    and not any(anchor_hits(item, task) for item in items[:10])
                ):
                    task["initial_query"] = task["query"]
                    task["query"] = task["fallback_query"]
                    save()
                    items, cursor = (
                        provider.search(task["query"], state["client_version"])
                        if run["platform"] == "youtube"
                        else provider.search(task["query"])
                    )
                    task["query_simplified"] = True
                merge_page(run, items, "search", task, http.last_receipt, cursor)
                state["index"] += 1
                observe_query(store, run, task, items, http.last_receipt)
                save(
                    (
                        "discovery_page",
                        {
                            "source": "search",
                            "family_id": task["family_id"],
                            "returned": len(items),
                        },
                    )
                )
            if state["phase"] == "queries":
                state["creator_tasks"] = _creator_tasks(run)
                state["phase"] = "creators"
                save()
            while state["creator_index"] < len(state["creator_tasks"]):
                task = state["creator_tasks"][state["creator_index"]]
                if run["platform"] == "youtube":
                    items, cursor = provider.creator(
                        task["creator_id"], state["client_version"], task["cursor"]
                    )
                    source = "creator_archive"
                else:
                    items, cursor = provider.creator(
                        task["creator_id"], task["name"], task["page"]
                    )
                    source = "creator_search"
                merge_page(run, items, source, task, http.last_receipt, cursor)
                task.update(cursor=cursor, page=task["page"] + 1)
                if (
                    not items
                    or not cursor
                    or task["page"] > run["settings"]["creator_pages"]
                ):
                    state["creator_index"] += 1
                save(
                    (
                        "discovery_page",
                        {
                            "source": source,
                            "creator_id": task["creator_id"],
                            "returned": len(items),
                        },
                    )
                )
            state["phase"] = "finished"
            state["shortlist"] = make_shortlist(run)
            checks = discovery_checks(store, run)
            run.update(
                status="completed" if all(checks.values()) else "insufficient",
                reason="Discovery checks passed; evidence inspection is the next step."
                if all(checks.values())
                else "Collection finished but discovery coverage checks remain unmet.",
            )
        except BudgetExhausted as error:
            run.update(status="budget_exhausted", reason=str(error))
        except (AccessPaused, EvidencePending, KeyboardInterrupt) as error:
            run.update(
                status="paused",
                reason=str(error) or "Interrupted; pending page is preserved.",
            )
        except Exception:
            run["state"] = store.get(run_id)["state"]
            state = run["state"]
            run.update(
                status="paused",
                reason="Unexpected failure; durable pending work is preserved.",
            )
            raise
        finally:
            save()
        return store.get(run_id)


def discovery_checks(store, run):
    state = run["state"]
    requests = {r["id"]: r for r in store.requests(run["id"]) if r["outcome"] == "ok"}
    verified = set()
    sources, intents, families = set(), set(), set()
    for identifier, item in state["candidates"].items():
        for origin in item["origins"]:
            receipt = origin.get("receipt") or {}
            request = requests.get(receipt.get("request_id"))
            try:
                logged = json.loads(request["detail"]) if request else None
            except json.JSONDecodeError:
                logged = None
            if (
                not logged
                or logged != receipt
                or receipt.get("transport") != "anonymous_http"
            ):
                continue
            verified.add(identifier)
            sources.add(origin["source"])
            if origin.get("intent"):
                intents.add(origin["intent"])
            if origin.get("family_id") and origin["source"] == "search":
                families.add(origin["family_id"])
    shortlist = make_shortlist(run)
    required_creator = (
        "creator_archive" if run["platform"] == "youtube" else "creator_search"
    )
    checks = {
        "at_least_100_observed_candidates": len(verified) >= 100,
        "50_distinct_shortlist_candidates": len(shortlist) == 50
        and all(s["canonical_id"] in verified for s in shortlist),
        "curated_search_and_creator_expansion": {"search", required_creator}.issubset(
            sources
        ),
        "bilibili_home_observed": run["platform"] != "bilibili" or "home" in sources,
        "all_discovery_intents_observed": {"intersection", "deep", "horizon"}.issubset(
            intents
        ),
        "at_least_six_query_families": len(families) >= 6,
        "intersection_shortlist_opportunities": sum(
            s["selected_for"]["intent"] == "intersection" for s in shortlist
        )
        >= 3,
        "collection_plan_finished": state["phase"] == "finished",
    }
    if "exploration" in state:
        checks.pop("all_discovery_intents_observed")
        checks.pop("intersection_shortlist_opportunities")
        checks["hierarchical_plan_validated"] = state["exploration"]["ready"]
        checks["all_planned_topics_searched"] = bool(state["plan"]) and state[
            "index"
        ] == len(state["plan"])
    return checks


def _validate_discovery_policy(run):
    validate_host(run["settings"].get("worker_host", "codex"))
    exploration = run["state"].get("exploration")
    if exploration is not None and exploration.get("policy_version") != 2:
        raise ValueError(
            "This discovery run uses an unsupported exploration policy; start a new discovery round."
        )


def discovery_report(store, run_id):
    run = store.get(run_id)
    if run["settings"].get("kind") != "discovery":
        raise ValueError("Not a discovery run.")
    state = run["state"]
    source_counts = Counter()
    for item in state["candidates"].values():
        source_counts.update({o["source"] for o in item["origins"]})
    return {
        "run_id": run_id,
        "platform": run["platform"],
        "status": run["status"],
        "reason": run["reason"],
        "created_at": run["created_at"],
        "generated_at": utc_now(),
        "settings": {
            **run["settings"],
            "creator_rules": policy_summary(run["settings"].get("creator_rules", {})),
        },
        "exploration": state.get("exploration"),
        "plan": state["plan"],
        "planning_model_calls": len(state.get("exploration", {}).get("attempts", [])),
        "elapsed_seconds": run["elapsed"],
        "http_requests": store.request_count(run_id),
        "candidate_count": len(state["candidates"]),
        "creator_count": len(state["creators"]),
        "source_counts": dict(source_counts),
        "page_limit_discards": sum(
            max(0, page["returned"] - 40) for page in state["pages"]
        ),
        "checks": discovery_checks(store, run),
        "shortlist": [
            {
                **state["candidates"][s["canonical_id"]],
                "selected_for": s["selected_for"],
            }
            for s in make_shortlist(run)
        ],
        "pages": state["pages"],
        "limitations": [
            "Candidates come from ranked public listings; coverage is neither uniform nor exhaustive.",
            "Query intent and anchor matches are retrieval hints, not verified semantic relevance or quality.",
            "Bilibili creator expansion uses owner-ID-verified public search, not complete archive enumeration.",
            "Published labels are platform text, not independently verified upload dates.",
            "Bilibili card duration may describe an archive; evidence preparation verifies the playable part and full-video access.",
            "Discovery does not inspect media or apply quality, profile or recommendation-history scores.",
        ],
    }


def write_discovery_report(store, run_id):
    report = discovery_report(store, run_id)
    root = store.root / "discovery" / run_id
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {report['platform']} discovery",
        "",
        f"Status: {report['status']}. {report['reason']}",
        "",
        f"Candidates: {report['candidate_count']}; creators: {report['creator_count']}; requests: {report['http_requests']}.",
        "",
        "These are unranked discovery candidates for evidence inspection.",
        "",
    ]
    for index, item in enumerate(report["shortlist"], 1):
        title = html.escape(
            item["title"].replace("\n", " ").replace("[", "(").replace("]", ")")
        )
        lines.append(
            f"{index}. [{title}]({item['url']}) — {item['selected_for']['source']}; duration {item.get('duration_seconds')}; published label {item.get('published_label')!r}"
        )
    lines.extend(["", *report["limitations"], ""])
    payloads = {
        "report.json": json.dumps(report, indent=2, ensure_ascii=False),
        "candidates.md": "\n".join(lines),
        "buffer.json": json.dumps(
            store.get(run_id)["state"]["candidates"], indent=2, ensure_ascii=False
        ),
    }
    for name, body in payloads.items():
        target = root / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        temp.replace(target)
    return root / "report.json"
