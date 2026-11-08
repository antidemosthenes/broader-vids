import math

from broader_vids.exploration.domains import normalized
from broader_vids.recommendations.originality import provenance
from broader_vids.learning.topic_labels import readable_topics


FEATURE_VERSION = 1
CRITERION_WEIGHTS = {
    "substance": 0.40,
    "directness": 0.25,
    "originality": 0.25,
    "visuals": 0.10,
}


def logit(probability):
    probability = min(0.99, max(0.01, probability))
    return math.log(probability / (1 - probability))


def capture(item, observation=None):
    observation = (
        item.get("quality_evidence", {}) if observation is None else observation
    )
    roots = sorted(
        {normalized(d) for d in item.get("domains", [])}
        | {
            normalized(d)
            for origin in item.get("origins", [])
            for d in origin.get("domains", [])
        }
    )
    if item.get("content_interest_only"):
        roots = []
    topics = sorted(
        {
            origin["family_id"]
            for origin in item.get("origins", [])
            if origin.get("family_id")
        }
        | {
            origin["topic_label"]
            for origin in item.get("origins", [])
            if origin.get("topic_label")
        }
    )
    quality = observation.get("quality")
    duration = observation.get("duration_seconds", item.get("duration_seconds"))
    duration_fraction = (
        min(duration / 3600, 1)
        if isinstance(duration, (int, float))
        and math.isfinite(duration)
        and 0 < duration <= 5400
        else 0
    )
    criteria, coverage = {}, 0
    for key, weight in CRITERION_WEIGHTS.items():
        record = observation.get("criteria", {}).get(key, {})
        assessed = (
            record.get("status") == "assessed"
            and type(record.get("grade")) is int
            and 0 <= record["grade"] <= 4
        )
        criteria[key] = {
            "value": record["grade"] / 4 if assessed else None,
            "assessed": assessed,
            "confidence": record.get("confidence", "unknown")
            if assessed
            else "unknown",
        }
        coverage += weight if assessed else 0
    semantic = readable_topics(observation.get("topics") or {})
    if semantic.get("specific"):
        topics = [normalized(label) for label in semantic["specific"]]
    if semantic.get("broad"):
        roots = [normalized(label) for label in semantic["broad"]]
    alignment = item.get("interest_alignment") or {}
    matches = (
        alignment.get("matches", [])
        if alignment.get("version") == 1
        and alignment.get("confidence") in {"medium", "high"}
        else []
    )
    if matches:
        roots = sorted({normalized(label) for label in matches})
    return {
        **({"interest_alignment": alignment} if matches else {}),
        "version": FEATURE_VERSION,
        "canonical_id": item["canonical_id"],
        "platform": item["platform"],
        "creator": str(item.get("creator_id") or ""),
        "domains": roots,
        "topics": topics,
        "content_topics": [normalized(label) for label in semantic.get("specific", [])],
        "content_domains": [normalized(label) for label in semantic.get("broad", [])],
        "topic_source": "content_evidence" if semantic.get("specific") else "unlabeled",
        "duration_fraction": duration_fraction,
        "audience_level": observation.get("audience_level", "unknown"),
        "audience_assessment": observation.get("audience_assessment"),
        "raw_quality": quality,
        **(
            {"missing_captions_penalty": observation["missing_captions_penalty"]}
            if observation.get("missing_captions_penalty")
            else {}
        ),
        "criteria": criteria,
        "coverage": coverage,
        "originality_indicators": sorted(
            {signal["kind"] for signal in provenance(item)["signals"]}
        ),
        "evidence_run": observation.get("evidence_run"),
        "base_score": item.get("base_score", item.get("score")),
        "policy_components": {
            key: item.get("components", {}).get(
                key, 50.0 if key != "diversity" else 100.0
            )
            for key in ("engagement", "recency", "diversity")
        },
    }


def interest_prior(features, profile):
    return (
        0.75
        if set(features["domains"]) & {normalized(d) for d in profile["interests"]}
        else 0.35
        if features["domains"]
        else 0.5
    )


def affinity_features(features):
    result = {
        "bias": 1.0,
        "bilibili": float(features["platform"] == "bilibili"),
        "length": features["duration_fraction"] - 0.5,
    }
    for group, prefix, strength in (
        ("domains", "domain:", 1),
        ("topics", "topic:", 0.5),
    ):
        values = features[group]
        if group == "domains" and features.get("interest_alignment"):
            values = sorted(set(values) | set(features.get("content_domains", [])))
        for value in values:
            result[prefix + value] = strength / len(values)
    if features["creator"]:
        result["creator:" + features["platform"] + ":" + features["creator"]] = 1
    return result


def criterion_values(features):
    if features.get("raw_quality") is None:
        return None
    values = {}
    for key in CRITERION_WEIGHTS:
        record = features.get("criteria", {}).get(key, {})
        value = record.get("value") if record.get("assessed") else 0.5
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            return None
        values[key] = value
    return values


def quality_features(features):
    values = criterion_values(features)
    if values is None:
        return None
    return {
        "bias": 1.0,
        "bilibili": float(features["platform"] == "bilibili"),
        "length": features["duration_fraction"],
        "prior_logit": logit(quality_prior(features)),
        **{key: value - 0.5 for key, value in values.items()},
        **{
            "missing:" + key: float(
                not features.get("criteria", {}).get(key, {}).get("assessed")
            )
            for key in CRITERION_WEIGHTS
        },
        **{
            "metadata:" + name: 1.0
            for name in features.get("originality_indicators", [])
        },
    }


def quality_prior(features):
    values = criterion_values(features)
    if values is None:
        return None
    return min(
        0.99,
        max(
            0.01,
            (
                95
                * sum(CRITERION_WEIGHTS[key] * value for key, value in values.items())
                + 5 * features["duration_fraction"]
            )
            / 100,
        ),
    )
