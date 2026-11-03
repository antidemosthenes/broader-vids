from contextlib import closing
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

from broader_vids.blacklist import SYNC_LOCK, sync_blacklist
from broader_vids.creators import creator_id
from broader_vids.evidence.browser import bilibili_browser, browser_lease
from broader_vids.evidence.service import atomic_json
from broader_vids.preferences import platform_lock
from broader_vids.processes import run_bounded
from broader_vids.storage import Store, utc_now


class SwitchCleanupPending(ValueError):
    pass


ACTIVE = {
    "starting",
    "opening",
    "awaiting_sign_in",
    "verifying",
    "syncing",
    "activating",
    "cancelling",
}


def access_dir(root):
    return Path(root).resolve() / "access/bilibili"


def job_dir(root, identifier):
    Store.validate_id(identifier)
    target = access_dir(root) / "switches" / identifier
    if (
        not target.resolve().is_relative_to(access_dir(root))
        or target.is_symlink()
        or target.is_junction()
    ):
        raise ValueError(
            "Account-switch files must stay inside the Bilibili access directory."
        )
    return target


def remove_private(root, target):
    base = access_dir(root)
    target = Path(target)
    if (
        not target.resolve().is_relative_to(base)
        or target == base
        or target.is_symlink()
        or target.is_junction()
    ):
        raise ValueError(
            "Account cleanup cannot remove files outside its private access directory."
        )
    deadline = time.monotonic() + 2
    while target.exists():
        try:
            shutil.rmtree(target)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def verified_account(context):
    response = context.request.get(
        "https://api.bilibili.com/x/web-interface/nav", timeout=10000
    )
    value = response.json() if response.ok else None
    if (
        not isinstance(value, dict)
        or type(value.get("code")) is not int
        or value["code"] != 0
    ):
        raise ValueError(
            "Bilibili did not verify the new sign-in. Complete sign-in and try again."
        )
    data = value.get("data")
    if not isinstance(data, dict) or data.get("isLogin") is not True:
        raise ValueError(
            "The new browser is not signed in yet. Complete sign-in and try again."
        )
    name = data.get("uname", "")
    if not isinstance(name, str) or len(name) > 300:
        raise ValueError("Bilibili returned an unsupported account response.")
    return {"id": creator_id("bilibili", data.get("mid")), "name": name}


def prepare_login(root, identifier, timeout=600):
    job = job_dir(root, identifier)

    def report(status, message):
        atomic_json(job / "progress.json", {"status": status, "message": message})

    report("opening", "Opening a separate Bilibili sign-in window.")
    started = time.monotonic()
    with bilibili_browser(
        root, visible=True, profile_path=job / "profile", lease_owner=identifier
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            "https://www.bilibili.com/", wait_until="domcontentloaded", timeout=30000
        )
        report(
            "awaiting_sign_in",
            "Sign in in the Bilibili window. Keep it open, return to the dashboard, and select I've signed in. Broader Vids closes the window when sign-in checks finish.",
        )
        while time.monotonic() - started < timeout:
            if not context.pages:
                raise ValueError(
                    "The sign-in window closed. The previous session remains unchanged."
                )
            confirmation = job / "confirm.request"
            if confirmation.exists():
                confirmation.unlink()
                report("verifying", "Checking the new Bilibili sign-in.")
                try:
                    account = verified_account(context)
                except Exception:
                    report(
                        "awaiting_sign_in",
                        "The new sign-in could not be verified. Finish sign-in, keep the window open, and select I've signed in again.",
                    )
                    continue
                report(
                    "syncing", "Sign-in verified. Checking this account's blacklist."
                )
                with closing(Store(job / "rules")) as store:
                    sync = sync_blacklist(store, context=context)
                    entries = (
                        [
                            dict(row)
                            for row in store.db.execute(
                                "SELECT creator_id,name FROM creator_exclusions WHERE source='bilibili_blacklist' AND account_id=?",
                                (account["id"],),
                            )
                        ]
                        if sync["status"] == "complete"
                        else []
                    )
                if verified_account(context)["id"] != account["id"]:
                    report(
                        "awaiting_sign_in",
                        "The account changed during verification. Select I've signed in again for the intended account.",
                    )
                    continue
                if sync.get("account_id") != account["id"]:
                    sync = {
                        "status": "failed",
                        "error": "Blacklist verification used a different account.",
                        "last_success": None,
                    }
                    entries = []
                result = {"account": account, "sync": sync, "entries": entries}
                break
            page = next((item for item in context.pages if not item.is_closed()), page)
            page.wait_for_timeout(250)
        else:
            raise ValueError(
                "Bilibili sign-in timed out. The previous session remains unchanged."
            )
    atomic_json(job / "result.json", result)


