import html
import json

from broader_vids.time_budget import report_limit
from broader_vids.evidence.media import MAX_VIDEO_RETRIES

from broader_vids.evidence.service import atomic_json
from broader_vids.preferences import pending_videos
from broader_vids.creators import policy_summary
from broader_vids.recommendations.timing import live_elapsed
from broader_vids.recommendations.budgets import request_budget


def candidate_failures(store, run):
    state = run["state"]
    observations = state.get("evidence", {})
    identifiers = {
        value["evidence_run"]
        for value in observations.values()
        if value.get("evidence_run")
    }
    if not identifiers:
        return []
    details = {
        identifier: (
            json.loads(attempts or "[]"),
            json.loads(metadata or "[]"),
            json.loads(failure or "null"),
            json.loads(assessment or "null"),
            json.loads(captions or "null"),
            frame_count or 0,
            phase,
            media_present,
        )
        for identifier, attempts, metadata, failure, assessment, captions, frame_count, phase, media_present in store.db.execute(
            "SELECT id,json_extract(state,'$.download_attempts'),json_extract(state,'$.metadata_attempts'),json_extract(state,'$.failure'),json_extract(state,'$.assessments.augmented.card'),json_extract(state,'$.captions'),json_array_length(state,'$.frames'),json_extract(state,'$.phase'),json_type(state,'$.media') FROM runs WHERE id IN ("
            + ",".join("?" for _ in identifiers)
            + ")",
            tuple(identifiers),
        )
    }
    result = []
    cache_hits = set(state.get("cache_hit_ids", []))
    last_issues = {
        issue["canonical_id"]: issue
        for issue in state.get("issues", [])
        if issue.get("canonical_id") and issue.get("category") != "cancelled"
    }
    for item in state.get("shortlist", []):
        key = item["canonical_id"]
        observation = observations.get(key, {})
        (
            attempts,
            metadata,
            saved_failure,
            card,
            captions,
            frame_count,
            phase,
            media_present,
        ) = details.get(
            observation.get("evidence_run"), ([], [], None, None, None, 0, None, None)
        )
        failed_attempts = [
            attempt for attempt in attempts if attempt.get("status") == "failed"
        ]
        failed_metadata = [
            attempt for attempt in metadata if attempt.get("status") == "failed"
        ]
        retries = state.get("candidate_retry_counts", {}).get(key, 0)
        failure = observation.get("failure") or saved_failure or last_issues.get(key)
        recovered = observation.get("quality") is not None
        if recovered and key in cache_hits:
            continue
        if (
            not failed_attempts
            and not failed_metadata
            and not retries
            and (recovered or not failure)
        ):
            continue
        last_transfer = (failed_attempts or failed_metadata or [None])[-1]
        completed_downloads = sum(
            attempt.get("status") == "completed" for attempt in attempts
        )
        if completed_downloads:
            download_outcome = "completed"
        elif failed_attempts:
            download_outcome = "failed"
        elif attempts:
            download_outcome = "unknown"
        elif media_present == "object":
            download_outcome = "completed"
        elif phase in {"prepare", "metadata"}:
            download_outcome = "not_attempted"
        else:
            download_outcome = "unknown"
        category = (failure or {}).get("category")
        if recovered:
            stop_reason = "recovered"
        elif category == "insufficient_evidence":
            stop_reason = "assessment_insufficient_evidence"
        elif category in {"media_size", "media_timeout", "attempt_limit"}:
            stop_reason = "resource_bound"
        elif state.get("candidate_attempt_counts", {}).get(
            key, 0
        ) >= 1 + MAX_VIDEO_RETRIES or (
            failed_attempts
            and attempts[-1].get("status") == "failed"
            and attempts[-1].get("number", 0) >= 1 + MAX_VIDEO_RETRIES
        ):
            stop_reason = "retry_exhausted"
        elif failure and (
            failure.get("retryable") is False
            or not observation.get("status") == "pending"
        ):
            stop_reason = "non_retryable_failure"
        else:
            stop_reason = "unknown"
        criteria = (card or {}).get("criteria", {})
        if not isinstance(criteria, dict):
            criteria = {}
        unknown_criteria = [
            name[:80]
            for name, details in criteria.items()
            if isinstance(details, dict) and details.get("status") == "unknown"
        ][:16]
        unknowns = [
            value[:300]
            for value in (card or {}).get("unknowns", [])[:8]
            if isinstance(value, str)
        ]
        captions = captions if isinstance(captions, dict) else {}
        result.append(
            {
                "canonical_id": key,
                "platform": run["platform"],
                "title": item["title"],
                "url": item["url"],
                "status": "recovered"
                if recovered
                else "skipped"
                if observation.get("status") == "unavailable"
                else "pending",
                "stage": (failure or {}).get("stage")
                or (
                    "download"
                    if failed_attempts
                    else "metadata"
                    if failed_metadata
                    else "worker"
                ),
                "reason": (last_transfer or {}).get("error")
                or (failure or {}).get(
                    "reason", "Candidate step recovered after retry."
                )
                if recovered
                else observation.get("reason")
                or (failure or {}).get("reason", "Evidence unavailable."),
                "download_attempts": len(attempts),
                "download_retries": max(
                    0,
                    len(attempts)
                    - max(1, sum(attempt.get("number") == 1 for attempt in attempts)),
                ),
                "failed_download_attempts": len(failed_attempts),
                "completed_download_attempts": completed_downloads,
                "download_outcome": download_outcome,
                "metadata_attempts": len(metadata),
                "metadata_retries": max(
                    0,
                    len(metadata)
                    - max(1, sum(attempt.get("number") == 1 for attempt in metadata)),
                ),
                "candidate_retries": retries,
                "stop_reason": stop_reason,
                "assessment_summary": {
                    "overall_rationale": (card or {}).get("overall_rationale", "")[
                        :1000
                    ],
                    "unknowns": unknowns,
                    "unknown_criteria": unknown_criteria,
                    "caption_source": captions.get("source", "unknown"),
                    "caption_reason": (captions.get("reason") or "")[:500],
                    "frame_count": frame_count,
                }
                if category == "insufficient_evidence" and isinstance(card, dict)
                else None,
            }
        )
    return result


