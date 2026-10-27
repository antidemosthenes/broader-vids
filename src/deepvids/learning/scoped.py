import math

from deepvids.exploration.domains import normalized
from deepvids.learning.features import quality_features, quality_prior
from deepvids.learning.inference import fit_logistic, predict, sigmoid


VALUES = ("low", "medium", "high")
DEPTH = ("deeper", "suitable", "lighter")


def group_key(target, level=None):
    return normalized(target) + ("|" + level if level else "")


def category_posterior(counts, base=0.5, depth=False):
    prior = (
        [0.5, 1.0, 0.5]
        if depth
        else [2 * (1 - base) ** 2, 4 * base * (1 - base), 2 * base**2]
    )
    alpha = [a + n for a, n in zip(prior, counts)]
    total = sum(alpha)
    probabilities = [a / total for a in alpha]
    values = [0, 1, 0] if depth else [0, 0.5, 1]
    mean = sum(p * v for p, v in zip(probabilities, values))
    variance = (sum(p * v**2 for p, v in zip(probabilities, values)) - mean**2) / (
        total + 1
    )
    return {
        "probabilities": probabilities,
        "mean": mean,
        "sd": math.sqrt(max(0, variance)),
        "observations": sum(counts),
    }


def fit_scoped(rows, profile, scopes=None):
    groups = {name: {} for name in ("topic_interest", "domain_interest", "depth")}
    actions, pending = [], 0
    video_topics, video_domains, video_levels = {}, {}, {}
    for row in rows:
        if row["split"] != "train" or row["dimension"] not in groups:
            continue
        dimension = row["dimension"]
        level = row.get("level", "unknown") if dimension == "depth" else None
        key = group_key(row["target"], level)
        categories = DEPTH if dimension == "depth" else VALUES
        counts = groups[dimension].setdefault(key, [0, 0, 0])
        counts[categories.index(row["value"])] += 1
        pending += dimension == "depth" and level == "unknown"
        actions.append(row["action_id"])
        mapping = video_domains if dimension == "domain_interest" else video_topics
        mapping.setdefault(row["id"], []).append(row["target"])
        if dimension == "depth" and level != "unknown":
            video_levels[row["id"]] = level
    interests = {normalized(d) for d in profile["interests"]}
    anchors = dict(scopes or {})
    for row in sorted(rows, key=lambda row: row["action_id"]):
        if row["dimension"] in {"topic_interest", "depth"}:
            anchors.setdefault(
                group_key(row["target"]),
                {"domains": (row.get("features") or {}).get("domains", [])},
            )
    priors = {}
    for dimension, cells in groups.items():
        if dimension == "depth":
            continue
        for key in cells:
            domains = {normalized(d) for d in anchors.get(key, {}).get("domains", [])}
            priors[(dimension, key)] = (
                0.75
                if (
                    key in interests
                    if dimension == "domain_interest"
                    else bool(domains & interests)
                )
                else 0.35
            )
    return {
        "groups": {
            name: {
                key: category_posterior(
                    counts, priors.get((name, key), 0.5), name == "depth"
                )
                for key, counts in cells.items()
            }
            for name, cells in groups.items()
        },
        "training_actions": actions,
        "pending_depth_level": pending,
        "video_topics": video_topics,
        "video_domains": video_domains,
        "video_levels": video_levels,
    }


def interest_prediction(features, base, scoped):
    groups = scoped.get("groups", {})
    result = {"probability": base, "scope": "legacy", "matches": [], "depth": None}
    identifier = features["canonical_id"]
    for dimension, labels in (
        (
            "domain_interest",
            [
                *features.get("domains", []),
                *scoped.get("video_domains", {}).get(identifier, []),
            ],
        ),
        (
            "topic_interest",
            [
                *features.get("topics", []),
                *scoped.get("video_topics", {}).get(identifier, []),
            ],
        ),
    ):
        labels = sorted({normalized(label) for label in labels})
        matches = [
            (label, groups.get(dimension, {}).get(group_key(label))) for label in labels
        ]
        matches = [(label, value) for label, value in matches if value]
        if matches:
            result.update(
                probability=sum(v["mean"] for _, v in matches) / len(matches),
                scope=dimension,
                matches=[label for label, _ in matches],
            )
    level = scoped.get("video_levels", {}).get(
        identifier, features.get("audience_level", "unknown")
    )
    if level != "unknown":
        labels = {
            normalized(label)
            for label in [
                *features.get("topics", []),
                *scoped.get("video_topics", {}).get(identifier, []),
            ]
        }
        matches = [
            groups.get("depth", {}).get(group_key(label, level)) for label in labels
        ]
        matches = [v for v in matches if v]
        if matches:
            fit = sum(v["mean"] for v in matches) / len(matches)
            result["depth"] = {"suitability": fit, "level": level}
            p = result["probability"]
            if 0 < p < 1:
                result["probability"] = sigmoid(
                    math.log(p / (1 - p)) + math.log(fit / (1 - fit))
                )
    return result