def recover_switch(store):
    base = access_dir(store.root)
    journal_path = base / "switch-commit.json"
    if not journal_path.exists():
        return
    if journal_path.stat().st_size > 4096:
        raise ValueError("The session recovery journal exceeded its size limit.")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if (
        not isinstance(journal, dict)
        or not isinstance(journal.get("id"), str)
        or type(journal.get("had_profile")) is not bool
        or type(journal.get("had_status")) is not bool
    ):
        raise ValueError(
            "The session recovery journal is invalid. Restore it before using Bilibili."
        )
    creator_id("bilibili", journal.get("account_id"))
    job = job_dir(store.root, journal["id"])
    profile, backup = base / "profile", job / "old-profile"
    row = store.db.execute(
        "SELECT payload FROM creator_sync WHERE account_id=?", (journal["account_id"],)
    ).fetchone()
    applied = bool(row and json.loads(row[0]).get("account_switch_id") == journal["id"])
    if not applied:
        if backup.exists():
            if profile.exists():
                remove_private(store.root, profile)
            backup.replace(profile)
        elif not journal["had_profile"] and profile.exists():
            remove_private(store.root, profile)
        old_status = job / "old-status.json"
        status = base / "status.json"
        if old_status.exists():
            old_status.replace(status)
        elif not journal["had_status"]:
            status.unlink(missing_ok=True)
    remove_private(store.root, job)
    journal_path.unlink()
    return {"activated": applied, "account_id": journal["account_id"]}


