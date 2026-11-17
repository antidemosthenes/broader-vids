import json
import math
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from broader_vids.onboarding import status as onboarding_status, PLATFORMS
from broader_vids.time_budget import effective_limit
from broader_vids.preferences import current_recommendation_id, get_profile
from broader_vids.processes import run_bounded
from broader_vids.worker_context import resolve_worker_settings
from broader_vids.identity import identify


DIMENSIONS = ("topic_interest", "domain_interest", "depth", "quality_level")
DIMENSION_LABELS = {
    "topic_interest": "Specific topic",
    "domain_interest": "Broader subject",
    "depth": "Depth for this video",
    "quality_level": "Video quality",
}


def feedback_summary(store, platform):
    platforms = ("youtube", "bilibili") if platform == "both" else (platform,)
    result = {
        name: {
            "total": 0,
            "no_ratings": 0,
            "partial": 0,
            "missing_dimensions": {dimension: 0 for dimension in DIMENSIONS},
            "videos": [],
        }
        for name in platforms
    }
    displayed = {}
    for name in platforms:
        run_id = current_recommendation_id(store, name)
        if not run_id:
            continue
        items = store.db.execute(
            """SELECT json_extract(item.value,'$.canonical_id') AS canonical_id,
            json_extract(item.value,'$.title') AS title,
            json_extract(item.value,'$.url') AS url
            FROM runs r, json_each(r.state, CASE
                WHEN COALESCE(json_array_length(r.state,'$.selected'),0)>0
                THEN '$.selected' ELSE '$.preview' END) item
            WHERE r.id=? ORDER BY item.key""",
            (run_id,),
        )
        for item in items:
            key = item["canonical_id"]
            if isinstance(key, str) and key:
                displayed.setdefault((name, key), dict(item))
    rows = {}
    if displayed:
        values = ",".join("(?,?)" for _ in displayed)
        parameters = [value for key in displayed for value in key]
        query = f"""WITH displayed(platform,canonical_id) AS (VALUES {values})
            SELECT d.platform,d.canonical_id,
            json_extract(v.payload,'$.title') AS saved_title,
            json_extract(v.payload,'$.url') AS saved_url,
            GROUP_CONCAT(f.dimension) AS dimensions
            FROM displayed d LEFT JOIN video_state v USING(canonical_id)
            LEFT JOIN feedback_details f USING(canonical_id)
            WHERE COALESCE(v.dismissed,0)=0
            GROUP BY d.platform,d.canonical_id"""
        rows = {
            (row["platform"], row["canonical_id"]): row
            for row in store.db.execute(query, parameters)
        }
    for (name, key), metadata in displayed.items():
        row = rows.get((name, key))
        if row is None:
            continue
        saved = set((row["dimensions"] or "").split(","))
        missing = [dimension for dimension in DIMENSIONS if dimension not in saved]
        if not missing:
            continue
        item = result[name]
        item["total"] += 1
        item["no_ratings" if not saved.intersection(DIMENSIONS) else "partial"] += 1
        for dimension in missing:
            item["missing_dimensions"][dimension] += 1
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            title = row["saved_title"]
        if not isinstance(title, str) or not title.strip():
            title = row["canonical_id"]
        title = title.strip()[:500]
        url = metadata.get("url")
        if not isinstance(url, str):
            url = row["saved_url"]
        try:
            identity = identify(url) if isinstance(url, str) else None
            if not identity or identity["canonical_id"] != row["canonical_id"]:
                url = None
            else:
                url = identity["url"]
        except (TypeError, ValueError):
            url = None
        item["videos"].append(
            {
                "canonical_id": row["canonical_id"],
                "title": title,
                "url": url,
                "missing_dimensions": missing,
            }
        )
    return {
        "total": sum(item["total"] for item in result.values()),
        "platforms": result,
        "dimension_labels": DIMENSION_LABELS.copy(),
    }


