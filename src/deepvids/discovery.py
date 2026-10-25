from collections import Counter
import html
import json

from .discovery_sources import BilibiliDiscovery, YouTubeDiscovery
from .http import AccessPaused, BudgetExhausted, PublicHttp
from .querybank import anchor_hits, load_bank, query_plan
from .storage import utc_now


def create_discovery(store, platform, bank_path=None, round_number=None, max_requests=100, max_seconds=600,
                     creator_limit=4, creator_pages=2):
    if platform not in {"youtube", "bilibili"}:
        raise ValueError("Unsupported discovery platform.")
    if not 1 <= max_requests <= 1000 or not 0 < max_seconds <= 3600:
        raise ValueError("Discovery requires 1–1,000 requests and at most 3,600 seconds.")
    if not 1 <= creator_limit <= 10 or not 1 <= creator_pages <= 5:
        raise ValueError("Use 1–10 creators and 1–5 pages per creator.")
    bank = load_bank(bank_path)
    if round_number is None:
        previous = [r for r in store.list_runs() if r["platform"] == platform and r["settings"].get("kind") == "discovery"
                    and r["settings"].get("bank_sha256") == bank["sha256"]]
        round_number = max((r["settings"]["round"] for r in previous), default=-1) + 1
    plan = query_plan(bank, platform, round_number)
    settings = {"kind": "discovery", "discovery_schema": 1, "bank_sha256": bank["sha256"], "bank_version": bank["version"],
                "round": round_number, "max_requests": max_requests, "max_seconds": max_seconds,
                "creator_limit": creator_limit, "creator_pages": creator_pages, "buffer_limit": 1000,
                "shortlist_target": 50, "creator_cap": 3}
    state = {"phase": "bootstrap", "plan": plan, "index": 0, "creator_index": 0, "creator_tasks": [],
             "candidates": {}, "creators": {}, "shortlist": [], "pages": [], "client_version": None}
    return store.create(platform, settings, state)


def merge_page(run, items, source, task, receipt, cursor=None):
    state = run["state"]
    origin_base = {"source": source, "family_id": task.get("family_id"), "query": task.get("query"),
                   "intent": task.get("intent"), "domains": task.get("domains", []),
                   "creator_id": task.get("creator_id"), "receipt": receipt}
    for rank, item in enumerate(items[:40], 1):
        identifier = item["canonical_id"]
        if identifier not in state["candidates"] and len(state["candidates"]) >= run["settings"]["buffer_limit"]:
            continue
        origin = {**origin_base, "rank": rank, "anchor_hits": anchor_hits(item, task)}
        if identifier not in state["candidates"]:
            state["candidates"][identifier] = {**item, "first_seen": utc_now(), "origins": []}
        record = state["candidates"][identifier]
        if origin not in record["origins"]:
            record["origins"].append(origin)
        creator = item.get("creator_id")
        if creator and item.get("creator_name"):
            entry = state["creators"].setdefault(creator, {"id": creator, "name": item["creator_name"], "seed_query": task.get("query"),
                                                        "family_id": task.get("family_id"), "domains": task.get("domains", []), "observed_videos": []})
            if identifier not in entry["observed_videos"]:
                entry["observed_videos"].append(identifier)
    state["pages"].append({**origin_base, "returned": len(items), "continuation": cursor,
                           "retained_input": min(len(items), 40), "source_page_truncated": len(items) > 40,
                           "unique_buffer": len(state["candidates"]), "observed_at": utc_now()})


