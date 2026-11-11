from collections import Counter, defaultdict
import hashlib
import json
import random

from broader_vids.learning.features import capture, interest_prior
from broader_vids.learning.service import source_data
from broader_vids.learning.evaluation import evaluate, reviewed_channels
from broader_vids.preferences import get_profile, video_states
from broader_vids.recommendations.originality import (
    provenance,
    assessment_context,
    context_identity,
)
from broader_vids.recommendations.quality import cached_quality
from broader_vids.storage import utc_now


def calibration_list(store, *, latest=False, active=False):
    from broader_vids.learning.feedback import details, context_from_features
    from broader_vids.learning.evidence import latest_revisions, effective_features

    query = "SELECT * FROM calibration_items"
    if latest:
        query += " WHERE cohort=(SELECT cohort FROM calibration_items ORDER BY json_extract(payload,'$.created_at') DESC LIMIT 1)"
    elif active:
        query += " WHERE released=0"
    rows = [
        {
            "canonical_id": row["canonical_id"],
            "split": row["split"],
            "cohort": row["cohort"],
            "released": bool(row["released"]),
            **json.loads(row["payload"]),
        }
        for row in store.db.execute(query + " ORDER BY canonical_id")
    ]
    states = video_states(store, identifiers=[r["canonical_id"] for r in rows])
    structured = details(store, [r["canonical_id"] for r in rows])
    revisions = latest_revisions(store)
    from broader_vids.learning.annotations import latest_annotations, apply_annotation

    annotations = latest_annotations(store)
    from broader_vids.creators import (
        status,
        video_creators,
        checkbox_state,
        checkbox_index,
    )

    exclusions = status(store)
    creator_values = video_creators(
        store,
        [row["canonical_id"] for row in rows],
        state_items={key: value["payload"] for key, value in states.items()},
    )
    creator_rules_index = checkbox_index(exclusions)
    for row in rows:
        state = states.get(row["canonical_id"], {})
        row["ratings"] = state.get("ratings", {})
        row["watched"] = bool(state.get("watched"))
        row["details"] = structured.get(row["canonical_id"], {})
        row["creator"] = checkbox_state(
            creator_values[row["canonical_id"]], exclusions, creator_rules_index
        )
        row["feedback_context"] = context_from_features(
            apply_annotation(
                effective_features(row["features"], revisions.get(row["canonical_id"])),
                annotations.get(row["canonical_id"]),
            )
        )
    return rows


