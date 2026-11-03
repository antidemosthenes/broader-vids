from broader_vids.worker_context import resolve_worker_settings
from broader_vids.time_budget import (
    effective_limit,
    validate_optional_limit,
    report_limit,
)
from broader_vids.recommendations.budgets import DEFAULT_METADATA_REQUESTS
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import time
import json

from broader_vids.evidence.service import atomic_json
from broader_vids.recommendations.parallel import Runtime
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
    _validate_current_round,
)
from broader_vids.recommendations.reports import (
    recommendation_report,
    write_recommendation_report,
)
from broader_vids.storage import Store
from broader_vids.recommendations.timing import recover_elapsed, start_attempt
from broader_vids.recommendations.timing import live_elapsed


def create_batch(
    store,
    *,
    use_browser=False,
    max_seconds=None,
    max_requests=DEFAULT_METADATA_REQUESTS,
    max_assessments=52,
    max_mib=1024,
    asr_model=None,
    workers=4,
    reasoning_effort=None,
    discovery_runs=None,
    assessment_model=None,
):
    assessment_model, reasoning_effort = resolve_worker_settings(
        assessment_model, reasoning_effort
    )
    validate_optional_limit(max_seconds)
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Use one to four shared model workers.")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        identifier = store.create(
            "both",
            {
                "kind": "recommendation_batch",
                "max_seconds": max_seconds,
                "time_policy_version": 2,
                "workers": workers,
                "assessment_model": assessment_model,
                "reasoning_effort": reasoning_effort,
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
                assessment_model=assessment_model,
            )
        store.db.execute(
            "UPDATE runs SET state=? WHERE id=?",
            (json.dumps({"children": children, "results": {}}), identifier),
        )
    return identifier


def execute_batch(store, identifier, runner=execute_recommendation, *, progress=None):
    _validate_current_batch(store, store.get(identifier))
    with store.lease(identifier):
        run = store.get(identifier)
        _validate_current_batch(store, run)
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
            return (
                effective_limit(run["settings"]) - prior - (time.monotonic() - started)
            )

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
                reason="The requested batch time allowance is exhausted; progress is preserved.",
            )
            save()
            return run
        runtime = Runtime(models=run["settings"]["workers"])

        def platform_work(platform, child):
            local = Store(store.root)
            try:
                result = runner(
                    local,
                    child,
                    max_seconds=report_limit(
                        {"max_seconds": max(0.01, remaining() - 20)}
                    ),
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
            reason="Both platforms share bounded local/model workers.",
        )
        start_attempt(run)
        save()
        interruption = None
        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="broader-vids-platform"
        ) as executor:
            futures = {}
            try:
                for p, child in children.items():
                    futures[executor.submit(platform_work, p, child)] = p
                last_progress = 0
                while futures:
                    done, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        platform = futures.pop(future)
                        try:
                            _, result = future.result()
                        except Exception as error:
                            from broader_vids.evidence.media import public_error

                            result = {
                                "run_id": children[platform],
                                "status": "paused",
                                "reason": public_error(error),
                            }
                        run["state"]["results"][platform] = result
                    save()
                    if progress is not None and time.monotonic() - last_progress >= 5:
                        progress(
                            {
                                "elapsed_seconds": round(run["elapsed"], 1),
                                "platforms": {
                                    p: {
                                        "phase": child["state"].get("phase"),
                                        "status": child["status"],
                                        "assessed": sum(
                                            v.get("quality") is not None
                                            for v in child["state"]
                                            .get("evidence", {})
                                            .values()
                                        ),
                                    }
                                    for p, rid in children.items()
                                    for child in [store.get(rid)]
                                },
                            }
                        )
                        last_progress = time.monotonic()
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
        "elapsed_seconds": live_elapsed(run),
        "shared_max_seconds": report_limit(run["settings"]),
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


def _validate_current_batch(store, run):
    if run["settings"].get("time_policy_version") != 2:
        raise ValueError(
            "This batch uses an unsupported time policy; start a new round."
        )
    for child in run["state"].get("children", {}).values():
        _validate_current_round(store.get(child))
