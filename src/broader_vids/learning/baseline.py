import json


def current_baseline(store, *, identity_only=False):
    fields = "id" if identity_only else "id,state"
    row = store.db.execute(
        f"SELECT {fields} FROM runs WHERE json_extract(settings,'$.kind')='preference_import' AND status='completed' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return (
        {"id": row["id"]}
        if identity_only
        else {"id": row["id"], **json.loads(row["state"])}
    )
