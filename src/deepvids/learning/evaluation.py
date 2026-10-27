from collections import Counter, defaultdict
import json
import math
import random

from deepvids.learning.feedback import superseded_channels
from deepvids.learning.service import (
    MODEL_VERSION,
    source_data,
    ensure_model,
    component_prediction,
    digest,
)
from deepvids.learning.scoped import detail_prediction


def cluster_interval(values, groups):
    aggregates = {}
    for value, group in zip(values, groups):
        total, count = aggregates.get(group, (0, 0))
        aggregates[group] = (total + value, count + 1)
    if len(aggregates) < 2:
        return None
    cells, rng = list(aggregates.values()), random.Random(42)
    samples = []
    for _ in range(500):
        sampled = rng.choices(cells, k=len(cells))
        samples.append(
            sum(total for total, _ in sampled) / sum(count for _, count in sampled)
        )
    samples.sort()
    return [samples[12], samples[487]]


def metrics(rows, groups=None):
    if not rows:
        return None
    groups = list(range(len(rows))) if groups is None else groups
    losses = [(p - y) ** 2 for y, p, _ in rows]
    baselines = [(base - y) ** 2 for y, _, base in rows]
    bins = []
    for lo in range(5):
        indices = [i for i, (_, p, _) in enumerate(rows) if min(4, int(p * 5)) == lo]
        if indices:
            values = [rows[i][0] for i in indices]
            bins.append(
                {
                    "bin": [lo / 5, (lo + 1) / 5],
                    "n": len(indices),
                    "predicted": sum(rows[i][1] for i in indices) / len(indices),
                    "observed": sum(values) / len(values),
                    "observed_interval95": cluster_interval(
                        values, [groups[i] for i in indices]
                    )
                    if len(set(values)) > 1
                    else None,
                }
            )
    return {
        "n": len(rows),
        "positive": sum(y for y, _, _ in rows),
        "brier": sum(losses) / len(rows),
        "prior_brier": sum(baselines) / len(rows),
        "brier_change_interval95": cluster_interval(
            [a - b for a, b in zip(losses, baselines)], groups
        ),
        "log_loss": -sum(
            y * math.log(max(p, 1e-12)) + (1 - y) * math.log(max(1 - p, 1e-12))
            for y, p, _ in rows
        )
        / len(rows),
        "reliability": bins,
        "creator_groups": len(set(groups)),
        "uncertainty_method": "creator_cluster_bootstrap",
    }


def categorical_metrics(rows, groups=None):
    if not rows:
        return None
    groups = list(range(len(rows))) if groups is None else groups
    losses, baselines, counts, log_loss = [], [], Counter(), 0
    for value, probabilities, prior, categories in rows:
        target = categories.index(value)
        counts[value] += 1
        losses.append(
            sum((p - int(i == target)) ** 2 for i, p in enumerate(probabilities))
        )
        baselines.append(sum((p - int(i == target)) ** 2 for i, p in enumerate(prior)))
        log_loss -= math.log(max(probabilities[target], 1e-12))
    return {
        "n": len(rows),
        "classes": dict(counts),
        "brier": sum(losses) / len(rows),
        "prior_brier": sum(baselines) / len(rows),
        "brier_change_interval95": cluster_interval(
            [a - b for a, b in zip(losses, baselines)], groups
        ),
        "log_loss": log_loss / len(rows),
        "creator_groups": len(set(groups)),
        "uncertainty_method": "creator_cluster_bootstrap",
    }


def reviewed_channels(entry):
    ratings, details = entry["ratings"], entry.get("details", {})
    return {
        "interest": bool(
            ratings.get("interest", {}).get("vote")
            or details.get("topic_interest")
            or details.get("domain_interest")
        ),
        "quality": bool(
            ratings.get("quality", {}).get("vote") or details.get("quality_level")
        ),
    }


def evaluation_identity(data, cohort_ids):
    return digest(
        [
            {key: value for key, value in rating.items() if key != "split"}
            for rating in [*data["ratings"], *data.get("details", [])]
            if rating["id"] in cohort_ids
        ]
    )


