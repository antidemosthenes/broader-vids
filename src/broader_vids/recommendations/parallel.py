from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
import threading
import time
import uuid
import subprocess
import json

from broader_vids.evidence.assessment import assess_evidence
from broader_vids.evidence.browser import bilibili_browser, browser_cookie_jar
from broader_vids.evidence.media import EvidencePending, public_error
from broader_vids.evidence.failures import failure_details, save_failure
from broader_vids.evidence.service import create_evidence, prepare_evidence
from broader_vids.evidence.preparation_worker import (
    prepare_bounded,
    record_preparation_receipt,
)
from broader_vids.http import BudgetExhausted
from broader_vids.recommendations.originality import provenance, assessment_context
from broader_vids.recommendations.quality import cached_quality
from broader_vids.storage import Store
from broader_vids.creators import excluded as excluded_creator


class Runtime:
    def __init__(self, preparations=2, models=4):
        if (
            type(preparations) is not int
            or type(models) is not int
            or preparations < 1
            or models < 1
        ):
            raise ValueError("Shared worker limits must be positive integers.")
        self.preparations = threading.BoundedSemaphore(preparations)
        self.models = threading.BoundedSemaphore(models)
        self.cancelled = threading.Event()

    @contextmanager
    def slot(self, semaphore, remaining, cancelled):
        acquired = False
        try:
            while not acquired:
                if cancelled.is_set() or self.cancelled.is_set() or remaining() <= 0:
                    raise EvidencePending(
                        "Parallel work reached its deadline or checkpoint stop.",
                        retryable=True,
                    )
                acquired = semaphore.acquire(timeout=min(0.25, remaining()))
            if cancelled.is_set() or self.cancelled.is_set() or remaining() <= 0:
                raise EvidencePending(
                    "Parallel work was cancelled before entering its slot.",
                    retryable=True,
                )
            yield
        finally:
            if acquired:
                semaphore.release()


def process_candidate(root, work, settings, runtime, cookie_jar, cancelled):
    deadline = work.get(
        "parent_deadline", work.get("deadline", time.monotonic() + work["seconds"])
    )
    store = Store(root)

    def remaining():
        return max(0, deadline - time.monotonic())

    identifier = work["evidence_run"]

    class Cancellation:
        def is_set(self):
            return cancelled.is_set() or runtime.cancelled.is_set()

    cancellation = Cancellation()
    try:
        with store.lease(identifier) as owner:
            return _process_owned(
                store,
                identifier,
                work,
                settings,
                runtime,
                cookie_jar,
                cancelled,
                cancellation,
                remaining,
                owner,
            )
    except (ValueError, OSError, EvidencePending, subprocess.TimeoutExpired) as error:
        return {
            "status": "pending",
            "evidence_run": identifier,
            "reason": public_error(error),
            "failure": failure_details(error, "worker"),
        }
    finally:
        store.close()


