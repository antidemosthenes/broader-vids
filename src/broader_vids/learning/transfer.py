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
from broader_vids.preferences import ALIASES, platform_lock
from broader_vids.storage import utc_now


FORMAT = "broader-vids.preferences"
MAX_BYTES = 8 * 1024 * 1024
MAX_PARAMETERS = 384


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
        ),
        "Preference file",
    )
    if (
        bundle["format"] != FORMAT
        or type(bundle["version"]) is not int
        or bundle["version"] != 1
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


def _snapshot(store, **overrides):
    from broader_vids.learning.service import source_data

    if store.db.in_transaction:
        raise ValueError(
            "Finish the current database transaction before transferring preferences."
        )
    store.db.execute("BEGIN")
    try:
        return source_data(store, **overrides)
    finally:
        store.db.rollback()


def _bundle(data, snapshot=None):
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
            "version": 1,
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "policy": _policy(),
            "created_at": utc_now(),
            "engine_version": __version__,
            "preferences": {"interests": data["profile"]["interests"]},
            "parameters": parameters,
        }
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
    data = _snapshot(store)
    return _bundle(data, _cached_snapshot(store, data))


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


def preview_import(store, bundle, *, _validated=False):
    from broader_vids.learning.service import change_epoch

    deadline = time.monotonic() + 20
    with work_scope(lambda: _deadline(deadline)):
        parameters = (bundle if _validated else validate_bundle(bundle))["parameters"]
    return {
        "compatible": True,
        "interests": bundle["preferences"]["interests"],
        "model_parameters": sum(len(m["names"]) for m in parameters["models"].values())
        + sum(len(m["names"]) for m in parameters["structured"]["quality"]["models"]),
        "preference_cells": {
            key: len(value) for key, value in parameters["structured"]["groups"].items()
        },
        "revision": _digest(change_epoch(store)),
        "checksum": bundle["checksum"],
        "mode": "replace",
    }


def import_preferences(store, bundle, *, expected_revision=None, _validated=False):
    from broader_vids.learning.service import (
        LEARNING_LOCK,
        change_epoch,
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
        data = _snapshot(store)
        revision = _digest(data["epoch"])
        if expected_revision is not None and expected_revision != revision:
            raise ValueError(
                "Preferences changed after the preview. Preview the file again."
            )
        backup = store.root / "backups" / f"preferences-{uuid.uuid4().hex}.json"
        write_export(backup, _bundle(data, _cached_snapshot(store, data)))
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
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                if _digest(change_epoch(store)) != revision:
                    raise ValueError(
                        "Feedback changed during import. Preview the file again."
                    )
                store.db.execute(
                    "INSERT INTO user_profile VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                    (profile_revision, json.dumps(payload)),
                )
                store.create(
                    "both",
                    {"kind": "preference_import", "format_version": 1},
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
        }
