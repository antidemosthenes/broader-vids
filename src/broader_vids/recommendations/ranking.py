from collections import Counter, deque
from datetime import datetime, timezone

from broader_vids.identity import identify
from broader_vids.recommendations.engagement import rate_scores
from broader_vids.exploration.domains import normalized
from broader_vids.recommendations.originality import provenance
from broader_vids.creators import excluded
from broader_vids.recommendations.screening import DEFAULT_INSPECTION_LIMIT


def domains(item):
    return {
        d for origin in item.get("origins", []) for d in origin.get("domains", [])
    } | set(item.get("domains", []))


def related(item, profile):
    return bool(
        {normalized(d) for d in domains(item)}
        & {normalized(d) for d in profile["interests"]}
    )


def fair_buffer(items, limit):
    buckets = {}
    for item in items:
        key = next(
            (o.get("family_id") for o in item.get("origins", []) if o.get("family_id")),
            "other",
        )
        buckets.setdefault(key, deque()).append(item)
    result, seen = [], set()
    while any(buckets.values()) and len(result) < limit:
        for bucket in buckets.values():
            while bucket:
                item = bucket.popleft()
                if item["canonical_id"] not in seen:
                    seen.add(item["canonical_id"])
                    result.append(item)
                    break
            if len(result) == limit:
                break
    return result


def screen(
    items,
    pending,
    states,
    profile,
    creator_rules=None,
    *,
    policy_version=3,
    limit=DEFAULT_INSPECTION_LIMIT,
):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("The inspection limit must be an integer from 1 to 100.")
    blocked = {
        i["canonical_id"] for i in [*items, *pending] if excluded(i, creator_rules)
    }
    pending = [i for i in pending if i["canonical_id"] not in blocked]
    all_items = {i["canonical_id"]: dict(i) for i in items}
    for item in pending:
        all_items[item["canonical_id"]] = {
            **all_items.get(item["canonical_id"], {}),
            **item,
        }
    for key, item in all_items.items():
        if identify(item["url"])["canonical_id"] != key:
            raise ValueError(
                "Candidate URL and canonical ID must identify the same playable video."
            )
    from broader_vids.recommendations.screening import rejection_reason, screening_score

    rates = rate_scores(list(all_items.values()))
    pending_ids = {item["canonical_id"] for item in pending}
    for identifier, item in all_items.items():
        item.update(
            carryover=False,
            related=related(item, profile),
            engagement_score=rates[identifier],
            originality_evidence=provenance(item, profile),
        )
    if policy_version < 3:
        from broader_vids.recommendations.legacy import screen_legacy

        return screen_legacy(
            all_items, [i["canonical_id"] for i in pending], states, blocked
        )
    eligible, rejected = [], {}
    for identifier, item in all_items.items():
        reason = rejection_reason(item, states, profile, creator_rules)
        if reason:
            rejected[identifier] = reason
            continue
        item["carryover"] = identifier in pending_ids
        item["screening_score"] = screening_score(item)
        eligible.append(item)
    ordered = sorted(
        eligible, key=lambda item: (-item["screening_score"], item["canonical_id"])
    )
    if policy_version >= 4:
        from broader_vids.recommendations.diversity import balanced_inspection

        selected = balanced_inspection(ordered, limit)
    else:
        selected = ordered[:limit]
    chosen = {item["canonical_id"] for item in selected}
    for item in eligible:
        if item["canonical_id"] not in chosen:
            rejected[item["canonical_id"]] = (
                "lower_screening_priority_or_query_diversity"
                if policy_version >= 4
                else "lower_screening_priority_or_no_free_slot"
            )
    return selected, rejected


