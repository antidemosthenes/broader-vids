import http.cookiejar
import json
import os
import subprocess
import sys
import time
import math
import tempfile

from broader_vids.evidence.media import EvidencePending, public_error
from broader_vids.evidence.failures import save_failure
from broader_vids.processes import ProcessCleanupError
from broader_vids.evidence.service import prepare_evidence
from broader_vids.processes import _terminate, start_owned, release_owned
from broader_vids.storage import Store
from broader_vids.storage import utc_now


COOKIE_FIELDS = (
    "version",
    "name",
    "value",
    "port",
    "port_specified",
    "domain",
    "domain_specified",
    "domain_initial_dot",
    "path",
    "path_specified",
    "secure",
    "expires",
    "discard",
    "comment",
    "comment_url",
    "rfc2109",
)


def _prepare_bounded_owned(
    root,
    identifier,
    asr_model,
    seconds,
    byte_limit,
    cookie_jar,
    cancelled,
    attempt_token=None,
    lease_owner=None,
):
    Store.validate_id(identifier)
    if (
        type(byte_limit) is not int
        or byte_limit < 0
        or type(seconds) not in {int, float}
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise ValueError(
            "Preparation requires positive finite time and a nonnegative byte allowance."
        )
    if cancelled.is_set():
        raise InterruptedError("Media preparation was cancelled before launch.")
    cookies = (
        [
            {
                **{field: getattr(cookie, field) for field in COOKIE_FIELDS},
                "rest": cookie._rest,
            }
            for cookie in cookie_jar
        ]
        if cookie_jar is not None
        else None
    )
    payload = json.dumps(
        {
            "root": str(root),
            "run_id": identifier,
            "asr_model": asr_model,
            "seconds": seconds,
            "bytes": byte_limit,
            "cookies": cookies,
            "attempt_token": attempt_token,
            "lease_owner": lease_owner,
        }
    ).encode()
    if len(payload) > 64 * 1024:
        raise EvidencePending("The in-memory preparation request exceeds 64 KiB.")
    options = (
        {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
        if os.name == "nt"
        else {"start_new_session": True}
    )
    diagnostics = tempfile.TemporaryFile()
    try:
        process = start_owned(
            [sys.executable, "-m", "broader_vids.evidence.preparation_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=diagnostics,
            **options,
        )
    except BaseException:
        diagnostics.close()
        raise
    deadline, sent = time.monotonic() + seconds, False
    try:
        while process.poll() is None:
            if cancelled.is_set():
                raise InterruptedError("The parent cancelled media preparation.")
            left = deadline - time.monotonic()
            if os.fstat(diagnostics.fileno()).st_size > 1024 * 1024:
                raise EvidencePending("Preparation diagnostics exceeded 1 MiB.")
            if left <= 0:
                raise subprocess.TimeoutExpired("media preparation", seconds)
            try:
                process.communicate(
                    input=payload if not sent else None, timeout=min(0.2, left)
                )
            except subprocess.TimeoutExpired:
                pass
            sent = True
        if process.returncode:
            diagnostics.seek(max(0, os.fstat(diagnostics.fileno()).st_size - 4096))
            raise EvidencePending(
                "The preparation worker stopped. "
                + public_error(diagnostics.read().decode("utf-8", errors="replace")),
                retryable=True,
            )
    except BaseException as error:
        try:
            _terminate(process)
        finally:
            process.wait(timeout=5)
        diagnostics.seek(max(0, os.fstat(diagnostics.fileno()).st_size - 4096))
        error.stderr = public_error(
            diagnostics.read().decode("utf-8", errors="replace")
        )
        raise
    finally:
        release_owned(process)
        if process.stdin is not None:
            process.stdin.close()
        diagnostics.close()


def prepare_bounded(
    root,
    identifier,
    asr_model,
    seconds,
    byte_limit,
    cookie_jar,
    cancelled,
    attempt_token=None,
    lease_owner=None,
):
    Store.validate_id(identifier)
    store = Store(root)
    try:
        with store.lease(identifier, owner=lease_owner) as owner:
            baseline = store.get(identifier)
            prior = baseline["elapsed"]
            started = time.monotonic()
            try:
                return _prepare_bounded_owned(
                    root,
                    identifier,
                    asr_model,
                    seconds,
                    byte_limit,
                    cookie_jar,
                    cancelled,
                    attempt_token,
                    owner,
                )
            except BaseException as error:
                if not isinstance(error, ProcessCleanupError):
                    current = store.get(identifier)
                    state = current["state"]
                    failure = save_failure(
                        current, error, state.get("stage", "preparation")
                    )
                    state["retryable_failure"] = True
                    current.update(status="pending", reason=failure["reason"])
                    resources = state.setdefault("resources", {})
                    resources["preparation_seconds"] = max(
                        resources.get("preparation_seconds", 0),
                        baseline["state"]
                        .get("resources", {})
                        .get("preparation_seconds", 0)
                        + time.monotonic()
                        - started,
                    )
                    progress = state.get("preparation_progress", {})
                    before = (
                        baseline["state"]
                        .get("resources", {})
                        .get("media_download_bytes", 0)
                    )
                    observed = max(0, resources.get("media_download_bytes", 0) - before)
                    if attempt_token:
                        known = progress.get("token") == attempt_token
                        charge = (
                            min(
                                byte_limit,
                                max(observed, progress.get("new_bytes", 0))
                                + progress.get("unreported_byte_bound", 0),
                            )
                            if known
                            else byte_limit
                        )
                        state.setdefault("preparation_usage", {})[attempt_token] = (
                            charge
                        )
                        state.setdefault("preparation_usage_details", {})[
                            attempt_token
                        ] = {
                            "observed_bytes": observed,
                            "charged_bytes": charge,
                            "upper_bound": not known
                            or bool(progress.get("unreported_byte_bound")),
                            "interrupted": True,
                        }
                        state["preparation_receipt"] = attempt_token
                    store.save(current, ("preparation_interrupted", failure))
                raise
            finally:
                with store.db:
                    store.db.execute(
                        "UPDATE runs SET elapsed=MAX(elapsed,?),updated_at=? WHERE id=?",
                        (prior + time.monotonic() - started, utc_now(), identifier),
                    )
    finally:
        store.close()


def main():
    payload = sys.stdin.buffer.read(64 * 1024 + 1)
    if len(payload) > 64 * 1024:
        raise ValueError("Preparation input exceeds its size limit.")
    request = json.loads(payload)
    jar = None
    if request["cookies"] is not None:
        jar = http.cookiejar.CookieJar()
        for cookie in request.pop("cookies"):
            jar.set_cookie(http.cookiejar.Cookie(**cookie))
    store = Store(request["root"])
    try:
        before = store.get(request["run_id"])["state"]["resources"][
            "media_download_bytes"
        ]
        prepare_evidence(
            store,
            request["run_id"],
            request["asr_model"],
            max_seconds=request["seconds"],
            max_new_bytes=request["bytes"],
            cookie_jar=jar,
            lease_owner=request.get("lease_owner"),
            attempt_token=request.get("attempt_token"),
        )
        record_preparation_receipt(
            store, request["run_id"], request["attempt_token"], before
        )
    finally:
        store.close()


def record_preparation_receipt(store, identifier, token, before):
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        current = store.get(identifier)
        usage = max(0, current["state"]["resources"]["media_download_bytes"] - before)
        if token is not None:
            path = "$.preparation_usage." + json.dumps(token)
            store.db.execute(
                "UPDATE runs SET state=json_set(state,'$.preparation_receipt',?,?,?),updated_at=? WHERE id=?",
                (token, path, usage, utc_now(), identifier),
            )
            details = "$.preparation_usage_details." + json.dumps(token)
            store.db.execute(
                "UPDATE runs SET state=json_set(state,?,json(?)) WHERE id=?",
                (
                    details,
                    json.dumps(
                        {
                            "observed_bytes": usage,
                            "charged_bytes": usage,
                            "upper_bound": False,
                        }
                    ),
                    identifier,
                ),
            )


if __name__ == "__main__":
    main()
