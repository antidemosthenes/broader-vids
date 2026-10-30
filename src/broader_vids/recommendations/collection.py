from broader_vids.evidence.media import EvidencePending
from broader_vids.http import BudgetExhausted, PublicHttp
from broader_vids.preferences import video_states
from broader_vids.recommendations.engagement import collect_snapshot, metadata_fresh
from broader_vids.recommendations.metadata import metadata_session, SnapshotPool
from broader_vids.recommendations.screening import rejection_reason
from broader_vids.creators import enrich
from broader_vids.storage import utc_now
import json
import hashlib


def refill_candidates(
    store,
    run,
    remaining,
    save,
    *,
    cancelled=None,
    request_gate=None,
    lane=None,
    visitor_session=None,
):
    from broader_vids.discovery.sources import YouTubeDiscovery, BilibiliDiscovery
    from broader_vids.discovery.service import (
        candidate_origin,
        merge_page,
        page_context,
    )

    state = run["state"]
    source_id = state.get("discovery_run")
    if not source_id:
        return False
    source = store.get(source_id)
    plans = {task["query"]: task for task in source["state"].get("plan", [])}
    tasks = state.get("refill_tasks")
    if tasks is None:
        tasks, seen = [], set()
        for page in source["state"].get("pages", []):
            if (
                page.get("source") != "search"
                or not page.get("query")
                or page["query"] in seen
            ):
                continue
            seen.add(page["query"])
            if run["platform"] == "youtube" and not page.get("continuation"):
                continue
            tasks.append(
                {
                    **plans.get(page["query"], {}),
                    **page,
                    "anchors": page.get("anchors")
                    or plans.get(page["query"], {}).get("anchors", []),
                    "page": 2,
                    "cursor": page.get("continuation"),
                    "done": False,
                }
            )
        state["refill_tasks"] = tasks
    from broader_vids.recommendations.collection_policy import collection_lane

    task = next(
        (
            t
            for t in tasks
            if not t["done"]
            and (
                lane is None
                or collection_lane({"origins": [t]}, run["settings"]["profile"]) == lane
            )
        ),
        None,
    )
    if task is None or len(state["candidate_pool"]) >= 4000:
        return False
    if not task.get("anchors"):
        task["anchors"] = plans.get(task["query"], {}).get("anchors", [])
    if remaining() <= 0:
        raise BudgetExhausted("Candidate collection time budget exhausted.")
    session_options = (
        {
            "cookie_jar": visitor_session.cookies,
            "clock": visitor_session.clock,
            "sleeper": visitor_session.sleeper,
        }
        if visitor_session is not None and run["platform"] == "youtube"
        else {}
    )
    retrieval = PublicHttp(
        store,
        {
            **run,
            "settings": {
                **run["settings"],
                "max_seconds": run["elapsed"] + remaining(),
            },
        },
        anonymous_session=True,
        **session_options,
    )
    retrieval.cancelled = cancelled
    if request_gate is not None:
        retrieval.request_gate = request_gate
    provider = (
        YouTubeDiscovery(retrieval)
        if run["platform"] == "youtube"
        else BilibiliDiscovery(retrieval)
    )
    with store.lease(source_id):
        source = store.get(source_id)
        if run["platform"] == "youtube":
            items, cursor = provider.search(
                task["query"], source["state"]["client_version"], task["cursor"]
            )
        else:
            items, cursor = provider.search(task["query"], page=task["page"])
        merge_page(source, items, "search", task, retrieval.last_receipt, cursor)
        store.save(source)
    fingerprint = hashlib.sha256(
        "\n".join(sorted({item["canonical_id"] for item in items})).encode()
    ).hexdigest()
    signatures = task.setdefault("page_signatures", [])
    repeated = fingerprint in signatures or (
        run["platform"] == "youtube" and cursor == task.get("cursor")
    )
    signatures.append(fingerprint)
    task.update(
        cursor=cursor, page=task["page"] + 1, done=not items or not cursor or repeated
    )
    tasks.append(tasks.pop(tasks.index(task)))
    known = {item["canonical_id"]: item for item in state["candidate_pool"]}
    context = page_context("search", task, retrieval.last_receipt)
    for rank, item in enumerate(items, 1):
        record = known.get(item["canonical_id"])
        if record is None:
            if len(state["candidate_pool"]) >= 4000:
                continue
            base = source["state"]["candidates"].get(item["canonical_id"], item)
            record = {
                **base,
                "first_seen": base.get("first_seen") or utc_now(),
                "origins": list(base.get("origins", [])),
            }
            state["candidate_pool"].append(record)
            known[item["canonical_id"]] = record
        origin = candidate_origin(item, context, rank)
        if origin not in record.setdefault("origins", []):
            record["origins"].append(origin)
    save()
    return True


