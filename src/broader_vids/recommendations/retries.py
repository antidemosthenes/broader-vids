from broader_vids.evidence.media import MAX_VIDEO_RETRIES
from broader_vids.evidence.failures import terminal_media_failure


def begin_candidate_attempt(state, key):
    counts = state.setdefault("candidate_attempt_counts", {})
    used = counts.get(key, state.get("candidate_retry_counts", {}).get(key, -1) + 1)
    if used >= 1 + MAX_VIDEO_RETRIES:
        observation = state["evidence"].setdefault(key, {})
        observation.setdefault("reason", "The candidate retry allowance is exhausted.")
        observation.setdefault(
            "failure",
            {
                "stage": "worker",
                "scope": "candidate",
                "category": "attempt_limit",
                "reason": observation["reason"],
            },
        )
        observation["status"] = "unavailable"
        observation["failure"]["retryable"] = False
        return False
    counts[key] = used + 1
    state.setdefault("candidate_retry_counts", {})[key] = used
    return True


def retry_candidate(state, key, observation):
    failure = observation["failure"]
    if failure["scope"] != "candidate" or failure["category"] in {
        "cancelled",
        "budget",
        "access",
        "storage",
    }:
        return False
    used = state.get("candidate_attempt_counts", {}).get(
        key, state.get("candidate_retry_counts", {}).get(key, 0) + 1
    )
    if (
        terminal_media_failure(failure)
        or not failure.get("retryable", True)
        or used >= 1 + MAX_VIDEO_RETRIES
    ):
        observation["status"] = "unavailable"
        failure["retryable"] = False
        return False
    return True


def release_cancelled_attempt(state, key):
    counts = state.get("candidate_attempt_counts", {})
    if key in counts:
        counts[key] = max(0, counts[key] - 1)
        state.setdefault("candidate_retry_counts", {})[key] = max(0, counts[key] - 1)
