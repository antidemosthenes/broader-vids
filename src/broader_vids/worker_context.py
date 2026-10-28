from functools import lru_cache
import json
import os
from pathlib import Path
import re


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


@lru_cache(maxsize=8)
def _turn_settings(path, modified, size):
    selected = None
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") != "turn_context":
                continue
            payload = record.get("payload") or {}
            settings = (payload.get("collaboration_mode") or {}).get("settings") or {}
            selected = (
                payload.get("model") or settings.get("model"),
                payload.get("effort")
                or payload.get("reasoning_effort")
                or settings.get("reasoning_effort"),
            )
    return selected


def resolve_worker_settings(model=None, effort=None, *, environ=None):
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