def ordinal_offsets(base):
    base = min(0.99, max(0.01, base))
    at_least_medium = 1 - (1 - base) ** 2
    high_if_not_low = base**2 / at_least_medium
    return tuple(math.log(p / (1 - p)) for p in (at_least_medium, high_if_not_low))


def fit_quality(rows, specifications, baseline=None):
    stages = [[], []]
    actions = []
    for row in rows:
        if (
            row["split"] != "train"
            or row["dimension"] != "quality_level"
            or row.get("quality_review_pending")
        ):
            continue
        features = row.get("features")
        x = quality_features(features) if features else None
        if x is None:
            continue
        offsets = ordinal_offsets(
            baseline(features) if baseline else quality_prior(features)
        )
        for stage in range(2 if row["value"] != "low" else 1):
            stages[stage].append(
                {
                    "x": {**x, "prior_logit": offsets[stage]},
                    "offset": offsets[stage],
                    "y": int(row["value"] != "low")
                    if stage == 0
                    else int(row["value"] == "high"),
                }
            )
        actions.append(row["action_id"])
    return {
        "models": [
            fit_logistic(specifications(data, "quality"), data) for data in stages
        ],
        "observations": len(actions),
        "training_actions": actions,
    }


def quality_prediction(features, ordinal, base=None):
    from deepvids.learning.service import missing_variance

    x = quality_features(features)
    if not ordinal.get("observations") or x is None:
        return None
    offsets = ordinal_offsets(quality_prior(features) if base is None else base)
    predictions = [
        predict(
            model,
            {**x, "prior_logit": offset},
            offset,
            missing_variance(features, model, {**x, "prior_logit": offset}),
        )
        for model, offset in zip(ordinal["models"], offsets)
    ]
    a, b = [p["probability"] for p in predictions]
    probabilities = [1 - a, a * (1 - b), a * b]
    return {
        "probability": 0.5 * probabilities[1] + probabilities[2],
        "probabilities": dict(zip(VALUES, probabilities)),
        "stages": predictions,
        "observations": ordinal["observations"],
    }


def detail_prediction(row, snapshot):
    from deepvids.learning.features import interest_prior

    dimension = row["dimension"]
    structured = snapshot.get("structured", {})
    if dimension == "quality_level":
        from deepvids.learning.service import component_prediction

        legacy = component_prediction(
            row["features"],
            snapshot["profile"],
            snapshot["models"]["quality"],
            "quality",
        )
        base = legacy["probability"] if legacy else quality_prior(row["features"])
        if base is None:
            return None
        base = min(0.99, max(0.01, base))
        prior = [(1 - base) ** 2, 2 * base * (1 - base), base**2]
        learned = quality_prediction(
            row["features"], structured.get("quality", {}), base
        )
        return (
            list(learned["probabilities"].values()) if learned else prior,
            prior,
            VALUES,
        )
    depth = dimension == "depth"
    base = interest_prior(row["features"], snapshot["profile"])
    if dimension == "domain_interest":
        base = (
            0.75
            if normalized(row["target"])
            in {normalized(d) for d in snapshot["profile"]["interests"]}
            else 0.35
        )
    prior = category_posterior([0, 0, 0], base, depth)["probabilities"]
    key = group_key(row["target"], row.get("level", "unknown") if depth else None)
    learned = structured.get("groups", {}).get(dimension, {}).get(key)
    return (
        learned["probabilities"] if learned else prior,
        prior,
        DEPTH if depth else VALUES,
    )
