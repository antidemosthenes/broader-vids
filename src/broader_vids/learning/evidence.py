import json
import time

from broader_vids.learning.features import capture
from broader_vids.preferences import canonical_identity, platform_lock
from broader_vids.recommendations.originality import (
    assessment_context,
    context_identity,
)
from broader_vids.recommendations.quality import cached_quality
from broader_vids.storage import utc_now


QUALITY_FIELDS = (
    "raw_quality",
    "criteria",
    "coverage",
    "duration_fraction",
    "audience_level",
    "originality_indicators",
    "evidence_run",
)


def latest_revisions(store, identifiers=None):
    clauses, values = (
        [
            "json_extract(settings,'$.kind')='learning_evidence_revision'",
            "status='completed'",
        ],
        [],
    )
    if identifiers is not None:
        if not identifiers:
            return {}
        clauses.append(
            "json_extract(settings,'$.identity.canonical_id') IN ("
            + ",".join("?" for _ in identifiers)
            + ")"
        )
        values.extend(identifiers)
    result = {}
    for row in store.db.execute(
        "SELECT id,settings,state FROM runs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY rowid",
        values,
    ):
        settings, state = json.loads(row["settings"]), json.loads(row["state"])
        result[settings["identity"]["canonical_id"]] = {"id": row["id"], **state}
    return result


def effective_features(features, revision=None, *, dimension=None, level="unknown"):
    if not features or revision is None:
        return features
    result = {
        **features,
        **revision["quality_features"],
        "quality_revision": revision["id"],
    }
    if dimension == "depth" and level != "unknown":
        result["audience_level"] = level
    return result


def record_review(store, identifier, evidence_run):
    identity = canonical_identity(identifier)
    observation = cached_quality(store, evidence_run)
    if not observation or observation["canonical_id"] != identity["canonical_id"]:
        raise ValueError("The reviewed evidence must verify the same playable video.")
    row = store.db.execute(
        "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
        (identity["canonical_id"],),
    ).fetchone()
    metadata = json.loads(row[0]) if row else {}
    expected = assessment_context({"engagement": metadata})
    actual = store.get(evidence_run)["settings"].get("originality_context")
    if context_identity(expected) != context_identity(actual):
        raise ValueError(
            "Metadata changed during evidence review; no calibration features were replaced."
        )
    features = capture({**identity, "engagement": metadata}, observation)
    quality = {key: features[key] for key in QUALITY_FIELDS}
    previous = latest_revisions(store, [identity["canonical_id"]]).get(
        identity["canonical_id"]
    )
    if previous and previous["quality_features"] == quality:
        return previous["id"]
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        current = store.db.execute(
            "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
            (identity["canonical_id"],),
        ).fetchone()
        current_context = assessment_context(
            {"engagement": json.loads(current[0]) if current else {}}
        )
        if context_identity(current_context) != context_identity(actual):
            raise ValueError(
                "Metadata changed before evidence publication; retry review."
            )
        run_id = store.create(
            identity["platform"],
            {"kind": "learning_evidence_revision", "identity": identity},
            {
                "quality_features": quality,
                "reviewed_at": utc_now(),
                "originality_identity": context_identity(actual),
            },
            commit=False,
        )
        run = store.get(run_id)
        run.update(
            status="completed",
            reason="Verified evidence revision; original ratings and feature snapshots are retained.",
        )
        store.save(run)
    return run_id