def recommendation_report(store, identifier):
    run = store.get(identifier)
    if run["settings"].get("kind") != "recommendation":
        raise ValueError("Not a recommendation run.")
    state = run["state"]
    return {
        "run_id": identifier,
        "platform": run["platform"],
        "status": run["status"],
        "reason": run["reason"],
        "settings": {**run["settings"], "max_seconds": report_limit(run["settings"])},
        "request_budget": request_budget(store, run),
        "candidate_failures": candidate_failures(store, run),
        "elapsed_seconds": live_elapsed(run),
        **state,
        "creator_rules": policy_summary(state.get("creator_rules", {})),
        "blacklist_sync": {
            k: v
            for k, v in state.get("blacklist_sync", {}).items()
            if k != "account_id"
        },
        "total_model_calls": state["model_calls"]
        + state.get("planning_model_calls", 0),
        "counts": {
            "fresh_buffer": len(state["buffer"]),
            "eligible_buffer": len(state["buffer"]),
            "raw_candidates_examined": state.get("pool_index", 0),
            "collection_batches": state.get("collection", {}).get(
                "batches_completed", 0
            ),
            "pending_batch_eligible": len(
                state.get("collection", {}).get("active", {}).get("eligible", [])
            ),
            "screening_rejected": len(state.get("screening_rejections", {})),
            "metadata_observed": sum("engagement" in i for i in state["buffer"]),
            "inspection": len(state["shortlist"]),
            "quality_assessed": sum(
                v.get("quality") is not None for v in state["evidence"].values()
            ),
            "pending_reserved": 0,
            "pending_carryover": sum(
                i.get("carryover", False) for i in state["shortlist"]
            ),
            "related_inspection": sum(
                i.get("related", False) for i in state["shortlist"]
            ),
            "evidence_unavailable": sum(
                v.get("status") == "unavailable" for v in state["evidence"].values()
            ),
            "recommended": len(state["selected"]),
            "provisional_preview": len(state.get("preview", [])),
            "quality_cache_hits": len(state.get("cache_hit_ids", [])),
            "caption_unavailable": sum(
                source == "unavailable"
                for source in state.get("caption_sources", {}).values()
            ),
            "caption_accepted": sum(
                source in {"author", "automatic", "local_asr"}
                for source in state.get("caption_sources", {}).values()
            ),
            "pending_now": len(pending_videos(store, run["platform"])),
        },
    }


