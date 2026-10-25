import argparse
import http.client
import json
import shutil
import sqlite3
import sys

from . import __version__
from .catalogs import SOURCES, fetch_catalog, get_catalog, import_catalog, list_catalogs, sample_catalog
from .experiments import create_experiment, create_probe, create_search_control, execute
from .providers import MAX_AID, YOUTUBE_ID
from .reports import make_report, release_gate, write_report
from .sampling import simulation_report
from .storage import Store, data_root


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
        if args.command == "doctor":
            emit({"version": __version__, "stage": "catalog_discovery", "python": sys.version.split()[0],
                  "data_directory": str(data_root(args.data_dir)),
                  "tools": {name: bool(shutil.which(name)) for name in ("uv", "node", "codex", "ffmpeg", "ffprobe")},
                  "authentication_checked": False, "model_calls_required_for_this_stage": False,
                  "recommendations_available": False})
            return 0
        if args.command == "simulate":
            report = simulation_report()
            emit(report)
            return 0 if report["passed"] else 1
        store = Store(data_root(args.data_dir))
        try:
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
    except (ValueError, OSError, EOFError, UnicodeError, sqlite3.Error, http.client.HTTPException) as error:
        print(f"deepvids: {error}", file=sys.stderr)
        return 2

