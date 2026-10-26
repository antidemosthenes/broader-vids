import http.client
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from deepvids import __version__
from deepvids.cli.arguments import parser
from deepvids.discovery.service import (
    create_discovery,
    discovery_report,
    execute_discovery,
    write_discovery_report,
)
from deepvids.discovery.queries import load_bank, query_plan
from deepvids.storage import Store, data_root
from deepvids.evidence.browser import confirm_bilibili_login, login_bilibili
from deepvids.evidence.service import (
    add_evidence_frame,
    create_evidence,
    prepare_evidence,
    refresh_native_captions,
)
from deepvids.evidence.reports import (
    evidence_report,
    pilot_report,
    write_evidence_report,
)
from deepvids.evidence.assessment import assess_evidence
from deepvids.evidence.media import EvidencePending
from deepvids.preferences import get_profile, set_profile, user_action, video_states
from deepvids.recommendations.service import (
    create_recommendation,
    execute_recommendation,
    publish_recommendation,
)
from deepvids.recommendations.reports import (
    recommendation_report,
    write_recommendation_report,
)
from deepvids.readiness import release_gate


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from deepvids.web.server import make_server

            if not 0 <= args.port <= 65535:
                raise ValueError("Port must be between zero and 65535.")
            server = make_server(data_root(args.data_dir), args.port)
            print(
                f"Deepvids dashboard: http://127.0.0.1:{server.server_port}", flush=True
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
            emit(
                {
                    "version": __version__,
                    "stage": "manual_recommendations",
                    "python": sys.version.split()[0],
                    "data_directory": str(data_root(args.data_dir)),
                    "tools": {
                        name: bool(
                            shutil.which(name)
                            or (
                                name == "ffmpeg"
                                and importlib.util.find_spec("imageio_ffmpeg")
                            )
                        )
                        for name in ("uv", "node", "codex", "ffmpeg", "ffprobe")
                    },
                    "evidence_dependencies": {
                        name: importlib.util.find_spec(name) is not None
                        for name in ("yt_dlp", "PIL", "playwright", "faster_whisper")
                    },
                    "authentication_checked": False,
                    "model_calls_required_for_assessment": True,
                    "recommendations_available": True,
                    "bayesian_learning_available": False,
                }
            )
            return 0
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
        store = Store(data_root(args.data_dir))
        try:
            if args.command == "cleanup":
                from deepvids.maintenance import cleanup_storage

                summary = cleanup_storage(store, apply=args.apply)
                emit(summary)
                return 2 if summary["issues"] else 0
            if args.command == "topics":
                from deepvids.exploration.domains import map_report

                emit(map_report(store))
                return 0
            if (args.command == "recommend" and args.platform == "both") or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind")
                == "recommendation_batch"
            ):
                from deepvids.recommendations.batch import (
                    create_batch,
                    execute_batch,
                    batch_report,
                )

                if args.command == "recommend":
                    if args.discovery_run:
                        raise ValueError(
                            "A single --discovery-run cannot supply both platforms."
                        )
                    if not release_gate(store)["phase_two_complete"]:
                        raise ValueError(
                            "Complete the discovery and evidence pilot gates first."
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
                    )
                else:
                    run_id = args.run_id
                if args.command != "report":
                    execute_batch(store, run_id)
                report, path = batch_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "status": report["status"],
                        "reason": report["reason"],
                        "elapsed_seconds": report["elapsed_seconds"],
                        "report_path": str(path),
                        "platforms": {
                            p: {
                                "run_id": r["run_id"],
                                "status": r["status"],
                                "counts": r["counts"],
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
                emit(
                    set_profile(store, args.interest)
                    if args.profile_command == "set"
                    else get_profile(store)
                )
                return 0
            if args.command == "video":
                emit(
                    user_action(
                        store, args.identifier, args.action, args.reason, args.kind
                    )
                )
                return 0
            if args.command == "queue":
                emit(list(video_states(store, args.platform).values()))
                return 0
            if args.command in {"recommend", "publish"} or (
                args.command in {"continue", "report"}
                and store.get(args.run_id)["settings"].get("kind") == "recommendation"
            ):
                if args.command == "recommend":
                    gate = release_gate(store)
                    if not gate["phase_two_complete"]:
                        raise ValueError(
                            "Complete the local discovery and evidence pilot gates before recommendations."
                        )
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
                    )
                else:
                    run_id = args.run_id
                if args.command == "publish":
                    publish_recommendation(store, run_id, args.allow_incomplete)
                elif args.command != "report":
                    execute_recommendation(store, run_id)
                path = write_recommendation_report(store, run_id)
                report = recommendation_report(store, run_id)
                emit(
                    {
                        "run_id": run_id,
                        "platform": report["platform"],
                        "status": report["status"],
                        "reason": report["reason"],
                        "counts": report["counts"],
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
                if (
                    args.command == "evidence"
                    and args.evidence_command == "pilot-report"
                ):
                    report = pilot_report(store)
                    emit(
                        {
                            "phase_two_complete": report["phase_two_complete"],
                            "verified_videos": report["verified_videos"],
                            "totals": report["totals"],
                            "report_path": str(
                                store.root / "evidence" / "pilot-report.md"
                            ),
                        }
                    )
                    return 0
                if args.command == "evidence" and args.evidence_command == "prepare":
                    run_id = create_evidence(
                        store,
                        args.url,
                        args.media,
                        args.captions,
                        args.use_browser,
                        args.max_mib,
                        args.max_seconds,
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
                        curated=args.curated,
                        seed=args.seed,
                        max_planning_calls=args.max_planning_calls,
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
            if args.command == "gate":
                gate = release_gate(store)
                emit(gate)
                return 0
            raise ValueError(
                "This saved job type belongs to a retired research workflow."
            )
        finally:
            store.close()
    except KeyboardInterrupt:
        print(
            "deepvids: Interrupted; saved checkpoints and original allowances are preserved.",
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
        print(f"deepvids: {error}", file=sys.stderr)
        return 2
