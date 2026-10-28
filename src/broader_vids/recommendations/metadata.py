from contextlib import contextmanager
import json
import copy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import threading
import time

from broader_vids.evidence.browser import bilibili_browser, browser_cookie_jar
from broader_vids.evidence.media import EvidencePending, public_error
from broader_vids.http import AccessPaused, BudgetExhausted, PublicHttp, RequestGate
from broader_vids.storage import Store
from broader_vids.preferences import canonical_identity, platform_lock
from broader_vids.recommendations.engagement import collect_snapshot
from broader_vids.recommendations.originality import provenance


class SnapshotPool:
    def __init__(self, store, http, remaining, collector, *, enabled=None):
        self.root, self.http, self.remaining, self.collector = (
            store.root,
            http,
            remaining,
            collector,
        )
        self.enabled = (
            isinstance(http, PublicHttp) and collector is collect_snapshot
            if enabled is None
            else enabled
        )
        self.executor = (
            ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="broader-vids-metadata"
            )
            if self.enabled
            else None
        )
        self.futures = {}
        self.cancelled = threading.Event()
        self.first_error = None
        self.error_lock = threading.Lock()
        self.gate = RequestGate()
        if self.enabled:
            http.request_gate = self.gate

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cancelled.set()
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)

    def _fetch(self, item):
        store = Store(self.root)
        parent = self

        class Cancellation:
            def is_set(self):
                event = getattr(parent.http, "cancelled", None)
                return parent.cancelled.is_set() or (
                    event is not None and event.is_set()
                )

        try:
            seconds = self.remaining()
            if seconds <= 0:
                raise BudgetExhausted(
                    "The metadata deadline was reached before retrieval."
                )
            run = {
                **self.http.run,
                "elapsed": 0,
                "settings": {
                    **self.http.run["settings"],
                    "max_seconds": seconds,
                },
            }
            client = PublicHttp(store, run, anonymous_session=True)
            for cookie in self.http.cookies:
                client.cookies.set_cookie(copy.copy(cookie))
            client.transport = self.http.transport
            client.request_gate = self.gate
            client.cancelled = Cancellation()
            return self.collector(client, item)
        except AccessPaused as error:
            with self.error_lock:
                if self.first_error is None:
                    self.first_error = error
            self.cancelled.set()
            raise
        finally:
            store.close()

    def prefetch(self, items, eligible, max_pending=2):
        if not self.enabled or self.cancelled.is_set():
            return
        for item in items:
            if len(self.futures) >= max_pending:
                break
            key = item["canonical_id"]
            if key not in self.futures and eligible(item):
                self.futures[key] = self.executor.submit(self._fetch, dict(item))

    def get(self, item, save):
        if not self.enabled:
            return self.collector(self.http, item)
        key = item["canonical_id"]
        future = self.futures.pop(key, None) or self.executor.submit(
            self._fetch, dict(item)
        )
        heartbeat = time.monotonic()
        while True:
            if self.remaining() <= 0:
                self.cancelled.set()
                raise BudgetExhausted("The metadata deadline was reached.")
            try:
                return future.result(timeout=0.25)
            except FutureTimeout:
                if future.done():
                    raise
                if time.monotonic() - heartbeat >= 1:
                    save()
                    heartbeat = time.monotonic()
            except Exception:
                if self.first_error is not None:
                    raise self.first_error from None
                raise


@contextmanager
def metadata_session(store, http, authenticated=False):
    if not authenticated:
        yield
        return
    with bilibili_browser(store.root) as browser:
        jar = browser_cookie_jar(browser)
        if not {"SESSDATA", "DedeUserID"}.issubset({c.name for c in jar if c.value}):
            raise AccessPaused(
                "The isolated Bilibili profile is not signed in; run login bilibili first. No anonymous retry was made."
            )
        previous_cookies = list(http.cookies)
        previous_transport = http.transport
        try:
            for cookie in jar:
                http.cookies.set_cookie(cookie)
            http.transport = "isolated_browser_http"
            yield
        finally:
            http.cookies.clear()
            for cookie in previous_cookies:
                http.cookies.set_cookie(cookie)
            http.transport = previous_transport


def refresh_metadata(store, identifier, *, use_browser=False, http_factory=PublicHttp):
    item = canonical_identity(identifier)
    if use_browser and item["platform"] != "bilibili":
        raise ValueError(
            "The isolated authenticated browser is only configured for Bilibili."
        )
    with store.lease(platform_lock(item["platform"])):
        run_id = store.create(
            item["platform"],
            {
                "kind": "metadata",
                "identity": item,
                "use_browser": use_browser,
                "max_seconds": 60,
                "max_requests": 1,
            },
            {"phase": "fetch"},
        )
        run = store.get(run_id)
        http = http_factory(store, run, anonymous_session=True)
        try:
            with metadata_session(store, http, use_browser):
                snapshot = collect_snapshot(http, item)
                if http.elapsed() >= run["settings"]["max_seconds"]:
                    raise BudgetExhausted(
                        "Metadata refresh exceeded its 60-second budget."
                    )
            run["state"] = {"phase": "completed", "snapshot": snapshot}
            run.update(status="completed", reason="")
            run["elapsed"] = http.elapsed()
            with store.db:
                store.db.execute(
                    "INSERT OR REPLACE INTO engagement_snapshots VALUES(?,?)",
                    (item["canonical_id"], json.dumps(snapshot)),
                )
                store.save(run)
        except (
            AccessPaused,
            BudgetExhausted,
            EvidencePending,
            ValueError,
            OSError,
        ) as error:
            run.update(status="paused", reason=public_error(error))
            run["elapsed"] = http.elapsed()
            store.save(run)
        return {
            "run_id": run_id,
            "status": run["status"],
            "reason": run["reason"],
            "snapshot": run["state"].get("snapshot"),
            "originality_evidence": provenance({"engagement": snapshot})
            if run["status"] == "completed"
            else None,
        }