def refresh_rated_metadata(store, *, max_seconds=120):
    from broader_vids.http import PublicHttp, AccessPaused, BudgetExhausted
    from broader_vids.recommendations.engagement import metadata_fresh, collect_snapshot
    from broader_vids.recommendations.metadata import metadata_session

    if not 1 <= max_seconds <= 600:
        raise ValueError("Metadata audit allows one to 600 seconds.")
    keys = [
        row[0]
        for row in store.db.execute(
            "SELECT DISTINCT v.canonical_id FROM video_state v WHERE v.platform='bilibili' AND (EXISTS (SELECT 1 FROM feedback_ratings r WHERE r.canonical_id=v.canonical_id) OR EXISTS (SELECT 1 FROM feedback_details d WHERE d.canonical_id=v.canonical_id))"
        )
    ]
    pending = []
    for key in keys:
        row = store.db.execute(
            "SELECT payload FROM engagement_snapshots WHERE canonical_id=?", (key,)
        ).fetchone()
        if not row or not metadata_fresh(json.loads(row[0]), "bilibili"):
            pending.append(key)
    if not pending:
        return {"refreshed": 0, "remaining": 0}
    pending_count = len(pending)
    pending = pending[:50]
    with store.lease(platform_lock("bilibili")):
        identifier = store.create(
            "bilibili",
            {
                "kind": "metadata_audit",
                "max_seconds": max_seconds,
                "max_requests": len(pending),
            },
            {"index": 0, "candidates": pending},
        )
        run = store.get(identifier)
        http = PublicHttp(store, run, anonymous_session=True)
        try:
            with metadata_session(store, http, True):
                for key in pending:
                    snapshot = collect_snapshot(http, canonical_identity(key))
                    with store.db:
                        store.db.execute(
                            "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                            (key, json.dumps(snapshot)),
                        )
                    run["state"]["index"] += 1
                    run["elapsed"] = http.elapsed()
                    store.save(run)
            run.update(
                status="completed",
                reason="Rated-video metadata refreshed without media downloads.",
            )
        except (AccessPaused, BudgetExhausted, ValueError, OSError) as error:
            from broader_vids.evidence.media import public_error

            run.update(status="paused", reason=public_error(error))
        finally:
            run["elapsed"] = http.elapsed()
            store.save(run)
    return {
        "refreshed": run["state"]["index"],
        "remaining": pending_count - run["state"]["index"],
        "status": run["status"],
        "reason": run["reason"],
        "run_id": identifier,
    }


def repair_pending(store, *, max_seconds=180, max_videos=1, model_runner=None):
    from broader_vids.evidence.assessment import assess_evidence
    from broader_vids.evidence.service import create_evidence
    from broader_vids.evidence.media import EvidencePending, public_error
    from broader_vids.learning.service import source_data, ensure_model

    if not 1 <= max_videos <= 3 or not 1 <= max_seconds <= 600:
        raise ValueError(
            "Evidence repair allows one to three videos and at most 600 seconds."
        )
    deadline = time.monotonic() + max_seconds
    metadata = refresh_rated_metadata(store, max_seconds=min(120, max_seconds))
    data = source_data(store)
    keys = sorted(
        {
            row["id"]
            for row in [*data["ratings"], *data.get("details", [])]
            if row.get("quality_review_pending")
        }
    )
    results = []
    for key in keys[:max_videos]:
        if time.monotonic() >= deadline:
            break
        identity = canonical_identity(key)
        child_id = None
        if any(
            row["id"] == key and row.get("metadata_review_pending")
            for row in [*data["ratings"], *data.get("details", [])]
        ):
            results.append(
                {
                    "id": key,
                    "status": "pending",
                    "reason": "Complete public metadata is required before evidence repair; no model call was made.",
                }
            )
            continue
        try:
            with store.lease(platform_lock(identity["platform"])):
                row = store.db.execute(
                    "SELECT payload FROM engagement_snapshots WHERE canonical_id=?",
                    (key,),
                ).fetchone()
                context = assessment_context(
                    {"engagement": json.loads(row[0]) if row else {}}
                )
                child_id = create_evidence(
                    store,
                    identity["url"],
                    use_browser=identity["platform"] == "bilibili",
                    max_seconds=max(1, deadline - time.monotonic()),
                    max_mib=1,
                    originality_context=context,
                )
                child = store.get(child_id)
                if child["state"]["phase"] not in {"prepared", "completed"}:
                    raise EvidencePending(
                        "Repair requires retained verified captions and frames; no download or transcription was started."
                    )
                if "augmented" not in child["state"]["assessments"]:
                    options = {"model_runner": model_runner} if model_runner else {}
                    assess_evidence(
                        store,
                        child_id,
                        "augmented",
                        max_seconds=max(0, deadline - time.monotonic()),
                        **options,
                    )
                revision = record_review(store, key, child_id)
                results.append(
                    {
                        "id": key,
                        "status": "repaired",
                        "evidence_run": child_id,
                        "revision": revision,
                    }
                )
        except (ValueError, OSError, EvidencePending) as error:
            results.append(
                {
                    "id": key,
                    "status": "pending",
                    "evidence_run": child_id,
                    "reason": public_error(error),
                }
            )
    ensure_model(store)
    return {
        "metadata": metadata,
        "results": results,
        "remaining": len(keys) - sum(row["status"] == "repaired" for row in results),
        "downloads_started": 0,
        "user_votes_changed": 0,
    }
