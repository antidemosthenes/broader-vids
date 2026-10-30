from broader_vids.worker_context import resolve_worker_settings
from collections import Counter
import copy
import json
import time

from broader_vids.evidence.assessment import assess_evidence
from broader_vids.discovery.service import create_discovery, execute_discovery
from broader_vids.recommendations.engagement import (
    collect_snapshot,
    fresh,
    metadata_fresh,
)
from broader_vids.recommendations.collection_policy import default_collection_policy
from broader_vids.recommendations.metadata import metadata_session
from broader_vids.evidence.service import create_evidence, prepare_evidence
from broader_vids.evidence.media import EvidencePending, public_error
from broader_vids.http import AccessPaused, BudgetExhausted, PublicHttp
from broader_vids.preferences import (
    get_profile,
    pending_videos,
    platform_lock,
    video_states,
)
from broader_vids.storage import utc_now
from broader_vids.policy import Policy
from broader_vids.recommendations.budgets import (
    DEFAULT_METADATA_REQUESTS,
    collection_budget_message,
)
from broader_vids.recommendations.ranking import fair_buffer, screen, rank_slate
from broader_vids.recommendations.quality import cached_quality
from broader_vids.recommendations.screening import (
    DEFAULT_INSPECTION_LIMIT,
    inspection_limit,
)
from broader_vids.recommendations.originality import provenance, assessment_context
from broader_vids.recommendations.timing import recover_elapsed, start_attempt
from broader_vids.creators import (
    enrich as enrich_creator,
    excluded as excluded_creator,
    initialize_round,
)


def create_recommendation(
    store,
    platform,
    discovery_run=None,
    use_browser=False,
    max_seconds=1800,
    max_requests=DEFAULT_METADATA_REQUESTS,
    max_assessments=52,
    max_mib=1024,
    asr_model=None,
    workers=4,
    reasoning_effort=None,
    *,
    commit=True,
    batch_id=None,
    assessment_model=None,
):
    profile = get_profile(store)
    if not profile or not profile.get("interests"):
        raise ValueError(
            "Set an onboarding profile with profile set --interest TOPIC before recommending."
        )
    if (
        platform not in {"youtube", "bilibili"}
        or not 0 < max_seconds <= 1800
        or not 1 <= max_requests <= 1000
        or not 0 <= max_assessments <= 52
        or not 1 <= max_mib <= 8192
    ):
        raise ValueError(
            "Recommendation limits are 1,800 seconds, 1–1,000 metadata requests, 0–52 total model calls and 1–8,192 MiB."
        )
    assessment_model, reasoning_effort = resolve_worker_settings(
        assessment_model, reasoning_effort
    )
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use one to four workers.")
    if use_browser and platform != "bilibili":
        raise ValueError(
            "The isolated authenticated browser is only configured for Bilibili."
        )
    if asr_model not in {None, "tiny", "base", "small"}:
        raise ValueError("Unsupported local ASR model.")
    if discovery_run:
        source = store.get(discovery_run)
        if (
            source["platform"] != platform
            or source["settings"].get("kind") != "discovery"
        ):
            raise ValueError("Use a discovery run from the same platform.")
    settings = {
        "kind": "recommendation",
        "token_usage_version": 1,
        "creator_policy_version": 1,
        "schema": 1,
        "screening_policy_version": 4,
        "collection_policy": default_collection_policy(),
        "inspection_limit": DEFAULT_INSPECTION_LIMIT,
        "worker_policy_version": 2,
        "assessment_model": assessment_model,
        "batch_id": batch_id,
        "profile": profile,
        "use_browser": use_browser,
        "max_seconds": max_seconds,
        "max_requests": max_requests,
        "max_assessments": max_assessments,
        "max_bytes": max_mib * 1024 * 1024,
        "asr_model": asr_model,
        "workers": workers,
        "reasoning_effort": reasoning_effort,
        "discovery_initial_planning_calls": len(
            source["state"].get("exploration", {}).get("attempts", [])
        )
        if discovery_run
        else 0,
        "target": 10,
        "quality_threshold": 60,
        "weights": Policy().weights,
    }
    return store.create(
        platform,
        settings,
        {
            "phase": "discovery",
            "discovery_run": discovery_run,
            "buffer": [],
            "enrichment_index": 0,
            "shortlist": [],
            "rejections": {},
            "evidence": {},
            "assessment_index": 0,
            "model_calls": 0,
            "planning_model_calls": 0,
            "download_bytes": 0,
            "selected": [],
            "issues": [],
            "pending_at_start": [],
            "published_at": None,
        },
        commit=commit,
    )


