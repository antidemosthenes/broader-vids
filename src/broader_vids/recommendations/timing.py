import time
import math

from broader_vids.time_budget import effective_limit


def recover_elapsed(run):
    attempt = run["state"].pop("active_attempt", None)
    if attempt is None:
        return
    limit = effective_limit(run["settings"])
    gap = time.time() - attempt["started_wall"]
    if gap < 0:
        recovered = attempt["prior_elapsed"] if math.isinf(limit) else limit
        run["state"]["elapsed_clock_uncertain"] = True
    else:
        recovered = min(limit, attempt["prior_elapsed"] + gap)
    run["elapsed"] = max(run["elapsed"], recovered)
    run["state"]["elapsed_is_upper_bound"] = True


def start_attempt(run):
    run["state"]["active_attempt"] = {
        "started_wall": time.time(),
        "prior_elapsed": run["elapsed"],
    }


def live_elapsed(run):
    attempt = run["state"].get("active_attempt")
    if run["status"] == "running" and attempt:
        value = attempt["prior_elapsed"] + max(0, time.time() - attempt["started_wall"])
        return max(run["elapsed"], min(effective_limit(run["settings"]), value))
    return run["elapsed"]
