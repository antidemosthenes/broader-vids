from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json

from deepvids.evidence.service import atomic_json
from deepvids.recommendations.parallel import Runtime
from deepvids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
)
from deepvids.recommendations.reports import (
    recommendation_report,
    write_recommendation_report,
)
from deepvids.storage import Store
from deepvids.recommendations.timing import recover_elapsed, start_attempt


def create_batch(
    store,
    *,
    use_browser=False,
    max_seconds=1800,
    max_requests=160,
    max_assessments=52,
    max_mib=1024,
    asr_model=None,
    workers=4,
    reasoning_effort="low",
    discovery_runs=None,
):
    if not 0 < max_seconds <= 1800:
        raise ValueError(
            "The two-platform batch has one allowance of at most 1,800 seconds."
        )
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use one to four shared model workers.")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        identifier = store.create(
            "both",
            {
                "kind": "recommendation_batch",
                "max_seconds": max_seconds,
                "workers": workers,
            },
            {"children": {}, "results": {}},
            commit=False,
        )
        children = {}
        for platform in ("youtube", "bilibili"):
            children[platform] = create_recommendation(
                store,
                platform,
                (discovery_runs or {}).get(platform),
                use_browser and platform == "bilibili",
                max_seconds,
                max_requests,
                max_assessments,
                max_mib,
                asr_model,
                workers,
                reasoning_effort,
                commit=False,
                batch_id=identifier,
            )
        store.db.execute(
            "UPDATE runs SET state=? WHERE id=?",
            (json.dumps({"children": children, "results": {}}), identifier),
        )
    return identifier


def execute_batch(store, identifier, runner=execute_recommendation):
    with store.lease(identifier):
        run = store.get(identifier)
        if run["settings"].get("kind") != "recommendation_batch":
            raise ValueError("Not a two-platform recommendation batch.")
        recover_elapsed(run)
        if run["status"] == "completed":
            store.save(run)
            return run
        children = run["state"].get("children", {})
        if set(children) != {"youtube", "bilibili"} or len(set(children.values())) != 2:
            raise ValueError(
                "The batch has incomplete or duplicated child membership; start a new batch."
            )
        for platform, child in children.items():
            saved = store.get(child)
            if (
                saved["platform"] != platform
                or saved["settings"].get("kind") != "recommendation"
                or saved["settings"].get("batch_id") != identifier
            ):
                raise ValueError(
                    "The batch does not own both platform checkpoints; start a new batch."
                )
        started, prior = time.monotonic(), run["elapsed"]

        def remaining():
            return run["settings"]["max_seconds"] - prior - (time.monotonic() - started)

        def save():
            run["elapsed"] = prior + time.monotonic() - started
            store.save(run)

        saved_children = {p: store.get(child) for p, child in children.items()}
        if all(child["status"] == "completed" for child in saved_children.values()):
            run["state"]["results"] = {
                p: {
                    "run_id": child["id"],
                    "status": "completed",
                    "reason": child["reason"],
                }
                for p, child in saved_children.items()
            }
            run.update(
                status="completed",
                reason="Both completed platform checkpoints were recovered without new work.",
            )
            save()
            return run

        if remaining() <= 20:
            run.update(
                status="paused",
                reason="The shared 30-minute allowance is exhausted; no automatic reset.",
            )
            save()
            return run
        runtime = Runtime(preparations=2, models=run["settings"]["workers"])

        def platform_work(platform, child):
            local = Store(store.root)
            try:
                result = runner(
                    local,
                    child,
                    max_seconds=max(0.01, remaining() - 20),
                    runtime=runtime,
                    batch_id=identifier,
                )
                write_recommendation_report(local, child)
                return platform, {
                    "run_id": child,
                    "status": result["status"],
                    "reason": result["reason"],
                }
            finally:
                local.close()

        run.update(
            status="running",
            reason="Both platforms share bounded local/model workers and one deadline.",
        )
        start_attempt(run)
        save()
        interruption = None
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="deepvids-platform"
        ) as executor:
            futures = {}
            try:
                for p, child in children.items():
                    futures[executor.submit(platform_work, p, child)] = p
                for future in as_completed(futures):
                    platform = futures[future]
                    try:
                        _, result = future.result()
                    except Exception as error:
                        result = {
                            "run_id": run["state"]["children"][platform],
                            "status": "paused",
                            "reason": type(error).__name__
                            + "; inspect the platform checkpoint.",
                        }
                    run["state"]["results"][platform] = result
                    save()
            except BaseException as error:
                runtime.cancelled.set()
                run.update(
                    status="paused",
                    reason="Interrupted; owned workers are being stopped and checkpoints are preserved.",
                )
                save()
                interruption = error
        if interruption is not None:
            run["state"].pop("active_attempt", None)
            save()
            raise interruption
        complete = len(run["state"]["results"]) == 2 and all(
            r["status"] == "completed" for r in run["state"]["results"].values()
        )
        run.update(
            status="completed" if complete else "paused",
            reason="Both ten-video lists completed."
            if complete
            else "One or both lists remain checkpointed; no claim of twenty completed recommendations.",
        )
        run["state"].pop("active_attempt", None)
        save()
        return run


def batch_report(store, identifier):
    run = store.get(identifier)
    root = store.root / "recommendations" / identifier
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": identifier,
        "status": run["status"],
        "reason": run["reason"],
        "elapsed_seconds": run["elapsed"],
        "shared_max_seconds": run["settings"]["max_seconds"],
        "platforms": {
            p: recommendation_report(store, child)
            for p, child in run["state"]["children"].items()
        },
    }
    atomic_json(root / "report.json", report)
    for child in run["state"]["children"].values():
        write_recommendation_report(store, child)
    (root / "digest.md").write_text(
        "\n\n".join(
            (store.root / "recommendations" / child / "digest.md").read_text(
                encoding="utf-8"
            )
            for child in run["state"]["children"].values()
        ),
        encoding="utf-8",
    )
    return report, root / "report.json"
