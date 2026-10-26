from collections import Counter
from contextlib import nullcontext
import copy
import json
import time

from deepvids.evidence.assessment import assess_evidence
from deepvids.evidence.browser import bilibili_browser, browser_cookie_jar
from deepvids.discovery.service import create_discovery, execute_discovery
from deepvids.recommendations.engagement import collect_snapshot, fresh
from deepvids.evidence.service import create_evidence, prepare_evidence
from deepvids.evidence.media import EvidencePending, public_error
from deepvids.http import AccessPaused, BudgetExhausted, PublicHttp
from deepvids.preferences import (
    get_profile,
    pending_videos,
    platform_lock,
    video_states,
)
from deepvids.storage import utc_now
from deepvids.policy import Policy
from deepvids.recommendations.ranking import fair_buffer, screen, rank_slate
from deepvids.recommendations.quality import cached_quality
from deepvids.recommendations.originality import provenance, assessment_context
from deepvids.recommendations.timing import recover_elapsed, start_attempt


def create_recommendation(
    store,
    platform,
    discovery_run=None,
    use_browser=False,
    max_seconds=1800,
    max_requests=160,
    max_assessments=52,
    max_mib=1024,
    asr_model=None,
    workers=4,
    reasoning_effort="low",
    *,
    commit=True,
    batch_id=None,
):
    profile = get_profile(store)
    if not profile:
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
    if (
        type(workers) is not int
        or not 1 <= workers <= 4
        or reasoning_effort not in {"low", "medium"}
    ):
        raise ValueError("Use one to four workers and low or medium reasoning.")
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
        "schema": 1,
        "screening_policy_version": 2,
        "worker_policy_version": 1,
        "assessment_model": "gpt-6.1-sol",
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


def _publish(store, run):
    draft = copy.deepcopy(run)
    state = draft["state"]
    for key, observation in state["evidence"].items():
        if observation.get("quality") is not None:
            state["evidence"][key] = cached_quality(
                store, observation["evidence_run"]
            ) or {"status": "unavailable"}
            verified = state["evidence"][key]
            if "canonical_id" in verified and verified["canonical_id"] != key:
                raise ValueError(
                    "Quality evidence belongs to a different playable video."
                )
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        _publish_locked(store, draft)
    run.clear()
    run.update(draft)


def _publish_locked(store, run):
    state = run["state"]
    if get_profile(store)["revision"] != run["settings"]["profile"]["revision"]:
        raise ValueError(
            "The profile changed; start a new run to rescore with the revised profile."
        )
    states = video_states(store, run["platform"])
    pending = pending_videos(store, run["platform"])
    short_ids = {i["canonical_id"] for i in state["shortlist"]}
    if len(pending) > 50 or any(i["canonical_id"] not in short_ids for i in pending):
        raise ValueError(
            "Pending membership changed; a new run is required to honor guaranteed slots."
        )
    candidates = [
        i
        for i in state["shortlist"]
        if not states.get(i["canonical_id"], {}).get("watched")
        and not states.get(i["canonical_id"], {}).get("dismissed")
    ]
    observations = {
        k: v for k, v in state["evidence"].items() if v.get("quality") is not None
    }
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
    )
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
        and len(state["shortlist"]) == 50
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
    if len(pending_ids) > 50:
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
    state = run["state"]
    authenticated = run["settings"]["use_browser"] and run["platform"] == "bilibili"
    with (
        bilibili_browser(store.root) if authenticated else nullcontext(None) as browser
    ):
        if browser:
            jar = browser_cookie_jar(browser)
            if not {"SESSDATA", "DedeUserID"}.issubset({c.name for c in jar}):
                raise EvidencePending(
                    "The isolated Bilibili profile is not signed in; no anonymous retry was made."
                )
            for cookie in jar:
                http.cookies.set_cookie(cookie)
            http.transport = "isolated_browser_http"
        while state["enrichment_index"] < len(state["buffer"]):
            if remaining() <= 0:
                raise BudgetExhausted("Recommendation time budget exhausted.")
            item = state["buffer"][state["enrichment_index"]]
            cached = store.db.execute(
                "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                (item["canonical_id"],),
            ).fetchone()
            snapshot = json.loads(cached[0]) if cached else None
            if (
                not snapshot
                or not fresh(snapshot)
                or (
                    run["platform"] == "bilibili"
                    and snapshot.get("metadata_version") != 2
                )
            ):
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
                    snapshot = None
            if snapshot:
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
            if state.get("metadata_route_paused"):
                break
        if state["buffer"] and not state.get("metadata_route_paused"):
            for item in pending_videos(store, run["platform"]):
                row = store.db.execute(
                    "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                    (item["canonical_id"],),
                ).fetchone()
                if (
                    row
                    and fresh(json.loads(row[0]))
                    and (
                        run["platform"] != "bilibili"
                        or json.loads(row[0]).get("metadata_version") == 2
                    )
                ):
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
                    break
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
    state["shortlist"], state["rejections"] = screen(
        state["buffer"],
        pending,
        video_states(store, run["platform"]),
        run["settings"]["profile"],
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

        def save():
            run["elapsed"] = initial_elapsed + time.monotonic() - started
            store.save(run)

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
                pending = pending_videos(store, run["platform"])
                if len(pending) > 50:
                    raise ValueError(
                        "Pending queue exceeds 50; preserve records and dismiss entries first."
                    )
                state["pending_at_start"] = [i["canonical_id"] for i in pending]
                if len(pending) < 50:
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
                            from deepvids.evidence.assessment import run_codex

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
                    ]
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
                    from deepvids.recommendations.parallel import assess_parallel

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
                    if provenance(item)["excluded"]:
                        state["evidence"][key] = {
                            "status": "unavailable",
                            "reason": "excluded_translation_tag",
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
                        or state["model_calls"] >= 50
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
                            reasoning_effort=settings.get("reasoning_effort", "low"),
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
            _publish(store, run)
        except (BudgetExhausted, EvidencePending, AccessPaused) as error:
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
            run["state"].pop("active_attempt", None)
            save()
        return store.get(identifier)
