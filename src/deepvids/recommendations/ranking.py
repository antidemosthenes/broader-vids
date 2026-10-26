from collections import Counter
from datetime import datetime, timezone

from deepvids.identity import identify
from deepvids.recommendations.engagement import rate_scores
from deepvids.exploration.domains import normalized
from deepvids.recommendations.originality import provenance


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
        buckets.setdefault(key, []).append(item)
    result, seen = [], set()
    while any(buckets.values()) and len(result) < limit:
        for bucket in buckets.values():
            while bucket:
                item = bucket.pop(0)
                if item["canonical_id"] not in seen:
                    seen.add(item["canonical_id"])
                    result.append(item)
                    break
            if len(result) == limit:
                break
    return result


def screen(items, pending, states, profile):
    if len(pending) > 50:
        raise ValueError(
            "More than 50 guaranteed pending entries; preserve them and dismiss entries before publication."
        )
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
    rates = rate_scores(list(all_items.values()))
    selected, rejected, creators = [], {}, Counter()
    for item in pending:
        entry = {
            **all_items[item["canonical_id"]],
            "carryover": True,
            "related": related(item, profile),
            "engagement_score": rates[item["canonical_id"]],
            "originality_evidence": provenance(all_items[item["canonical_id"]]),
        }
        selected.append(entry)
        creators[item.get("creator_id") or item["canonical_id"]] += 1
    seen = {i["canonical_id"] for i in selected}
    lanes = {True: {}, False: {}}
    for identifier, item in all_items.items():
        status = states.get(identifier, {})
        if identifier in seen:
            continue
        if status.get("watched") or status.get("dismissed"):
            rejected[identifier] = "watched_or_dismissed"
            continue
        item["originality_evidence"] = provenance(item)
        if item["originality_evidence"]["excluded"]:
            rejected[identifier] = item["originality_evidence"]["reason"]
            continue
        duration = item.get("duration_seconds")
        if (
            isinstance(duration, (int, float))
            and (duration <= 0 or duration > 5400)
            and item.get("duration_scope") != "listing_archive_or_default_part_unknown"
        ):
            rejected[identifier] = "duration_exclusion"
            continue
        item.update(
            carryover=False,
            related=related(item, profile),
            engagement_score=rates[identifier],
        )
        key = next(
            (o.get("family_id") for o in item.get("origins", []) if o.get("family_id")),
            "other",
        )
        lanes[item["related"]].setdefault(key, []).append(item)
    for lane in lanes.values():
        for bucket in lane.values():
            bucket.sort(
                key=lambda i: (
                    -(
                        0.7 * i["engagement_score"]["score"]
                        + 0.3
                        * min(
                            100,
                            50
                            * max(
                                (o.get("anchor_hits", 0) for o in i.get("origins", [])),
                                default=0,
                            ),
                        )
                    ),
                    i["canonical_id"],
                )
            )
    offsets = Counter()
    while len(selected) < 50:
        counts = Counter(i["related"] for i in selected)
        preferred = counts[True] < counts[False]
        progress = False
        for flag in (preferred, not preferred):
            buckets = list(lanes[flag].values())
            for step in range(len(buckets)):
                index = (offsets[flag] + step) % len(buckets)
                bucket = buckets[index]
                while bucket:
                    item = bucket.pop(0)
                    creator = item.get("creator_id") or item["canonical_id"]
                    if creators[creator] >= 3:
                        rejected[item["canonical_id"]] = "creator_cap"
                        continue
                    selected.append(item)
                    seen.add(item["canonical_id"])
                    creators[creator] += 1
                    offsets[flag] = index + 1
                    progress = True
                    break
                if progress:
                    break
            if progress:
                break
        if not progress:
            break
    for identifier in all_items.keys() - seen - rejected.keys():
        rejected[identifier] = "lower_screening_priority_or_no_free_slot"
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


def rank_slate(items, observations, profile, weights, now=None, recent=None):
    now, recent = now or datetime.now(timezone.utc), recent or Counter()
    pool = [
        dict(i)
        for i in items
        if i["canonical_id"] in observations
        and observations[i["canonical_id"]]["quality"] >= 60
        and not provenance(i)["excluded"]
    ]
    slate, creators = [], Counter()
    target_unrelated = min(2, sum(not related(i, profile) for i in pool))
    while pool and len(slate) < 10:
        need = target_unrelated - sum(not i["related"] for i in slate)
        eligible = (
            [i for i in pool if not related(i, profile)]
            if 10 - len(slate) <= need
            else pool
        )
        if not eligible:
            eligible = pool
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
            scored.append((total, item["canonical_id"], item, parts))
        total, _, chosen, parts = max(scored, key=lambda entry: (entry[0], entry[1]))
        slate.append(
            {
                **chosen,
                "related": related(chosen, profile),
                "score": total,
                "components": parts,
                "quality_evidence": observations[chosen["canonical_id"]],
            }
        )
        creators[chosen.get("creator_id") or chosen["canonical_id"]] += 1
        pool.remove(chosen)
    return slate
