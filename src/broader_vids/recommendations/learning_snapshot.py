import json

from broader_vids.learning.service import ensure_model


def round_learning(store, run):
    reference = run["state"].get("learning_snapshot")
    if not reference:
        return None
    row = store.db.execute(
        "SELECT payload FROM learning_models WHERE id=?", (reference["id"],)
    ).fetchone()
    if not row:
        raise ValueError(
            "The saved round model is unavailable; restore its profile backup."
        )
    return {"id": reference["id"], **json.loads(row[0])}


def freeze_learning(store, run):
    if run["state"].get("learning_snapshot"):
        round_learning(store, run)
        return
    for _ in range(3):
        model = ensure_model(store)
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            if not store.db.execute(
                "SELECT 1 FROM learning_models WHERE id=?", (model["id"],)
            ).fetchone():
                continue
            run["state"]["learning_snapshot"] = {
                "id": model["id"],
                "version": model["version"],
            }
            store.save(run)
            return
    raise ValueError(
        "Preferences changed while saving the round model; retry this round."
    )