def activate_session(store, identifier, result):
    job = job_dir(store.root, identifier)
    base = access_dir(store.root)
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("account"), dict)
        or not isinstance(result.get("sync"), dict)
        or not isinstance(result.get("entries"), list)
    ):
        raise ValueError(
            "The new account result is incomplete. The previous session remains unchanged."
        )
    account = result["account"]
    entries = result["entries"]
    if (
        not isinstance(account.get("name"), str)
        or len(account["name"]) > 300
        or len(entries) > 5000
        or result["sync"].get("status") not in {"complete", "failed"}
    ):
        raise ValueError("The new account result has invalid metadata.")
    identifiers = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or len(entry["name"]) > 300
        ):
            raise ValueError("The new blacklist has invalid creator metadata.")
        key = creator_id("bilibili", entry.get("creator_id"))
        if key in identifiers:
            raise ValueError("The new blacklist repeats creators.")
        identifiers.add(key)
    account_id = creator_id("bilibili", account["id"])
    profile, staged = base / "profile", job / "profile"
    if (
        not staged.is_dir()
        or staged.is_symlink()
        or staged.is_junction()
        or profile.is_symlink()
        or profile.is_junction()
    ):
        raise ValueError("The new browser profile is unavailable or unsafe.")
    status_path = base / "status.json"
    journal = {
        "id": identifier,
        "account_id": account_id,
        "had_profile": profile.exists(),
        "had_status": status_path.exists(),
    }
    atomic_json(base / "switch-commit.json", journal)
    try:
        if profile.exists():
            profile.replace(job / "old-profile")
        if status_path.exists():
            status_path.replace(job / "old-status.json")
        staged.replace(profile)
        atomic_json(
            status_path,
            {
                "authenticated": True,
                "account_id": account_id,
                "name": account["name"],
                "checked_at": utc_now(),
                "storage": "isolated_browser_profile",
            },
        )
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            previous = store.db.execute(
                "SELECT payload FROM creator_sync WHERE account_id=?", (account_id,)
            ).fetchone()
            sync = json.loads(previous[0]) if previous else {"last_success": None}
            if result["sync"]["status"] == "complete":
                sync.update(result["sync"])
                store.db.execute(
                    "DELETE FROM creator_exclusions WHERE platform='bilibili' AND source='bilibili_blacklist' AND account_id=?",
                    (account_id,),
                )
                store.db.executemany(
                    "INSERT INTO creator_exclusions VALUES('bilibili',?,'bilibili_blacklist',?,?,?)",
                    [
                        (
                            creator_id("bilibili", entry["creator_id"]),
                            account_id,
                            entry["name"],
                            utc_now(),
                        )
                        for entry in result["entries"]
                    ],
                )
            else:
                sync.update(
                    status="failed",
                    error=result["sync"].get("error")
                    or "Blacklist sync failed. Saved rules for this account remain active.",
                )
            sync.update(
                account_switch_id=identifier,
                account_id=account_id,
                last_attempt=utc_now(),
            )
            store.db.execute(
                "INSERT INTO creator_sync VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload",
                (account_id, json.dumps(sync)),
            )
            store.db.execute(
                "UPDATE creator_policy SET revision=revision+1,account_id=? WHERE id=1",
                (account_id,),
            )
    except BaseException:
        recover_switch(store)
        raise
    try:
        recover_switch(store)
    except OSError as error:
        raise SwitchCleanupPending(
            "The new account is active, but old session files still need cleanup. Select Finish recovery before switching again."
        ) from error
    return {
        "account_id": account_id,
        "account_name": account["name"],
        "blacklist_status": sync["status"],
    }