def response_check(name, report, observations):
    reasons = []
    if observations < 18:
        reasons.append(
            f"Need at least 18 usable training labels for {name}; have {observations}."
        )
    varied = False
    if report:
        varied = (
            len([n for n in report["classes"].values() if n >= 2]) >= 2
            if "classes" in report
            else 2 <= report["positive"] <= report["n"] - 2
        )
    if not report or report["n"] < 8 or not varied:
        reasons.append(
            f"Need at least eight held-out {name} labels with at least two examples in each of two response categories."
        )
    if report and report["creator_groups"] < 6:
        reasons.append(
            f"Need at least six held-out creator groups for {name}; have {report['creator_groups']}."
        )
    if (
        report
        and report["brier_change_interval95"]
        and report["brier_change_interval95"][0] > 0
    ):
        reasons.append(
            f"Held-out {name} predictions are detectably worse than the prior; review model specification."
        )
    return {
        "training_observations": observations,
        "held_out": report,
        "passed": not reasons,
        "reasons": reasons,
    }


def frozen_evaluation(store, data):
    for row in store.db.execute(
        "SELECT payload FROM learning_evaluations ORDER BY created_at DESC"
    ):
        saved = json.loads(row[0])
        if (
            saved.get("model_version") == MODEL_VERSION
            and saved["profile_revision"] == data["profile"]["revision"]
            and saved.get("rating_fingerprint")
            == evaluation_identity(data, saved["cohort_ids"])
        ):
            current_heads = required_response_heads(
                data.get("details", []), data["ratings"]
            )
            if set(current_heads) != set(saved.get("required_heads", [])):
                return {
                    **saved,
                    "phase_four_complete": False,
                    "reasons": [
                        "The stored evaluation covers earlier response types; newly active heads need a fresh unseen holdout."
                    ],
                    "validation_status": "frozen_for_earlier_response_types",
                }
            return {**saved, "validation_status": "frozen_before_holdout_release"}
    return None


def required_response_heads(details, ratings):
    active = {row["dimension"] for row in details}
    overrides = superseded_channels(details)
    legacy = {
        row["dimension"]
        for row in ratings
        if row["dimension"] not in overrides.get(row["id"], set())
    }
    required = sorted(active & {"quality_level", "topic_interest", "domain_interest"})
    required += sorted(legacy & {"quality", "interest"})
    if not (active & {"quality_level"} or "quality" in legacy):
        required.append("quality")
    if not (active & {"topic_interest", "domain_interest"} or "interest" in legacy):
        required.append("interest")
    if any(
        row["dimension"] == "depth" and row.get("level", "unknown") != "unknown"
        for row in details
    ):
        required.append("depth")
    return required


