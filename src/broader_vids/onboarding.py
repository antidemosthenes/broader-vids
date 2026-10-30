from datetime import datetime
from pathlib import Path
import hashlib
import json

from broader_vids.evidence.service import atomic_json
from broader_vids.storage import utc_now


PLATFORMS = ("youtube", "bilibili", "both")
LOCK = hashlib.sha256(b"onboarding-settings").hexdigest()[:32]


def status(root):
    path = Path(root) / "onboarding.json"
    if not path.exists():
        return {"version": 1, "default_platform": None, "completed_at": None}
    try:
        if path.stat().st_size > 4096:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] != 1
            or value.get("default_platform") not in PLATFORMS
            or "completed_at" not in value
            or (
                value.get("completed_at") is not None
                and not isinstance(value["completed_at"], str)
            )
        ):
            raise ValueError
        if (
            value["completed_at"] is not None
            and datetime.fromisoformat(value["completed_at"]).tzinfo is None
        ):
            raise ValueError
        return value
    except (ValueError, UnicodeError):
        raise ValueError(
            "Invalid onboarding.json. Restore this file or remove it to repeat guided setup. Your preferences remain in the database."
        ) from None


def configure(store, platform):
    if platform not in PLATFORMS:
        raise ValueError("Choose youtube, bilibili, or both.")
    with store.lease(LOCK):
        previous = status(store.root)
        value = {
            "version": 1,
            "default_platform": platform,
            "completed_at": previous["completed_at"]
            if previous["default_platform"] == platform
            else None,
        }
        atomic_json(store.root / "onboarding.json", value)
    return value


def complete(store, *, model=None, effort=None, asr_model=None):
    from broader_vids.preflight import preflight

    if asr_model not in (None, "tiny", "base", "small"):
        raise ValueError("Choose tiny, base, or small for requested transcription.")
    previous = status(store.root)
    report = preflight(
        store,
        previous["default_platform"],
        model=model,
        effort=effort,
        asr=bool(asr_model),
        require_setup=False,
        check_feedback=False,
    )
    if report["status"] != "ready":
        return report
    with store.lease(LOCK):
        if status(store.root) != previous:
            raise ValueError(
                "Onboarding changed during verification. Repeat onboarding complete."
            )
        value = {**previous, "completed_at": utc_now()}
        atomic_json(store.root / "onboarding.json", value)
    return {
        **report,
        "onboarding": value,
        "self_check": "passed",
        "next_steps": next_steps(asr_model=asr_model),
    }


def next_steps(*, asr_model=None):
    actions = {
        "dashboard": {
            "description": "The local dashboard lets you edit broader interests, rate videos, manage exclusions, and use optional calibration. Preferences combines interest editing with import and export. Token usage reports remain separate.",
            "request": "Use Broader Vids to open my dashboard.",
            "skill_name": "broader-vids-dashboard",
        },
        "recommendations": {
            "description": "Request recommendations for the saved platforms. Results appear in chat, and ratings improve later recommendations.",
            "request": "Use Broader Vids to recommend videos for my saved platforms.",
            "skill_name": "broader-vids",
        },
        "reset": {
            "description": "Reset learned preferences, weights, ratings, exclusions, histories, results, evidence, and recovery files. Keep installed tools and Bilibili sign-in.",
            "request": "Use Broader Vids to reset my preferences and histories for a fresh start.",
            "skill_name": "broader-vids-reset",
        },
        "clean": {
            "description": "Remove the complete managed data directory, including tools and sign-in. Keep the plugin installed. Preview before applying.",
            "request": "Use Broader Vids to remove all of its local data.",
            "skill_name": "broader-vids-clean",
        },
    }
    if asr_model:
        actions["recommendations"].update(
            request="Use Broader Vids to recommend videos for my saved platforms with transcription.",
            transcription_model=asr_model,
        )
    for action in actions.values():
        action["skill_invocation"] = "$" + action["skill_name"]
    return actions
