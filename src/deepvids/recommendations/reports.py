import html

from deepvids.evidence.service import atomic_json
from deepvids.preferences import pending_videos
from deepvids.recommendations.quality import cached_quality


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
            len(state.get("shortlist", [])) != 50
            or len({i["canonical_id"] for i in state["shortlist"]}) != 50
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
        "elapsed_seconds": run["elapsed"],
        **state,
        "total_model_calls": state["model_calls"]
        + state.get("planning_model_calls", 0),
        "counts": {
            "fresh_buffer": len(state["buffer"]),
            "metadata_observed": sum("engagement" in i for i in state["buffer"]),
            "inspection": len(state["shortlist"]),
            "quality_assessed": sum(
                v.get("quality") is not None for v in state["evidence"].values()
            ),
            "pending_reserved": sum(
                i.get("carryover", False) for i in state["shortlist"]
            ),
            "related_inspection": sum(
                i.get("related", False) for i in state["shortlist"]
            ),
            "evidence_unavailable": sum(
                v.get("status") == "unavailable" for v in state["evidence"].values()
            ),
            "recommended": len(state["selected"]),
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
        f"{report['counts']['fresh_buffer']} fresh candidates → {report['counts']['inspection']} inspection candidates → {report['counts']['quality_assessed']} assessed → {len(report['selected'])} recommendations.",
        "",
        "Quality estimates are provisional. Topic fit uses curated query tags; authorship remains unverified.",
        "",
    ]
    if report["status"] != "completed":
        lines.extend([f"Work remains: {html.escape(report['reason'])}", ""])
    for position, item in enumerate(report["selected"], 1):
        evidence = item["quality_evidence"]
        label = html.escape(item["title"]).replace("[", "\\[").replace("]", "\\]")
        lines.extend(
            [
                f"{position}. [{label}]({item['url']}) — {evidence['duration_seconds'] / 60:.1f} min · score {item['score']:.1f} · quality {evidence['quality']:.1f}",
                f"   {'Pending carryover' if item['carryover'] else 'New'} · {'Familiar' if item['related'] else 'Discovery'} · {html.escape(evidence['summary'])}",
                f"   ID: `{item['canonical_id']}` · Evidence: `{evidence['evidence_run']}`",
                "",
            ]
        )
    lines.extend(
        [
            "Mark watched: `deepvids video watched CANONICAL_ID`",
            "",
            "Feedback: `deepvids video up CANONICAL_ID --kind interest --reason TEXT`",
            "",
            "Dismiss: `deepvids video dismiss CANONICAL_ID`",
            "",
            "These actions are independent. Pending recommendations keep their next-run inspection slots. Use `deepvids serve` for local controls.",
        ]
    )
    (root / "digest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root / "digest.md"
