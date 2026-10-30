import html

from broader_vids.evidence.service import atomic_json
from broader_vids.preferences import pending_videos
from broader_vids.recommendations.quality import cached_quality
from broader_vids.creators import policy_summary
from broader_vids.recommendations.timing import live_elapsed
from broader_vids.recommendations.screening import inspection_limit
from broader_vids.recommendations.budgets import request_budget


def recommendation_readiness(store):
    result = {"youtube": [], "bilibili": []}
    for run in store.iter_runs(kind="recommendation"):
        if (
            run["settings"].get("kind") != "recommendation"
            or run["status"] != "completed"
        ):
            continue
        state = run["state"]
        if (
            len(state.get("shortlist", [])) != inspection_limit(run["settings"])
            or len({i["canonical_id"] for i in state["shortlist"]})
            != inspection_limit(run["settings"])
            or len(state.get("selected", [])) != 10
            or len({i["canonical_id"] for i in state["selected"]}) != 10
        ):
            continue
        try:
            observations = [
                (i, cached_quality(store, i["quality_evidence"]["evidence_run"]))
                for i in state["selected"]
            ]
            valid = all(
                o and o["canonical_id"] == i["canonical_id"] and o["quality"] >= 60
                for i, o in observations
            )
        except (ValueError, OSError, KeyError):
            valid = False
        if valid and state.get("assessment_complete"):
            result[run["platform"]].append(run["id"])
    return {"phase_three_complete": all(result.values()), "verified_runs": result}


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
        "settings": run["settings"],
        "request_budget": request_budget(store, run),
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
            "pending_reserved": 0
            if run["settings"].get("screening_policy_version", 2) >= 3
            else sum(i.get("carryover", False) for i in state["shortlist"]),
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
    if report.get("first_failure"):
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
