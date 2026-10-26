import http.cookiejar
import json
import os
import subprocess
import sys
import time
import math

from deepvids.evidence.media import EvidencePending
from deepvids.evidence.service import prepare_evidence
from deepvids.processes import _terminate, start_owned, release_owned
from deepvids.storage import Store
from deepvids.storage import utc_now


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
    process = start_owned(
        [sys.executable, "-m", "deepvids.evidence.preparation_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )
    deadline, sent = time.monotonic() + seconds, False
    try:
        while process.poll() is None:
            if cancelled.is_set():
                raise InterruptedError("The parent cancelled media preparation.")
            left = deadline - time.monotonic()
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
            raise EvidencePending(
                "The preparation worker stopped; its evidence checkpoint is preserved.",
                retryable=True,
            )
    except BaseException:
        try:
            _terminate(process)
        finally:
            process.wait(timeout=5)
        raise
    finally:
        release_owned(process)
        if process.stdin is not None:
            process.stdin.close()


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
            prior = store.get(identifier)["elapsed"]
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


if __name__ == "__main__":
    main()