def _publish(store, run, remaining=None):
    from broader_vids.learning.inference import work_scope

    def check():
        if remaining is not None and remaining() <= 0:
            raise BudgetExhausted(
                "The scoring deadline was reached; feedback and evidence are preserved."
            )

    with work_scope(check):
        return _publish_scored(store, run, check)


def _publish_scored(store, run, check):
    from broader_vids.learning.service import ensure_model

    learning = ensure_model(store)
    draft = copy.deepcopy(run)
    state = draft["state"]
    for key, observation in state["evidence"].items():
        check()
        if observation.get("quality") is not None:
            state["evidence"][key] = cached_quality(
                store, observation["evidence_run"]
            ) or {"status": "unavailable"}
            verified = state["evidence"][key]
            if "canonical_id" in verified and verified["canonical_id"] != key:
                raise ValueError(
                    "Quality evidence belongs to a different playable video."
                )
    _publish_locked(store, draft, learning, prepare_only=True)
    check()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        _publish_locked(store, draft, learning)
    run.clear()
    run.update(draft)


def _publish_locked(store, run, learning=None, *, prepare_only=False):
    state = run["state"]
    action_epoch = store.db.execute(
        "SELECT COALESCE(MAX(a.id),0) FROM user_actions a JOIN video_state v USING(canonical_id) WHERE v.platform=?",
        (run["platform"],),
    ).fetchone()[0]
    if prepare_only:
        state["publication_actions_revision"] = action_epoch
    elif state.get("publication_actions_revision") != action_epoch:
        raise EvidencePending(
            "Video actions changed during scoring; continue to rerank before publication.",
            retryable=True,
        )
    if learning is not None:
        from broader_vids.learning.service import change_epoch

        if change_epoch(store) != learning["epoch"]:
            raise EvidencePending(
                "Feedback changed before publication; continue to rerank with the current posterior.",
                retryable=True,
            )
        state["learning_snapshot"] = {
            "id": learning["id"],
            "version": learning["version"],
        }
    if get_profile(store)["revision"] != run["settings"]["profile"]["revision"]:
        raise ValueError(
            "The profile changed; start a new run to rescore with the revised profile."
        )
    pending = pending_videos(store, run["platform"])
    short_ids = {i["canonical_id"] for i in state["shortlist"]}
    states = video_states(store, run["platform"], identifiers=list(short_ids))
    if run["settings"].get("screening_policy_version", 2) < 3 and (
        len(pending) > 50 or any(i["canonical_id"] not in short_ids for i in pending)
    ):
        raise ValueError(
            "Pending membership changed; a new run is required to honor guaranteed slots."
        )
    candidates = [
        i
        for i in state["shortlist"]
        if not states.get(i["canonical_id"], {}).get("watched")
        and not states.get(i["canonical_id"], {}).get("dismissed")
        and not excluded_creator(i, state.get("creator_rules"))
    ]
    observations = {
        k: v for k, v in state["evidence"].items() if v.get("quality") is not None
    }
    if prepare_only:
        recent = Counter()
        rows = store.db.execute(
            "SELECT v.payload FROM recommendation_exposures e JOIN video_state v USING(canonical_id) WHERE v.platform=? ORDER BY e.created_at DESC LIMIT 100",
            (run["platform"],),
        ).fetchall()
        for row in rows:
            item = json.loads(row[0])
            recent[item.get("creator_id") or item["canonical_id"]] += 1
        state["selected"] = rank_slate(
            candidates,
            observations,
            run["settings"]["profile"],
            run["settings"]["weights"],
            recent=recent,
            learning=learning,
            creator_rules=state.get("creator_rules"),
            policy_version=run["settings"].get("screening_policy_version", 2),
        )
        return
    state["pending_at_publication"] = len(pending)
    state["published_at"] = utc_now()
    state["assessment_complete"] = all(
        i["canonical_id"] in observations
        or state["evidence"].get(i["canonical_id"], {}).get("status") == "unavailable"
        for i in state["shortlist"]
    )
    state["phase"] = "published"
    complete = (
        len(state["selected"]) == 10
        and state["assessment_complete"]
        and len(state["shortlist"]) == inspection_limit(run["settings"])
    )
    run.update(
        status="completed" if complete else "partial",
        reason="Manual recommendations published with verified quality evidence."
        if complete
        else "Provisional recommendations cover the assessed subset; remaining evidence/eligibility limitations are recorded."
        if state["selected"]
        else "No eligible assessed videos; partial work is saved.",
    )
    if any(i["canonical_id"] not in short_ids for i in state["selected"]):
        raise ValueError("Selected item is outside the inspection list.")
    pending_ids = {i["canonical_id"] for i in pending} | {
        i["canonical_id"] for i in state["selected"]
    }
    if run["settings"].get("screening_policy_version", 2) < 3 and len(pending_ids) > 50:
        raise ValueError("Publication would exceed guaranteed pending capacity.")
    with store.db:
        for position, item in enumerate(state["selected"], 1):
            store.db.execute(
                "INSERT INTO video_state VALUES(?,?,?,0,0,1,?) ON CONFLICT(canonical_id) DO UPDATE SET payload=excluded.payload,recommended=1,updated_at=excluded.updated_at",
                (
                    item["canonical_id"],
                    run["platform"],
                    json.dumps(item, ensure_ascii=False),
                    utc_now(),
                ),
            )
            store.db.execute(
                "INSERT INTO recommendation_exposures VALUES(?,?,?,?)",
                (run["id"], item["canonical_id"], position, state["published_at"]),
            )
        store.db.execute(
            "UPDATE runs SET status=?,reason=?,state=?,elapsed=?,updated_at=? WHERE id=?",
            (
                run["status"],
                run["reason"],
                json.dumps(state, ensure_ascii=False),
                run["elapsed"],
                utc_now(),
                run["id"],
            ),
        )


