from contextlib import nullcontext
import hashlib
import json
import time

from broader_vids.creators import creator_id, status
from broader_vids.evidence.browser import bilibili_browser
from broader_vids.storage import utc_now


SYNC_LOCK = hashlib.sha256(b"bilibili-blacklist-sync").hexdigest()[:32]


def sync_blacklist(
    store, *, context=None, max_seconds=60, max_requests=100, run_id=None
):
    if (
        not 0 < max_seconds <= 60
        or type(max_requests) is not int
        or not 4 <= max_requests <= 100
    ):
        raise ValueError("Blacklist sync permits 60 seconds and 4–100 requests.")
    started, requests, account = time.monotonic(), 0, ""

    def record(result):
        with store.db:
            previous = store.db.execute(
                "SELECT payload FROM creator_sync WHERE account_id=?", (account,)
            ).fetchone()
            payload = json.loads(previous[0]) if previous else {"last_success": None}
            payload.update(result, last_attempt=utc_now(), requests=requests)
            store.db.execute(
                "INSERT INTO creator_sync VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET payload=excluded.payload",
                (account, json.dumps(payload)),
            )
        return {**payload, "account_id": account, "requests": requests}

    try:
        if context is None and not (store.root / "access/bilibili/profile").is_dir():
            raise ValueError(
                "No saved Bilibili browser session is available for blacklist sync."
            )
        with (
            store.lease(SYNC_LOCK),
            (
                nullcontext(context)
                if context is not None
                else bilibili_browser(store.root)
            ) as browser,
        ):

            def get(url):
                nonlocal requests
                remaining = max_seconds - (time.monotonic() - started)
                if remaining <= 0 or requests >= max_requests:
                    raise ValueError(
                        "Blacklist sync reached its time or request limit."
                    )
                requests += 1
                receipt = store.begin_request(run_id, url) if run_id else None
                try:
                    response = browser.request.get(
                        url, timeout=max(1, min(15000, remaining * 1000))
                    )
                    value = response.json() if response.ok else None
                except Exception:
                    if receipt:
                        store.end_request(
                            receipt, "failed", "Blacklist retrieval failed."
                        )
                    raise ValueError(
                        "Blacklist request failed or access was denied."
                    ) from None
                if receipt:
                    store.end_request(receipt, "ok" if response.ok else "failed")
                if (
                    not isinstance(value, dict)
                    or type(value.get("code")) is not int
                    or value["code"] != 0
                    or not isinstance(value.get("data"), dict)
                ):
                    raise ValueError(
                        "Bilibili did not return an authenticated blacklist response."
                    )
                return value["data"]

            def account_id():
                nav = get("https://api.bilibili.com/x/web-interface/nav")
                if nav.get("isLogin") is not True:
                    raise ValueError("Bilibili sign-in is not verified.")
                return creator_id("bilibili", nav.get("mid"))

            account = account_id()
            with store.db:
                store.db.execute(
                    "UPDATE creator_policy SET revision=revision+1,account_id=? WHERE id=1 AND account_id!=?",
                    (account, account),
                )

            def page(number):
                data = get(
                    f"https://api.bilibili.com/x/relation/blacks?pn={number}&ps=50"
                )
                entries = data.get("list")
                if not isinstance(entries, list) or len(entries) > 50:
                    raise ValueError(
                        "Blacklist pagination has an unsupported response shape."
                    )
                result = {}
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError(
                            "Blacklist contains an invalid creator record."
                        )
                    mid = creator_id("bilibili", entry.get("mid"))
                    name = entry.get("uname", "")
                    if mid in result or not isinstance(name, str) or len(name) > 300:
                        raise ValueError(
                            "Blacklist contains duplicate or invalid creator records."
                        )
                    result[mid] = name
                total = data.get("total")
                if total is not None and (type(total) is not int or total < 0):
                    raise ValueError("Blacklist total is invalid.")
                return result, total, data.get("re_version")

            first, total, version = page(1)
            collected, current, number = {}, first, 1
            while True:
                if set(collected) & set(current):
                    raise ValueError(
                        "Blacklist pages repeat creators; the saved copy was preserved."
                    )
                collected.update(current)
                if total is not None and (
                    len(collected) > total or (not current and len(collected) != total)
                ):
                    raise ValueError(
                        "Blacklist pagination ended before its declared total."
                    )
                if not current:
                    break
                number += 1
                current, next_total, next_version = page(number)
                if total != next_total or version != next_version:
                    raise ValueError(
                        "Blacklist changed during pagination; the saved copy was preserved."
                    )
            checked, checked_total, checked_version = page(1)
            verified_account = account_id()
            if verified_account != account:
                account = verified_account
                with store.db:
                    store.db.execute(
                        "UPDATE creator_policy SET revision=revision+1,account_id=? WHERE id=1",
                        (account,),
                    )
                raise ValueError(
                    "Bilibili account changed during sync; no imported rules were replaced."
                )
            if first != checked or total != checked_total or version != checked_version:
                raise ValueError(
                    "Blacklist or account changed during sync; no imported rules were replaced."
                )
            if time.monotonic() - started >= max_seconds:
                raise ValueError("Blacklist sync reached its time limit.")
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                store.db.execute(
                    "DELETE FROM creator_exclusions WHERE platform='bilibili' AND source='bilibili_blacklist' AND account_id=?",
                    (account,),
                )
                store.db.executemany(
                    "INSERT INTO creator_exclusions VALUES('bilibili',?,'bilibili_blacklist',?,?,?)",
                    [
                        (mid, account, name, utc_now())
                        for mid, name in collected.items()
                    ],
                )
                store.db.execute(
                    "UPDATE creator_policy SET revision=revision+1 WHERE id=1"
                )
                result = record(
                    {
                        "status": "complete",
                        "last_success": utc_now(),
                        "error": None,
                        "count": len(collected),
                        "remote_version": version,
                    }
                )
            return result
    except Exception as error:
        account = account or status(store)["bilibili_sync"]["account_id"]
        message = (
            str(error)
            if isinstance(error, ValueError)
            else "Blacklist sync could not complete. The saved rules remain available."
        )
        return record({"status": "failed", "error": message})