class AccountSwitch:
    def __init__(self, root, *, runner=run_bounded):
        self.root = Path(root).resolve()
        self.runner = runner
        self.mutex = threading.RLock()
        self.thread = None
        self.closed = False
        self.cancelled = threading.Event()
        self.value = {"status": "idle", "message": ""}

    def status(self):
        with self.mutex:
            value = dict(self.value)
            active = bool(self.thread and self.thread.is_alive())
            if active and value["status"] in {
                "starting",
                "opening",
                "awaiting_sign_in",
                "verifying",
                "syncing",
            }:
                try:
                    progress = job_dir(self.root, value["id"]) / "progress.json"
                    if progress.stat().st_size > 4096:
                        raise ValueError("Account status exceeded its size limit.")
                    data = json.loads(progress.read_text(encoding="utf-8"))
                    if (
                        isinstance(data, dict)
                        and isinstance(data.get("status"), str)
                        and data["status"] in ACTIVE
                        and isinstance(data.get("message"), str)
                    ):
                        value.update(status=data["status"], message=data["message"])
                except (OSError, ValueError):
                    pass
            blockers = [
                self.root / f"{platform_lock('bilibili')}.lock",
                self.root / f"{SYNC_LOCK}.lock",
                access_dir(self.root) / "browser.lock",
            ]
            busy = any(path.exists() for path in blockers)
            if not active and (access_dir(self.root) / "switch-commit.json").exists():
                value.update(
                    status="cleanup_pending",
                    message="A session change needs recovery. Select Finish recovery to complete it safely.",
                )
            return {
                **value,
                "active": active,
                "can_start": not self.closed and not active and not busy,
                "busy_reason": "Finish active Bilibili work or blacklist sync before switching accounts."
                if busy and not active
                else "",
            }

    def start(self):
        with self.mutex:
            if not self.status()["can_start"]:
                raise ValueError(
                    "A Bilibili operation or account switch is already active. Finish it before switching accounts."
                )
            identifier = uuid.uuid4().hex
            self.cancelled = threading.Event()
            self.value = {
                "id": identifier,
                "status": "starting",
                "message": "Preparing a separate sign-in window.",
            }
            recovery_only = (access_dir(self.root) / "switch-commit.json").exists()
            self.thread = threading.Thread(
                target=self._work, args=(identifier, recovery_only), daemon=True
            )
            self.thread.start()
            return self.status()

    def confirm(self):
        with self.mutex:
            state = self.status()
            if not state["active"] or state["status"] != "awaiting_sign_in":
                raise ValueError("Wait for the new sign-in window before confirming.")
            (job_dir(self.root, state["id"]) / "confirm.request").write_text(
                utc_now(), encoding="utf-8"
            )
            return self.status()

    def cancel(self):
        with self.mutex:
            if self.status()["active"]:
                if self.value["status"] == "activating":
                    raise ValueError(
                        "The verified session is being activated. Wait for the result."
                    )
                self.cancelled.set()
                self.value.update(
                    status="cancelling",
                    message="Closing the new window. The previous session remains unchanged.",
                )
            return self.status()

    def close(self):
        with self.mutex:
            self.closed = True
            self.cancelled.set()
            thread = self.thread
        if thread:
            thread.join(timeout=15)

    def _work(self, identifier, recovery_only=False):
        job = None
        try:
            job = job_dir(self.root, identifier)
            with (
                closing(Store(self.root)) as store,
                store.lease(platform_lock("bilibili")),
                store.lease(SYNC_LOCK),
                browser_lease(self.root, token=identifier, recovery=True),
            ):
                recovered = recover_switch(store)
                if recovery_only:
                    with self.mutex:
                        self.value.update(
                            status="completed",
                            message="Session recovery completed. The account shown below is active.",
                            activated=bool(recovered and recovered["activated"]),
                        )
                    return
                if self.cancelled.is_set():
                    raise InterruptedError
                directory = access_dir(self.root) / "switches"
                if directory.exists():
                    for abandoned in directory.iterdir():
                        remove_private(self.root, job_dir(self.root, abandoned.name))
                job.mkdir(parents=True)
                process = self.runner(
                    [
                        sys.executable,
                        "-m",
                        "broader_vids.evidence.account_switch",
                        str(self.root),
                        identifier,
                    ],
                    timeout=690,
                    cancelled=self.cancelled,
                )
                if self.cancelled.is_set():
                    raise InterruptedError
                if process.returncode:
                    raise ValueError(
                        "The new sign-in did not complete. The previous session remains unchanged. Close any security challenge and retry."
                    )
                path = job / "result.json"
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("The new account result exceeded its size limit.")
                result = json.loads(path.read_text(encoding="utf-8"))
                with self.mutex:
                    if self.cancelled.is_set():
                        raise InterruptedError
                    self.value.update(
                        status="activating",
                        message="Activating the verified session and removing the old session.",
                    )
                activated = activate_session(store, identifier, result)
                with self.mutex:
                    self.value.update(
                        status="completed",
                        message="Bilibili account switched. The previous local session was removed.",
                        activated=True,
                        **activated,
                    )
        except Exception as error:
            with self.mutex:
                if isinstance(error, SwitchCleanupPending):
                    self.value.update(
                        status="cleanup_pending", activated=True, message=str(error)
                    )
                elif self.cancelled.is_set():
                    self.value.update(
                        status="cancelled",
                        message="Account switch cancelled. The previous session remains unchanged.",
                    )
                else:
                    self.value.update(
                        status="failed",
                        message=str(error)
                        if isinstance(error, ValueError)
                        else "Account switching failed. Check browser access and retry.",
                    )
        finally:
            if (
                job is not None
                and not (access_dir(self.root) / "switch-commit.json").exists()
                and job.exists()
            ):
                try:
                    remove_private(self.root, job)
                except OSError:
                    with self.mutex:
                        self.value.update(
                            status="failed",
                            message="Temporary sign-in files could not be removed. Close the sign-in window and retry.",
                        )


if __name__ == "__main__":
    try:
        prepare_login(Path(sys.argv[1]), sys.argv[2])
    except Exception:
        raise SystemExit(2)