def make_shortlist(run):
    state = run["state"]
    buckets = {}
    for identifier, item in state["candidates"].items():
        duration = item.get("duration_seconds")
        if run["platform"] == "youtube" and duration is not None and duration > 5400:
            continue
        if not item.get("creator_id"):
            continue
        for origin in item["origins"]:
            key = (origin["source"], origin.get("family_id") or origin.get("creator_id") or "home")
            buckets.setdefault(key, []).append((identifier, origin))
    for bucket in buckets.values():
        bucket.sort(key=lambda pair: (-pair[1]["anchor_hits"], pair[1]["rank"], pair[0]))
    selected, creator_counts = {}, Counter()
    while len(selected) < run["settings"]["shortlist_target"]:
        progress = False
        for bucket in buckets.values():
            while bucket:
                identifier, origin = bucket.pop(0)
                creator = state["candidates"][identifier]["creator_id"]
                if identifier in selected or creator_counts[creator] >= run["settings"]["creator_cap"]:
                    continue
                selected[identifier] = {"canonical_id": identifier, "selected_for": {k: origin[k] for k in ("source", "family_id", "intent", "creator_id")}}
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
        by_family.setdefault(creator.get("family_id") or "home", []).append(creator)
    result = []
    while len(result) < run["settings"]["creator_limit"] and any(by_family.values()):
        for group in by_family.values():
            if group and len(result) < run["settings"]["creator_limit"]:
                creator = group.pop(0)
                result.append({"creator_id": creator["id"], "name": creator["name"], "family_id": creator.get("family_id"),
                               "domains": creator["domains"], "query": creator["name"], "cursor": None, "page": 1})
    return result


def execute_discovery(store, run_id, http_factory=PublicHttp):
    with store.lease(run_id):
        run = store.get(run_id)
        if run["settings"].get("kind") != "discovery":
            raise ValueError("This command requires a discovery run.")
        if run["status"] == "completed":
            return run
        http = http_factory(store, run, anonymous_session=True)
        provider = YouTubeDiscovery(http) if run["platform"] == "youtube" else BilibiliDiscovery(http)
        state = run["state"]
        def save(event=None):
            run["elapsed"] = http.elapsed()
            state["shortlist"] = make_shortlist(run)
            store.save(run, event)
        run.update(status="running", reason="")
        try:
            http.check()
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
                items, cursor = provider.search(task["query"], state["client_version"]) if run["platform"] == "youtube" else provider.search(task["query"])
                merge_page(run, items, "search", task, http.last_receipt, cursor)
                state["index"] += 1
                save(("discovery_page", {"source": "search", "family_id": task["family_id"], "returned": len(items)}))
            if state["phase"] == "queries":
                state["creator_tasks"] = _creator_tasks(run)
                state["phase"] = "creators"
                save()
            while state["creator_index"] < len(state["creator_tasks"]):
                task = state["creator_tasks"][state["creator_index"]]
                if run["platform"] == "youtube":
                    items, cursor = provider.creator(task["creator_id"], state["client_version"], task["cursor"])
                    source = "creator_archive"
                else:
                    items, cursor = provider.creator(task["creator_id"], task["name"], task["page"])
                    source = "creator_search"
                merge_page(run, items, source, task, http.last_receipt, cursor)
                task.update(cursor=cursor, page=task["page"] + 1)
                if not items or not cursor or task["page"] > run["settings"]["creator_pages"]:
                    state["creator_index"] += 1
                save(("discovery_page", {"source": source, "creator_id": task["creator_id"], "returned": len(items)}))
            state["phase"] = "finished"
            state["shortlist"] = make_shortlist(run)
            checks = discovery_checks(store, run)
            run.update(status="completed" if all(checks.values()) else "insufficient",
                       reason="Discovery checks passed; evidence inspection is Phase 2." if all(checks.values()) else "Collection finished but discovery coverage checks remain unmet.")
        except BudgetExhausted as error:
            run.update(status="budget_exhausted", reason=str(error))
        except (AccessPaused, KeyboardInterrupt) as error:
            run.update(status="paused", reason=str(error) or "Interrupted; pending page is preserved.")
        except Exception:
            run["state"] = store.get(run_id)["state"]
            state = run["state"]
            run.update(status="paused", reason="Unexpected failure; durable pending work is preserved.")
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
            if not logged or logged != receipt or receipt.get("transport") != "anonymous_http":
                continue
            verified.add(identifier)
            sources.add(origin["source"])
            if origin.get("intent"):
                intents.add(origin["intent"])
            if origin.get("family_id") and origin["source"] == "search":
                families.add(origin["family_id"])
    shortlist = make_shortlist(run)
    required_creator = "creator_archive" if run["platform"] == "youtube" else "creator_search"
    return {"at_least_100_observed_candidates": len(verified) >= 100,
            "50_distinct_shortlist_candidates": len(shortlist) == 50 and all(s["canonical_id"] in verified for s in shortlist),
            "curated_search_and_creator_expansion": {"search", required_creator}.issubset(sources),
            "bilibili_home_observed": run["platform"] != "bilibili" or "home" in sources,
            "all_discovery_intents_observed": {"intersection", "deep", "horizon"}.issubset(intents),
            "at_least_six_query_families": len(families) >= 6,
            "intersection_shortlist_opportunities": sum(s["selected_for"]["intent"] == "intersection" for s in shortlist) >= 3,
            "collection_plan_finished": state["phase"] == "finished"}


