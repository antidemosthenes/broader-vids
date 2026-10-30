import math

from broader_vids.creators import excluded
from broader_vids.recommendations.originality import provenance


DEFAULT_INSPECTION_LIMIT = 35


def inspection_limit(settings):
    limit = settings.get("inspection_limit", 50)
    if settings.get("screening_policy_version", 2) < 3:
        return 50
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("The saved inspection limit must be an integer from 1 to 100.")
    return limit


MIN_VIEWS = 100
MIN_LIKES = 3
MIN_LIKE_RATE = 0.01
MIN_COIN_RATE = 0.001
MIN_FAVORITE_RATE = 0.002


def rejection_reason(
    item, states=None, profile=None, creator_rules=None, *, require_metrics=True
):
    if excluded(item, creator_rules):
        return "excluded_creator"
    state = (states or {}).get(item["canonical_id"], {})
    if state.get("watched") or state.get("dismissed"):
        return "watched_or_dismissed"
    original = provenance(item, profile)
    if original["excluded"]:
        return original["reason"]
    duration = item.get("duration_seconds")
    ambiguous = item.get("duration_scope") == "listing_archive_or_default_part_unknown"
    valid = (
        isinstance(duration, (float, int))
        and not isinstance(duration, bool)
        and math.isfinite(duration)
    )
    if not ambiguous and valid and not 60 < duration <= 5400:
        return "duration_exclusion"
    if not require_metrics:
        return None
    if ambiguous or not valid:
        return "unverified_duration"
    metrics = item.get("engagement") or {}
    views, likes = metrics.get("views"), metrics.get("likes")
    if any(
        not isinstance(n, (float, int))
        or isinstance(n, bool)
        or not math.isfinite(n)
        or n < 0
        for n in (views, likes)
    ):
        return "missing_community_metrics"
    if views < MIN_VIEWS:
        return "below_minimum_views"
    if likes < MIN_LIKES:
        return "below_minimum_likes"
    if likes / views < MIN_LIKE_RATE:
        return "below_minimum_like_rate"
    if item.get("platform") == "bilibili":
        for key, floor in (("coins", MIN_COIN_RATE), ("favorites", MIN_FAVORITE_RATE)):
            count = metrics.get(key)
            if (
                not isinstance(count, (float, int))
                or isinstance(count, bool)
                or not math.isfinite(count)
                or count < 0
            ):
                return "missing_community_metrics"
            if count / views < floor:
                return "below_minimum_" + key + "_rate"
    return None


def screening_score(item):
    anchors = min(
        100,
        50
        * max(
            (origin.get("anchor_hits", 0) for origin in item.get("origins", [])),
            default=0,
        ),
    )
    return round(0.7 * item["engagement_score"]["score"] + 0.3 * anchors, 6)
