from collections import Counter
import json

from broader_vids.evidence.media import EvidencePending
from broader_vids.http import BudgetExhausted
from broader_vids.preferences import video_states
from broader_vids.recommendations.collection_policy import (
    LANES,
    batch_allocations,
    collection_lane,
    default_collection_policy,
)
from broader_vids.recommendations.engagement import metadata_fresh, rate_scores
from broader_vids.recommendations.metadata import (
    metadata_session,
    SnapshotPool,
    apply_snapshot,
)
from broader_vids.recommendations.ranking import fair_buffer
from broader_vids.recommendations.screening import rejection_reason, screening_score
from broader_vids.recommendations.screening import CURRENT_SCREENING_POLICY
from broader_vids.recommendations.personalization import (
    blended_screening_score,
    compile_screening_learning,
)


def collect_batches(store, run, http, remaining, save, *, refill, snapshot_collector):
    state, settings = run["state"], run["settings"]
    if settings.get("screening_policy_version") != CURRENT_SCREENING_POLICY:
        raise ValueError(
            "This recommendation uses an unsupported screening policy; start a new round."
        )
    policy = settings["collection_policy"]
    if policy != default_collection_policy():
        raise ValueError(
            "The saved collection policy is unsupported; keep its original compatible runtime."
        )
    targets = policy["targets"]
    state.setdefault("candidate_pool", list(state["buffer"]))
    state.setdefault("pool_index", 0)
    state.setdefault("screening_rejections", {})
    collection = state.setdefault(
        "collection", {"seen": [], "batches_completed": 0, "history": []}
    )
    seen = set(collection["seen"])
    seen.update(item["canonical_id"] for item in state["buffer"])
    states = video_states(store, run["platform"])
    from broader_vids.recommendations.learning_snapshot import round_learning

    learning = round_learning(store, run)
    compiled_learning = compile_screening_learning(learning)

    def deficits():
        counts = Counter(
            collection_lane(item, settings["profile"]) for item in state["buffer"]
        )
        collection["counts"] = {lane: counts[lane] for lane in LANES}
        return {lane: max(0, targets[lane] - counts[lane]) for lane in LANES}

    def queues():
        values = {lane: [] for lane in LANES}
        for item in state["candidate_pool"]:
            if item["canonical_id"] not in seen:
                values[collection_lane(item, settings["profile"])].append(item)
        return values

    def reserve_batch():
        allocation = batch_allocations(deficits(), policy["batch_size"])
        exhausted = set()
        no_progress = Counter()
        while True:
            available = queues()
            lane = next(
                (
                    lane
                    for lane in LANES
                    if len(available[lane]) < allocation[lane] and lane not in exhausted
                ),
                None,
            )
            if lane is None:
                break
            before = len(available[lane])
            options = {"lane": lane}
            from broader_vids.recommendations.collection import refill_candidates

            if refill is refill_candidates:
                options.update(
                    cancelled=getattr(http, "cancelled", None),
                    request_gate=getattr(http, "request_gate", None),
                    visitor_session=http if run["platform"] == "youtube" else None,
                )
            if remaining() <= 0:
                raise BudgetExhausted(
                    "Candidate collection time budget exhausted during refill."
                )
            if not refill(store, run, remaining, save, **options):
                exhausted.add(lane)
            elif len(queues()[lane]) <= before:
                no_progress[lane] += 1
                if no_progress[lane] >= 2 and refill is not refill_candidates:
                    exhausted.add(lane)
            else:
                no_progress[lane] = 0
        available = queues()
        selected = []
        for lane in LANES:
            selected.extend(fair_buffer(available[lane], allocation[lane]))
        if not selected:
            missing = deficits()
            raise EvidencePending(
                f"Collection cannot fill the eligible pool: {missing['familiar']} familiar and {missing['exploratory']} exploratory slots remain. "
                "Saved source continuations have no further eligible candidates within the current limits."
            )
        selected = fair_buffer(selected, policy["batch_size"])
        active = {
            "items": selected,
            "index": 0,
            "eligible": [],
            "allocation": allocation,
        }
        seen.update(item["canonical_id"] for item in selected)
        collection.update(seen=sorted(seen), active=active)
        save()
        return active

    def needs_snapshot(item):
        if rejection_reason(
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

    def finish_batch(active):
        eligible = active["eligible"]
        rates = rate_scores([*state["buffer"], *eligible])
        missing = deficits()
        admitted = []
        for lane in LANES:
            values = [
                item
                for item in eligible
                if collection_lane(item, settings["profile"]) == lane
            ]
            for item in values:
                item["engagement_score"] = rates[item["canonical_id"]]
                base_score = screening_score(item)
                if compiled_learning is not None:
                    item["screening_score"], item["screening_detail"] = (
                        blended_screening_score(base_score, item, compiled_learning)
                    )
                else:
                    item["screening_score"] = base_score
            values.sort(
                key=lambda item: (-item["screening_score"], item["canonical_id"])
            )
            admitted.extend(values[: missing[lane]])
        state["buffer"].extend(admitted)
        state["enrichment_index"] = len(state["buffer"])
        collection["batches_completed"] += 1
        collection["history"].append(
            {
                "batch": collection["batches_completed"],
                "scanned": len(active["items"]),
                "eligible": len(eligible),
                "admitted": len(admitted),
                "admitted_by_lane": dict(
                    Counter(
                        collection_lane(item, settings["profile"]) for item in admitted
                    )
                ),
            }
        )
        collection["history"] = collection["history"][-40:]
        collection.pop("active", None)
        deficits()
        save()

    authenticated = settings["use_browser"] and run["platform"] == "bilibili"
    with (
        metadata_session(store, http, authenticated),
        SnapshotPool(store, http, remaining, snapshot_collector) as snapshots,
    ):
        while any(deficits().values()):
            if remaining() <= 0:
                raise BudgetExhausted(
                    "Candidate collection time budget exhausted before 100 balanced eligible videos."
                )
            active = collection.get("active") or reserve_batch()
            while active["index"] < len(active["items"]):
                if remaining() <= 0:
                    raise BudgetExhausted(
                        "Candidate collection time budget exhausted during a saved batch."
                    )
                item = dict(active["items"][active["index"]])
                snapshots.prefetch(active["items"][active["index"] :], needs_snapshot)
                reason = rejection_reason(
                    item,
                    states,
                    settings["profile"],
                    state.get("creator_rules"),
                    require_metrics=False,
                )
                if reason is None:
                    row = store.db.execute(
                        "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                        (item["canonical_id"],),
                    ).fetchone()
                    snapshot = json.loads(row[0]) if row else None
                    if not snapshot or not metadata_fresh(snapshot, run["platform"]):
                        snapshot = snapshots.get(item, save)
                        with store.db:
                            store.db.execute(
                                "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                                (item["canonical_id"], json.dumps(snapshot)),
                            )
                    apply_snapshot(item, snapshot or {})
                    reason = rejection_reason(
                        item, states, settings["profile"], state.get("creator_rules")
                    )
                if reason:
                    state["screening_rejections"][item["canonical_id"]] = reason
                else:
                    active["eligible"].append(item)
                active["index"] += 1
                state["pool_index"] += 1
                save()
            finish_batch(active)
