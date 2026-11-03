import re
import math

from broader_vids.exploration.domains import normalized
from broader_vids.learning.depth import suitability
from broader_vids.learning.scoped import group_key

BLEND = 0.2


def compile_screening_learning(learning):
    structured = (learning or {}).get("structured") or {}
    groups = structured.get("groups", {})
    topics = groups.get("topic_interest", {})
    domains = groups.get("domain_interest", {})
    topic_index = {}
    for label, cell in topics.items():
        topic_index.setdefault(group_key(label), []).append(
            (label, cell.get("mean", 0.5) * 100)
        )
    domain_index = {
        normalized(label): (label, cell.get("mean", 0.5) * 100)
        for label, cell in domains.items()
    }
    alternatives = sorted(topic_index, key=lambda value: (-len(value), value))
    topic_pattern = (
        re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(value) for value in alternatives)
            + r")(?!\w)"
        )
        if alternatives
        else None
    )
    return {
        "topics": topic_index,
        "topic_pattern": topic_pattern,
        "domains": domain_index,
        "structured": structured,
    }


def _matched_labels(text, labels, pattern):
    found = {}
    if pattern:
        for match in pattern.finditer(text):
            for label, value in labels[match.group(0)]:
                found[group_key(label)] = (label, value)
    return list(found.values())


def screening_interest(item, compiled):
    if not compiled:
        return {"score": 50.0, "scope": "unknown", "matched_labels": []}
    structured = compiled["structured"]
    identifier = item.get("canonical_id")
    video_topics = structured.get("video_topics", {}).get(identifier, [])
    if video_topics:
        verified = {}
        for topic in video_topics:
            for label, value in compiled["topics"].get(normalized(topic), []):
                verified[group_key(label)] = (label, value)
        verified = list(verified.values())
        if verified:
            interest = sum(value for _, value in verified) / len(verified) / 100
            interest = min(0.99, max(0.01, interest))
            level = structured.get("video_levels", {}).get(identifier, "unknown")
            if level != "unknown":
                fit = suitability(
                    structured.get("depth_models", {}),
                    [normalized(topic) for topic in video_topics],
                    level,
                )
                if fit:
                    log_odds = (
                        math.log(interest / (1 - interest)) + fit["log_odds_adjustment"]
                    )
                    interest = (
                        1 / (1 + math.exp(-log_odds))
                        if log_odds >= 0
                        else math.exp(log_odds) / (1 + math.exp(log_odds))
                    )
            return {
                "score": interest * 100,
                "scope": "video_topics",
                "matched_labels": sorted({label for label, _ in verified}),
            }
    tags = item.get("tags") or (item.get("engagement") or {}).get("tags", [])
    if not isinstance(tags, list):
        tags = []
    text = " ".join(normalized(str(value)) for value in [item.get("title", ""), *tags])
    topic_hits = _matched_labels(text, compiled["topics"], compiled["topic_pattern"])
    if topic_hits:
        return {
            "score": sum(value for _, value in topic_hits) / len(topic_hits),
            "scope": "topic_interest",
            "matched_labels": sorted({label for label, _ in topic_hits}),
        }
    explicit_domains = {normalized(value) for value in item.get("domains", [])}
    explicit_domains.update(
        normalized(value)
        for origin in item.get("origins", [])
        for value in origin.get("domains", [])
    )
    domain_hits = [
        compiled["domains"][domain]
        for domain in sorted(explicit_domains)
        if domain in compiled["domains"]
    ]
    if domain_hits:
        return {
            "score": sum(value for _, value in domain_hits) / len(domain_hits),
            "scope": "domain_interest",
            "matched_labels": sorted({label for label, _ in domain_hits}),
        }
    return {"score": 50.0, "scope": "unknown", "matched_labels": []}


def blended_screening_score(base_score, item, compiled):
    interest = screening_interest(item, compiled)
    score = round((1 - BLEND) * base_score + BLEND * interest["score"], 6)
    return score, {
        "base_score": base_score,
        "personalized_score": interest["score"],
        "personalization_scope": interest["scope"],
        "matched_labels": interest["matched_labels"],
        "blend": BLEND,
    }