def _process_owned(
    store,
    identifier,
    work,
    settings,
    runtime,
    cookie_jar,
    cancelled,
    cancellation,
    remaining,
    owner,
):
    def timing(name, started):
        saved = store.get(identifier)
        timings = saved["state"].setdefault("timings", {})
        timings[name] = timings.get(name, 0) + time.monotonic() - started
        store.save(saved)

    def stage(name):
        saved = store.get(identifier)
        saved["state"]["stage"] = name
        store.save(saved)

    try:
        current_bytes = store.get(identifier)["state"]["resources"][
            "media_download_bytes"
        ]
        record_preparation_receipt(
            store, identifier, work["preparation_token"], current_bytes
        )
        queued = time.monotonic()
        stage("queued_preparation")
        with runtime.slot(runtime.preparations, remaining, cancelled):
            timing("preparation_queue_seconds", queued)
            if store.get(identifier)["state"]["phase"] in {"prepared", "completed"}:
                prepared = prepare_evidence(
                    store,
                    identifier,
                    max_seconds=max(0.01, remaining()),
                    lease_owner=owner,
                )
            else:
                path = "$.preparation_usage." + json.dumps(work["preparation_token"])
                with store.db:
                    store.db.execute(
                        "UPDATE runs SET state=json_remove(state,?) WHERE id=?",
                        (path, identifier),
                    )
                prepare_bounded(
                    store.root,
                    identifier,
                    settings["asr_model"],
                    max(0.01, min(work["seconds"], remaining())),
                    work["bytes"],
                    cookie_jar,
                    cancellation,
                    attempt_token=work["preparation_token"],
                    lease_owner=owner,
                )
                prepared = store.get(identifier)
        if (
            prepared["state"]["phase"] in {"prepared", "completed"}
            and "augmented" not in prepared["state"]["assessments"]
        ):
            queued = time.monotonic()
            stage("queued_model")
            with runtime.slot(runtime.models, remaining, cancelled):
                timing("model_queue_seconds", queued)
                assess_evidence(
                    store,
                    identifier,
                    "augmented",
                    max_seconds=max(0.01, min(180, remaining())),
                    cancel_event=cancellation,
                    lease_owner=owner,
                    work_token=work["preparation_token"],
                )
        observation = cached_quality(store, identifier)
        if observation:
            return observation
        failed = store.get(identifier)
        failure = failed["state"].get("failure") or failure_details(
            EvidencePending(
                failed["reason"],
                retryable=failed["state"].get("retryable_failure", False),
            ),
            failed["state"].get("stage", "assessment"),
        )
        return {
            "status": "pending"
            if failed["state"].get("retryable_failure")
            or failure["scope"] != "candidate"
            else "unavailable",
            "evidence_run": identifier,
            "reason": failed["reason"]
            or "Evidence did not support a defensible quality score.",
            "failure": failure,
        }
    except (ValueError, OSError, EvidencePending, subprocess.TimeoutExpired) as error:
        failed = store.get(identifier)
        failure = failed["state"].get("failure")
        if not failure or failure.get("reason") != public_error(error):
            failure = save_failure(
                failed, error, failed["state"].get("stage", "worker")
            )
        failed["state"]["retryable_failure"] = (
            failure.get("category") != "attempt_limit"
        )
        failed.update(status="pending", reason=failure["reason"])
        store.save(failed)
        return {
            "status": "pending",
            "evidence_run": identifier,
            "reason": failure["reason"],
            "failure": failure,
        }


