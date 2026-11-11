import html

from broader_vids.evidence.service import atomic_json, evidence_root
from broader_vids.policy import Policy
from broader_vids.storage import utc_now


def evidence_report(store, identifier):
    run = store.get(identifier)
    if run["settings"].get("kind") != "evidence":
        raise ValueError("Not an evidence run.")
    state = run["state"]
    from broader_vids.evidence.assessment import current_card

    assessments = {
        mode: {**value, "card": current_card(value["card"])}
        for mode, value in state["assessments"].items()
    }
    return {
        "run_id": identifier,
        "platform": run["platform"],
        "identity": run["settings"]["identity"],
        "status": run["status"],
        "reason": run["reason"],
        "generated_at": utc_now(),
        "metadata": state["metadata"],
        "media": state["media"],
        "captions": state["captions"],
        "caption_excerpt": state.get("caption_excerpt"),
        "frames": state["frames"],
        "assessments": assessments,
        "resources": {
            **state["resources"],
            "submitted_images": state["submitted_images"],
            "model_calls": state["model_calls"],
            "attempts": state["attempts"],
        },
        "duration_prior": Policy().duration_prior(state["media"]["duration_seconds"])
        if state["media"]
        else None,
        "limitations": [
            "Sampled frames and caption excerpts do not establish whole-video coverage.",
            "Transfer counters exclude extractor metadata, inline-caption fetches and model-weight downloads.",
            "Original asset provenance, factual accuracy and absence of filler cannot be established from limited evidence alone.",
        ],
    }


def write_evidence_report(store, identifier):
    report = evidence_report(store, identifier)
    root = evidence_root(store, identifier)
    atomic_json(root / "report.json", report)
    lines = [
        f"# Evidence: {html.escape((report['metadata'] or {}).get('title', report['identity']['video_id']))}",
        "",
        f"Status: {report['status']}. {report['reason']}",
        "",
        f"Source: {report['identity']['url']}",
        "",
    ]
    for mode, assessment in report["assessments"].items():
        lines.extend(
            [
                f"## {mode}",
                "",
                f"Provisional quality: {assessment.get('overall_quality')}; images: {assessment['usage']['submitted_images']}",
                "",
                assessment["card"]["summary"],
                "",
            ]
        )
        for criterion, details in assessment["card"]["criteria"].items():
            lines.append(
                f"- {criterion}: {details['status']}; {details['interpretation']}"
            )
    lines.extend(
        ["", "## Limits", "", *[f"- {text}" for text in report["limitations"]], ""]
    )
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return root / "report.json"
