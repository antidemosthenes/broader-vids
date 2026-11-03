from collections import Counter
import hashlib
import json
import math

from broader_vids.learning.features import (
    affinity_features,
    capture,
    interest_prior,
    logit,
    quality_features,
    quality_prior,
    CRITERION_WEIGHTS,
)
from broader_vids.learning.inference import fit_logistic, predict, sigmoid
from broader_vids.preferences import get_profile, video_payload
from broader_vids.storage import utc_now
from broader_vids.recommendations.originality import (
    assessment_context,
    context_identity,
)
from broader_vids.learning.baseline import current_baseline


MODEL_VERSION = 8
LEARNING_LOCK = hashlib.sha256(b"broader-vids-learning").hexdigest()[:32]


def rating_metadata(store):
    return {
        row["canonical_id"]: json.loads(row["payload"])
        for row in store.db.execute(
            "SELECT e.canonical_id,e.payload FROM engagement_snapshots e WHERE EXISTS (SELECT 1 FROM feedback_ratings r WHERE r.canonical_id=e.canonical_id) OR EXISTS (SELECT 1 FROM feedback_details d WHERE d.canonical_id=e.canonical_id)"
        )
    }


def change_epoch(
    store, metadata=None, *, baseline_override=None, profile_override=None
):
    metadata = rating_metadata(store) if metadata is None else metadata
    revision = store.db.execute(
        "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='learning_evidence_revision' AND status='completed' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    baseline = (
        baseline_override
        if baseline_override is not None
        else current_baseline(store, identity_only=True)
    )
    return {
        "imported_baseline": baseline["id"] if baseline else None,
        "feedback": store.db.execute(
            "SELECT COALESCE(MAX(id),0) FROM user_actions WHERE action='feedback'"
        ).fetchone()[0],
        "profile": (
            profile_override
            if profile_override is not None
            else get_profile(store) or {}
        ).get("revision", 0),
        "cohorts": [
            list(row)
            for row in store.db.execute(
                "SELECT DISTINCT cohort,released FROM calibration_items ORDER BY cohort,released"
            )
        ],
        "evidence_revision": revision[0] if revision else None,
        "content_annotation": [
            row[0]
            for row in store.db.execute(
                "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='content_annotation' AND status='completed' ORDER BY rowid DESC LIMIT 1"
            )
        ],
        "metadata": digest(
            [
                [
                    key,
                    value.get("provenance_status"),
                    context_identity(assessment_context({"engagement": value})),
                ]
                for key, value in sorted(metadata.items())
            ]
        ),
    }


def capture_rating(store, identifier):
    from broader_vids.learning.evidence import latest_revisions, effective_features
    from broader_vids.learning.annotations import latest_annotations, apply_annotation

    revision = latest_revisions(store, [identifier]).get(identifier)
    row = store.db.execute(
        "SELECT payload FROM calibration_items WHERE canonical_id=?", (identifier,)
    ).fetchone()
    if row:
        return apply_annotation(
            effective_features(json.loads(row[0])["features"], revision),
            latest_annotations(store).get(identifier),
        )
    item = video_payload(store, identifier)
    observation = item.get("quality_evidence", {})
    if observation.get("evidence_run"):
        from broader_vids.recommendations.quality import cached_quality

        try:
            observation = cached_quality(store, observation["evidence_run"]) or {}
        except (ValueError, OSError, KeyError, TypeError):
            observation = {}
    return apply_annotation(
        effective_features(capture(item, observation), revision),
        latest_annotations(store).get(identifier),
    )


