import time


def recover_elapsed(run):
    attempt = run["state"].pop("active_attempt", None)
    if attempt is None:
        return
    limit = run["settings"]["max_seconds"]
    gap = time.time() - attempt["started_wall"]
    recovered = limit if gap < 0 else min(limit, attempt["prior_elapsed"] + gap)
    run["elapsed"] = max(run["elapsed"], recovered)
    run["state"]["elapsed_is_upper_bound"] = True


def start_attempt(run):
    run["state"]["active_attempt"] = {
        "started_wall": time.time(),
        "prior_elapsed": run["elapsed"],
    }