def recency(item, now):
    value = item.get("engagement", {}).get("published_at")
    try:
        published = (
            datetime.fromtimestamp(value, timezone.utc)
            if isinstance(value, (float, int))
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = max(0, (now - published).total_seconds()) / (86400 * 365.25)
        return 100 / (1 + age / 10)
    except (ValueError, TypeError, AttributeError, OSError, OverflowError):
        return 50


def rank_slate(
    items,
    observations,
    profile,
    weights,
    now=None,
    recent=None,
    learning=None,
    creator_rules=None,
    *,
    policy_version=3,
):
    now, recent = now or datetime.now(timezone.utc), recent or Counter()
    pool = [
        dict(i)
        for i in items
        if i["canonical_id"] in observations
        and observations[i["canonical_id"]]["quality"] >= 60
        and isinstance(
            observations[i["canonical_id"]].get("duration_seconds"), (int, float)
        )
        and (60 if policy_version >= 3 else 0)
        < observations[i["canonical_id"]]["duration_seconds"]
        <= 5400
        and not provenance(i, profile)["excluded"]
        and not excluded(i, creator_rules)
    ]
    from broader_vids.recommendations.diversity import signature, overlap_count

    signatures = (
        {i["canonical_id"]: signature(i, observations[i["canonical_id"]]) for i in pool}
        if policy_version >= 4
        else {}
    )
    chosen_signatures = []
    slate, creators = [], Counter()
    learned_channels, learned_scores = {}, {}
    target_unrelated = (
        min(2, sum(not related(i, profile) for i in pool)) if policy_version < 3 else 0
    )
    while pool and len(slate) < 10:
        need = target_unrelated - sum(not i["related"] for i in slate)
        eligible = (
            [i for i in pool if not related(i, profile)]
            if 10 - len(slate) <= need
            else pool
        ) or pool
        repeats = 0
        if policy_version >= 4:
            overlaps = {
                i["canonical_id"]: overlap_count(
                    signatures[i["canonical_id"]], chosen_signatures
                )
                for i in eligible
            }
            repeats = min(overlaps.values())
            eligible = [i for i in eligible if overlaps[i["canonical_id"]] == repeats]
        scored = []
        for item in eligible:
            creator = item.get("creator_id") or item["canonical_id"]
            parts = {
                "quality": observations[item["canonical_id"]]["quality"],
                "interest": 100
                if related(item, profile)
                else 25
                if domains(item)
                else 50,
                "engagement": item["engagement_score"]["score"],
                "recency": recency(item, now),
                "diversity": 100 / (1 + creators[creator] + 0.1 * recent[creator]),
            }
            total = round(sum(parts[k] * weights[k] for k in parts), 4)
            details = None
            if learning is not None:
                from broader_vids.learning.features import capture
                from broader_vids.learning.service import final_score, score_components

                key = item["canonical_id"]
                if key not in learned_channels:
                    features = capture(item, observations[key])
                    learned_channels[key] = (
                        features,
                        score_components(
                            features,
                            learning["profile"],
                            learning["models"],
                            {},
                            structured=learning.get("structured"),
                        ),
                    )
                cache_key = (key, parts["diversity"])
                if cache_key not in learned_scores:
                    features, channels = learned_channels[key]
                    learned_scores[cache_key] = final_score(
                        features, learning, parts, weights, channel_cache=channels
                    )
                total, parts, details = learned_scores[cache_key]
                total = round(total, 4)
            scored.append((total, item["canonical_id"], item, parts, details))
        total, _, chosen, parts, details = max(
            scored, key=lambda entry: (entry[0], entry[1])
        )
        slate.append(
            {
                **chosen,
                "related": related(chosen, profile),
                "score": total,
                "components": parts,
                "quality_evidence": observations[chosen["canonical_id"]],
                **(
                    {
                        "selection_diversity": {
                            "version": 1,
                            "overlap_count": repeats,
                            "relaxed": repeats > 0,
                        }
                    }
                    if policy_version >= 4
                    else {}
                ),
                **(
                    {"learning": details, "base_score": details["base_score"]}
                    if details
                    else {}
                ),
            }
        )
        if policy_version >= 4:
            chosen_signatures.append(signatures[chosen["canonical_id"]])
        creators[chosen.get("creator_id") or chosen["canonical_id"]] += 1
        pool.remove(chosen)
    return slate