def start_calibration(store, size=30, seed=42, new=False):
    if size != 30:
        raise ValueError("The development calibration cohort uses 30 distinct videos.")
    if store.db.execute(
        "SELECT 1 FROM calibration_items WHERE released=0 LIMIT 1"
    ).fetchone() or (
        not new
        and store.db.execute("SELECT 1 FROM calibration_items LIMIT 1").fetchone()
    ):
        return calibration_list(store)
    profile = get_profile(store)
    if not profile:
        raise ValueError("Set explicit interests before creating a calibration cohort.")
    items = {}
    for row in store.db.execute(
        "SELECT id FROM runs WHERE json_extract(settings,'$.kind')='recommendation' ORDER BY created_at DESC LIMIT 6"
    ):
        run = store.get(row[0])
        for item in [
            *run["state"].get("shortlist", []),
            *run["state"].get("buffer", []),
        ]:
            items.setdefault(item["canonical_id"], item)
    rated = {
        r[0]
        for r in store.db.execute(
            "SELECT DISTINCT canonical_id FROM user_actions WHERE action='feedback'"
        )
    }
    existing_creators = set()
    source = source_data(store)
    for rating in [*source["ratings"], *source.get("details", [])]:
        features = rating.get("features") or {}
        if features.get("creator"):
            existing_creators.add(features["platform"] + ":" + features["creator"])
    pool = {"youtube": [], "bilibili": []}
    latest_metadata = {
        row["canonical_id"]: json.loads(row["payload"])
        for row in store.db.execute(
            "SELECT canonical_id,payload FROM engagement_snapshots"
        )
    }
    for run in store.iter_runs(kind="evidence"):
        key = run["settings"]["identity"]["canonical_id"]
        if (
            key in rated
            or key not in items
            or "augmented" not in run["state"]["assessments"]
        ):
            continue
        try:
            observation = cached_quality(store, run["id"])
        except (ValueError, OSError, KeyError):
            continue
        item = {
            **items[key],
            "engagement": latest_metadata.get(key, items[key].get("engagement", {})),
        }
        if (
            not observation
            or provenance(item, source["profile"])["excluded"]
            or run["platform"] + ":" + str(item.get("creator_id") or "")
            in existing_creators
            or context_identity(assessment_context(item))
            != context_identity(run["settings"].get("originality_context"))
        ):
            continue
        pool[run["platform"]].append(
            {"item": item, "features": capture(item, observation)}
        )
    rng, chosen, creators, seen = random.Random(seed), [], Counter(), set()
    families, domains = Counter(), Counter()

    def family(candidate):
        features = candidate["features"]
        return (
            features["platform"],
            tuple(features["topics"]) or (features["canonical_id"],),
        )

    for platform, candidates in pool.items():
        rng.shuffle(candidates)
        buckets = defaultdict(list)
        for candidate in candidates:
            features = candidate["features"]
            buckets[
                (
                    interest_prior(features, profile) > 0.5,
                    min(3, int(features["raw_quality"] // 25)),
                )
            ].append(candidate)
        count = 0
        while count < 15 and any(buckets.values()):
            advanced = False
            for bucket in buckets.values():
                while bucket:
                    index = max(
                        range(len(bucket)),
                        key=lambda i: sum(
                            domains[(platform, name)] == 0
                            for name in bucket[i]["features"]["domains"]
                        ),
                    )
                    candidate = bucket.pop(index)
                    features = candidate["features"]
                    creator = (
                        platform
                        + ":"
                        + (features["creator"] or features["canonical_id"])
                    )
                    if (
                        features["canonical_id"] in seen
                        or creators[creator] >= 2
                        or families[family(candidate)] >= 2
                    ):
                        continue
                    chosen.append(candidate)
                    seen.add(features["canonical_id"])
                    creators[creator] += 1
                    families[family(candidate)] += 1
                    domains.update((platform, name) for name in features["domains"])
                    count += 1
                    advanced = True
                    break
                if count == 15:
                    break
            if not advanced:
                break
        if count < 15:
            raise ValueError(
                f"Need 15 previously unrated, verified {platform} videos across creators; found {count}. No new downloads or ratings were created."
            )
    held_out = set()
    for platform in pool:
        groups = defaultdict(list)
        for candidate in chosen:
            features = candidate["features"]
            if features["platform"] == platform:
                groups[features["creator"] or features["canonical_id"]].append(
                    features["canonical_id"]
                )
        groups = list(groups.values())
        rng.shuffle(groups)
        n = 0
        for group in groups:
            if n >= 5:
                break
            held_out.update(group)
            n += len(group)
    cohort = hashlib.sha256(json.dumps([seed, sorted(seen)]).encode()).hexdigest()[:32]
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if store.db.execute(
            "SELECT 1 FROM calibration_items WHERE released=0 LIMIT 1"
        ).fetchone():
            return calibration_list(store)
        if any(
            store.db.execute(
                "SELECT 1 FROM user_actions WHERE canonical_id=? AND action='feedback' LIMIT 1",
                (key,),
            ).fetchone()
            for key in seen
        ):
            raise ValueError(
                "Feedback arrived during cohort selection; retry to keep holdout labels unseen."
            )
        after_action = store.db.execute(
            "SELECT COALESCE(MAX(id),0) FROM user_actions"
        ).fetchone()[0]
        for candidate in chosen:
            item, features = candidate["item"], candidate["features"]
            key = features["canonical_id"]
            store.db.execute(
                "INSERT INTO calibration_items(canonical_id,cohort,split,payload) VALUES(?,?,?,?)",
                (
                    key,
                    cohort,
                    "holdout" if key in held_out else "train",
                    json.dumps(
                        {
                            "item": {
                                k: item[k]
                                for k in ("canonical_id", "platform", "url", "title")
                            },
                            "features": features,
                            "created_at": utc_now(),
                            "seed": seed,
                            "after_action_id": after_action,
                        }
                    ),
                ),
            )
    return calibration_list(store)


def finalize_ready_cohort(store):
    current = list(
        store.db.execute(
            "SELECT canonical_id,cohort,payload FROM calibration_items WHERE released=0"
        )
    )
    if not current:
        return False
    paired = calibration_list(store)
    entries = [
        entry
        for entry in paired
        if entry["canonical_id"] in {r["canonical_id"] for r in current}
    ]
    reviewed = [reviewed_channels(entry) for entry in entries]
    ready = (
        len(entries) >= 30
        and all(any(row.values()) for row in reviewed)
        and all(
            sum(row[channel] for row in reviewed) >= 26
            for channel in ("interest", "quality")
        )
    )
    after_action = min(json.loads(r["payload"])["after_action_id"] for r in current)
    distinct_new = store.db.execute(
        "SELECT COUNT(DISTINCT canonical_id) FROM user_actions WHERE action='feedback' AND id>?",
        (after_action,),
    ).fetchone()[0]
    if not ready and distinct_new < 60:
        return False
    report = evaluate(store, use_frozen=False)
    from broader_vids.learning.service import digest

    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if digest(source_data(store)) != report["snapshot_id"]:
            return False
        report["release_reason"] = (
            "independent_channel_review_complete"
            if ready
            else "finite_holdout_reservation_expired"
        )
        store.db.execute(
            "INSERT OR IGNORE INTO learning_evaluations VALUES(?,?,?)",
            (current[0]["cohort"], utc_now(), json.dumps(report)),
        )
        store.db.execute(
            "UPDATE calibration_items SET released=1 WHERE cohort=?",
            (current[0]["cohort"],),
        )
    return True
