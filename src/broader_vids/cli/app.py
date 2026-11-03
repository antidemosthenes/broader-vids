import http.client
import json
import os
import subprocess
from pathlib import Path
import sqlite3
import sys

from broader_vids.cli.arguments import parser
from broader_vids.discovery.service import (
    create_discovery,
    discovery_report,
    execute_discovery,
    write_discovery_report,
)
from broader_vids.discovery.queries import load_bank, query_plan
from broader_vids.storage import Store, data_root
from broader_vids.evidence.browser import confirm_bilibili_login, login_bilibili
from broader_vids.evidence.service import (
    add_evidence_frame,
    create_evidence,
    prepare_evidence,
    refresh_native_captions,
)
from broader_vids.evidence.reports import (
    evidence_report,
    write_evidence_report,
)
from broader_vids.evidence.assessment import assess_evidence
from broader_vids.evidence.media import EvidencePending
from broader_vids.preferences import get_profile, set_profile, user_action, video_states
from broader_vids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
    publish_recommendation,
)
from broader_vids.recommendations.reports import (
    recommendation_report,
    write_recommendation_report,
)


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def show_progress(value):
    print(
        json.dumps({"progress": value}, ensure_ascii=False), file=sys.stderr, flush=True
    )


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command in {"reset", "clean"}:
            if (
                args.command == "clean"
                and args.apply
                and Path(sys.executable)
                .resolve()
                .is_relative_to(data_root(args.data_dir))
            ):
                raise ValueError(
                    "Use the plugin's Node launcher for Clean so the Python runtime exits before its files are removed."
                )
            launcher = os.environ.get("BROADER_VIDS_LAUNCHER")
            if not launcher:
                candidate = Path(__file__).resolve().parents[3] / "bin/broader-vids.cjs"
                if candidate.is_file():
                    launcher = str(candidate)
            if not launcher:
                raise ValueError(
                    "Use the installed reset or clean skill, or its Node launcher, to manage the data directory."
                )
            command = [
                "node",
                launcher,
                "--data-dir",
                str(data_root(args.data_dir)),
                args.command,
            ]
            if args.apply:
                command.append("--apply")
            if args.confirm_path:
                command.extend(["--confirm-path", args.confirm_path])
            return subprocess.run(
                command,
                check=False,
                stdout=sys.stdout,
                stderr=sys.stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode
        if args.command == "dashboard":
            from broader_vids.web.runtime import dashboard

            emit(
                dashboard(
                    data_root(args.data_dir),
                    args.operation,
                    port=args.port,
                    open_browser=not args.no_browser,
                )
            )
            return 0
        if args.command == "serve":
            from broader_vids.web.server import make_server

            if not 0 <= args.port <= 65535:
                raise ValueError("Port must be between zero and 65535.")
            server = make_server(data_root(args.data_dir), args.port)
            print(
                f"Broader Vids dashboard: http://127.0.0.1:{server.server_port}",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == "login":
            emit(
                confirm_bilibili_login(data_root(args.data_dir))
                if args.confirm
                else login_bilibili(data_root(args.data_dir), args.timeout)
            )
            return 0
        if args.command == "doctor":
            from broader_vids.cli.environment import environment_report

            report = environment_report(data_root(args.data_dir))
            emit(report)
            return 0 if report["local_requirements_met"] else 2
        if args.command == "queries":
            bank = load_bank(args.bank)
            if args.export:
                with Path(args.export).open("x", encoding="utf-8") as destination:
                    json.dump(
                        {k: v for k, v in bank.items() if k != "sha256"},
                        destination,
                        ensure_ascii=False,
                        indent=2,
                    )
            emit(
                {
                    "bank_version": bank["version"],
                    "sha256": bank["sha256"],
                    "plan": query_plan(bank, args.platform, args.round_number),
                }
            )
            return 0
        try:
            store = Store(data_root(args.data_dir))
        except (OSError, sqlite3.Error):
            if args.command not in {"preflight", "recommend", "continue", "onboarding"}:
                raise
            emit(
                {
                    "status": "blocked",
                    "checks": {
                        "profile": {
                            "status": "unusable",
                            "remedy": "Check the selected profile path, database access, and directory permissions, then retry.",
                        }
                    },
                }
            )
            return 2
        try:
            if args.command == "usage":
                from broader_vids.token_usage import report

                emit(report(store, args.run_id))
                return 0
            if args.command == "onboarding":
                from broader_vids.onboarding import (
                    status,
                    configure,
                    complete,
                    setup_questions,
                )

                if args.operation == "status":
                    emit(status(store.root))
                elif args.operation == "questions":
                    emit(setup_questions(store))
                elif args.operation == "configure":
                    emit(configure(store, args.platform))
                else:
                    report = complete(
                        store,
                        model=args.model,
                        effort=args.effort,
                        asr_model=args.asr_model,
                        skill_namespace=args.skill_namespace,
                    )
                    if args.format == "chat" and report.get("self_check") == "passed":
                        print(report["next_steps_message"])
                    else:
                        emit(report)
                    return 0 if report["status"] == "ready" else 2
                return 0
            if args.command == "preflight":
                from broader_vids.preflight import preflight

                report = preflight(
                    store,
                    args.platform,
                    model=args.model,
                    effort=args.effort,
                    asr=bool(args.asr_model),
                )
                emit(report)
                return 0 if report["status"] == "ready" else 2
            if args.command in {"recommend", "continue"}:
                from broader_vids.cli.startup import prepare_recommendation

                report = prepare_recommendation(store, args)
                if report is not None:
                    if report["status"] != "ready":
                        emit(report)
                        return 2
                    print(
                        json.dumps({"preflight": report}, ensure_ascii=False),
                        file=sys.stderr,
                    )
            if args.command == "exclusions":
                from broader_vids.creators import (
                    status,
                    set_exclusion,
                    video_creator,
                    creator_id,
                )
                from broader_vids.preferences import update_tag_rule

                if args.operation in {"creator", "video"}:
                    creator = (
                        video_creator(store, args.value)
                        if args.operation == "video"
                        else {
                            "platform": args.platform,
                            "creator_id": creator_id(args.platform, args.value),
                            "name": args.name,
                        }
                    )
                    if not creator["creator_id"]:
                        raise ValueError(
                            "This video's creator ID is unresolved. Refresh its metadata first."
                        )
                    result = set_exclusion(
                        store,
                        creator["platform"],
                        creator["creator_id"],
                        args.change == "add",
                        creator["name"],
                    )
                    emit(
                        {
                            "platform": creator["platform"],
                            "creator_id": creator["creator_id"],
                            "excluded": creator["creator_id"]
                            in {
                                entry["creator_id"]
                                for entry in result["creators"][creator["platform"]]
                            },
                            "applies_to": "next_new_round",
                        }
                    )
                elif args.operation == "tag":
                    update_tag_rule(
                        store, args.platform, args.value, remove=args.change == "remove"
                    )
                    emit(status(store))
                elif args.operation == "sync-bilibili":
                    from broader_vids.blacklist import sync_blacklist

                    result = sync_blacklist(store)
                    emit(result)
                    return 0 if result["status"] == "complete" else 2
                else:
                    emit(status(store))
                return 0
            if args.command == "rate":
                from broader_vids.learning.feedback import save_detail

                emit(
                    save_detail(
                        store,
                        args.identifier,
                        args.dimension,
                        args.value,
                        target=args.target,
                        level=args.level,
                    )
                )
                return 0
            if args.command == "metadata":
                from broader_vids.recommendations.metadata import refresh_metadata

                result = refresh_metadata(
                    store, args.identifier, use_browser=args.use_browser
                )
                emit(result)
                return 0 if result["status"] == "completed" else 2
            if args.command == "learn":
                from broader_vids.learning.service import ensure_model, learning_status
                from broader_vids.learning.calibration import evaluate

                if args.operation == "label-videos":
                    from broader_vids.learning.annotations import annotate_videos

                    keys = [
                        r[0]
                        for r in store.db.execute(
                            "SELECT canonical_id FROM video_state WHERE recommended=1 UNION SELECT canonical_id FROM calibration_items UNION SELECT canonical_id FROM feedback_ratings"
                        )
                    ]
                    emit(
                        annotate_videos(
                            store, keys[: args.max_videos], max_seconds=args.max_seconds
                        )
                    )
                    ensure_model(store)
                    return 0

                if args.operation == "repair-evidence":
                    from broader_vids.learning.evidence import repair_pending

                    result = repair_pending(
                        store, max_seconds=args.max_seconds, max_videos=args.max_videos
                    )
                    emit(result)
                    return 0 if not result["remaining"] else 2
                if args.operation == "fit":
                    ensure_model(store)
                emit(
                    evaluate(store)
                    if args.operation == "evaluate"
                    else learning_status(store)
                )
                return 0
            if args.command == "calibrate":
                from broader_vids.learning.calibration import (
                    start_calibration,
                    calibration_list,
                )

                emit(
                    start_calibration(store, seed=args.seed, new=args.new)
                    if args.operation == "start"
                    else calibration_list(store)
                )
                return 0
            if args.command == "cleanup":
                from broader_vids.maintenance import cleanup_storage

                summary = cleanup_storage(store, apply=args.apply)
                emit(summary)
                return 2 if summary["issues"] else 0
            if args.command == "topics":
                from broader_vids.exploration.domains import map_report

                emit(map_report(store))
                return 0
            if (args.command == "recommend" and args.platform == "both") or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind")
                == "recommendation_batch"
            ):
                from broader_vids.recommendations.batch import (
                    create_batch,
                    execute_batch,
                    batch_report,
                )

                if args.command == "recommend":
                    if args.discovery_run:
                        raise ValueError(
                            "A single --discovery-run cannot supply both platforms."
                        )
                    run_id = create_batch(
                        store,
                        use_browser=args.use_browser,
                        max_seconds=args.max_seconds,
                        max_requests=args.max_requests,
                        max_assessments=args.max_assessments,
                        max_mib=args.max_mib,
                        asr_model=args.asr_model,
                        workers=args.workers,
                        reasoning_effort=args.effort,
                        assessment_model=args.model,
                    )
                else:
                    run_id = args.run_id
                if args.command == "recommend":
                    from broader_vids.preflight import charge_elapsed

                    charge_elapsed(store, run_id, args.preflight_seconds)
                if args.command != "report":
                    execute_batch(store, run_id, progress=show_progress)
                report, path = batch_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "status": report["status"],
                        "reason": report["reason"],
                        "elapsed_seconds": report["elapsed_seconds"],
                        "report_path": str(path),
                        "digest_path": str(
                            store.root / "recommendations" / run_id / "digest.md"
                        ),
                        "platforms": {
                            p: {
                                "run_id": r["run_id"],
                                "status": r["status"],
                                "reason": r["reason"],
                                "first_failure": r.get("first_failure"),
                                "counts": r["counts"],
                                "blacklist_sync": r.get("blacklist_sync", {}),
                            }
                            for p, r in report["platforms"].items()
                        },
                    }
                )
                return (
                    0
                    if report["status"] == "completed" or args.command == "report"
                    else 2
                )
            if args.command == "profile":
                if args.profile_command in {"export", "import", "preset"}:
                    from broader_vids.learning.transfer import (
                        export_preferences,
                        import_preferences,
                        parse_bundle,
                        preview_import,
                        write_export,
                        MAX_BYTES,
                    )

                    if args.profile_command == "preset":
                        from broader_vids.learning.presets import starter_preferences

                        bundle = starter_preferences()
                        emit(
                            import_preferences(store, bundle, _validated=True)
                            if args.apply
                            else preview_import(store, bundle, _validated=True)
                        )
                    elif args.profile_command == "export":
                        bundle = export_preferences(store)
                        target = write_export(
                            args.file, bundle, overwrite=args.overwrite
                        )
                        emit(
                            {
                                "exported": True,
                                "path": str(target),
                                "checksum": bundle["checksum"],
                            }
                        )
                    else:
                        with Path(args.file).expanduser().open("rb") as handle:
                            raw = handle.read(MAX_BYTES + 1)
                        if len(raw) > MAX_BYTES:
                            raise ValueError(
                                "The preference file must not exceed 8 MiB."
                            )
                        bundle = parse_bundle(raw.decode("utf-8-sig"))
                        emit(
                            import_preferences(store, bundle, _validated=True)
                            if args.apply
                            else preview_import(store, bundle, _validated=True)
                        )
                    return 0
                emit(
                    set_profile(store, args.interest)
                    if args.profile_command == "set"
                    else get_profile(store)
                )
                return 0
            if args.command == "video":
                emit(user_action(store, args.identifier, args.action))
                return 0
            if args.command == "queue":
                emit(list(video_states(store, args.platform).values()))
                return 0
            if args.command in {"recommend", "publish"} or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind") == "recommendation"
            ):
                if args.command == "recommend":
                    run_id = create_recommendation(
                        store,
                        args.platform,
                        args.discovery_run,
                        args.use_browser,
                        args.max_seconds,
                        args.max_requests,
                        args.max_assessments,
                        args.max_mib,
                        args.asr_model,
                        args.workers,
                        args.effort,
                        assessment_model=args.model,
                    )
                else:
                    run_id = args.run_id
                if args.command == "recommend":
                    from broader_vids.preflight import charge_elapsed

                    charge_elapsed(store, run_id, args.preflight_seconds)
                if args.command == "publish":
                    publish_recommendation(store, run_id, args.allow_incomplete)
                elif args.command != "report":
                    execute_recommendation(store, run_id, progress=show_progress)
                path = write_recommendation_report(store, run_id)
                report = recommendation_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "platform": report["platform"],
                        "status": report["status"],
                        "reason": report["reason"],
                        "counts": report["counts"],
                        "request_budget": report["request_budget"],
                        "first_failure": report.get("first_failure"),
                        "digest_path": str(
                            store.root / "recommendations" / run_id / "digest.md"
                        ),
                        "blacklist_sync": report.get("blacklist_sync", {}),
                        "model_calls": report["model_calls"],
                        "planning_model_calls": report.get("planning_model_calls", 0),
                        "total_model_calls": report["total_model_calls"],
                        "download_bytes": report["download_bytes"],
                        "download_bytes_is_upper_bound": report.get(
                            "download_bytes_is_upper_bound", False
                        ),
                        "report_path": str(path),
                    }
                )
                return (
                    0
                    if report["status"] in {"completed", "partial"}
                    or args.command == "report"
                    else 2
                )
            if args.command == "evidence" or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind") == "evidence"
            ):
                if args.command == "evidence" and args.evidence_command == "prepare":
                    run_id = create_evidence(
                        store,
                        args.url,
                        args.media,
                        args.captions,
                        args.use_browser,
                        args.max_mib,
                        args.max_seconds,
                        assessment_model=args.model,
                        reasoning_effort=args.effort,
                    )
                    prepare_evidence(store, run_id, args.asr_model)
                else:
                    run_id = args.run_id
                    if args.command == "continue":
                        prepare_evidence(store, run_id)
                    elif (
                        args.command == "evidence" and args.evidence_command == "frame"
                    ):
                        add_evidence_frame(
                            store, run_id, args.timestamp, args.reason, args.long_edge
                        )
                    elif (
                        args.command == "evidence"
                        and args.evidence_command == "captions"
                    ):
                        refresh_native_captions(store, run_id)
                    elif args.command == "evidence":
                        assess_evidence(store, run_id, args.mode)
                path = write_evidence_report(store, run_id)
                report = evidence_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "platform": report["platform"],
                        "status": report["status"],
                        "reason": report["reason"],
                        "report_path": str(path),
                        "frame_count": len(report["frames"]),
                        "caption_source": (report["captions"] or {}).get("source"),
                        "assessments": list(report["assessments"]),
                        "resources": report["resources"],
                    }
                )
                return (
                    0
                    if report["status"] in {"prepared", "assessed", "completed"}
                    or args.command == "report"
                    else 2
                )
            if args.command == "discover" or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind") == "discovery"
            ):
                run_id = (
                    create_discovery(
                        store,
                        args.platform,
                        args.bank,
                        args.round_number,
                        args.max_requests,
                        args.max_seconds,
                        args.creators,
                        args.creator_pages,
                        planning_policy=2,
                        curated=args.curated,
                        seed=args.seed,
                        max_planning_calls=args.max_planning_calls,
                        assessment_model=args.model,
                        reasoning_effort=args.effort,
                    )
                    if args.command == "discover"
                    else args.run_id
                )
                if args.command != "report":
                    print(
                        f"Discovery {run_id}: collecting {store.get(run_id)['platform']} public listings",
                        file=sys.stderr,
                        flush=True,
                    )
                    execute_discovery(store, run_id)
                path = write_discovery_report(store, run_id)
                report = discovery_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "status": report["status"],
                        "reason": report["reason"],
                        "report_path": str(path),
                        "candidate_count": report["candidate_count"],
                        "planning_model_calls": report["planning_model_calls"],
                        "exploration": {
                            key: report["exploration"].get(key)
                            for key in (
                                "existing_shortfall",
                                "new_shortfall",
                                "related_queries",
                                "unrelated_queries",
                            )
                        }
                        if report["exploration"]
                        else None,
                        "shortlist_count": len(report["shortlist"]),
                        "source_counts": report["source_counts"],
                        "checks": report["checks"],
                    }
                )
                return (
                    0
                    if report["status"] == "completed" or args.command == "report"
                    else 2
                )
            if args.command == "status":
                emit(
                    [
                        {
                            k: r[k]
                            for k in ("id", "platform", "status", "reason", "elapsed")
                        }
                        for r in store.list_runs()
                    ]
                )
                return 0
            raise ValueError(
                "This saved job type belongs to a retired research workflow."
            )
        finally:
            store.close()
    except KeyboardInterrupt:
        print(
            "broader-vids: Interrupted; saved checkpoints and original allowances are preserved.",
            file=sys.stderr,
        )
        return 130
    except (
        ValueError,
        EvidencePending,
        OSError,
        EOFError,
        UnicodeError,
        sqlite3.Error,
        http.client.HTTPException,
    ) as error:
        print(f"broader-vids: {error}", file=sys.stderr)
        return 2