def source_data(store, *, baseline_override=None, profile_override=None):
    baseline = (
        baseline_override if baseline_override is not None else current_baseline(store)
    )
    profile = (
        profile_override
        if profile_override is not None
        else get_profile(store) or {"revision": 0, "interests": []}
    )
    cutoff = baseline["cutoff_action"] if baseline else 0
    metadata = rating_metadata(store)
    held_out_creators = set()
    for row in store.db.execute(
        "SELECT payload FROM calibration_items WHERE split='holdout' AND released=0"
    ):
        features = json.loads(row[0])["features"]
        if features["creator"]:
            held_out_creators.add(features["platform"] + ":" + features["creator"])
    data = {
        "version": MODEL_VERSION,
        "epoch": change_epoch(
            store, metadata, baseline_override=baseline, profile_override=profile
        ),
        "profile": profile,
        "ratings": [
            {
                "id": row["canonical_id"],
                "dimension": row["dimension"],
                "action_id": row["action_id"],
                "split": row["split"] if not row["released"] else "train",
                **json.loads(row["payload"]),
            }
            for row in store.db.execute(
                "SELECT r.*,COALESCE(c.split,'train') AS split,COALESCE(c.released,0) AS released FROM feedback_ratings r LEFT JOIN calibration_items c USING(canonical_id) WHERE r.action_id>? ORDER BY r.canonical_id,r.dimension",
                (cutoff,),
            )
        ],
        "details": [
            {
                "id": row["canonical_id"],
                "dimension": row["dimension"],
                "action_id": row["action_id"],
                "split": row["split"] if not row["released"] else "train",
                **json.loads(row["payload"]),
            }
            for row in store.db.execute(
                "SELECT r.*,COALESCE(c.split,'train') AS split,COALESCE(c.released,0) AS released FROM feedback_details r LEFT JOIN calibration_items c USING(canonical_id) ORDER BY r.canonical_id,r.dimension"
            )
        ],
    }
    if baseline:
        data["baseline"] = baseline["parameters"]
        data["local_overrides"] = [
            r for r in data["details"] if r["action_id"] <= cutoff
        ]
        data["details"] = [r for r in data["details"] if r["action_id"] > cutoff]
    from broader_vids.learning.evidence import latest_revisions, effective_features

    revisions = latest_revisions(store)
    from broader_vids.learning.annotations import latest_annotations, apply_annotation

    annotations = latest_annotations(store)
    targets = sorted(
        {
            row["target"]
            for row in data["details"]
            if row["dimension"] in {"topic_interest", "depth"}
        }
    )
    data["scopes"] = {}
    for offset in range(0, len(targets), 500):
        batch = targets[offset : offset + 500]
        for row in store.db.execute(
            "SELECT target,payload FROM feedback_scopes WHERE target IN ("
            + ",".join("?" for _ in batch)
            + ")",
            batch,
        ):
            data["scopes"][row["target"]] = json.loads(row["payload"])
    for rating in [
        *data["ratings"],
        *data["details"],
        *data.get("local_overrides", []),
    ]:
        rating["features"] = effective_features(
            rating.get("features"),
            revisions.get(rating["id"]),
            dimension=rating["dimension"],
            level=rating.get("level", "unknown"),
        )
        rating["features"] = apply_annotation(
            rating["features"], annotations.get(rating["id"])
        )
        if rating["dimension"] == "depth" and rating["features"]:
            if rating.get("level_source") in ("ai", "pending") or (
                rating.get("level", "unknown") == "unknown"
                and rating.get("level_source") != "user"
            ):
                rating["level"] = rating["features"].get("audience_level") or "unknown"
            rating["features"]["audience_level"] = rating.get("level") or "unknown"
        features = rating.get("features") or {}
        if (
            features.get("creator")
            and features["platform"] + ":" + features["creator"] in held_out_creators
        ):
            rating["split"] = "holdout"
    evidence_ids = sorted(
        {
            rating["features"]["evidence_run"]
            for rating in [*data["ratings"], *data["details"]]
            if rating.get("features") and rating["features"].get("evidence_run")
        }
    )
    contexts = {}
    for offset in range(0, len(evidence_ids), 500):
        batch = evidence_ids[offset : offset + 500]
        for row in store.db.execute(
            "SELECT id,settings FROM runs WHERE id IN ("
            + ",".join("?" for _ in batch)
            + ")",
            batch,
        ):
            contexts[row["id"]] = json.loads(row["settings"]).get("originality_context")
    reviewed = {}
    for rating in [*data["ratings"], *data["details"]]:
        key = rating["id"]
        evidence_id = (rating.get("features") or {}).get("evidence_run")
        review_key = (key, evidence_id)
        if review_key not in reviewed:
            desired = assessment_context({"engagement": metadata.get(key, {})})
            original = contexts.get(evidence_id)
            reviewed[review_key] = bool(
                (rating.get("features") or {}).get("platform") == "bilibili"
                and metadata.get(key, {}).get("provenance_status") != "complete"
                or desired
                and context_identity(desired) != context_identity(original)
            )
        rating["quality_review_pending"] = reviewed[review_key]
        rating["metadata_review_pending"] = (rating.get("features") or {}).get(
            "platform"
        ) == "bilibili" and metadata.get(key, {}).get("provenance_status") != "complete"
    return data


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def specifications(rows, channel, posterior=None):
    names = {name for row in rows for name in row["x"]} | {
        "bias",
        "bilibili",
        "length",
        "prior_logit",
    }
    if channel == "quality":
        names.update(CRITERION_WEIGHTS)
    if posterior:
        names.update(posterior["names"])
    return [
        {
            "name": name,
            "mean": 0.0,
            "sd": 0.35
            if name.startswith("creator:")
            else 0.6
            if name.startswith(("domain:", "topic:"))
            else 0.35,
        }
        for name in sorted(names)
    ]


