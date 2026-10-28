import subprocess

from broader_vids.evidence.media import public_error
from broader_vids.http import AccessPaused, BudgetExhausted


def failure_details(error, stage):
    reason = public_error(error)
    text = str(error).casefold()
    scope = getattr(error, "scope", "candidate")
    category = getattr(error, "category", None)
    if isinstance(error, AccessPaused) or any(
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
        category = "timeout"
    elif isinstance(error, ValueError):
        category = "validation"
    result = {
        "stage": stage,
        "scope": scope,
        "category": category or "transient",
        "reason": reason,
        "retryable": getattr(error, "retryable", True),
    }
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
