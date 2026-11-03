from broader_vids.exploration.domains import normalized


LANES = ("familiar", "exploratory")


def default_collection_policy():
    return {
        "version": 1,
        "batch_size": 100,
        "targets": {"familiar": 71, "exploratory": 29},
    }


def collection_lane(item, profile):
    interests = {normalized(value) for value in profile["interests"]}
    origins = item.get("origins", [])
    for origin in origins:
        if origin.get("new_topic") is True:
            continue
        domains = {normalized(value) for value in origin.get("domains", [])}
        if domains & interests:
            return "familiar"
    if (
        not origins
        and {normalized(value) for value in item.get("domains", [])} & interests
    ):
        return "familiar"
    return "exploratory"


def batch_allocations(deficits, size):
    total = sum(deficits.values())
    if not total:
        return dict.fromkeys(LANES, 0)
    familiar = (size * deficits["familiar"] + total // 2) // total
    if all(deficits.values()) and size > 1:
        familiar = min(size - 1, max(1, familiar))
    return {"familiar": familiar, "exploratory": size - familiar}