def missing_variance(features, model, x):
    known = set(model["names"])
    return sum(
        (0.6 if name.startswith(("domain:", "topic:")) else 0.35) ** 2 * value**2
        for name, value in x.items()
        if name not in known
    )


def component_prediction(features, profile, model, channel):
    x = (
        quality_features(features)
        if channel == "quality"
        else affinity_features(features)
    )
    base = (
        quality_prior(features)
        if channel == "quality"
        else interest_prior(features, profile)
    )
    if x is None or base is None:
        return None
    x = {**x, "prior_logit": logit(base)}
    extra = missing_variance(features, model, x)
    evidence_variance = 0
    if channel == "quality":
        evidence_variance = sum(
            (
                1 / 12
                if not record["assessed"]
                else {"high": 0.0025, "medium": 0.01, "low": 0.04}.get(
                    record["confidence"], 0.04
                )
            )
            * (4 * CRITERION_WEIGHTS[name]) ** 2
            for name, record in features["criteria"].items()
        )
    result = predict(model, x, logit(base), extra)
    result.update(
        prior=base,
        evidence_coverage=features["coverage"] if channel == "quality" else None,
    )
    result["logit_contributions"] = {
        name: coefficient * x.get(name, 0)
        for name, coefficient in zip(model["names"], model["mean"])
        if x.get(name, 0)
    }
    evidence_sd = math.sqrt(result["logit_variance"] + evidence_variance)
    result["evidence_sensitivity_interval90"] = [
        sigmoid(result["logit_mean"] - 1.645 * evidence_sd),
        sigmoid(result["logit_mean"] + 1.645 * evidence_sd),
    ]
    if model["observations"] == 0:
        result["probability"] = base
    return result


def score_components(features, profile, models, components, *, structured=None):
    result = dict(components)
    details = {}
    for channel in ("interest", "quality"):
        prediction = component_prediction(features, profile, models[channel], channel)
        if prediction:
            result[channel] = prediction["probability"] * 100
            details[channel] = prediction
    if structured:
        from broader_vids.learning.scoped import interest_prediction, quality_prediction

        scoped = interest_prediction(
            features, result.get("interest", 50) / 100, structured
        )
        result["interest"] = 100 * scoped["probability"]
        details["interest_scope"] = scoped
        ordinal = quality_prediction(
            features, structured.get("quality", {}), result.get("quality", 50) / 100
        )
        if ordinal:
            result["quality"] = 100 * ordinal["probability"]
            details["quality_ordinal"] = ordinal
    return result, details


def overall_features(features, components, base):
    return {
        **affinity_features(features),
        **{
            "quality:" + key: value
            for key, value in (quality_features(features) or {}).items()
            if key not in {"bias", "bilibili", "length"}
        },
        **{"policy:" + name: value / 100 - 0.5 for name, value in components.items()},
        "prior_logit": logit(base / 100),
    }


def base_components(features, profile):
    return {
        "quality": 100 * (quality_prior(features) or 0.5),
        "interest": 100 * interest_prior(features, profile),
        **features.get(
            "policy_components",
            {"engagement": 50.0, "recency": 50.0, "diversity": 100.0},
        ),
    }


