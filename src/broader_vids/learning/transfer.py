from contextlib import ExitStack
import copy
import json
import math
import os
import sqlite3
from pathlib import Path
import time
import uuid

from broader_vids import __version__
from broader_vids.learning.depth import LEVELS, PRIOR_MEAN, PRIOR_SD, WIDTH
from broader_vids.learning.features import FEATURE_VERSION, CRITERION_WEIGHTS
from broader_vids.learning.inference import cholesky, work_scope, check_work
from broader_vids.learning.posterior import portable_model
from broader_vids.policy import Policy
from broader_vids.preferences import ALIASES, canonical_identity, platform_lock
from broader_vids.storage import utc_now


FORMAT = "broader-vids.preferences"
MAX_BYTES = 8 * 1024 * 1024
MAX_PARAMETERS = 384
MAX_WATCHED_VIDEOS = 100_000
MAX_CREATOR_EXCLUSIONS = 10_000


def _coverage():
    from broader_vids.exploration.coverage import (
        export_summary,
        import_summary,
        validate_summary,
    )

    return export_summary, import_summary, validate_summary


def _digest(value):
    from broader_vids.learning.service import digest

    return digest(value)


def _policy():
    return _digest(
        {
            "weights": Policy().weights,
            "criteria": CRITERION_WEIGHTS,
            "depth": [WIDTH, PRIOR_MEAN, PRIOR_SD],
        }
    )


def _finite(value, maximum=1e12):
    return (
        type(value) in (int, float) and abs(value) <= maximum and math.isfinite(value)
    )


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{name} has unsupported fields.")


def _label(value, maximum=200):
    return (
        isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value
    )


def _count(value):
    return type(value) is int and 0 <= value <= 1_000_000_000