def external_checks(root, platform, *, asr=False, timeout=30, host="codex"):
    from broader_vids.worker_context import validate_host

    host = validate_host(host)
    token = uuid.uuid4().hex
    lock = root / "access/bilibili/browser.lock"
    try:
        arguments = [
            sys.executable,
            "-m",
            "broader_vids.preflight_checks",
            str(root),
            platform,
            token,
            "asr" if asr else "native",
        ]
        if host == "claude":
            arguments.append(host)
        report = run_bounded(
            arguments,
            timeout=timeout,
        )
        value = json.loads(report.stdout)
        if (
            report.returncode
            or not isinstance(value, dict)
            or not isinstance(value.get("checks"), dict)
            or not value["checks"]
            or not isinstance(value.get("warnings"), dict)
            or any(
                not isinstance(item, dict)
                or item.get("status")
                not in {"available", "missing", "unusable", "unverified", "busy"}
                for item in value["checks"].values()
            )
        ):
            raise ValueError
        return value
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "checks": {
                "automated_checks": {
                    "status": "unverified",
                    "remedy": "Automated checks failed or reached their deadline. Run doctor, check host permissions, then retry.",
                }
            },
            "warnings": {},
        }
    finally:
        try:
            if lock.read_text(encoding="utf-8") == token:
                lock.unlink(missing_ok=True)
        except (OSError, UnicodeError):
            pass


def preflight(
    store,
    platform=None,
    *,
    model=None,
    effort=None,
    asr=False,
    require_setup=True,
    check_feedback=True,
    allow_missing_feedback=False,
    max_seconds=30,
    host="codex",
):
    from broader_vids.worker_context import validate_host

    host = validate_host(host)
    started = time.monotonic()
    settings = onboarding_status(store.root)
    platform = platform or settings["default_platform"]
    report = {
        "status": "blocked",
        "data_directory": str(store.root),
        "platform": platform,
        "onboarding": settings,
        "checks": {},
        "warnings": {},
        "feedback": {"total": 0, "platforms": {}},
    }

    def finish(status):
        report.update(status=status, elapsed_seconds=time.monotonic() - started)
        return report

    if platform not in PLATFORMS or (require_setup and not settings["completed_at"]):
        report["next_action"] = (
            "Use the setup skill to select platforms and complete onboarding."
        )
        return finish("setup_required")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        report["checks"]["time_allowance"] = {
            "status": "unusable",
            "remedy": "The saved allowance is exhausted. Start a new round without resetting the existing round.",
        }
        return finish("blocked")
    if store.db.in_transaction:
        report["checks"]["profile"] = {
            "status": "busy",
            "remedy": "Finish the current database transaction before checking readiness.",
        }
        return finish("blocked")
    try:
        with tempfile.TemporaryFile(dir=store.root):
            pass
        store.db.execute("BEGIN IMMEDIATE")
        store.db.rollback()
        profile = get_profile(store)
        if not profile or not profile.get("interests"):
            report["checks"]["profile"] = {
                "status": "missing",
                "remedy": "Import preferences or use profile set --interest TOPIC.",
            }
        else:
            report["checks"]["profile"] = {"status": "available"}
    except (OSError, sqlite3.Error) as error:
        if store.db.in_transaction:
            store.db.rollback()
        report["checks"]["profile"] = {
            "status": "busy"
            if isinstance(error, sqlite3.OperationalError) and "locked" in str(error)
            else "unusable",
            "remedy": "Check profile permissions and close conflicting writers, then retry.",
        }
    try:
        report["model"], report["effort"] = resolve_worker_settings(
            model, effort, host=host
        )
        report["checks"]["model_settings"] = {"status": "available"}
    except (OSError, ValueError) as error:
        report["checks"]["model_settings"] = {
            "status": "unverified",
            "remedy": str(error),
        }
    remaining = min(30, effective_limit({"max_seconds": max_seconds})) - (
        time.monotonic() - started
    )
    if remaining > 0:
        automated = external_checks(
            store.root, platform, asr=asr, timeout=remaining, host=host
        )
        report["checks"].update(automated["checks"])
        report["warnings"] = automated["warnings"]
    else:
        report["checks"]["automated_checks"] = {
            "status": "unverified",
            "remedy": "The readiness-check deadline expired. Retry when the profile is available.",
        }
    if check_feedback:
        report["feedback"] = feedback_summary(store, platform)
    if any(check["status"] != "available" for check in report["checks"].values()):
        return finish("blocked")
    if report["feedback"]["total"] and not allow_missing_feedback:
        report["choices"] = ["Open dashboard and stop", "Continue this round"]
        report["next_action"] = (
            "Open the dashboard and end this request, or explicitly continue with --allow-missing-feedback."
        )
        return finish("feedback_required")
    return finish("ready")


def charge_elapsed(store, identifier, seconds):
    from broader_vids.recommendations.timing import recover_elapsed

    with store.lease(identifier):
        run = store.get(identifier)
        previous = run["elapsed"]
        recover_elapsed(run)
        run["elapsed"] = min(
            effective_limit(run["settings"]), max(run["elapsed"], previous + seconds)
        )
        store.save(run, ("preflight", {"elapsed_seconds": seconds}))