def publish_recommendation(store, identifier, allow_incomplete=False):
    run = store.get(identifier)
    if run["settings"].get("kind") != "recommendation":
        raise ValueError("Not a recommendation run.")
    with store.lease(platform_lock(run["platform"])), store.lease(identifier):
        run = store.get(identifier)
        if run["state"]["phase"] == "published":
            return run
        if run["state"]["phase"] != "assess":
            raise ValueError("Finish screening before publishing recommendations.")
        if not allow_incomplete and run["state"]["assessment_index"] != len(
            run["state"]["shortlist"]
        ):
            raise ValueError(
                "Assessment remains incomplete; use --allow-incomplete for a clearly labeled provisional digest."
            )
        _publish(store, run)
    return store.get(identifier)


def enrich_buffer(store, run, http, remaining, save):
    if run["settings"].get("screening_policy_version", 2) >= 3:
        from broader_vids.recommendations.collection import collect_eligible_buffer

        collect_eligible_buffer(
            store, run, http, remaining, save, snapshot_collector=collect_snapshot
        )
        pending = [
            item
            for item in run["state"]["buffer"]
            if item["canonical_id"]
            in {
                entry["canonical_id"]
                for entry in pending_videos(store, run["platform"])
            }
        ]
        run["state"]["shortlist"], rejected = screen(
            run["state"]["buffer"],
            pending,
            video_states(store, run["platform"]),
            run["settings"]["profile"],
            run["state"].get("creator_rules"),
            policy_version=run["settings"].get("screening_policy_version", 2),
            limit=inspection_limit(run["settings"]),
        )
        run["state"]["rejections"] = {
            **run["state"].get("screening_rejections", {}),
            **rejected,
        }
        limit = inspection_limit(run["settings"])
        if len(run["state"]["shortlist"]) != limit:
            raise EvidencePending(
                f"Fewer than {limit} eligible screening entries remain; quality assessment has not started."
            )
        run["state"]["phase"] = "assess"
        save()
        return
    state = run["state"]
    authenticated = run["settings"]["use_browser"] and run["platform"] == "bilibili"
    state.pop("metadata_route_paused", None)
    with metadata_session(store, http, authenticated):
        while state["enrichment_index"] < len(state["buffer"]):
            if remaining() <= 0:
                raise BudgetExhausted("Recommendation time budget exhausted.")
            item = state["buffer"][state["enrichment_index"]]
            cached = store.db.execute(
                "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                (item["canonical_id"],),
            ).fetchone()
            snapshot = json.loads(cached[0]) if cached else None
            if not snapshot or not metadata_fresh(snapshot, run["platform"]):
                try:
                    snapshot = collect_snapshot(http, item)
                    with store.db:
                        store.db.execute(
                            "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                            (item["canonical_id"], json.dumps(snapshot)),
                        )
                except AccessPaused as error:
                    state["issues"].append(
                        {
                            "canonical_id": item["canonical_id"],
                            "stage": "metadata",
                            "reason": public_error(error),
                        }
                    )
                    state["metadata_route_paused"] = True
                    save()
                    raise
            if snapshot:
                enrich_creator(item, snapshot)
                item["engagement"] = snapshot
                if run["platform"] == "youtube" and snapshot.get("duration_seconds"):
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
            state["enrichment_index"] += 1
            save()
        if state["buffer"]:
            for item in pending_videos(store, run["platform"]):
                row = store.db.execute(
                    "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                    (item["canonical_id"],),
                ).fetchone()
                if row and metadata_fresh(json.loads(row[0]), run["platform"]):
                    continue
                if remaining() <= 0:
                    raise BudgetExhausted(
                        "Recommendation time budget exhausted during pending-count refresh."
                    )
                try:
                    snapshot = collect_snapshot(http, item)
                    with store.db:
                        store.db.execute(
                            "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                            (item["canonical_id"], json.dumps(snapshot)),
                        )
                except AccessPaused as error:
                    state["issues"].append(
                        {
                            "canonical_id": item["canonical_id"],
                            "stage": "pending_metadata",
                            "reason": public_error(error),
                        }
                    )
                    state["metadata_route_paused"] = True
                    save()
                    raise
    pending = pending_videos(store, run["platform"])
    for item in pending:
        row = store.db.execute(
            "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
            (item["canonical_id"],),
        ).fetchone()
        snapshot = json.loads(row[0]) if row else item.get("engagement", {})
        item["engagement"] = (
            snapshot
            if fresh(snapshot)
            else {"stale": True, "observed_at": snapshot.get("observed_at")}
        )
        enrich_creator(item, snapshot)
    blocked_pending = [
        item["canonical_id"]
        for item in pending
        if excluded_creator(item, state.get("creator_rules"))
    ]
    if blocked_pending:
        with store.db:
            store.db.executemany(
                "UPDATE video_state SET recommended=0,updated_at=? WHERE canonical_id=?",
                [(utc_now(), key) for key in blocked_pending],
            )
        state["creator_released_pending"] = sorted(
            set(state.get("creator_released_pending", [])) | set(blocked_pending)
        )
        pending = [
            item for item in pending if item["canonical_id"] not in blocked_pending
        ]
    state["shortlist"], state["rejections"] = screen(
        state["buffer"],
        pending,
        video_states(store, run["platform"]),
        run["settings"]["profile"],
        state.get("creator_rules"),
        policy_version=run["settings"].get("screening_policy_version", 2),
        limit=inspection_limit(run["settings"]),
    )
    state["phase"] = "assess"
    save()