def train(data):
    profile, models = data["profile"], {}
    baseline = data.get("baseline", {})
    rows = {name: [] for name in ("interest", "quality", "overall")}
    excluded = Counter()
    used = set()
    from broader_vids.learning.scoped import fit_scoped, fit_quality
    from broader_vids.learning.feedback import superseded_channels

    structured_rows = data.get("details", [])
    structured = fit_scoped(
        structured_rows,
        profile,
        data.get("scopes"),
        baseline=baseline.get("structured"),
        overrides=data.get("local_overrides", []),
    )
    used.update(structured["training_actions"])
    overrides = superseded_channels(structured_rows)
    for rating in data["ratings"]:
        if rating["dimension"] in overrides.get(rating.get("id"), set()):
            excluded["clarified_by_structured_feedback"] += 1
            continue
        if rating["split"] == "holdout":
            excluded["held_out"] += 1
            continue
        features = rating.get("features")
        if not features or features.get("version") != 1:
            excluded["missing_versioned_features"] += 1
            continue
        channel = rating["dimension"]
        if channel == "overall":
            continue
        if channel == "quality" and rating.get("quality_review_pending"):
            excluded["provenance_review_pending"] += 1
            continue
        x = (
            quality_features(features)
            if channel == "quality"
            else affinity_features(features)
        )
        prior = (
            quality_prior(features)
            if channel == "quality"
            else interest_prior(features, profile)
        )
        if x is None or prior is None:
            excluded["missing_quality_evidence"] += 1
            continue
        rows[channel].append(
            {
                "x": {**x, "prior_logit": logit(prior)},
                "y": int(rating["vote"] == "up"),
                "offset": logit(prior),
            }
        )
        used.add(rating["action_id"])
    for channel in ("interest", "quality"):
        prior = baseline.get("models", {}).get(channel)
        models[channel] = fit_logistic(
            specifications(rows[channel], channel, prior),
            rows[channel],
            posterior=prior,
        )
    structured["quality"] = fit_quality(
        structured_rows,
        specifications,
        baseline=lambda features: component_prediction(
            features, profile, models["quality"], "quality"
        )["probability"],
        posterior=baseline.get("structured", {}).get("quality"),
    )
    used.update(structured["quality"]["training_actions"])
    from broader_vids.policy import Policy

    for rating in data["ratings"]:
        if (
            rating["split"] != "train"
            or rating.get("quality_review_pending")
            or "overall" in overrides.get(rating.get("id"), set())
            or rating["dimension"] != "overall"
            or not rating.get("features")
            or rating["features"].get("version") != 1
        ):
            continue
        features = rating["features"]
        components, _ = score_components(
            features,
            profile,
            models,
            base_components(features, profile),
            structured=structured,
        )
        overall_base = (
            sum(components[k] * weight for k, weight in Policy().weights.items()) / 100
        )
        rows["overall"].append(
            {
                "x": overall_features(features, components, 100 * overall_base),
                "y": int(rating["vote"] == "up"),
                "offset": logit(overall_base),
            }
        )
        used.add(rating["action_id"])
    prior = baseline.get("models", {}).get("overall")
    models["overall"] = fit_logistic(
        specifications(rows["overall"], "overall", prior),
        rows["overall"],
        posterior=prior,
    )
    return {
        "version": MODEL_VERSION,
        "epoch": data.get("epoch"),
        "profile": profile,
        "models": models,
        "structured": structured,
        "excluded": dict(excluded),
        "training_actions": sorted(used),
        "rating_counts": dict(Counter(r["dimension"] for r in data["ratings"])),
    }


def prune_models(store, identifier):
    store.db.execute(
        "DELETE FROM learning_models WHERE id<>? AND id NOT IN (SELECT json_extract(state,'$.learning_snapshot.id') FROM runs WHERE json_extract(state,'$.learning_snapshot.id') IS NOT NULL) AND id NOT IN (SELECT json_extract(payload,'$.snapshot_id') FROM learning_evaluations WHERE json_extract(payload,'$.snapshot_id') IS NOT NULL)",
        (identifier,),
    )


