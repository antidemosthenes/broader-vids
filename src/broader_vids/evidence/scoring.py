RUBRIC_VERSION = 5
MISSING_CAPTIONS_PENALTY = 10


def caption_penalty(state):
    if (state.get("captions") or {}).get("source") == "unavailable":
        return MISSING_CAPTIONS_PENALTY
    return 0


def adjusted_quality(value, duration_prior, penalty):
    return round(max(0, min(100, value + duration_prior - penalty)), 2)