def evaluate(store, *, fit=True, use_frozen=True, data=None, snapshot=None):
    from deepvids.learning.calibration import calibration_list

    data = source_data(store) if data is None else data
    if use_frozen:
        saved = frozen_evaluation(store, data)
        if saved:
            return saved
    identifier = digest(data)
    if snapshot is not None:
        if snapshot["id"] != identifier:
            raise ValueError("Evaluation data and fitted snapshot differ.")
    elif fit:
        snapshot = ensure_model(store)
        if snapshot["id"] != identifier:
            data = source_data(store)
            if snapshot["id"] != digest(data):
                raise ValueError(
                    "Feedback changed during evaluation; retry without releasing holdouts."
                )
    else:
        row = store.db.execute(
            "SELECT payload FROM learning_models WHERE id=?", (identifier,)
        ).fetchone()
        if row:
            snapshot = {"id": identifier, **json.loads(row[0])}
    if snapshot is None:
        return {
            "snapshot_id": identifier,
            "phase_four_complete": False,
            "reasons": [
                "Current feedback needs fitting; no model was fitted during this read-only check."
            ],
            "paired_videos": 0,
            "channel_videos": {},
            "held_out": {},
            "structured_held_out": {},
        }

    cohort = calibration_list(store, active=True)
    cohort_ids = {entry["canonical_id"] for entry in cohort}
    reviewed = [(entry, reviewed_channels(entry)) for entry in cohort]
    channel_counts = Counter(
        channel
        for _, flags in reviewed
        for channel, present in flags.items()
        if present
    )
    platform_counts = {
        channel: dict(
            Counter(
                entry["item"]["platform"] for entry, flags in reviewed if flags[channel]
            )
        )
        for channel in ("interest", "quality")
    }
    paired = sum(all(flags.values()) for _, flags in reviewed)
    details = data.get("details", [])
    overrides = superseded_channels(details)
    binary_rows, categorical_rows, groups = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for rating in data["ratings"]:
        channel = rating["dimension"]
        if channel not in {"interest", "quality"} or channel in overrides.get(
            rating["id"], set()
        ):
            continue
        if (
            rating["split"] != "holdout"
            or rating["id"] not in cohort_ids
            or not rating.get("features")
        ):
            continue
        if channel == "quality" and rating.get("quality_review_pending"):
            continue
        prediction = component_prediction(
            rating["features"],
            snapshot["profile"],
            snapshot["models"][channel],
            channel,
        )
        if prediction:
            binary_rows[channel].append(
                (
                    int(rating["vote"] == "up"),
                    prediction["probability"],
                    prediction["prior"],
                )
            )
            features = rating["features"]
            groups[channel].append(
                features["platform"] + ":" + (features.get("creator") or rating["id"])
            )
    for rating in details:
        if (
            rating["split"] != "holdout"
            or rating["id"] not in cohort_ids
            or not rating.get("features")
        ):
            continue
        if rating["dimension"] == "quality_level" and rating.get(
            "quality_review_pending"
        ):
            continue
        if (
            rating["dimension"] == "depth"
            and rating.get("level", "unknown") == "unknown"
        ):
            continue
        prediction = detail_prediction(rating, snapshot)
        if prediction:
            probabilities, prior, categories = prediction
            categorical_rows[rating["dimension"]].append(
                (rating["value"], probabilities, prior, categories)
            )
            features = rating["features"]
            groups[rating["dimension"]].append(
                features["platform"] + ":" + (features.get("creator") or rating["id"])
            )
    reports = {
        name: metrics(binary_rows[name], groups[name])
        for name in ("interest", "quality")
    }
    categorical = {
        name: categorical_metrics(values, groups[name])
        for name, values in categorical_rows.items()
    }
    training_counts = {
        name: model["observations"] for name, model in snapshot["models"].items()
    }
    used = set(snapshot["training_actions"])
    for rating in details:
        if rating["action_id"] in used and not (
            rating["dimension"] == "depth"
            and rating.get("level", "unknown") == "unknown"
        ):
            training_counts[rating["dimension"]] = (
                training_counts.get(rating["dimension"], 0) + 1
            )
    heads = {
        name: response_check(name, report, training_counts.get(name, 0))
        for name, report in {
            **reports,
            **{r["dimension"]: categorical.get(r["dimension"]) for r in details},
        }.items()
    }
    required = required_response_heads(details, data["ratings"])
    reasons = []
    pending = {
        row["id"]
        for row in [*data["ratings"], *details]
        if row.get("quality_review_pending") and row["id"] in cohort_ids
    }
    if pending:
        reasons.append(
            f"{len(pending)} cohort videos need provenance-aware evidence review; saved ratings are retained and should not be repeated."
        )
    if len(cohort) < 30 or any(
        channel_counts[channel] < 26 for channel in ("interest", "quality")
    ):
        reasons.append(
            f"Need a 30-video cohort and at least 26 independent labels per channel; have {channel_counts['interest']} interest and {channel_counts['quality']} quality. Blanks are abstentions."
        )
    for channel, counts in platform_counts.items():
        if any(counts.get(platform, 0) < 10 for platform in ("youtube", "bilibili")):
            reasons.append(
                f"Need ten reviewed {channel} labels per platform; paired votes are not required."
            )
    for name in required:
        reasons.extend(heads[name]["reasons"])
    return {
        "snapshot_id": snapshot["id"],
        "paired_videos": paired,
        "channel_videos": dict(channel_counts),
        "platforms": platform_counts,
        "held_out": reports,
        "structured_held_out": categorical,
        "heads": heads,
        "required_heads": required,
        "phase_four_complete": not reasons,
        "reasons": reasons,
        "scope": "creator-grouped development holdout on explicitly rated retrieved videos; no unbiased off-policy or confirmatory claim",
        "asymptotic_consistency_verified_by_data": False,
        "cohort_ids": sorted(cohort_ids),
        "rating_actions": {
            row["id"] + "|" + row["dimension"]: row["action_id"]
            for row in [*data["ratings"], *details]
            if row["id"] in cohort_ids
        },
        "rating_fingerprint": evaluation_identity(data, cohort_ids),
        "profile_revision": data["profile"]["revision"],
        "model_version": MODEL_VERSION,
    }
