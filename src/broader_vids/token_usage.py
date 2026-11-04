from contextvars import ContextVar
import json
import uuid

from broader_vids.storage import utc_now


_CAPTURE = ContextVar("broader_vids_usage_capture", default=None)
STAGES = {
    "exploration": "Topic exploration",
    "screening": "Search and screening",
    "preparation": "Evidence preparation",
    "assessment": "Quality assessment",
    "selection": "Scoring and selection",
}


def normalize(usage):
    if not isinstance(usage, dict):
        return None
    values = {}
    for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
        value = usage.get(name)
        if type(value) is int and 0 <= value <= 10**12:
            values[name] = value
        elif name == "cached_input_tokens" and value is None:
            values[name] = None
        else:
            return None
    if (
        values["cached_input_tokens"] is not None
        and values["cached_input_tokens"] > values["input_tokens"]
    ):
        return None
    if "reasoning_output_tokens" in usage:
        reasoning = usage["reasoning_output_tokens"]
        values["reasoning_output_tokens"] = (
            reasoning
            if type(reasoning) is int and 0 <= reasoning <= values["output_tokens"]
            else None
        )
    return values


def from_jsonl(text):
    values = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") in {
            "turn.completed",
            "turn.failed",
        }:
            usage = normalize(event.get("usage"))
            if usage is not None:
                values.append(usage)
    if not values:
        return None
    result = {
        name: sum(value[name] for value in values)
        if all(value[name] is not None for value in values)
        else None
        for name in ("input_tokens", "output_tokens", "cached_input_tokens")
    }
    if any("reasoning_output_tokens" in value for value in values):
        result["reasoning_output_tokens"] = (
            sum(value["reasoning_output_tokens"] for value in values)
            if all(value.get("reasoning_output_tokens") is not None for value in values)
            else None
        )
    return result


def capture(usage):
    callback = _CAPTURE.get()
    if callback is not None:
        callback(usage)


def tracked_call(store, owner, source, stage, attempt, runner, *args, **kwargs):
    receipt = {
        "version": 1,
        "call_id": uuid.uuid4().hex,
        "source_run": source,
        "stage": stage,
        "attempt": attempt,
        "status": "started",
        "usage": None,
    }

    def save(status):
        receipt["status"] = status
        with store.db:
            store.db.execute(
                "CREATE INDEX IF NOT EXISTS token_usage_lookup ON events(kind,run_id,id)"
            )
            store.db.execute(
                "INSERT INTO events(run_id,created_at,kind,payload) VALUES(?,?,'token_usage',?)",
                (owner, utc_now(), json.dumps(receipt)),
            )

    def measured(usage):
        value = normalize(usage)
        if value is not None:
            receipt["usage"] = value
            save("reported")

    save("started")
    token = _CAPTURE.set(measured)
    try:
        result, usage = runner(*args, **kwargs)
        if normalize(usage) != receipt["usage"]:
            measured(usage)
        save("completed")
        return result, usage
    except BaseException:
        save("failed")
        raise
    finally:
        _CAPTURE.reset(token)


def _legacy_calls(store, run, ledger):
    state, settings = run["state"], run["settings"]
    seen = {
        (call.get("source_run"), call.get("stage"), call.get("attempt"))
        for call in ledger
    }
    result = []
    source_id = state.get("discovery_run")
    if source_id:
        try:
            source = store.get(source_id)
        except ValueError:
            source = {"state": {}}
        offset = settings.get("discovery_initial_planning_calls", 0)
        for index, attempt in enumerate(
            source["state"].get("exploration", {}).get("attempts", []), 1
        ):
            if index > offset and (source_id, "exploration", index) not in seen:
                result.append(
                    {
                        "source_run": source_id,
                        "stage": "exploration",
                        "attempt": index,
                        "usage": normalize(attempt.get("token_usage")),
                        "legacy": True,
                    }
                )
    evidence_ids = {
        value["evidence_run"]
        for collection in (state.get("evidence", {}), state.get("inflight", {}))
        for value in collection.values()
        if isinstance(value, dict) and value.get("evidence_run")
    }
    for identifier in evidence_ids:
        try:
            child = store.get(identifier)
        except ValueError:
            continue
        attempts = child["state"].get("attempts", [])
        for index, attempt in enumerate(attempts, 1):
            if (identifier, "assessment", index) in seen:
                continue
            started = attempt.get("started_at", "")
            if not (run["created_at"] <= started <= run["updated_at"]):
                continue
            usage = normalize(attempt.get("token_usage"))
            if usage is None and attempt.get("status") == "completed":
                assessment = (
                    child["state"].get("assessments", {}).get(attempt.get("mode"), {})
                )
                usage = normalize(assessment.get("usage", {}).get("token_usage"))
            result.append(
                {
                    "work_owned": any(
                        work.get("evidence_run") == identifier
                        and work.get("preparation_token") == attempt.get("work_token")
                        and attempt.get("work_token")
                        for work in state.get("inflight", {}).values()
                    ),
                    "source_run": identifier,
                    "stage": "assessment",
                    "attempt": index,
                    "usage": usage,
                    "legacy": True,
                }
            )
    return result