def assess_parallel(
    store,
    run,
    save,
    remaining,
    runtime=None,
    worker=process_candidate,
    quality_lookup=cached_quality,
):
    state, settings = run["state"], run["settings"]
    runtime = runtime or Runtime(models=settings.get("workers", 4))
    cancelled = threading.Event()
    inflight = state.setdefault("inflight", {})

    def reconcile(key, work):
        child = store.get(work["evidence_run"])
        if work.get("accounting_version") == 2:
            state["model_calls"] += sum(
                a.get("work_token") == work["preparation_token"]
                for a in child["state"]["attempts"]
            )
        else:
            state["model_calls"] += max(
                0, child["state"]["model_calls"] - work["base_calls"]
            )
        downloaded = max(
            0, child["state"]["resources"]["media_download_bytes"] - work["base_bytes"]
        )
        usage = (
            child["state"]
            .get("preparation_usage", {})
            .get(work.get("preparation_token"))
        )
        if work.get("accounting_version") == 2 and usage is not None:
            downloaded = usage
            detail = (
                child["state"]
                .get("preparation_usage_details", {})
                .get(work.get("preparation_token"), {})
            )
            if detail.get("upper_bound"):
                state["download_bytes_is_upper_bound"] = True
        elif work.get("accounting_version") == 2 and not work.get("preparation_needed"):
            downloaded = 0
        elif work.get("preparation_needed") and (
            work.get("accounting_version") == 2
            or child["state"].get("preparation_receipt") != work["preparation_token"]
        ):
            downloaded = max(downloaded, work["bytes"])
            state["download_bytes_is_upper_bound"] = True
        state["download_bytes"] += downloaded
        metrics = state.setdefault("runtime_metrics", {})
        for name, value in child["state"].get("timings", {}).items():
            metrics[name] = metrics.get(name, 0) + max(
                0, value - work.get("base_timings", {}).get(name, 0)
            )
        metrics["assessment_seconds"] = metrics.get("assessment_seconds", 0) + sum(
            a.get("seconds", 0)
            for a in child["state"]["attempts"]
            if a.get("work_token") == work.get("preparation_token")
        )
        for name, status in (
            ("successful_assessment_seconds", "completed"),
            ("failed_assessment_seconds", "failed"),
        ):
            metrics[name] = metrics.get(name, 0) + sum(
                a.get("seconds", 0)
                for a in child["state"]["attempts"]
                if a.get("work_token") == work.get("preparation_token")
                and a.get("status") == status
            )
        metrics["preparation_seconds"] = metrics.get("preparation_seconds", 0) + max(
            0,
            child["state"]["resources"]["preparation_seconds"]
            - work.get("base_preparation_seconds", 0),
        )
        state["observed_download_bytes"] = state.get(
            "observed_download_bytes", 0
        ) + max(
            0, child["state"]["resources"]["media_download_bytes"] - work["base_bytes"]
        )
        captions = child["state"].get("captions")
        if captions:
            state.setdefault("caption_sources", {})[key] = captions.get(
                "source", "unknown"
            )
        inflight.pop(key, None)

    for key, work in list(inflight.items()):
        reconcile(key, work)
    save()
    cache = {}
    for child in store.iter_runs(
        kind="evidence",
        platform=run["platform"],
        identities=[item["canonical_id"] for item in state["shortlist"]],
    ):
        if not child["settings"].get("local_media"):
            cache.setdefault(child["settings"]["identity"]["canonical_id"], []).append(
                child["id"]
            )
    queue = []
    for item in state["shortlist"]:
        key = item["canonical_id"]
        if excluded_creator(item, state.get("creator_rules")):
            state["evidence"][key] = {
                "status": "unavailable",
                "reason": "excluded_creator",
            }
            continue
        if provenance(item)["excluded"]:
            state["evidence"][key] = {
                "status": "unavailable",
                "reason": "excluded_translation_tag",
                "originality_evidence": provenance(item),
            }
            continue
        if state["evidence"].get(key, {}).get("status") == "unavailable":
            continue
        context = assessment_context(item)
        observation = None
        for child_id in reversed(cache.get(key, [])):
            child = store.get(child_id)
            if context and child["settings"].get("originality_context") != context:
                continue
            observation = quality_lookup(store, child["id"])
            if observation:
                break
        if observation:
            state["evidence"][key] = observation
            hits = state.setdefault("cache_hit_ids", [])
            if key not in hits:
                hits.append(key)
        else:
            if state["evidence"].get(key, {}).get("quality") is not None:
                state["evidence"][key] = {
                    "status": "pending",
                    "reason": "Cached quality needs fresh verification.",
                }
            queue.append((item, context))

    def progress():
        state["assessment_index"] = next(
            (
                i
                for i, item in enumerate(state["shortlist"])
                if not (
                    state["evidence"].get(item["canonical_id"], {}).get("quality")
                    is not None
                    or state["evidence"].get(item["canonical_id"], {}).get("status")
                    == "unavailable"
                )
            ),
            len(state["shortlist"]),
        )
        save()

    progress()
    if not queue:
        return
    if remaining() <= 20:
        raise BudgetExhausted(
            "Insufficient time for another bounded worker; progress is checkpointed."
        )
    jar = None
    futures, stop_reason = {}, None
    repairs = set()
    last_heartbeat = time.monotonic()
    with ThreadPoolExecutor(
        max_workers=settings.get("workers", 4),
        thread_name_prefix="broader-vids-evidence",
    ) as executor:
        try:
            while queue or futures:
                while (
                    queue
                    and len(futures) < settings.get("workers", 4)
                    and not stop_reason
                ):
                    if runtime.cancelled.is_set() or cancelled.is_set():
                        stop_reason = "The parent cancelled parallel work."
                        cancelled.set()
                        break
                    calls_left = (
                        settings["max_assessments"]
                        - state["model_calls"]
                        - state.get("planning_model_calls", 0)
                        - sum(w["model_reservation"] for w in futures.values())
                    )
                    bytes_left = (
                        settings["max_bytes"]
                        - state["download_bytes"]
                        - sum(w["bytes"] for w in futures.values())
                    )
                    if remaining() <= 20:
                        if not futures:
                            stop_reason = "Parallel evidence reached its model, media or 30-minute time allowance."
                        break
                    item, context = queue[0]
                    key = item["canonical_id"]
                    allowance = min(128 * 1024 * 1024, max(0, bytes_left))
                    identifier = create_evidence(
                        store,
                        item["url"],
                        use_browser=settings["use_browser"],
                        max_mib=128,
                        max_seconds=min(480, remaining() - 20),
                        originality_context=context,
                        reasoning_effort=settings.get("reasoning_effort"),
                        assessment_model=settings.get("assessment_model"),
                    )
                    child = store.get(identifier)
                    if (
                        child["state"]["model_calls"]
                        >= child["settings"]["max_model_calls"]
                        or child["state"]["submitted_images"]
                        + len(child["state"].get("frames", []))
                        > child["settings"]["max_images"]
                        or child["state"].get("failure", {}).get("category")
                        == "attempt_limit"
                    ) and "augmented" not in child["state"]["assessments"]:
                        queue.pop(0)
                        state["evidence"][key] = {
                            "status": "unavailable",
                            "evidence_run": identifier,
                            "reason": "The per-video assessment allowance is exhausted.",
                        }
                        progress()
                        continue
                    needs_download = child["state"].get("media") is None and child[
                        "state"
                    ]["phase"] not in {"prepared", "completed"}
                    model_reservation = int(
                        "augmented" not in child["state"]["assessments"]
                    )
                    if calls_left < model_reservation or (
                        needs_download and bytes_left < 1024 * 1024
                    ):
                        if not futures:
                            stop_reason = (
                                "Recommendation model-call budget exhausted."
                                if calls_left < model_reservation
                                else "Recommendation media-byte budget exhausted."
                            )
                        break
                    if (
                        needs_download
                        and jar is None
                        and settings["use_browser"]
                        and run["platform"] == "bilibili"
                    ):
                        with bilibili_browser(store.root) as browser:
                            jar = browser_cookie_jar(browser)
                        if not {"SESSDATA", "DedeUserID"}.issubset(
                            {c.name for c in jar}
                        ):
                            raise EvidencePending(
                                "The isolated Bilibili profile needs sign-in."
                            )
                    if not needs_download:
                        allowance = 0
                    queue.pop(0)
                    work = {
                        "accounting_version": 2,
                        "model_reservation": model_reservation,
                        "canonical_id": key,
                        "evidence_run": identifier,
                        "bytes": allowance,
                        "seconds": min(300, remaining() - 20),
                        "deadline": time.monotonic() + min(300, remaining() - 20),
                        "parent_deadline": time.monotonic() + remaining() - 15,
                        "base_calls": child["state"]["model_calls"],
                        "base_bytes": child["state"]["resources"][
                            "media_download_bytes"
                        ],
                        "preparation_needed": child["state"]["phase"]
                        not in {"prepared", "completed"},
                        "preparation_token": uuid.uuid4().hex,
                        "base_timings": child["state"].get("timings", {}),
                        "base_preparation_seconds": child["state"]["resources"][
                            "preparation_seconds"
                        ],
                    }
                    inflight[key] = work
                    save()
                    futures[
                        executor.submit(
                            worker, store.root, work, settings, runtime, jar, cancelled
                        )
                    ] = work
                if not futures:
                    break
                done, _ = wait(futures, timeout=0.25, return_when=FIRST_COMPLETED)
                if remaining() <= 15:
                    stop_reason = (
                        stop_reason
                        or "The shared deadline was reached; unfinished work is checkpointed."
                    )
                    cancelled.set()
                for future in done:
                    work = futures.pop(future)
                    key = work["canonical_id"]
                    try:
                        observation = future.result()
                    except Exception as error:
                        observation = {
                            "status": "pending",
                            "evidence_run": work["evidence_run"],
                            "reason": public_error(error),
                        }
                    reconcile(key, work)
                    state["evidence"][key] = observation
                    if observation.get("status") in {"pending", "unavailable"}:
                        failure = observation.get("failure") or failure_details(
                            EvidencePending(observation["reason"], retryable=True),
                            "worker",
                        )
                        issue = {
                            "canonical_id": key,
                            "evidence_run": work["evidence_run"],
                            "work_token": work["preparation_token"],
                            **failure,
                        }
                        state.setdefault("issues", []).append(issue)
                        if failure["category"] != "cancelled":
                            state.setdefault("first_failure", issue)
                        if observation.get("status") == "pending":
                            if failure["scope"] != "candidate":
                                stop_reason = stop_reason or failure["reason"]
                                cancelled.set()
                                if failure["scope"] == "run":
                                    runtime.cancelled.set()
                            elif (
                                failure["category"] == "validation"
                                and key not in repairs
                            ):
                                repairs.add(key)
                                item = next(
                                    i
                                    for i in state["shortlist"]
                                    if i["canonical_id"] == key
                                )
                                queue.append((item, assessment_context(item)))
                    progress()
                if time.monotonic() - last_heartbeat >= 1:
                    state["active_workers"] = {
                        key: store.get(work["evidence_run"])["state"].get(
                            "stage", "queued"
                        )
                        for work in futures.values()
                        for key in [work["canonical_id"]]
                    }
                    save()
                    last_heartbeat = time.monotonic()
                if stop_reason and not futures:
                    break
        except BaseException:
            cancelled.set()
            raise
    progress()
    state["active_workers"] = {}
    if stop_reason or queue or state["assessment_index"] != len(state["shortlist"]):
        pending_reason = next(
            (
                v["reason"]
                for v in state["evidence"].values()
                if v.get("status") == "pending"
            ),
            None,
        )
        raise EvidencePending(
            stop_reason
            or pending_reason
            or "Parallel evidence remains incomplete; continue the saved run."
        )
