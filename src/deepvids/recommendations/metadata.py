from contextlib import contextmanager
import json

from deepvids.evidence.browser import bilibili_browser, browser_cookie_jar
from deepvids.evidence.media import EvidencePending, public_error
from deepvids.http import AccessPaused, BudgetExhausted, PublicHttp
from deepvids.preferences import canonical_identity, platform_lock
from deepvids.recommendations.engagement import collect_snapshot
from deepvids.recommendations.originality import provenance


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
