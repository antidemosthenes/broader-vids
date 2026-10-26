from deepvids.evidence.retention import compact_evidence


def cleanup_storage(store, *, apply=False):
    summary = {
        "dry_run": not apply,
        "files": 0,
        "bytes": 0,
        "compacted_runs": 0,
        "retained_runs": 0,
        "issues": [],
    }
    for row in store.db.execute(
        "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='evidence' ORDER BY created_at"
    ):
        try:
            result = compact_evidence(store, row[0], dry_run=not apply)
        except (OSError, ValueError, KeyError, TypeError) as error:
            summary["issues"].append({"run_id": row[0], "reason": str(error)[:400]})
            continue
        if result["status"] == "retained":
            summary["retained_runs"] += 1
        else:
            summary["compacted_runs"] += 1
            summary["files"] += result["files"]
            summary["bytes"] += result["bytes"]
    return summary
