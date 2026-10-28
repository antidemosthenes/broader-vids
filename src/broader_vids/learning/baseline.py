import json


def current_baseline(store):
    row = store.db.execute(
        "SELECT id,state FROM runs WHERE json_extract(settings,'$.kind')='preference_import' AND status='completed' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return {"id": row["id"], **json.loads(row["state"])} if row else None
