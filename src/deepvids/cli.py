import argparse
import http.client
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from . import __version__
from .catalogs import SOURCES, fetch_catalog, get_catalog, import_catalog, list_catalogs, sample_catalog
from .experiments import create_experiment, create_probe, create_search_control, execute
from .discovery import create_discovery, discovery_report, execute_discovery, write_discovery_report
from .querybank import load_bank, query_plan
from .providers import MAX_AID, YOUTUBE_ID
from .reports import make_report, release_gate, write_report
from .sampling import simulation_report
from .storage import Store, data_root
from .browser_access import confirm_bilibili_login, login_bilibili
from .evidence import add_evidence_frame, create_evidence, evidence_report, pilot_report, prepare_evidence, refresh_native_captions, write_evidence_report
from .assessment import assess_evidence
from .evidence_media import EvidencePending


def parser():
    result = argparse.ArgumentParser(prog="deepvids", description="Independent video discovery feasibility experiments")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--data-dir", help="External state directory; defaults to LOCALAPPDATA/deepvids on Windows")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inspect local prerequisites without reading credentials")
    commands.add_parser("simulate", help="Verify finite-domain sampling invariants without network access")
    commands.add_parser("status", help="List experiment checkpoints")
    commands.add_parser("gate", help="Explain why the full recommender is gated")
    commands.add_parser("recommend", help="Check the recommendation release gate")
    login = commands.add_parser("login", help="Open an isolated Playwright browser for user-controlled platform sign-in")
    login.add_argument("platform", choices=("bilibili",))
    login.add_argument("--timeout", type=int, default=600)
    login.add_argument("--confirm", action="store_true", help="Verify sign-in after completing it in the open isolated browser")
    evidence = commands.add_parser("evidence", help="Prepare bounded captions/frames and compare quality assessments")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    prepare = evidence_commands.add_parser("prepare", help="Resolve one playable video/part and prepare timestamped evidence")
    prepare.add_argument("url")
    prepare.add_argument("--media", help="Explicit local video file instead of downloading")
    prepare.add_argument("--captions", help="Explicit local timestamped JSON3/JSON/VTT/SRT captions")
    prepare.add_argument("--use-browser", action="store_true", help="Use the user-authenticated isolated Bilibili profile")
    prepare.add_argument("--max-mib", type=int, default=128)
    prepare.add_argument("--max-seconds", type=float, default=1200)
    prepare.add_argument("--asr-model", choices=("tiny", "base", "small"))
    assess = evidence_commands.add_parser("assess", help="Use the ChatGPT-authenticated Codex CLI for bounded scorecards")
    assess.add_argument("run_id")
    assess.add_argument("--mode", choices=("baseline", "augmented", "both"), default="both")
    frame = evidence_commands.add_parser("frame", help="Add a targeted view before augmented assessment, within the eight-image cap")
    frame.add_argument("run_id")
    frame.add_argument("--at", type=float, required=True, dest="timestamp")
    frame.add_argument("--reason", required=True)
    frame.add_argument("--long-edge", type=int, default=768)
    captions = evidence_commands.add_parser("captions", help="Refresh native captions while preserving already-assessed inputs")
    captions.add_argument("run_id")
    evidence_commands.add_parser("pilot-report", help="Compare paired evidence assessments and report actual resource usage")
    discover = commands.add_parser("discover", help="Collect curated live search, creator and Bilibili Home candidates")
    discover.add_argument("platform", choices=("youtube", "bilibili"))
    discover.add_argument("--bank", help="Optional versioned JSON query bank")
    discover.add_argument("--round", type=int, dest="round_number")
    discover.add_argument("--max-requests", type=int, default=100)
    discover.add_argument("--max-seconds", type=float, default=600)
    discover.add_argument("--creators", type=int, default=4)
    discover.add_argument("--creator-pages", type=int, default=2)
    queries = commands.add_parser("queries", help="Inspect the curated query bank and deterministic round plan")
    queries.add_argument("--bank")
    queries.add_argument("--platform", choices=("youtube", "bilibili"), default="youtube")
    queries.add_argument("--round", type=int, default=0, dest="round_number")
    queries.add_argument("--export", help="Create an editable copy of the bank at a new JSON file")
    report = commands.add_parser("report", help="Write standalone JSON, Markdown and trace reports")
    report.add_argument("run_id")
    continuation = commands.add_parser("continue", help="Resume a paused experiment without resetting its budget or random state")
    continuation.add_argument("run_id")
    experiment = commands.add_parser("experiment", help="Run a bounded anonymous sampling experiment")
    experiment.add_argument("platform", choices=("youtube", "bilibili"))
    experiment.add_argument("--seed", type=int, default=0)
    experiment.add_argument("--max-requests", type=int, default=1000)
    experiment.add_argument("--max-seconds", type=float, default=3600)
    experiment.add_argument("--queries", type=int, default=100)
    experiment.add_argument("--steps", type=int, default=250, help="Transitions per Bilibili chain, including rejection self-loops")
    experiment.add_argument("--seed-aid", action="append", type=int, default=[])
    experiment.add_argument("--seed-provenance", default="")
    probe = commands.add_parser("probe", help="Make one metadata/playability request without discovery")
    probe.add_argument("platform", choices=("youtube", "bilibili"))
    probe.add_argument("--aid", type=int)
    probe.add_argument("--video-id")
    control = commands.add_parser("search-control", help="Check YouTube identifier search against a known public ID")
    control.add_argument("--video-id", required=True)
    catalog = commands.add_parser("catalog", help="Import and sample declared historical video catalogs")
    catalogs = catalog.add_subparsers(dest="catalog_command", required=True)
    fetch = catalogs.add_parser("fetch", help="Download ChinaOpen or a bounded YouNiverse preview and import it")
    fetch.add_argument("source", choices=SOURCES)
    fetch.add_argument("--max-download-mib", type=int, default=8)
    fetch.add_argument("--preview-rows", type=int, default=2000)
    importer = catalogs.add_parser("import", help="Import a local URL list or gzipped metadata file")
    importer.add_argument("source", choices=SOURCES)
    importer.add_argument("file")
    importer.add_argument("--scope", choices=("local_file", "source_snapshot", "prefix_preview"), default="local_file")
    importer.add_argument("--limit", type=int)
    catalogs.add_parser("list", help="List local catalog manifests")
    info = catalogs.add_parser("info", help="Inspect a catalog's provenance and scope")
    info.add_argument("catalog_id")
    sample = catalogs.add_parser("sample", help="Draw linked videos uniformly without replacement within a catalog")
    sample.add_argument("catalog_id")
    sample.add_argument("--count", type=int, default=100)
    sample.add_argument("--seed", type=int, default=0)
    sample.add_argument("--show-items", action="store_true")
    return result


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "login":
            emit(confirm_bilibili_login(data_root(args.data_dir)) if args.confirm else login_bilibili(data_root(args.data_dir), args.timeout))
            return 0
        if args.command == "doctor":
            emit({"version": __version__, "stage": "evidence_pilot", "python": sys.version.split()[0],
                  "data_directory": str(data_root(args.data_dir)),
                  "tools": {name: bool(shutil.which(name) or (name == "ffmpeg" and importlib.util.find_spec("imageio_ffmpeg"))) for name in ("uv", "node", "codex", "ffmpeg", "ffprobe")},
                  "evidence_dependencies": {name: importlib.util.find_spec(name) is not None for name in ("yt_dlp", "PIL", "playwright", "faster_whisper")},
                  "authentication_checked": False, "model_calls_required_for_assessment": True,
                  "recommendations_available": False})
            return 0
        if args.command == "queries":
            bank = load_bank(args.bank)
            if args.export:
                with Path(args.export).open("x", encoding="utf-8") as destination:
                    json.dump({k: v for k, v in bank.items() if k != "sha256"}, destination, ensure_ascii=False, indent=2)
            emit({"bank_version": bank["version"], "sha256": bank["sha256"], "plan": query_plan(bank, args.platform, args.round_number)})
            return 0
        if args.command == "simulate":
            report = simulation_report()
            emit(report)
            return 0 if report["passed"] else 1
        store = Store(data_root(args.data_dir))
        try:
            if args.command == "evidence" or (args.command in {"continue", "report"} and store.get(args.run_id)["settings"].get("kind") == "evidence"):
                if args.command == "evidence" and args.evidence_command == "pilot-report":
                    report = pilot_report(store)
                    emit({"phase_two_complete": report["phase_two_complete"], "verified_videos": report["verified_videos"],
                          "totals": report["totals"], "report_path": str(store.root / "evidence" / "pilot-report.md")})
                    return 0
                if args.command == "evidence" and args.evidence_command == "prepare":
                    run_id = create_evidence(store, args.url, args.media, args.captions, args.use_browser, args.max_mib, args.max_seconds)
                    prepare_evidence(store, run_id, args.asr_model)
                else:
                    run_id = args.run_id
                    if args.command == "continue":
                        prepare_evidence(store, run_id)
                    elif args.command == "evidence" and args.evidence_command == "frame":
                        add_evidence_frame(store, run_id, args.timestamp, args.reason, args.long_edge)
                    elif args.command == "evidence" and args.evidence_command == "captions":
                        refresh_native_captions(store, run_id)
                    elif args.command == "evidence":
                        assess_evidence(store, run_id, args.mode)
                path = write_evidence_report(store, run_id)
                report = evidence_report(store, run_id)
                emit({"run_id": run_id, "platform": report["platform"], "status": report["status"], "reason": report["reason"],
                      "report_path": str(path), "frame_count": len(report["frames"]),
                      "caption_source": (report["captions"] or {}).get("source"), "assessments": list(report["assessments"]),
                      "resources": report["resources"]})
                return 0 if report["status"] in {"prepared", "assessed", "completed"} or args.command == "report" else 2
            if args.command == "discover" or (args.command in {"continue", "report"} and store.get(args.run_id)["settings"].get("kind") == "discovery"):
                run_id = create_discovery(store, args.platform, args.bank, args.round_number, args.max_requests,
                                          args.max_seconds, args.creators, args.creator_pages) if args.command == "discover" else args.run_id
                if args.command != "report":
                    print(f"Discovery {run_id}: collecting {store.get(run_id)['platform']} public listings", file=sys.stderr, flush=True)
                    execute_discovery(store, run_id)
                path = write_discovery_report(store, run_id)
                report = discovery_report(store, run_id)
                emit({"run_id": run_id, "status": report["status"], "reason": report["reason"], "report_path": str(path),
                      "candidate_count": report["candidate_count"], "shortlist_count": len(report["shortlist"]),
                      "source_counts": report["source_counts"], "checks": report["checks"]})
                return 0 if report["status"] == "completed" or args.command == "report" else 2
            if args.command == "catalog":
                if args.catalog_command == "fetch":
                    emit(fetch_catalog(store, args.source, args.max_download_mib * 1024 * 1024, args.preview_rows))
                elif args.catalog_command == "import":
                    emit(import_catalog(store, args.source, args.file, args.scope, args.limit))
                elif args.catalog_command == "list":
                    emit(list_catalogs(store))
                elif args.catalog_command == "info":
                    emit(get_catalog(store, args.catalog_id))
                else:
                    draw = sample_catalog(store, args.catalog_id, args.count, args.seed)
                    emit(draw if args.show_items else {k: v for k, v in draw.items() if k != "items"})
                return 0
            if args.command == "status":
                emit([{k: r[k] for k in ("id", "platform", "status", "reason", "elapsed")} for r in store.list_runs()])
                return 0
            if args.command in {"gate", "recommend"}:
                gate = release_gate(store)
                emit(gate)
                return 0 if args.command == "gate" else 2
            if args.command == "report":
                path = write_report(store, args.run_id)
                emit({"report_path": str(path), "report": make_report(store, args.run_id)})
                return 0
            if args.command == "experiment":
                run_id = create_experiment(store, args.platform, args.seed, args.max_requests, args.max_seconds,
                                           args.queries, args.steps, args.seed_aid, args.seed_provenance)
            elif args.command == "probe":
                if args.platform == "youtube":
                    if not args.video_id or not YOUTUBE_ID.fullmatch(args.video_id) or args.aid is not None:
                        raise ValueError("YouTube probes require --video-id with one canonical video ID.")
                    identifier = args.video_id
                else:
                    if args.aid is None or not 1 <= args.aid <= MAX_AID or args.video_id is not None:
                        raise ValueError("Bilibili probes require --aid within the declared encoding domain.")
                    identifier = args.aid
                run_id = create_probe(store, args.platform, identifier)
            elif args.command == "search-control":
                if not YOUTUBE_ID.fullmatch(args.video_id):
                    raise ValueError("The search control requires a canonical YouTube video ID.")
                run_id = create_search_control(store, args.video_id)
            else:
                run_id = args.run_id
            print(f"Experiment {run_id}: running {store.get(run_id)['platform']}", file=sys.stderr, flush=True)
            run = execute(store, run_id)
            path = write_report(store, run_id)
            emit({"run_id": run_id, "status": run["status"], "reason": run["reason"],
                  "report_path": str(path), "report": make_report(store, run_id)})
            return 0 if run["status"] == "completed" else 2
        finally:
            store.close()
    except (ValueError, EvidencePending, OSError, EOFError, UnicodeError, sqlite3.Error, http.client.HTTPException) as error:
        print(f"deepvids: {error}", file=sys.stderr)
        return 2