def write_recommendation_report(store, identifier):
    report = recommendation_report(store, identifier)
    root = store.root / "recommendations" / identifier
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / "report.json", report)
    platform_name = {"youtube": "YouTube", "bilibili": "Bilibili"}[report["platform"]]
    lines = [
        f"# {platform_name} recommendations",
        "",
        f"Run: `{identifier}` · Status: **{report['status']}**",
        "",
        f"{report['counts']['eligible_buffer']} eligible candidates → {report['counts']['inspection']} inspection candidates → {report['counts']['quality_assessed']} assessed → {len(report['selected'])} recommendations.",
        "",
        "Quality estimates are provisional. Topic fit uses curated query tags; authorship remains unverified.",
        "",
    ]
    if report["status"] != "completed":
        lines.extend([f"Work remains: {html.escape(report['reason'])}", ""])
    sync = report.get("blacklist_sync", {})
    if sync.get("status") == "failed":
        lines.extend(
            [
                f"Bilibili blacklist sync failed: {html.escape(sync['error'])} Using saved creator rules. Last complete sync: {sync.get('last_success') or 'never'}.",
                "",
            ]
        )
    displayed = report["selected"] or report.get("preview", [])
    repeated = sum(
        item.get("selection_diversity", {}).get("relaxed", False) for item in displayed
    )
    if repeated:
        lines.extend(
            [
                f"Topic diversity: {repeated} picks repeat earlier topics, queries, or course formats because no nonoverlapping candidate remained.",
                "",
            ]
        )
    for position, item in enumerate(report["selected"], 1):
        evidence = item["quality_evidence"]
        if evidence.get("missing_captions_penalty"):
            lines.append(
                "Visual evidence only: no usable transcript; quality includes a 10-point deduction."
            )
        label = html.escape(item["title"]).replace("[", "\\[").replace("]", "\\]")
        lines.extend(
            [
                f"{position}. [{label}]({item['url']}) — {evidence['duration_seconds'] / 60:.1f} min · score {item['score']:.1f} · quality {evidence['quality']:.1f}",
                f"   {'Pending carryover' if item['carryover'] else 'New'} · {'Familiar' if item['related'] else 'Discovery'} · {html.escape(evidence['summary'])}",
                f"   ID: `{item['canonical_id']}` · Evidence: `{evidence['evidence_run']}`",
                "",
            ]
        )
    if not report["selected"] and report.get("preview"):
        lines.extend(
            [
                "## Provisional preview — not published",
                "",
                "These videos come from the assessed subset. Remaining inspections can change the order.",
                "This preview does not add exposure history or reserve pending slots.",
                "",
            ]
        )
        for position, item in enumerate(report["preview"], 1):
            title = html.escape(item["title"]).replace("[", "\\[").replace("]", "\\]")
            lines.append(
                f"{position}. [{title}]({item['url']}) — score {item['score']:.1f} · quality {item['quality_evidence']['quality']:.1f}"
            )
        lines.append("")
    if report["candidate_failures"]:
        lines.extend(["## Candidate failures and retries", ""])
        for failure in report["candidate_failures"]:
            title = (
                html.escape(failure["title"]).replace("[", "\\[").replace("]", "\\]")
            )
            attempts = failure["download_attempts"]
            counts = []
            if attempts:
                counts.append(f"{attempts} download attempts")
            if failure["metadata_attempts"]:
                counts.append(f"{failure['metadata_attempts']} metadata attempts")
            if failure["candidate_retries"]:
                counts.append(f"{failure['candidate_retries']} candidate retries")
            counts.append(f"download {failure['download_outcome']}")
            transfers = " · " + ", ".join(counts) if counts else ""
            assessment = failure.get("assessment_summary")
            assessment_note = ""
            if assessment:
                assessment_note = (
                    f" Captions: {html.escape(assessment['caption_source'])}"
                    f"; {assessment['frame_count']} frames."
                )
                if assessment["unknown_criteria"]:
                    assessment_note += (
                        " Unknown criteria: "
                        + html.escape(", ".join(assessment["unknown_criteria"]))
                        + "."
                    )
                if assessment["caption_reason"]:
                    assessment_note += " " + html.escape(assessment["caption_reason"])
            lines.append(
                f"- **{failure['status'].capitalize()}**: [{title}]({failure['url']}){transfers} · {html.escape(failure['reason'])} · Stop: {failure['stop_reason']}.{assessment_note}"
            )
        lines.append("")
    elif report.get("first_failure"):
        failure = report["first_failure"]
        lines.extend(["First recorded failure: " + html.escape(failure["reason"]), ""])
    lines.extend(
        [
            "Mark watched: `broader-vids video watched CANONICAL_ID`",
            "",
            "Feedback: `broader-vids rate CANONICAL_ID quality_level high`",
            "",
            "Dismiss: `broader-vids video dismiss CANONICAL_ID`",
            "",
            "These actions are independent. Pending videos compete with new candidates in each new round. Open the local feedback dashboard with `broader-vids dashboard open`.",
            "Creator exclusions apply to new rounds and remove affected entries from eligibility. Current lists and saved ratings remain unchanged.",
        ]
    )
    (root / "digest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root / "digest.md"
