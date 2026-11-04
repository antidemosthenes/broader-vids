from datetime import datetime
from pathlib import Path
import hashlib
import json
import re

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


def setup_questions(store):
    from broader_vids.preferences import get_profile

    previous = status(store.root)
    profile = get_profile(store)

    def choice(title, options):
        return {
            "input": "choice",
            "title": title,
            "options": [{"value": value, "label": label} for value, label in options],
        }

    sources = [
        ("preset", "Use calibrated starter preferences"),
        ("manual", "Start fresh and enter interests"),
        ("import", "Import my preference file"),
    ]
    return {
        "version": 1,
        "requires_explicit_answer": True,
        "saved_platform": previous["default_platform"],
        "setup_complete": previous["completed_at"] is not None,
        "questions": {
            "platform": choice(
                "Which platforms should Broader Vids recommend?",
                [("youtube", "YouTube"), ("bilibili", "Bilibili"), ("both", "Both")],
            ),
            **(
                {
                    "existing_preferences": choice(
                        "Keep your saved preferences or change them?",
                        [
                            ("keep", "Keep saved preferences"),
                            ("change", "Change preferences"),
                        ],
                    )
                }
                if profile and profile.get("interests")
                else {}
            ),
            "preference_source": choice(
                "How would you like to set your interests and preferences?", sources
            ),
            "import_path": {
                "input": "text",
                "title": "Enter the full local path to your Broader Vids preferences JSON file.",
            },
            "interests": {
                "input": "text",
                "title": "Enter your broad interests, separated by commas or lines.",
            },
            "bilibili_signin": choice(
                "Have you finished signing in to Bilibili? Keep the sign-in window open while you answer.",
                [
                    ("signed_in", "Signed in"),
                    ("trouble", "Having trouble"),
                    ("waiting", "Still signing in"),
                ],
            ),
            "bilibili_trouble": {
                "input": "text",
                "title": "What problem appeared during Bilibili sign-in? Do not include passwords or cookies.",
            },
        },
    }


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


def complete(
    store,
    *,
    model=None,
    effort=None,
    asr_model=None,
    skill_namespace=None,
    host="codex",
):
    from broader_vids.preflight import preflight
    from broader_vids.worker_context import validate_host

    host = validate_host(host)

    if asr_model not in (None, "tiny", "base", "small"):
        raise ValueError("Choose tiny, base, or small for requested transcription.")
    actions = next_steps(
        asr_model=asr_model, skill_namespace=skill_namespace, host=host
    )
    previous = status(store.root)
    report = preflight(
        store,
        previous["default_platform"],
        model=model,
        effort=effort,
        asr=bool(asr_model),
        require_setup=False,
        check_feedback=False,
        host=host,
    )
    if report["status"] != "ready":
        return report
    guidance = completion_message(report, actions)
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
        "next_steps": actions,
        "next_steps_message": guidance,
    }


def completion_message(report, actions):
    platform = {
        "youtube": "YouTube",
        "bilibili": "Bilibili",
        "both": "YouTube and Bilibili",
    }[report["platform"]]
    sections = [
        f"Broader Vids is ready for {platform}. Self-check: passed.",
        f"Data directory: `{report['data_directory']}`. This directory is separate from the plugin and remains after uninstall.",
    ]
    warnings = [
        f"- {name}: {check['status']}. {check.get('remedy') or ''}".rstrip()
        for name, check in report.get("warnings", {}).items()
        if check["status"] != "available"
    ]
    if warnings:
        sections.append("Optional tools (setup can continue):\n" + "\n".join(warnings))
    for name, action in actions.items():
        sections.append(
            f"**{name.capitalize()}** — `{action['skill_invocation']}`\n\n"
            f"{action['description']}\n\n"
            f'Say: "{action["request"]}"'
        )
    sections.append(
        "Reset and Clean require a preview and confirmation. Stop active recommendations, sign-in, and the dashboard before applying either action."
    )
    return "\n\n".join(sections)


def next_steps(*, asr_model=None, skill_namespace=None, host="codex"):
    from broader_vids.worker_context import validate_host

    host = validate_host(host)
    if skill_namespace is not None and (
        not isinstance(skill_namespace, str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", skill_namespace) is None
    ):
        raise ValueError(
            "The skill namespace must be a lowercase plugin name with letters, digits, or hyphens."
        )
    actions = {
        "dashboard": {
            "description": "The local dashboard lets you edit interests, rate videos, manage exclusions, and use optional calibration. Open Preferences, then Import/Export to transfer interests, learned weights, watched video IDs, compact topic coverage, and manual creator exclusions. Other video history, tag rules, and account-specific blacklists stay on this device.",
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
            "description": "Remove the complete managed data directory, including tools and sign-in. Keep the plugin installed. Preview before applying. Run setup before reuse.",
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
        namespace = skill_namespace or ("broader-vids" if host == "claude" else None)
        prefix = "/" if host == "claude" else "$"
        action["skill_invocation"] = (
            prefix + (namespace + ":" if namespace else "") + action["skill_name"]
        )
    return actions
