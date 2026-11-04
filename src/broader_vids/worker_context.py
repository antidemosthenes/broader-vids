from functools import lru_cache, partial
import json
import os
from pathlib import Path
import re
import time


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _recent_lines(path):
    deadline = time.monotonic() + 3
    with Path(path).open("rb") as handle:
        position = handle.seek(0, 2)
        pending, skipping = b"", False
        while position:
            if time.monotonic() >= deadline:
                return
            length = min(65536, position)
            position -= length
            handle.seek(position)
            block = handle.read(length)
            if skipping:
                boundary = block.rfind(b"\n")
                if boundary < 0:
                    continue
                block, skipping = block[: boundary + 1], False
            lines = (block + pending).split(b"\n")
            pending = lines[0]
            for line in reversed(lines[1:]):
                if len(line) <= 1024 * 1024:
                    yield line
            if len(pending) > 1024 * 1024:
                pending, skipping = b"", True
        if pending and not skipping:
            yield pending


@lru_cache(maxsize=8)
def _turn_settings(path, modified, size):
    for line in _recent_lines(path):
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(record, dict) or record.get("type") != "turn_context":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        mode = payload.get("collaboration_mode")
        settings = mode.get("settings") if isinstance(mode, dict) else None
        settings = settings if isinstance(settings, dict) else {}
        return (
            payload.get("model") or settings.get("model"),
            payload.get("effort")
            or payload.get("reasoning_effort")
            or settings.get("reasoning_effort"),
        )
    return None


def validate_host(host):
    if host not in ("codex", "claude"):
        raise ValueError("Use --host codex or --host claude.")
    return host


def host_options(host):
    return {"host": host} if validate_host(host) == "claude" else {}


def host_snapshot(host):
    return {"worker_host": host} if validate_host(host) == "claude" else {}


def worker_runner(settings, *, cancelled=None):
    host = validate_host(settings.get("worker_host", "codex"))
    if host == "claude":
        from broader_vids.claude_worker import run_claude as runner
    else:
        from broader_vids.evidence.assessment import run_codex as runner
    return partial(
        runner,
        model=settings.get("assessment_model"),
        effort=settings.get("reasoning_effort"),
        cancelled=cancelled,
    )


def resolve_worker_settings(model=None, effort=None, *, environ=None, host="codex"):
    if validate_host(host) == "claude":
        from broader_vids.claude_worker import resolve_settings

        return resolve_settings(model, effort, environ=environ)
    env = os.environ if environ is None else environ
    if model is None or effort is None:
        thread = env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID")
        selected = None
        if thread and re.fullmatch(r"[a-fA-F0-9-]{36}", thread):
            home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
            for path in (home / "sessions").glob(f"**/*{thread}.jsonl"):
                stat = path.stat()
                selected = _turn_settings(str(path), stat.st_mtime_ns, stat.st_size)
                if selected:
                    break
        if selected:
            model = model or selected[0]
            effort = effort or selected[1]
    if (
        not isinstance(model, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", model)
        or effort not in EFFORTS
    ):
        raise ValueError(
            "The invoking chat's model and reasoning effort could not be resolved; pass both --model and --effort from the host's selected settings."
        )
    return model, effort