def _validate_model(model):
    if not isinstance(model, dict):
        raise ValueError("Learned models require a JSON object.")
    matrix_key = "covariance" if "covariance" in model else "precision"
    _keys(model, ("names", "mean", "observations", matrix_key), "Learned model")
    names, means, matrix = model["names"], model["mean"], model[matrix_key]
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= MAX_PARAMETERS
        or not all(_label(n) for n in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError(
            "Learned model parameter names are invalid or exceed the supported limit."
        )
    size = len(names)
    if (
        not isinstance(means, list)
        or len(means) != size
        or not all(_finite(n, 1e6) for n in means)
        or not _count(model["observations"])
    ):
        raise ValueError("Learned model means or observation counts are invalid.")
    if (
        not isinstance(matrix, list)
        or len(matrix) != size
        or any(
            not isinstance(row, list)
            or len(row) != size
            or not all(_finite(n) for n in row)
            for row in matrix
        )
    ):
        raise ValueError("Learned model uncertainty has invalid dimensions or numbers.")
    if any(
        abs(matrix[i][j] - matrix[j][i])
        > 1e-8 * max(1, abs(matrix[i][j]), abs(matrix[j][i]))
        for i in range(size)
        for j in range(i)
    ):
        raise ValueError("Learned model uncertainty must be symmetric.")
    cholesky(matrix)


def validate_bundle(bundle):
    check_work()
    from broader_vids.learning.service import MODEL_VERSION

    if (
        not isinstance(bundle, dict)
        or type(bundle.get("version")) is not int
        or bundle["version"] != 4
    ):
        raise ValueError(
            "Only preference format 4 is supported. Use a current Broader Vids export."
        )
    _keys(
        bundle,
        (
            "format",
            "version",
            "model_version",
            "feature_version",
            "policy",
            "created_at",
            "engine_version",
            "preferences",
            "parameters",
            "checksum",
            "watched_video_ids",
            "exploration_summary",
            "creator_exclusions",
        ),
        "Preference file",
    )
    if (
        bundle["format"] != FORMAT
        or type(bundle["version"]) is not int
        or bundle["version"] != 4
        or type(bundle["model_version"]) is not int
        or bundle["model_version"] != MODEL_VERSION
        or type(bundle["feature_version"]) is not int
        or bundle["feature_version"] != FEATURE_VERSION
        or bundle["policy"] != _policy()
    ):
        raise ValueError(
            "This preference file requires a compatible Broader Vids scoring version."
        )
    if (
        not _label(bundle["created_at"], 80)
        or not _label(bundle["engine_version"], 80)
        or bundle["checksum"]
        != _digest({k: v for k, v in bundle.items() if k != "checksum"})
    ):
        raise ValueError("The preference file checksum or metadata is invalid.")
    watched = bundle["watched_video_ids"]
    if not isinstance(watched, list) or len(watched) > MAX_WATCHED_VIDEOS:
        raise ValueError("Watched exclusions require at most 100,000 video IDs.")
    seen = set()
    for key in watched:
        check_work()
        if (
            not _label(key, 80)
            or canonical_identity(key)["canonical_id"] != key
            or key in seen
        ):
            raise ValueError("Watched exclusions require distinct canonical video IDs.")
        seen.add(key)
    _, _, validate_summary = _coverage()
    validate_summary(bundle["exploration_summary"])
    from broader_vids.creators import creator_id

    rules = bundle["creator_exclusions"]
    _keys(rules, ("youtube", "bilibili"), "Creator exclusions")
    total = 0
    for platform, entries in rules.items():
        if not isinstance(entries, list):
            raise ValueError("Creator exclusions require platform-separated lists.")
        total += len(entries)
        if total > MAX_CREATOR_EXCLUSIONS:
            raise ValueError("Creator exclusions exceed the 10,000-rule limit.")
        seen_creators = set()
        for entry in entries:
            check_work()
            _keys(entry, ("creator_id", "name"), "Creator exclusion")
            identifier, name = entry["creator_id"], entry["name"]
            if (
                not isinstance(identifier, str)
                or creator_id(platform, identifier) != identifier
                or identifier in seen_creators
                or not isinstance(name, str)
                or len(name) > 300
                or name != name.strip()
            ):
                raise ValueError(
                    "Creator exclusions require distinct stable IDs and bounded display names."
                )
            seen_creators.add(identifier)
    _keys(bundle["preferences"], ("interests",), "Preferences")
    interests = bundle["preferences"]["interests"]
    if (
        not isinstance(interests, list)
        or len(interests) > 40
        or not all(
            _label(s, 80) and ALIASES.get(s.casefold(), s.casefold()) == s
            for s in interests
        )
        or len(set(interests)) != len(interests)
    ):
        raise ValueError(
            "Interests must contain up to forty distinct normalized subject names."
        )
    parameters = bundle["parameters"]
    _keys(parameters, ("models", "structured"), "Learned parameters")
    _keys(parameters["models"], ("interest", "quality", "overall"), "Model channels")
    for model in parameters["models"].values():
        _validate_model(model)
    structured = parameters["structured"]
    _keys(structured, ("groups", "depth_models", "quality"), "Structured preferences")
    _keys(
        structured["groups"],
        ("topic_interest", "domain_interest", "depth"),
        "Preference groups",
    )
    for cells in structured["groups"].values():
        if not isinstance(cells, dict) or len(cells) > 2000:
            raise ValueError("The preference group limit is 2,000 cells per dimension.")
        for label, cell in cells.items():
            check_work()
            _keys(cell, ("alpha", "observations"), "Preference cell")
            alpha, observations = cell["alpha"], cell["observations"]
            if (
                not _label(label, 140)
                or not _count(observations)
                or not isinstance(alpha, list)
                or len(alpha) != 3
                or not all(_finite(n) and n > 0 for n in alpha)
                or abs(sum(alpha) - observations - 2) > 1e-7 * max(1, observations)
            ):
                raise ValueError(
                    "Preference cells require a proper three-category posterior."
                )
    depth = structured["depth_models"]
    if not isinstance(depth, dict) or len(depth) > 2000:
        raise ValueError("The depth preference limit is 2,000 subjects.")
    for label, model in depth.items():
        check_work()
        _keys(
            model,
            ("mean", "sd", "observations", "predictions", "approximation", "counts"),
            "Depth model",
        )
        counts = model["counts"]
        if (
            not _label(label, 120)
            or not _finite(model["mean"], 1e6)
            or not _finite(model["sd"], 1e6)
            or model["sd"] <= 0
            or not _count(model["observations"])
            or not isinstance(counts, list)
            or len(counts) != 3
            or any(
                not isinstance(row, list)
                or len(row) != 3
                or not all(_count(n) for n in row)
                for row in counts
            )
            or sum(map(sum, counts)) != model["observations"]
            or model["approximation"] != "adaptive_one_dimensional_quadrature"
        ):
            raise ValueError(
                "Depth posterior parameters or aggregate counts are invalid."
            )
        _keys(model["predictions"], LEVELS, "Depth predictions")
        if any(
            not isinstance(ps, list)
            or len(ps) != 3
            or not all(_finite(p) and 0 <= p <= 1 for p in ps)
            or abs(sum(ps) - 1) > 1e-8
            for ps in model["predictions"].values()
        ):
            raise ValueError(
                "Depth predictions must contain three probabilities for each level."
            )
    quality = structured["quality"]
    _keys(quality, ("models", "observations"), "Quality calibration")
    if (
        not _count(quality["observations"])
        or not isinstance(quality["models"], list)
        or len(quality["models"]) != 2
    ):
        raise ValueError("Quality calibration requires two probability models.")
    for model in quality["models"]:
        _validate_model(model)
    if (
        quality["models"][0]["observations"] != quality["observations"]
        or quality["models"][1]["observations"] > quality["observations"]
    ):
        raise ValueError("Quality calibration observation counts disagree.")
    return bundle


def parse_bundle(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("The preference file must not exceed 8 MiB.")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("The preference file contains duplicate fields.")
            result[key] = value
        return result

    try:
        bundle = json.loads(
            text,
            object_pairs_hook=unique,
            parse_constant=lambda n: (_ for _ in ()).throw(
                ValueError("Nonfinite JSON numbers are not supported.")
            ),
        )
        deadline = time.monotonic() + 20
        with work_scope(lambda: _deadline(deadline)):
            return validate_bundle(bundle)
    except (RecursionError, TypeError, KeyError) as error:
        raise ValueError("The preference file has an invalid structure.") from error


def _deadline(deadline):
    if time.monotonic() > deadline:
        raise ValueError("Preference processing reached its time allowance.")


def _manual_creators(store):
    rules = {"youtube": [], "bilibili": []}
    count = 0
    for platform, identifier, name in store.db.execute(
        "SELECT platform,creator_id,name FROM creator_exclusions WHERE source='manual' ORDER BY platform,creator_id"
    ):
        count += 1
        if count > MAX_CREATOR_EXCLUSIONS:
            raise ValueError("Creator exclusions exceed the 10,000-rule limit.")
        rules[platform].append({"creator_id": identifier, "name": name.strip()})
    return {"fingerprint": _digest(rules), "rules": rules}


def _transfer_revision(store, epoch=None, creator_fingerprint=None):
    from broader_vids.learning.service import change_epoch

    if epoch is None:
        epoch = change_epoch(store)
    if creator_fingerprint is None:
        creator_fingerprint = _manual_creators(store)["fingerprint"]
    return _digest({"learning": epoch, "creator_rules": creator_fingerprint})


def _replace_creators(store, rules, timestamp, *, current):
    if all(
        {entry["creator_id"]: entry["name"] for entry in current[platform]}
        == {entry["creator_id"]: entry["name"] for entry in rules[platform]}
        for platform in ("youtube", "bilibili")
    ):
        return

    def rows():
        for platform, entries in rules.items():
            for entry in entries:
                check_work()
                yield platform, entry["creator_id"], entry["name"], timestamp

    store.db.execute("DELETE FROM creator_exclusions WHERE source='manual'")
    store.db.executemany(
        "INSERT INTO creator_exclusions VALUES(?,?,'manual','',?,?)", rows()
    )
    store.db.execute("UPDATE creator_policy SET revision=revision+1 WHERE id=1")


def _snapshot(
    store,
    *,
    include_watched=False,
    include_exploration=False,
    include_creators=False,
    **overrides,
):
    from broader_vids.learning.service import source_data

    if store.db.in_transaction:
        raise ValueError(
            "Finish the current database transaction before transferring preferences."
        )
    store.db.execute("BEGIN")
    try:
        result = [source_data(store, **overrides)]
        if include_watched:
            result.append(
                [
                    row[0]
                    for row in store.db.execute(
                        "SELECT canonical_id FROM video_state WHERE watched=1 ORDER BY canonical_id"
                    )
                ]
            )
        if include_exploration:
            export_summary, _, _ = _coverage()
            result.append(export_summary(store))
        if include_creators:
            result.append(_manual_creators(store))
        return tuple(result) if len(result) > 1 else result[0]
    finally:
        store.db.rollback()


def _bundle(
    data,
    snapshot=None,
    *,
    watched_video_ids,
    exploration_summary,
    creator_exclusions,
):
    from broader_vids.learning.service import train, MODEL_VERSION

    deadline = time.monotonic() + 60
    with work_scope(lambda: _deadline(deadline)):
        snapshot = train(data) if snapshot is None else snapshot
        structured = snapshot["structured"]
        parameters = {
            "models": {k: portable_model(v) for k, v in snapshot["models"].items()},
            "structured": {
                "groups": {
                    name: {
                        label: {
                            "alpha": cell["alpha"],
                            "observations": cell["observations"],
                        }
                        for label, cell in cells.items()
                    }
                    for name, cells in structured["groups"].items()
                },
                "depth_models": structured["depth_models"],
                "quality": {
                    "models": [
                        portable_model(m) for m in structured["quality"]["models"]
                    ],
                    "observations": structured["quality"]["observations"],
                },
            },
        }
        value = {
            "format": FORMAT,
            "version": 4,
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "policy": _policy(),
            "created_at": utc_now(),
            "engine_version": __version__,
            "preferences": {"interests": data["profile"]["interests"]},
            "parameters": parameters,
            "watched_video_ids": list(watched_video_ids),
        }
        value["exploration_summary"] = exploration_summary
        value["creator_exclusions"] = creator_exclusions
        value["checksum"] = _digest(value)
        validate_bundle(value)
        if len(_encode(value)) > MAX_BYTES:
            raise ValueError("The calibrated parameter file exceeds the 8 MiB limit.")
        return value


def _cached_snapshot(store, data):
    row = store.db.execute(
        "SELECT payload FROM learning_models WHERE id=?", (_digest(data),)
    ).fetchone()
    return json.loads(row[0]) if row else None


def export_preferences(store):
    data, watched, exploration, manual = _snapshot(
        store, include_watched=True, include_exploration=True, include_creators=True
    )
    return _bundle(
        data,
        _cached_snapshot(store, data),
        watched_video_ids=watched,
        exploration_summary=exploration,
        creator_exclusions=manual["rules"],
    )


def _encode(bundle):
    return json.dumps(
        bundle, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def write_export(path, bundle, *, overwrite=False):
    destination = Path(path).expanduser().absolute()
    if destination.suffix.casefold() != ".json":
        raise ValueError("Choose a .json file for exported preferences.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = _encode(bundle)
    if len(raw) > MAX_BYTES:
        raise ValueError("The preference file exceeds the 8 MiB limit.")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            temporary.replace(destination)
        elif os.name == "nt":
            temporary.rename(destination)
        else:
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _creator_summary(bundle):
    rules = bundle.get("creator_exclusions", {})
    counts = {
        platform: len(rules.get(platform, [])) for platform in ("youtube", "bilibili")
    }
    return {
        "creator_exclusions_mode": "replace",
        "creator_exclusions": sum(counts.values()),
        "creator_exclusions_by_platform": counts,
    }


def preview_import(store, bundle, *, _validated=False):
    deadline = time.monotonic() + 20
    with work_scope(lambda: _deadline(deadline)):
        bundle = bundle if _validated else validate_bundle(bundle)
        parameters = bundle["parameters"]
        from broader_vids.exploration.coverage import validate_merge

        validate_merge(store, bundle["exploration_summary"])
    return {
        "compatible": True,
        "interests": bundle["preferences"]["interests"],
        "model_parameters": sum(len(m["names"]) for m in parameters["models"].values())
        + sum(len(m["names"]) for m in parameters["structured"]["quality"]["models"]),
        "preference_cells": {
            key: len(value) for key, value in parameters["structured"]["groups"].items()
        },
        "revision": _transfer_revision(store),
        "checksum": bundle["checksum"],
        "mode": "replace",
        "watched_videos": len(bundle["watched_video_ids"]),
        "watched_by_platform": {
            platform: sum(
                key.startswith(platform + ":") for key in bundle["watched_video_ids"]
            )
            for platform in ("youtube", "bilibili")
        },
        "exploration_nodes": len(
            bundle.get("exploration_summary", {}).get("nodes", [])
        ),
        **_creator_summary(bundle),
    }


def import_preferences(store, bundle, *, expected_revision=None, _validated=False):
    from broader_vids.learning.service import (
        LEARNING_LOCK,
        prune_models,
        train,
    )

    deadline = time.monotonic() + 20
    with work_scope(lambda: _deadline(deadline)):
        bundle = copy.deepcopy(bundle)
        if not _validated:
            validate_bundle(bundle)
    with ExitStack() as locks:
        for key in (platform_lock("youtube"), platform_lock("bilibili"), LEARNING_LOCK):
            locks.enter_context(store.lease(key))
        data, watched, exploration, manual = _snapshot(
            store, include_watched=True, include_exploration=True, include_creators=True
        )
        revision = _transfer_revision(store, data["epoch"], manual["fingerprint"])
        if expected_revision is not None and expected_revision != revision:
            raise ValueError(
                "Preferences changed after the preview. Preview the file again."
            )
        from broader_vids.exploration.coverage import validate_merge

        deadline = time.monotonic() + 20
        with work_scope(lambda: _deadline(deadline)):
            validate_merge(store, bundle["exploration_summary"])
        backup = store.root / "backups" / f"preferences-{uuid.uuid4().hex}.json"
        write_export(
            backup,
            _bundle(
                data,
                _cached_snapshot(store, data),
                watched_video_ids=watched,
                exploration_summary=exploration,
                creator_exclusions=manual["rules"],
            ),
        )
        committed = False
        identifier = uuid.uuid4().hex
        try:
            previous = data["profile"]
            payload = {k: v for k, v in previous.items() if k != "revision"}
            interests = bundle["preferences"]["interests"]
            payload.update(
                interests=interests,
                updated_at=utc_now(),
                learning="explicit_onboarding_only",
            )
            profile_revision = previous.get("revision", 0) + (
                previous.get("revision", 0) == 0 or previous["interests"] != interests
            )
            baseline = {
                "id": identifier,
                "parameters": bundle["parameters"],
                "cutoff_action": data["epoch"]["feedback"],
            }
            deadline = time.monotonic() + 20
            with work_scope(lambda: _deadline(deadline)):
                restored_data = _snapshot(
                    store,
                    baseline_override=baseline,
                    profile_override={"revision": profile_revision, **payload},
                )
                snapshot = train(restored_data)
                model_id = _digest(restored_data)
                serialized = json.dumps(snapshot)
                check_work()
            commit_deadline = time.monotonic() + 20
            with work_scope(lambda: _deadline(commit_deadline)), store.db:
                store.db.execute("BEGIN IMMEDIATE")
                if _transfer_revision(store) != revision:
                    raise ValueError(
                        "Feedback or creator rules changed during import. Preview the file again."
                    )
                store.db.execute(
                    "INSERT INTO user_profile VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                    (profile_revision, json.dumps(payload)),
                )
                _, import_summary, _ = _coverage()
                import_summary(store, bundle["exploration_summary"])
                timestamp = utc_now()

                def watched_rows():
                    for index, key in enumerate(bundle["watched_video_ids"]):
                        if index % 64 == 0:
                            check_work()
                        identity = canonical_identity(key)
                        yield (
                            key,
                            identity["platform"],
                            json.dumps(identity),
                            timestamp,
                        )

                store.db.executemany(
                    "INSERT INTO video_state VALUES(?,?,?,1,0,0,?) "
                    "ON CONFLICT(canonical_id) DO UPDATE SET watched=1,updated_at=excluded.updated_at WHERE video_state.watched=0",
                    watched_rows(),
                )
                _replace_creators(
                    store,
                    bundle["creator_exclusions"],
                    timestamp,
                    current=manual["rules"],
                )
                store.create(
                    "both",
                    {"kind": "preference_import", "format_version": bundle["version"]},
                    {
                        "parameters": bundle["parameters"],
                        "cutoff_action": baseline["cutoff_action"],
                        "source_checksum": bundle["checksum"],
                    },
                    commit=False,
                    run_id=identifier,
                )
                store.db.execute(
                    "UPDATE runs SET status='completed' WHERE id=?", (identifier,)
                )
                store.db.execute(
                    "INSERT OR REPLACE INTO learning_models VALUES(?,?,?)",
                    (model_id, utc_now(), serialized),
                )
                prune_models(store, model_id)
                check_work()
            committed = True
        finally:
            if not committed:
                try:
                    applied = store.db.execute(
                        "SELECT 1 FROM runs WHERE id=? AND status='completed'",
                        (identifier,),
                    ).fetchone()
                except sqlite3.Error:
                    applied = True
                if not applied:
                    backup.unlink(missing_ok=True)
        return {
            "imported": True,
            "backup_path": str(backup),
            "snapshot_id": model_id,
            "mode": "replace",
            "watched_videos": len(bundle["watched_video_ids"]),
            **_creator_summary(bundle),
        }
