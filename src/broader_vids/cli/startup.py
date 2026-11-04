from broader_vids.time_budget import (
    effective_limit,
    validate_optional_limit,
    report_limit,
)

from broader_vids.onboarding import status as onboarding_status
from broader_vids.worker_context import host_options, validate_host
from broader_vids.preferences import platform_lock
from broader_vids.preflight import preflight, charge_elapsed


def prepare_recommendation(store, args):
    resume = args.command == "continue"
    run = store.get(args.run_id) if resume else None
    request_limit = getattr(args, "max_requests", None) if resume else None
    if resume and run["settings"].get("kind") not in {
        "recommendation",
        "recommendation_batch",
    }:
        if request_limit is not None:
            raise ValueError(
                "A request-limit increase requires a recommendation run or batch."
            )
        return None
    if request_limit is not None:
        from broader_vids.recommendations.budgets import validate_request_limit

        validate_request_limit(request_limit)
    if resume and run["settings"].get("batch_id"):
        raise ValueError(
            f"Continue batch {run['settings']['batch_id']} to preserve its shared budget."
        )
    if resume and run["status"] == "completed":
        if request_limit is not None:
            raise ValueError("The recommendation run is complete; start a new round.")
        return None
    if resume:
        if run["settings"]["kind"] == "recommendation_batch":
            from broader_vids.recommendations.batch import _validate_current_batch

            _validate_current_batch(store, run)
        else:
            from broader_vids.recommendations.service import _validate_current_round

            _validate_current_round(run)
    platform = (
        run["platform"]
        if resume
        else args.platform or onboarding_status(store.root)["default_platform"]
    )
    settings = run["settings"] if resume else {}
    host = validate_host(
        settings.get("worker_host", "codex")
        if resume
        else getattr(args, "host", "codex")
    )
    if resume and settings["kind"] == "recommendation_batch":
        children = run["state"].get("children", {})
        if set(children) != {"youtube", "bilibili"}:
            raise ValueError("The batch has incomplete child membership.")
        saved_children = {
            platform: store.get(child) for platform, child in children.items()
        }
        if len(set(children.values())) != 2 or any(
            child["platform"] != platform
            or child["settings"].get("batch_id") != run["id"]
            or child["settings"].get("kind") != "recommendation"
            for platform, child in saved_children.items()
        ):
            raise ValueError("The batch does not own both platform checkpoints.")
        pairs = {
            (
                child["settings"].get("assessment_model"),
                child["settings"].get("reasoning_effort"),
                child["settings"].get("worker_host", "codex"),
            )
            for child in saved_children.values()
        }
        if len(pairs) != 1:
            raise ValueError("The batch has inconsistent saved model settings.")
        model, effort, child_host = pairs.pop()
        if child_host != host:
            raise ValueError("The batch has inconsistent saved AI hosts.")
        asr = any(
            child["settings"].get("asr_model") for child in saved_children.values()
        )
    else:
        model = settings.get("assessment_model") if resume else args.model
        effort = settings.get("reasoning_effort") if resume else args.effort
        asr = settings.get("asr_model") if resume else args.asr_model
    if not resume:
        validate_optional_limit(args.max_seconds)
    limit = (
        effective_limit(settings) - run["elapsed"]
        if resume
        else effective_limit({"max_seconds": args.max_seconds})
    )
    locks = [
        platform_lock(p)
        for p in (("youtube", "bilibili") if platform == "both" else (platform,))
        if p
    ]
    if resume:
        locks += [run["id"], *run["state"].get("children", {}).values()]
    if any((store.root / f"{identifier}.lock").exists() for identifier in locks):
        return {
            "status": "blocked",
            "platform": platform,
            "checks": {
                "active_run": {
                    "status": "busy",
                    "remedy": "Wait for the active recommendation operation. Do not remove its lock.",
                }
            },
            "elapsed_seconds": 0,
        }
    if resume:
        from broader_vids.recommendations.timing import recover_elapsed

        recover_elapsed(run)
        limit = effective_limit(settings) - run["elapsed"]
        if model is None or effort is None:
            return {
                "status": "blocked",
                "platform": platform,
                "checks": {
                    "model_settings": {
                        "status": "unverified",
                        "remedy": "This checkpoint has incomplete saved model settings. Use a compatible release or start a new round. Current chat settings cannot replace the saved pair.",
                    }
                },
                "elapsed_seconds": 0,
            }
    report = preflight(
        store,
        platform,
        model=model,
        effort=effort,
        asr=bool(asr),
        require_setup=not resume,
        check_feedback=not resume,
        allow_missing_feedback=getattr(args, "allow_missing_feedback", False),
        max_seconds=report_limit({"max_seconds": limit}),
        **host_options(host),
    )
    if resume:
        charge_elapsed(store, run["id"], report["elapsed_seconds"])
        if report["status"] == "ready" and request_limit is not None:
            from broader_vids.recommendations.budgets import increase_request_limit

            report["request_limit_changes"] = increase_request_limit(
                store, run["id"], request_limit
            )
    if report["status"] == "ready" and not resume:
        if report["elapsed_seconds"] >= limit:
            report.update(
                status="blocked",
                next_action="Preflight consumed the selected time allowance. Request a larger allowance.",
            )
            return report
        args.platform = report["platform"]
        args.model, args.effort = report["model"], report["effort"]
        if args.platform in {"bilibili", "both"}:
            args.use_browser = True
        args.preflight_seconds = report["elapsed_seconds"]
    return report
