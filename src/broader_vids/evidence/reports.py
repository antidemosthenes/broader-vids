from broader_vids.learning.topic_labels import readable_card_topics
import html
import json
from pathlib import Path

from broader_vids.evidence.service import atomic_json, evidence_root, verify_bundle
from broader_vids.policy import Policy
from broader_vids.storage import utc_now


def evidence_checks(store, run):
    state = run["state"]
    checks = {
        "completed_pair": run["status"] == "completed"
        and {"baseline", "augmented"}.issubset(state["assessments"]),
        "network_resolved_playable_unit": bool(
            state["metadata"]
            and state["metadata"].get("source") == "yt_dlp_playable_unit"
        ),
        "within_resource_limits": 0 < len(state["frames"]) <= 8
        and state["submitted_images"] <= run["settings"].get("max_images", 8)
        and state["model_calls"] <= run["settings"].get("max_model_calls", 2),
        "verified_bundle": False,
        "valid_scorecards": False,
    }
    try:
        verify_bundle(store, run)
        checks["verified_bundle"] = True
        from broader_vids.evidence.assessment import validate_card

        cues = json.loads(
            Path(state["caption_excerpt"]["path"]).read_text(encoding="utf-8")
        )
        for mode in ("baseline", "augmented"):
            assessment = state["assessments"][mode]
            frames = state["frames"] if mode == "augmented" else []
            expected = {
                "media_sha256": state["media"]["sha256"],
                "caption_sha256": state["caption_excerpt"]["sha256"],
                "frame_sha256": [frame["sha256"] for frame in frames],
            }
            if (
                assessment["evidence_identity"] != expected
                or assessment["overall_quality"] is None
            ):
                return checks
            validate_card(
                readable_card_topics(assessment["card"]),
                {cue["id"] for cue in cues} | {frame["id"] for frame in frames},
                mode,
                assessment["rubric_version"],
            )
        checks["valid_scorecards"] = True
    except (ValueError, KeyError, TypeError, OSError):
        pass
    return checks


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