def ensure_model(store):
    data = source_data(store)
    identifier = digest(data)
    row = store.db.execute(
        "SELECT payload FROM learning_models WHERE id=?", (identifier,)
    ).fetchone()
    if row:
        return {"id": identifier, **json.loads(row[0])}
    with store.lease(LEARNING_LOCK):
        for _ in range(3):
            data = source_data(store)
            identifier = digest(data)
            payload = train(data)
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                if digest(source_data(store)) != identifier:
                    continue
                store.db.execute(
                    "INSERT OR IGNORE INTO learning_models VALUES(?,?,?)",
                    (identifier, utc_now(), json.dumps(payload)),
                )
                prune_models(store, identifier)
                return {"id": identifier, **payload}
    raise ValueError(
        "Feedback changed repeatedly during fitting; ratings are saved and fitting can be retried."
    )


def refresh_after_feedback(store):
    try:
        ensure_model(store)
        from broader_vids.learning.calibration import finalize_ready_cohort

        if finalize_ready_cohort(store):
            ensure_model(store)
    except (ValueError, OSError):
        pass


def final_score(features, snapshot, components, weights, *, channel_cache=None):
    if channel_cache is None:
        parts, details = score_components(
            features,
            snapshot["profile"],
            snapshot["models"],
            components,
            structured=snapshot.get("structured"),
        )
    else:
        parts, details = {**components, **channel_cache[0]}, channel_cache[1]
    penalty = features.get("missing_captions_penalty", 0)
    if penalty:
        parts = {**parts, "quality": max(0, parts["quality"] - penalty)}
    base = sum(parts[key] * weights[key] for key in parts)
    x = overall_features(features, parts, base)
    overall = predict(
        snapshot["models"]["overall"],
        x,
        logit(base / 100),
        missing_variance(features, snapshot["models"]["overall"], x),
    )
    total = (
        base
        if snapshot["models"]["overall"]["observations"] == 0
        else 100 * overall["probability"]
    )
    return (
        total,
        parts,
        {
            "snapshot_id": snapshot["id"],
            "base_score": base,
            "overall_adjustment": total - base,
            "overall": overall,
            "components": details,
            "criterion_prior_weights": CRITERION_WEIGHTS,
            "raw_quality": features["raw_quality"],
            "coverage": features["coverage"],
        },
    )


def learning_status(store):
    data = source_data(store)
    row = store.db.execute(
        "SELECT payload FROM learning_models WHERE id=?", (digest(data),)
    ).fetchone()
    model = json.loads(row[0]) if row else None
    from broader_vids.learning.calibration import evaluate

    try:
        validation = evaluate(
            store,
            fit=False,
            data=data,
            snapshot={"id": digest(data), **model} if model else None,
        )
    except (ValueError, OSError) as error:
        validation = {"phase_four_complete": False, "reasons": [str(error)]}
    return {
        "imported_baseline": data["epoch"].get("imported_baseline"),
        "excluded_tags": data["profile"].get("excluded_tags", {}),
        "available": True,
        "current": bool(row),
        "snapshot_id": digest(data),
        "ratings": dict(Counter(r["dimension"] for r in data["ratings"])),
        "structured_ratings": dict(Counter(r["dimension"] for r in data["details"])),
        "pending_depth_level": model.get("structured", {}).get("pending_depth_level", 0)
        if model
        else 0,
        "quality_review_pending": len(
            {
                r["id"]
                for r in [*data["ratings"], *data["details"]]
                if r.get("quality_review_pending")
            }
        ),
        "trained": {k: v["observations"] for k, v in model["models"].items()}
        if model
        else {},
        "excluded": model["excluded"] if model else {},
        "empirically_calibrated": validation["phase_four_complete"],
        "validation": {
            k: validation.get(k)
            for k in ("snapshot_id", "paired_videos", "validation_status", "reasons")
        },
        "information": {
            k: {
                field: v[field]
                for field in (
                    "parameter_count",
                    "information_rank",
                    "identifiable_on_observed_design",
                )
            }
            for k, v in model["models"].items()
        }
        if model
        else {},
        "consistency": "conditional_on_stationarity_correct_model_and_identifiable_persistent_coverage",
        "last_action": max(
            (r["action_id"] for r in [*data["ratings"], *data.get("details", [])]),
            default=0,
        ),
    }