def _member_calls(store, run):
    ledger = {}
    for row in store.db.execute(
        "SELECT payload FROM events WHERE kind='token_usage' AND run_id=? ORDER BY id",
        (run["id"],),
    ):
        call = json.loads(row[0])
        ledger[call["call_id"]] = call
    calls = list(ledger.values())
    legacy = (
        []
        if run["settings"].get("token_usage_version") == 1
        else _legacy_calls(store, run, calls)
    )
    calls.extend(
        call
        for call in legacy
        if call["stage"] == "exploration" or call.get("work_owned")
    )
    remaining = max(
        0,
        run["state"].get("model_calls", 0)
        - sum(call["stage"] == "assessment" for call in calls),
    )
    uncertain = [
        call
        for call in legacy
        if call["stage"] == "assessment" and not call.get("work_owned")
    ]
    if len(uncertain) <= remaining:
        calls.extend(uncertain)
    for stage, expected in (
        ("exploration", run["state"].get("planning_model_calls", 0)),
        ("assessment", run["state"].get("model_calls", 0)),
    ):
        observed = sum(call["stage"] == stage for call in calls)
        calls.extend(
            {"stage": stage, "usage": None, "legacy": True}
            for _ in range(max(0, expected - observed))
        )
    return calls


def report(store, identifier=None):
    roots = [
        dict(row)
        for row in store.db.execute("""SELECT id,platform,status,created_at FROM runs
        WHERE json_extract(settings,'$.kind')='recommendation_batch'
        OR (json_extract(settings,'$.kind')='recommendation' AND json_extract(settings,'$.batch_id') IS NULL)
        ORDER BY created_at DESC,id DESC LIMIT 20""")
    ]
    if identifier is None:
        identifier = roots[0]["id"] if roots else None
    if identifier is None:
        return {"runs": roots, "report": None}
    root = store.get(identifier)
    kind = root["settings"].get("kind")
    if kind not in {"recommendation", "recommendation_batch"}:
        raise ValueError("Select a recommendation round for token usage.")
    members = (
        [
            store.get(child)
            for child in dict.fromkeys(root["state"].get("children", {}).values())
        ]
        if kind == "recommendation_batch"
        else [root]
    )
    if any(
        member["settings"].get("kind") != "recommendation"
        or (
            kind == "recommendation_batch"
            and member["settings"].get("batch_id") != identifier
        )
        for member in members
    ):
        raise ValueError("The recommendation batch has invalid child ownership.")
    stages = {
        key: {
            "key": key,
            "label": label,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "calls": 0,
            "reported_calls": 0,
            "cached_reported_calls": 0,
        }
        for key, label in STAGES.items()
    }
    platforms = []
    legacy = False
    for member in members:
        platform_total = 0
        platform_calls = 0
        platform_reported = 0
        for call in _member_calls(store, member):
            stage = stages.get(call["stage"])
            if stage is None:
                continue
            stage["calls"] += 1
            platform_calls += 1
            legacy |= bool(call.get("legacy"))
            usage = normalize(call.get("usage"))
            if usage is None:
                continue
            stage["reported_calls"] += 1
            platform_reported += 1
            for name in ("input_tokens", "output_tokens"):
                stage[name] += usage[name]
            if usage["cached_input_tokens"] is not None:
                stage["cached_input_tokens"] += usage["cached_input_tokens"]
                stage["cached_reported_calls"] += 1
            platform_total += usage["input_tokens"] + usage["output_tokens"]
        platforms.append(
            {
                "platform": member["platform"],
                "status": member["status"],
                "tokens": platform_total,
                "unreported_calls": platform_calls - platform_reported,
            }
        )
    totals = {
        name: sum(stage[name] for stage in stages.values())
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "calls",
            "reported_calls",
            "cached_reported_calls",
        )
    }
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["unreported_calls"] = totals["calls"] - totals["reported_calls"]
    for stage in stages.values():
        stage["total_tokens"] = stage["input_tokens"] + stage["output_tokens"]
        stage["share_percent"] = (
            100 * stage["total_tokens"] / totals["total_tokens"]
            if totals["total_tokens"]
            and (not stage["calls"] or stage["reported_calls"])
            else None
        )
        stage["unreported_calls"] = stage["calls"] - stage["reported_calls"]
    result = {
        "runs": roots,
        "report": {
            "run_id": identifier,
            "platform": root["platform"],
            "status": root["status"],
            "created_at": root["created_at"],
            "totals": totals,
            "stages": list(stages.values()),
            "platforms": platforms,
            "legacy": legacy,
            "scope": "Recommendation model calls only. The host chat is not included. Input totals already include cached input.",
        },
    }
    if root["settings"].get("worker_host") == "claude":
        result["report"]["worker_host"] = "claude"
    return result
