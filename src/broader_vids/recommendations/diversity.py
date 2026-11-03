from collections import Counter
import re
import unicodedata

from broader_vids.exploration.domains import normalized
from broader_vids.learning.topic_labels import readable_topics


SECONDARY_SCHOOL = re.compile(r"高考|高中|\b(?:gaokao|high[- ]school)\b", re.IGNORECASE)


def query_keys(item):
    keys = set()
    for origin in item.get("origins", []):
        raw = origin.get("query")
        query = normalized(raw) if isinstance(raw, str) else ""
        if query:
            keys.add("query:" + query)
        elif origin.get("family_id"):
            keys.add("family:" + str(origin["family_id"]))
    return frozenset(keys)


def balanced_inspection(ordered, limit):
    remaining = [(item, query_keys(item)) for item in ordered]
    selected, counts = [], Counter()
    while remaining and len(selected) < limit:
        position = min(
            range(len(remaining)),
            key=lambda index: max(
                (counts[key] for key in remaining[index][1]), default=0
            ),
        )
        item, keys = remaining.pop(position)
        selected.append(item)
        counts.update(keys)
    return selected


def signature(item, observation):
    topics = readable_topics(observation.get("topics") or {})
    labels = topics.get("specific", []) if topics.get("confidence") != "low" else []
    primary = labels[0] if labels and isinstance(labels[0], str) else ""
    primary = unicodedata.normalize("NFKC", primary).casefold()
    primary = normalized(re.sub(r"(?<=\w)[’']s\b", "", primary))
    tags = item.get("engagement", {}).get("tags") or []
    if not isinstance(tags, list):
        tags = []
    curriculum = any(
        SECONDARY_SCHOOL.search(value)
        for value in [item.get("title", ""), *tags]
        if isinstance(value, str)
    )
    return {
        "queries": query_keys(item),
        "primary": primary,
        "formats": frozenset({"secondary_school"}) if curriculum else frozenset(),
    }


def same_topic(first, second):
    if first["queries"] & second["queries"] or first["formats"] & second["formats"]:
        return True
    a, b = first["primary"], second["primary"]
    if not a or not b:
        return False
    if a == b:
        return True
    for shorter, longer in ((a, b), (b, a)):
        if len(shorter.split()) >= 2 and " " + shorter + " " in " " + longer + " ":
            return True
    return False


def overlap_count(candidate, chosen):
    return sum(same_topic(candidate, previous) for previous in chosen)
