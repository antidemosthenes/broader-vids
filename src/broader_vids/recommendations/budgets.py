from contextlib import ExitStack
import json

from broader_vids.preferences import platform_lock
from broader_vids.storage import utc_now


DEFAULT_METADATA_REQUESTS = 320
MAX_METADATA_REQUESTS = 1000


def request_budget(store, run):
    used = store.request_count(run["id"])
    limit = run["settings"]["max_requests"]
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def collection_budget_message(store, run):
    budget = request_budget(store, run)
    state = run["state"]
    active = state.get("collection", {}).get("active", {})
    pending = len(active.get("eligible", []))
    detail = (
        f" Another {pending} eligible videos await completion of the saved batch."
        if pending
        else ""
    )
    return (
        f"Metadata request allowance exhausted ({budget['used']}/{budget['limit']} used); "
        f"{len(state['buffer'])}/100 videos passed admission and "
        f"{len(state.get('screening_rejections', {}))} were rejected.{detail} "
        "Saved candidates remain available. Explicitly increase --max-requests when continuing; "
        "any explicit time allowance and prior request use do not reset."
    )


def recommendation_targets(store, run):
    kind = run["settings"].get("kind")
    if kind == "recommendation":
        if run["settings"].get("batch_id"):
            raise ValueError(
                f"Continue batch {run['settings']['batch_id']} to preserve its shared budget."
            )
        return [run]
    if kind != "recommendation_batch":
        raise ValueError(
            "A request-limit increase requires a recommendation run or batch."
        )
    children = run["state"].get("children", {})
    if set(children) != {"youtube", "bilibili"} or len(set(children.values())) != 2:
        raise ValueError("The batch has incomplete or duplicated child membership.")
    targets = [store.get(identifier) for identifier in children.values()]
    if any(
        child["settings"].get("kind") != "recommendation"
        or child["settings"].get("batch_id") != run["id"]
        or children.get(child["platform"]) != child["id"]
        for child in targets
    ):
        raise ValueError("The batch does not own both platform checkpoints.")
    return targets


def validate_request_limit(limit):
    if type(limit) is not int or not 1 <= limit <= MAX_METADATA_REQUESTS:
        raise ValueError("Use a metadata request cap from 1 to 1,000.")


def increase_request_limit(store, identifier, limit):
    validate_request_limit(limit)
    run = store.get(identifier)
    targets = recommendation_targets(store, run)
    locks = {identifier}
    locks.update(target["id"] for target in targets)
    locks.update(platform_lock(target["platform"]) for target in targets)
    with ExitStack() as leases:
        for key in sorted(locks):
            leases.enter_context(store.lease(key))
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            current = recommendation_targets(store, store.get(identifier))
            if {target["id"] for target in targets} != {
                target["id"] for target in current
            }:
                raise ValueError(
                    "The batch membership changed before the request-limit update."
                )
            unfinished = [
                target
                for target in current
                if target["status"] != "completed"
                and target["state"].get("phase") != "published"
            ]
            if not unfinished:
                raise ValueError(
                    "All recommendation checkpoints are complete; start a new round."
                )
            if any(limit < target["settings"]["max_requests"] for target in unfinished):
                raise ValueError(
                    "Continuation can increase a request cap, but cannot reduce it."
                )
            changes = []
            for target in unfinished:
                previous = target["settings"]["max_requests"]
                if previous == limit:
                    continue
                used = store.request_count(target["id"])
                if limit < used:
                    raise ValueError(
                        "The new request cap cannot be below requests already used."
                    )
                change = {
                    "run_id": target["id"],
                    "platform": target["platform"],
                    "previous_limit": previous,
                    "new_limit": limit,
                    "requests_used": used,
                }
                settings = {**target["settings"], "max_requests": limit}
                store.db.execute(
                    "UPDATE runs SET settings=?,updated_at=? WHERE id=?",
                    (json.dumps(settings), utc_now(), target["id"]),
                )
                store.db.execute(
                    "INSERT INTO events(run_id,created_at,kind,payload) VALUES(?,?,'request_limit_increased',?)",
                    (target["id"], utc_now(), json.dumps(change)),
                )
                changes.append(change)
            return changes
