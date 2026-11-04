import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from broader_vids.evidence.media import EvidencePending
from broader_vids.evidence.service import atomic_json
from broader_vids.processes import run_bounded


_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_MODEL = re.compile(r"(?:claude-[A-Za-z0-9][A-Za-z0-9._-]{0,119}|sonnet|opus|haiku)\Z")
_MAX_RESULT = 128 * 1024
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_INPUT_BYTES = 10 * 1024 * 1024
_WINDOWS = os.name == "nt"


def resolve_settings(model=None, effort=None, *, environ=None):
    env = os.environ if environ is None else environ
    model = model or env.get("BROADER_VIDS_CLAUDE_MODEL")
    effort = effort or env.get("BROADER_VIDS_CLAUDE_EFFORT")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ValueError(
            "Set an explicit supported Claude model with --model or BROADER_VIDS_CLAUDE_MODEL."
        )
    if not isinstance(effort, str) or effort not in _EFFORTS:
        raise ValueError(
            "Set a Claude effort with --effort or BROADER_VIDS_CLAUDE_EFFORT: low, medium, high, xhigh or max."
        )
    return model, effort


def _worker_environment(model=None, effort=None):
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if model is not None:
        env["ANTHROPIC_MODEL"] = model
    if effort is not None:
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    return env


def command():
    executable = shutil.which("claude")
    if not executable:
        return None
    if _WINDOWS and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        cli = (
            Path(executable).parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "cli.js"
        )
        node = shutil.which("node")
        return [node, str(cli)] if node and cli.is_file() else None
    return [executable]


def auth_check():
    executable = command()
    if executable is None:
        return {
            "status": "missing",
            "remedy": "Install Claude Code and sign in with `claude`.",
        }
    try:
        result = run_bounded(
            [*executable, "auth", "status", "--json"],
            env=_worker_environment(),
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired, InterruptedError):
        return {
            "status": "unverified",
            "remedy": "Check Claude Code with `claude auth status --json` and try again.",
        }
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        payload = None
    if (
        result.returncode == 0
        and isinstance(payload, dict)
        and payload.get("loggedIn") is True
    ):
        return {"status": "available", "remedy": None}
    if (
        result.returncode == 1
        and isinstance(payload, dict)
        and payload.get("loggedIn") is False
    ):
        return {
            "status": "missing",
            "remedy": "Sign in with `claude` and verify with `claude auth status --json`.",
        }
    return {
        "status": "unverified",
        "remedy": "Check Claude Code with `claude auth status --json` and try again.",
    }


def _check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise EvidencePending(
            "Claude assessment was cancelled.",
            scope="candidate",
            category="cancelled",
            retryable=True,
        )


