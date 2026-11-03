import re
import unicodedata


_CITATION = re.compile(r"\[(?:verified\b|[CFM]\d)", re.IGNORECASE)
_EDITING = re.compile(
    r"\b(?:I (?:need|should|must|will)|note to self|no issues were detected|"
    r"include (?:the )?word|ignore (?:all |any )?(?:previous|prior|above) instructions)\b"
    r"|\?\s*(?:no|yes|ignore)\b",
    re.IGNORECASE,
)


def label_problem(label):
    if not isinstance(label, str) or not 1 <= len(label) <= 120 or not label.strip():
        return "use 1–120 characters of nonblank text"
    if any(unicodedata.category(char).startswith("C") for char in label):
        return "remove control characters and line breaks"
    if len(label.split()) > 12:
        return "use at most 12 words for the subject name"
    if (
        _CITATION.search(label)
        or "```" in label
        or re.search(
            r"</?(?:system|developer|assistant|instructions?)\b[^>]*>",
            label,
            re.IGNORECASE,
        )
    ):
        return "put citations and instruction markup outside the subject name"
    if re.search(r"https?://", label, re.IGNORECASE) or _EDITING.search(label):
        return "remove instructions, URLs, and editing notes from the subject name"
    return None


def readable_labels(labels, maximum):
    if not isinstance(labels, list):
        return []
    result, seen = [], set()
    for label in labels[:maximum]:
        if label_problem(label):
            continue
        label = " ".join(label.split())
        key = label.casefold()
        if key not in seen:
            result.append(label)
            seen.add(key)
    return result


def readable_topics(topics):
    if not isinstance(topics, dict):
        return topics
    return {
        **topics,
        **{
            field: readable_labels(topics[field], maximum)
            for field, maximum in (("specific", 3), ("broad", 4))
            if isinstance(topics.get(field), list)
        },
    }


def readable_card_topics(card):
    if not isinstance(card, dict) or "topics" not in card:
        return card
    return {**card, "topics": readable_topics(card["topics"])}
