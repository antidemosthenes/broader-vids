import json
from pathlib import Path

from broader_vids.evidence.assessment import validate_card
from broader_vids.evidence.service import verify_bundle
from broader_vids.policy import Policy
from broader_vids.evidence.scoring import adjusted_quality
from broader_vids.recommendations.originality import context_identity


def cached_quality(store, evidence_run):
    run = store.get(evidence_run)
    if run["settings"].get("local_media") or run["settings"].get("kind") not in {
        "evidence",
        "learning_evidence",
    }:
        return None
    state = run["state"]
    if (
        not state.get("media")
        or not state.get("captions")
        or not state.get("metadata")
        or state["metadata"].get("source") != "yt_dlp_playable_unit"
    ):
        return None
    for mode in ("augmented", "baseline"):
        observation = state["assessments"].get(mode)
        if not observation or observation["overall_quality"] is None:
            continue
        verify_bundle(store, run)
        frames = state["frames"] if mode == "augmented" else []
        identity = {
            "media_sha256": state["media"]["sha256"],
            "caption_sha256": state["caption_excerpt"]["sha256"],
            "frame_sha256": [f["sha256"] for f in frames],
            **context_identity(run["settings"].get("originality_context")),
        }
        if observation["evidence_identity"] != identity or observation.get(
            "rubric_version"
        ) not in {1, 2, 3, 4}:
            raise ValueError("Cached scorecard evidence identity or rubric differs.")
        cues = json.loads(
            Path(state["caption_excerpt"]["path"]).read_text(encoding="utf-8")
        )
        card = validate_card(
            observation["card"],
            {c["id"] for c in cues}
            | {f["id"] for f in frames}
            | {
                s["id"]
                for s in (run["settings"].get("originality_context") or {}).get(
                    "signals", []
                )
            },
            mode,
            observation["rubric_version"],
        )
        duration = state["media"]["duration_seconds"]
        if not 0 < duration <= 5400:
            return None
        duration_prior = Policy().duration_prior(duration)
        penalty = observation.get("missing_captions_penalty", 0)
        expected = adjusted_quality(
            observation["card"]["content_quality"], duration_prior, penalty
        )
        if observation["overall_quality"] != expected:
            raise ValueError(
                "Cached overall quality does not match its validated scorecard and duration prior."
            )
        return {
            "evidence_run": evidence_run,
            "canonical_id": run["settings"]["identity"]["canonical_id"],
            "quality": expected,
            "mode": mode,
            "duration_seconds": duration,
            "summary": card["summary"],
            "unknowns": card["unknowns"],
            "content_quality": card["content_quality"],
            "missing_captions_penalty": penalty,
            "caption_status": state["captions"]["source"],
            "criteria": card["criteria"],
            "topics": card.get("topics"),
            "audience_level": card.get("audience", {}).get("level", "unknown"),
            "audience_assessment": card.get("audience"),
            "quality_interval": [
                adjusted_quality(card["quality_low"], duration_prior, penalty),
                adjusted_quality(card["quality_high"], duration_prior, penalty),
            ],
        }
    return None