def _message(prompt, images, *, cancelled=None):
    _check_cancelled(cancelled)
    if len(images) > 16:
        raise EvidencePending("Claude assessment exceeds the frame count limit.")
    if len(prompt) > _MAX_INPUT_BYTES:
        raise EvidencePending(
            "Claude assessment input exceeds the 10 MiB stream limit."
        )
    prefix = (
        '{"type":"user","session_id":"","parent_tool_use_id":null,'
        '"message":{"role":"user","content":['
    )
    suffix = "]}}\n"
    text = json.dumps(
        {"type": "text", "text": prompt}, ensure_ascii=False, separators=(",", ":")
    )
    size = len(prefix) + len(suffix) + len(text.encode("utf-8"))
    if size > _MAX_INPUT_BYTES:
        raise EvidencePending(
            "Claude assessment input exceeds the 10 MiB stream limit."
        )
    parts = [prefix, text]
    for image in images:
        _check_cancelled(cancelled)
        path = Path(image)
        suffix_type = path.suffix.casefold()
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix_type)
        if media_type is None:
            raise EvidencePending(
                "Claude assessment received an unsupported frame image."
            )
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            raise EvidencePending(
                "Claude assessment received an oversized frame image."
            )
        with path.open("rb") as handle:
            data = handle.read(_MAX_IMAGE_BYTES + 1)
        if len(data) > _MAX_IMAGE_BYTES:
            raise EvidencePending(
                "Claude assessment received an oversized frame image."
            )
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": ""},
        }
        size += (
            1
            + len(json.dumps(block, separators=(",", ":")))
            + 4 * ((len(data) + 2) // 3)
        )
        if size > _MAX_INPUT_BYTES:
            raise EvidencePending(
                "Claude assessment input exceeds the 10 MiB stream limit."
            )
        block["source"]["data"] = base64.b64encode(data).decode("ascii")
        parts.extend((",", json.dumps(block, separators=(",", ":"))))
    parts.append(suffix)
    return "".join(parts)


def _token_count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _usage_from(value):
    if not isinstance(value, dict):
        return None
    regular_input = _token_count(value.get("input_tokens"))
    output_tokens = _token_count(value.get("output_tokens"))
    if regular_input is None or output_tokens is None:
        return None
    cached = _token_count(value.get("cache_read_input_tokens"))
    created = _token_count(value.get("cache_creation_input_tokens"))
    if ("cache_read_input_tokens" in value and cached is None) or (
        "cache_creation_input_tokens" in value and created is None
    ):
        return None
    input_tokens = regular_input + (cached or 0) + (created or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
    }


def _stream_result(stream):
    final = None
    fallback = {}
    has_error = False
    provider_error = False
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            final = event
        elif event.get("type") == "assistant":
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            identity = (
                (message.get("id") if isinstance(message, dict) else None)
                or event.get("uuid")
                or event.get("id")
            )
            measured = _usage_from(usage)
            if measured is not None:
                if not isinstance(identity, (str, int)):
                    identity = ("unidentified", len(fallback))
                fallback[identity] = measured
        if event.get("is_error") is True or event.get("type") == "error":
            has_error = True
            provider_error |= _is_provider_error(event)
    scorecard = final.get("structured_output") if isinstance(final, dict) else None
    if isinstance(final, dict):
        has_error = final.get("is_error") is True or final.get("subtype") != "success"
        provider_error = (
            (provider_error or _is_provider_error(final)) if has_error else False
        )
    usage = _usage_from(final.get("usage")) if isinstance(final, dict) else None
    if usage is None and fallback:
        usage_rows = list(fallback.values())
    else:
        usage_rows = []
    if usage is None and usage_rows:
        usage = {
            "input_tokens": sum(row["input_tokens"] for row in usage_rows),
            "output_tokens": sum(row["output_tokens"] for row in usage_rows),
            "cached_input_tokens": (
                sum(row["cached_input_tokens"] for row in usage_rows)
                if all(row["cached_input_tokens"] is not None for row in usage_rows)
                else None
            ),
        }
    return scorecard, usage, has_error, provider_error


def _is_provider_error(event):
    error = event.get("error")
    message = (
        error.get("message", "")
        if isinstance(error, dict)
        else event.get("message", "")
    )
    code = error.get("type", "") if isinstance(error, dict) else ""
    details = event.get("errors")
    details = (
        " ".join(value[:1000] for value in details[:4] if isinstance(value, str))
        if isinstance(details, list)
        else ""
    )
    result = event.get("result")
    result = result[:4000] if isinstance(result, str) else ""
    text = f"{code} {message} {event.get('subtype', '')} {details} {result}".casefold()
    return any(
        term in text
        for term in (
            "auth",
            "unauthorized",
            "forbidden",
            "not logged in",
            "invalid api key",
            "401",
            "403",
            "rate_limit",
            "rate limit",
            "429",
            "too many requests",
            "usage_limit",
            "usage limit",
            "quota",
        )
    )


def run_claude(
    prompt,
    schema_path,
    output_path,
    images,
    timeout,
    *,
    model=None,
    effort=None,
    cancelled=None,
):
    from broader_vids.token_usage import capture

    model, effort = resolve_settings(model, effort)
    deadline = time.monotonic() + timeout
    output_path = Path(output_path)
    output_path.unlink(missing_ok=True)
    _check_cancelled(cancelled)
    executable = command()
    if executable is None:
        raise EvidencePending(
            "Claude Code CLI is unavailable.", scope="run", category="setup"
        )
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    if len(schema_text.encode("utf-8")) > 20 * 1024:
        raise EvidencePending(
            "The scorecard schema exceeds the Claude CLI argument limit."
        )
    worker_command = [
        *executable,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema_text,
        "--model",
        model,
        "--effort",
        effort,
        "--no-session-persistence",
        "--safe-mode",
        "--tools",
        "",
    ]
    payload = _message(prompt, images, cancelled=cancelled)
    _check_cancelled(cancelled)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvidencePending(
            "Claude assessment timed out before startup.",
            category="timeout",
            retryable=True,
        )
    try:
        process = run_bounded(
            worker_command,
            input=payload,
            env=_worker_environment(model, effort),
            cwd=output_path.parent,
            timeout=remaining,
            cancelled=cancelled,
        )
    except (OSError, subprocess.TimeoutExpired, InterruptedError) as error:
        _, interrupted_usage, _, _ = _stream_result(getattr(error, "output", "") or "")
        capture(interrupted_usage)
        if isinstance(error, subprocess.TimeoutExpired):
            raise EvidencePending(
                "Claude assessment timed out.",
                scope="candidate",
                category="timeout",
                retryable=True,
            ) from error
        if isinstance(error, InterruptedError):
            raise EvidencePending(
                "Claude assessment was cancelled.",
                scope="candidate",
                category="cancelled",
                retryable=True,
            ) from error
        raise EvidencePending(
            "Claude assessment could not start.", scope="run", category="setup"
        ) from error
    scorecard, usage, has_error, provider_error = _stream_result(process.stdout)
    capture(usage)
    if process.returncode or has_error or not isinstance(scorecard, dict):
        provider_error |= _is_provider_error({"message": process.stderr})
        scope = "run" if provider_error else "candidate"
        category = "provider" if has_error or process.returncode else "invalid_response"
        raise EvidencePending(
            "Claude assessment failed; no replacement score was generated.",
            scope=scope,
            category=category,
        )
    try:
        encoded = json.dumps(
            scorecard, ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise EvidencePending(
            "Claude assessment returned an invalid scorecard."
        ) from None
    if len(encoded) > _MAX_RESULT:
        raise EvidencePending("The model response exceeded the scorecard size limit.")
    atomic_json(output_path, scorecard)
    return scorecard, usage
