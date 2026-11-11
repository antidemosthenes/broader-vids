import subprocess

from broader_vids.evidence.media import file_in_use, public_error, storage_failure
from broader_vids.http import AccessPaused, BudgetExhausted


def failure_details(error, stage):
    reason = public_error(error) or type(error).__name__
    text = str(error).casefold()
    scope = getattr(error, "scope", "candidate")
    category = getattr(error, "category", None)
    if category is None and file_in_use(error):
        scope, category = "candidate", "file_in_use"
    elif storage_failure(error):
        scope, category = "run", "storage"
    elif isinstance(error, AccessPaused) or any(
        token in text
        for token in (
            "http 403",
            "http error 403",
            "http 412",
            "http error 412",
            "http 429",
            "http error 429",
            "captcha",
            "security check",
            "sign in to confirm",
            "profile needs sign-in",
            "profile is not signed in",
        )
    ):
        scope, category = "run" if scope == "run" else "platform", "access"
    elif isinstance(error, BudgetExhausted):
        scope, category = "platform", "budget"
    elif isinstance(error, InterruptedError):
        category = "cancelled"
    elif isinstance(error, subprocess.TimeoutExpired):
        category = "media_timeout" if stage == "download" else "timeout"
        if stage != "download":
            reason = f"Worker timed out after {error.timeout:g} seconds."
            if "model is at capacity" in str(getattr(error, "stderr", "")).casefold():
                reason += " The selected model reported that it was at capacity."
    elif isinstance(error, ValueError):
        category = "validation"
    result = {
        "stage": stage,
        "scope": scope,
        "category": category or "transient",
        "reason": reason,
        "retryable": False
        if category == "storage"
        or (
            scope == "candidate"
            and category in {"media_size", "media_timeout", "media_transfer"}
        )
        else getattr(error, "retryable", True),
    }
    if getattr(error, "recovery", None):
        result["recovery"] = error.recovery
    if getattr(error, "metadata_diagnostic", None):
        result["metadata"] = error.metadata_diagnostic
    if getattr(error, "stderr", None):
        result["diagnostic"] = public_error(error.stderr)
    return result


def save_failure(run, error, stage):
    failure = failure_details(error, stage)
    state = run["state"]
    state["failure"] = failure
    state.setdefault("failure_history", []).append(failure)
    state["failure_history"] = state["failure_history"][-20:]
    return failure


def terminal_media_failure(failure):
    return failure.get("scope") == "candidate" and failure.get("category") in {
        "media_size",
        "media_timeout",
        "media_transfer",
    }
