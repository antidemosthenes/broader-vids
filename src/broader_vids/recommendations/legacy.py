from collections import Counter

from broader_vids.recommendations.screening import screening_score


def screen_legacy(items, pending, states, blocked):
    if len(pending) > 50:
        raise ValueError(
            "More than 50 guaranteed pending entries; preserve them and dismiss entries before publication."
        )
    selected = [{**items[key], "carryover": True} for key in pending]
    rejected = {key: "excluded_creator" for key in blocked}
    creators = Counter(i.get("creator_id") or i["canonical_id"] for i in selected)
    seen = set(pending)
    lanes = {True: {}, False: {}}
    for key, item in items.items():
        if key in blocked or key in seen:
            continue
        status = states.get(key, {})
        if status.get("watched") or status.get("dismissed"):
            rejected[key] = "watched_or_dismissed"
            continue
        if item["originality_evidence"]["excluded"]:
            rejected[key] = item["originality_evidence"]["reason"]
            continue
        duration = item.get("duration_seconds")
        if (
            isinstance(duration, (int, float))
            and (duration <= 0 or duration > 5400)
            and item.get("duration_scope") != "listing_archive_or_default_part_unknown"
        ):
            rejected[key] = "duration_exclusion"
            continue
        family = next(
            (o["family_id"] for o in item.get("origins", []) if o.get("family_id")),
            "other",
        )
        lanes[item["related"]].setdefault(family, []).append(item)
    for lane in lanes.values():
        for bucket in lane.values():
            bucket.sort(key=lambda i: (-screening_score(i), i["canonical_id"]))
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
    for key in items.keys() - seen - rejected.keys():
        rejected[key] = "lower_screening_priority_or_no_free_slot"
    return selected, rejected