def discovery_report(store, run_id):
    run = store.get(run_id)
    if run["settings"].get("kind") != "discovery":
        raise ValueError("Not a discovery run.")
    state = run["state"]
    source_counts = Counter()
    for item in state["candidates"].values():
        source_counts.update({o["source"] for o in item["origins"]})
    return {"run_id": run_id, "platform": run["platform"], "status": run["status"], "reason": run["reason"],
            "created_at": run["created_at"], "generated_at": utc_now(), "settings": run["settings"],
            "elapsed_seconds": run["elapsed"], "http_requests": store.request_count(run_id),
            "candidate_count": len(state["candidates"]), "creator_count": len(state["creators"]), "source_counts": dict(source_counts),
            "page_limit_discards": sum(max(0, page["returned"] - 40) for page in state["pages"]),
            "checks": discovery_checks(store, run), "shortlist": [
                {**state["candidates"][s["canonical_id"]], "selected_for": s["selected_for"]} for s in make_shortlist(run)],
            "pages": state["pages"], "limitations": [
                "Candidates come from ranked public listings; coverage is neither uniform nor exhaustive.",
                "Query intent and anchor matches are retrieval hints, not verified semantic relevance or quality.",
                "Bilibili creator expansion uses owner-ID-verified public search, not complete archive enumeration.",
                "Published labels are platform text, not independently verified upload dates.",
                "Bilibili card duration may describe an archive; playable parts and full-video login access require Phase 2.",
                "No quality/model/media inspection, personal profile, recommendation history or daily scheduling is implemented."]}


def write_discovery_report(store, run_id):
    report = discovery_report(store, run_id)
    root = store.root / "discovery" / run_id
    root.mkdir(parents=True, exist_ok=True)
    lines = [f"# {report['platform']} discovery", "", f"Status: {report['status']}. {report['reason']}", "",
             f"Candidates: {report['candidate_count']}; creators: {report['creator_count']}; requests: {report['http_requests']}.", "",
             "These are unranked candidates for Phase 2 inspection, not recommendations.", ""]
    for index, item in enumerate(report["shortlist"], 1):
        title = html.escape(item["title"].replace("\n", " ").replace("[", "(").replace("]", ")"))
        lines.append(f"{index}. [{title}]({item['url']}) — {item['selected_for']['source']}; duration {item.get('duration_seconds')}; published label {item.get('published_label')!r}")
    lines.extend(["", *report["limitations"], ""])
    payloads = {"report.json": json.dumps(report, indent=2, ensure_ascii=False), "candidates.md": "\n".join(lines),
                "buffer.json": json.dumps(store.get(run_id)["state"]["candidates"], indent=2, ensure_ascii=False)}
    for name, body in payloads.items():
        target = root / name
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        temp.replace(target)
    return root / "report.json"