def execute_recommendation(
    store,
    identifier,
    http_factory=PublicHttp,
    prepare=prepare_evidence,
    assess=assess_evidence,
    *,
    max_seconds=None,
    runtime=None,
    batch_id=None,
    progress=None,
):
    initial = store.get(identifier)
    if initial["settings"].get("kind") != "recommendation":
        raise ValueError("Not a recommendation run.")
    if initial["settings"].get("batch_id") not in {None, batch_id}:
        raise ValueError(
            f"Continue batch {initial['settings']['batch_id']} to preserve its shared budget."
        )
    with store.lease(platform_lock(initial["platform"])), store.lease(identifier):
        run = store.get(identifier)
        state, settings = run["state"], run["settings"]
        recover_elapsed(run)
        if state["phase"] == "published":
            store.save(run)
            return run
        started, initial_elapsed = time.monotonic(), run["elapsed"]
        limit = min(
            settings["max_seconds"],
            1800,
            initial_elapsed + max_seconds if max_seconds is not None else float("inf"),
        )
        limit -= min(20, limit * 0.05)

        def remaining():
            if runtime is not None and runtime.cancelled.is_set():
                return 0
            elapsed = time.monotonic() - started
            return min(
                limit - initial_elapsed - elapsed,
                (max_seconds - elapsed) if max_seconds is not None else float("inf"),
            )

        last_progress = [0]
        phase_clock = [state["phase"], started]

        def save():
            now = time.monotonic()
            current_state = run["state"]
            durations = current_state.setdefault("phase_seconds", {})
            durations[phase_clock[0]] = (
                durations.get(phase_clock[0], 0) + now - phase_clock[1]
            )
            phase_clock[:] = current_state["phase"], now
            run["elapsed"] = initial_elapsed + time.monotonic() - started
            store.save(run)
            if progress is not None and time.monotonic() - last_progress[0] >= 5:
                progress(
                    {
                        "platform": run["platform"],
                        "phase": current_state["phase"],
                        "elapsed_seconds": round(run["elapsed"], 1),
                        "eligible": len(current_state["buffer"]),
                        "inspection": len(current_state["shortlist"]),
                        "assessed": sum(
                            v.get("quality") is not None
                            for v in current_state["evidence"].values()
                        ),
                    }
                )
                last_progress[0] = time.monotonic()

        start_attempt(run)
        run.update(status="running", reason="")
        save()
        try:
            if get_profile(store)["revision"] != settings["profile"]["revision"]:
                raise ValueError(
                    "The profile changed; start a new run to use the current interests."
                )
            if remaining() <= 0:
                raise BudgetExhausted(
                    "Recommendation time budget exhausted; no automatic budget reset."
                )
            if state["phase"] == "discovery":
                initialize_round(store, run, remaining)
                pending = pending_videos(store, run["platform"])
                if (
                    settings.get("screening_policy_version", 2) < 3
                    and len(pending) > 50
                ):
                    raise ValueError(
                        "Pending queue exceeds 50; preserve records and dismiss entries first."
                    )
                state["pending_at_start"] = [i["canonical_id"] for i in pending]
                if (
                    settings.get("screening_policy_version", 2) >= 3
                    or len(pending) < 50
                ):
                    if not state["discovery_run"]:
                        state["discovery_run"] = create_discovery(
                            store,
                            run["platform"],
                            max_requests=min(60, settings["max_requests"]),
                            max_seconds=min(600, remaining()),
                            max_planning_calls=min(
                                2,
                                max(
                                    0,
                                    settings["max_assessments"] - state["model_calls"],
                                ),
                            ),
                            creator_rules=state.get("creator_rules"),
                            assessment_model=settings["assessment_model"],
                            reasoning_effort=settings["reasoning_effort"],
                        )
                        save()
                    source = store.get(state["discovery_run"])
                    state["planning_model_calls"] = max(
                        0,
                        len(source["state"].get("exploration", {}).get("attempts", []))
                        - settings.get("discovery_initial_planning_calls", 0),
                    )
                    if source["state"]["phase"] != "finished":
                        if remaining() <= 0:
                            raise BudgetExhausted(
                                "Recommendation time budget exhausted during discovery."
                            )
                        model_options = {}
                        if runtime is not None:
                            from broader_vids.evidence.assessment import run_codex

                            def planning_runner(
                                prompt, schema, output, images, timeout
                            ):
                                with runtime.slot(
                                    runtime.models, remaining, runtime.cancelled
                                ):
                                    return run_codex(
                                        prompt,
                                        schema,
                                        output,
                                        images,
                                        min(timeout, remaining()),
                                        cancelled=runtime.cancelled,
                                        model=settings["assessment_model"],
                                        effort=settings["reasoning_effort"],
                                    )

                            model_options["model_runner"] = planning_runner
                        execute_discovery(
                            store,
                            source["id"],
                            max_seconds=remaining(),
                            max_new_planning_calls=max(
                                0,
                                settings["max_assessments"]
                                - state["model_calls"]
                                - state["planning_model_calls"],
                            ),
                            **model_options,
                            usage_owner=run["id"],
                            cancel_event=runtime.cancelled if runtime else None,
                        )
                        source = store.get(source["id"])
                        state["planning_model_calls"] = max(
                            0,
                            len(
                                source["state"]
                                .get("exploration", {})
                                .get("attempts", [])
                            )
                            - settings.get("discovery_initial_planning_calls", 0),
                        )
                    if source["state"]["phase"] != "finished":
                        raise EvidencePending(
                            "Discovery is incomplete; continue its source run before continuing recommendations."
                        )
                    states = video_states(store, run["platform"])
                    available = [
                        i
                        for i in source["state"]["candidates"].values()
                        if not states.get(i["canonical_id"], {}).get("watched")
                        and not states.get(i["canonical_id"], {}).get("dismissed")
                        and not states.get(i["canonical_id"], {}).get("pending")
                        and not excluded_creator(i, state.get("creator_rules"))
                    ]
                    if settings.get("screening_policy_version", 2) >= 3:
                        state["candidate_pool"] = fair_buffer(
                            [*available, *pending], 1000
                        )
                        state["pool_index"] = 0
                        state["buffer"] = []
                        state["screening_rejections"] = {}
                    else:
                        state["buffer"] = fair_buffer(available, 100)
                state["phase"] = "enrich"
                save()
            http = http_factory(
                store,
                {**run, "settings": {**settings, "max_seconds": limit}},
                anonymous_session=True,
            )
            http.cancelled = runtime.cancelled if runtime else None
            if state["phase"] == "enrich":
                enrich_buffer(store, run, http, remaining, save)
            if state["phase"] == "assess":
                if prepare is prepare_evidence and assess is assess_evidence:
                    from broader_vids.recommendations.parallel import assess_parallel

                    assess_parallel(
                        store,
                        run,
                        save,
                        remaining,
                        runtime,
                        quality_lookup=cached_quality,
                    )
                cache = {}
                for previous in store.iter_runs(
                    kind="evidence",
                    platform=run["platform"],
                    identities=[item["canonical_id"] for item in state["shortlist"]],
                ):
                    if (
                        previous["settings"].get("kind") == "evidence"
                        and previous["platform"] == run["platform"]
                        and not previous["settings"].get("local_media")
                    ):
                        cache.setdefault(
                            previous["settings"]["identity"]["canonical_id"], []
                        ).append(previous["id"])
                while state["assessment_index"] < len(state["shortlist"]):
                    if remaining() < 5:
                        raise BudgetExhausted(
                            "Recommendation time budget exhausted; assessment cursor is saved."
                        )
                    item = state["shortlist"][state["assessment_index"]]
                    key, observation = item["canonical_id"], None
                    if excluded_creator(item, state.get("creator_rules")):
                        state["evidence"][key] = {
                            "status": "unavailable",
                            "reason": "excluded_creator",
                        }
                        state["assessment_index"] += 1
                        save()
                        continue
                    original = provenance(item, settings["profile"])
                    if original["excluded"]:
                        state["evidence"][key] = {
                            "status": "unavailable",
                            "reason": original["reason"],
                        }
                        state["assessment_index"] += 1
                        save()
                        continue
                    context = assessment_context(item)
                    for previous in reversed(cache.get(key, [])):
                        if (
                            context
                            and store.get(previous)["settings"].get(
                                "originality_context"
                            )
                            != context
                        ):
                            continue
                        observation = cached_quality(store, previous)
                        if observation:
                            break
                    if not observation and (
                        state["model_calls"] + state.get("planning_model_calls", 0)
                        >= settings["max_assessments"]
                    ):
                        raise BudgetExhausted(
                            "Recommendation model-call budget exhausted; no automatic retry or score substitution."
                        )
                    if not observation:
                        bytes_left = settings["max_bytes"] - state["download_bytes"]
                        if bytes_left < 1024 * 1024:
                            raise BudgetExhausted(
                                "Recommendation media-byte budget exhausted."
                            )
                        evidence_id = create_evidence(
                            store,
                            item["url"],
                            use_browser=settings["use_browser"],
                            max_mib=min(128, bytes_left // (1024 * 1024)),
                            max_seconds=min(600, remaining()),
                            originality_context=context,
                            reasoning_effort=settings.get("reasoning_effort"),
                            assessment_model=settings.get("assessment_model"),
                        )
                        before = store.get(evidence_id)["state"]["resources"][
                            "media_download_bytes"
                        ]
                        try:
                            if remaining() <= 0:
                                raise BudgetExhausted(
                                    "Recommendation time budget exhausted before evidence preparation."
                                )
                            prepared = prepare(
                                store,
                                evidence_id,
                                settings["asr_model"],
                                max_seconds=remaining(),
                                max_new_bytes=bytes_left,
                            )
                            if (
                                prepared["status"]
                                in {"prepared", "completed", "assessed"}
                                and "augmented" not in prepared["state"]["assessments"]
                            ):
                                if remaining() <= 0:
                                    raise BudgetExhausted(
                                        "Recommendation time budget exhausted before assessment."
                                    )
                                state["model_calls"] += 1
                                save()
                                assess(
                                    store,
                                    evidence_id,
                                    "augmented",
                                    **(
                                        {"usage_owner": run["id"]}
                                        if assess is assess_evidence
                                        else {}
                                    ),
                                    max_seconds=remaining(),
                                )
                            observation = cached_quality(store, evidence_id)
                        except (EvidencePending, ValueError, OSError) as error:
                            state["issues"].append(
                                {
                                    "canonical_id": key,
                                    "stage": "evidence",
                                    "reason": public_error(error),
                                }
                            )
                        finally:
                            state["download_bytes"] += max(
                                0,
                                store.get(evidence_id)["state"]["resources"][
                                    "media_download_bytes"
                                ]
                                - before,
                            )
                        if not observation:
                            failed = store.get(evidence_id)
                            if failed["state"].get("retryable_failure"):
                                state["evidence"][key] = {
                                    "status": "pending",
                                    "evidence_run": evidence_id,
                                    "reason": failed["reason"],
                                }
                                raise EvidencePending(
                                    "Evidence collection needs retry or access review; the current candidate and cursor are preserved."
                                )
                            null_cards = [
                                a["card"]
                                for a in failed["state"]["assessments"].values()
                                if a["overall_quality"] is None
                            ]
                            reason = (
                                "Quality remains unknown: " + null_cards[-1]["summary"]
                                if null_cards
                                else failed["reason"]
                            )
                            state["evidence"][key] = {
                                "status": "unavailable",
                                "evidence_run": evidence_id,
                                "reason": reason,
                            }
                            reason = store.get(evidence_id)["reason"].casefold()
                            if any(
                                s in reason
                                for s in (
                                    "http error 412",
                                    "http error 403",
                                    "http error 429",
                                    "captcha",
                                    "sign in to confirm",
                                    "security check",
                                )
                            ):
                                raise EvidencePending(
                                    "Content access requires review; the affected evidence route has stopped."
                                )
                    if observation:
                        state["evidence"][key] = observation
                    state["assessment_index"] += 1
                    save()
            if remaining() <= 0:
                raise BudgetExhausted(
                    "The parent stopped before publication; progress is checkpointed."
                )
            run["elapsed"] = initial_elapsed + time.monotonic() - started
            _publish(store, run, remaining)
        except (BudgetExhausted, EvidencePending, AccessPaused) as error:
            from broader_vids.evidence.failures import failure_details

            if (
                isinstance(error, BudgetExhausted)
                and error.resource == "http_requests"
                and state["phase"] == "enrich"
                and settings.get("screening_policy_version", 2) >= 3
            ):
                error = BudgetExhausted(
                    collection_budget_message(store, run), resource="http_requests"
                )
            failure = failure_details(error, state["phase"])
            state.setdefault("first_failure", failure)
            run.update(status="paused", reason=str(error))
            save()
        except KeyboardInterrupt:
            run.update(
                status="paused",
                reason="Interrupted; durable work and pending queue are preserved.",
            )
            save()
        except Exception as error:
            run.update(status="paused", reason=public_error(error))
            save()
            raise
        finally:
            if run["status"] == "paused" and state["phase"] == "assess":
                from broader_vids.recommendations.preview import update_preview

                update_preview(store, run)
            run["state"]["active_workers"] = {}
            run["state"].pop("active_attempt", None)
            save()
        return store.get(identifier)