def collect_eligible_buffer(
    store,
    run,
    http,
    remaining,
    save,
    *,
    refill=refill_candidates,
    snapshot_collector=collect_snapshot,
):
    if (
        run["settings"].get("collection_policy")
        and run["settings"].get("screening_policy_version", 2) >= 3
    ):
        from broader_vids.recommendations.collection_batches import collect_batches

        return collect_batches(
            store,
            run,
            http,
            remaining,
            save,
            refill=refill,
            snapshot_collector=snapshot_collector,
        )
    state, settings = run["state"], run["settings"]
    state.setdefault("candidate_pool", list(state["buffer"]))
    state.setdefault("pool_index", 0)
    state.setdefault("screening_rejections", {})
    states = video_states(store, run["platform"])
    accepted = {item["canonical_id"] for item in state["buffer"]}

    def needs_snapshot(item):
        if item["canonical_id"] in accepted or rejection_reason(
            item,
            states,
            settings["profile"],
            state.get("creator_rules"),
            require_metrics=False,
        ):
            return False
        row = store.db.execute(
            "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
            (item["canonical_id"],),
        ).fetchone()
        return not row or not metadata_fresh(json.loads(row[0]), run["platform"])

    authenticated = settings["use_browser"] and run["platform"] == "bilibili"
    with (
        metadata_session(store, http, authenticated),
        SnapshotPool(store, http, remaining, snapshot_collector) as snapshots,
    ):
        while len(state["buffer"]) < 100:
            if remaining() <= 0:
                raise BudgetExhausted(
                    "Candidate collection time budget exhausted before 100 eligible videos."
                )
            if state["pool_index"] >= len(state["candidate_pool"]):
                options = (
                    {
                        "cancelled": getattr(http, "cancelled", None),
                        "request_gate": getattr(http, "request_gate", None),
                        "visitor_session": http
                        if run["platform"] == "youtube"
                        else None,
                    }
                    if refill is refill_candidates
                    else {}
                )
                if refill(store, run, remaining, save, **options):
                    continue
                raise EvidencePending(
                    f"Only {len(state['buffer'])}/100 videos passed duration and community-response floors; discovery sources are exhausted within the current limits."
                )
            item = dict(state["candidate_pool"][state["pool_index"]])
            snapshots.prefetch(
                state["candidate_pool"][state["pool_index"] :],
                needs_snapshot,
                min(2, 100 - len(state["buffer"])),
            )
            key = item["canonical_id"]
            if key in accepted:
                state["pool_index"] += 1
                continue
            reason = rejection_reason(
                item,
                states,
                settings["profile"],
                state.get("creator_rules"),
                require_metrics=False,
            )
            if reason is None:
                cached = store.db.execute(
                    "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                    (key,),
                ).fetchone()
                snapshot = json.loads(cached[0]) if cached else None
                if not snapshot or not metadata_fresh(snapshot, run["platform"]):
                    snapshot = snapshots.get(item, save)
                    with store.db:
                        store.db.execute(
                            "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                            (key, json.dumps(snapshot)),
                        )
                snapshot = snapshot or {}
                item["engagement"] = {**item.get("engagement", {}), **snapshot}
                enrich(item, item["engagement"])
                if (
                    run["platform"] == "youtube"
                    and snapshot.get("duration_seconds") is not None
                ):
                    item["duration_seconds"] = snapshot["duration_seconds"]
                elif run["platform"] == "bilibili":
                    part = next(
                        (
                            p
                            for p in snapshot.get("pages", [])
                            if p["page"] == item.get("part_index", 1)
                        ),
                        None,
                    )
                    if part:
                        item.update(
                            duration_seconds=part["duration"],
                            duration_scope="resolved_part",
                            cid=part["cid"],
                        )
                reason = rejection_reason(
                    item, states, settings["profile"], state.get("creator_rules")
                )
            state["pool_index"] += 1
            if reason:
                state["screening_rejections"][key] = reason
            else:
                state["buffer"].append(item)
                accepted.add(key)
                state["enrichment_index"] = len(state["buffer"])
            save()
