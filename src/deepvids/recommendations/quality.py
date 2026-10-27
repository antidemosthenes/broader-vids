import json
from pathlib import Path

from deepvids.evidence.assessment import validate_card
from deepvids.evidence.service import verify_bundle
from deepvids.policy import Policy
from deepvids.recommendations.originality import context_identity


def cached_quality(store, evidence_run):
    run = store.get(evidence_run)
    if run["settings"].get("local_media") or run["settings"].get("kind") != "evidence":
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
        ) not in {1, 2, 3}:
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
        expected = round(observation["card"]["content_quality"] + duration_prior, 2)
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
            "criteria": card["criteria"],
            "audience_level": card.get("audience", {}).get("level", "unknown"),
            "audience_assessment": card.get("audience"),
            "quality_interval": [
                round(card["quality_low"] + duration_prior, 2),
                round(card["quality_high"] + duration_prior, 2),
            ],
        }
    return None
