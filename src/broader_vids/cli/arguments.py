import argparse

from broader_vids import __version__
from broader_vids.worker_context import EFFORTS


def worker_arguments(command):
    command.add_argument(
        "--model",
        default=None,
        help="Model selected in the invoking chat; inherited when omitted",
    )
    command.add_argument(
        "--effort",
        choices=EFFORTS,
        default=None,
        help="Reasoning effort selected in the invoking chat; inherited when omitted",
    )


def parser():
    result = argparse.ArgumentParser(
        prog="broader-vids",
        description="Curated video recommendations, quality evidence and local feedback",
    )
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument(
        "--data-dir",
        help="External state directory; defaults to LOCALAPPDATA/broader-vids on Windows",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "doctor", help="Inspect local prerequisites without reading credentials"
    )
    commands.add_parser(
        "status", help="List discovery, evidence and recommendation checkpoints"
    )
    commands.add_parser(
        "gate", help="Inspect development validation, separate from normal onboarding"
    )
    exclusions = commands.add_parser(
        "exclusions", help="Manage platform creator and tag exclusion rules"
    )
    exclusion_commands = exclusions.add_subparsers(dest="operation", required=True)
    exclusion_commands.add_parser("list")
    exclusion_commands.add_parser("sync-bilibili")
    video_rule = exclusion_commands.add_parser("video")
    video_rule.add_argument("change", choices=("add", "remove"))
    video_rule.add_argument(
        "value", help="Video URL or canonical ID whose creator the rule concerns"
    )
    for kind in ("creator", "tag"):
        rule = exclusion_commands.add_parser(kind)
        rule.add_argument("change", choices=("add", "remove"))
        rule.add_argument("platform", choices=("youtube", "bilibili"))
        rule.add_argument("value")
        if kind == "creator":
            rule.add_argument("--name", default="")
    learning = commands.add_parser(
        "learn", help="Inspect, rebuild or evaluate explicit-feedback Bayesian models"
    )
    learning.add_argument(
        "operation",
        choices=(
            "status",
            "fit",
            "evaluate",
            "repair-evidence",
            "label-videos",
            "migrate-feedback",
        ),
        default="status",
        nargs="?",
    )
    learning.add_argument("--max-seconds", type=float, default=180)
    learning.add_argument("--max-videos", type=int, default=1)
    calibration = commands.add_parser(
        "calibrate",
        help="Create or inspect the 30-video interest/quality calibration cohort",
    )
    calibration.add_argument(
        "operation", choices=("start", "list"), default="list", nargs="?"
    )
    calibration.add_argument("--seed", type=int, default=42)
    calibration.add_argument(
        "--new",
        action="store_true",
        help="Start another unseen cohort after the previous reservation is released",
    )
    metadata = commands.add_parser(
        "metadata",
        help="Refresh one video's public metadata without downloading media or calling AI",
    )
    metadata.add_argument(
        "identifier", help="Canonical video URL or platform/video/part ID"
    )
    metadata.add_argument(
        "--use-browser",
        action="store_true",
        help="Use the existing isolated Bilibili login; no anonymous fallback",
    )
    rate = commands.add_parser(
        "rate",
        help="Rate a specific topic, broad subject, depth fit or quality independently",
    )
    rate.add_argument("identifier")
    rate.add_argument(
        "dimension",
        choices=("topic_interest", "domain_interest", "depth", "quality_level"),
    )
    rate.add_argument(
        "value",
        choices=("high", "medium", "low", "deeper", "suitable", "lighter", "clear"),
    )
    rate.add_argument("--target", default="")
    rate.add_argument(
        "--level",
        choices=("unknown", "introductory", "intermediate", "advanced"),
        default=None,
    )
    cleanup = commands.add_parser(
        "cleanup",
        help="Preview removal of assessed full videos and obsolete evidence intermediates",
    )
    cleanup.add_argument(
        "--apply", action="store_true", help="Remove the previewed disposable files"
    )
    recommend = commands.add_parser(
        "recommend", help="Run the manual 50-to-10 recommendation workflow"
    )
    recommend.add_argument("platform", choices=("youtube", "bilibili", "both"))
    recommend.add_argument(
        "--discovery-run",
        help="Reuse a saved live discovery buffer instead of collecting a fresh round",
    )
    recommend.add_argument("--use-browser", action="store_true")
    recommend.add_argument("--max-seconds", type=float, default=1800)
    recommend.add_argument("--workers", type=int, default=4)
    worker_arguments(recommend)
    recommend.add_argument("--max-requests", type=int, default=160)
    recommend.add_argument("--max-assessments", type=int, default=52)
    recommend.add_argument("--max-mib", type=int, default=1024)
    recommend.add_argument("--asr-model", choices=("tiny", "base", "small"))
    publish = commands.add_parser(
        "publish",
        help="Save a recommendation digest from completed or explicitly partial assessments",
    )
    publish.add_argument("run_id")
    publish.add_argument("--allow-incomplete", action="store_true")
    profile = commands.add_parser(
        "profile", help="Set or inspect explicit onboarding interests"
    )
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("show")
    profile_export = profile_commands.add_parser(
        "export", help="Export learned parameters and interests without video history"
    )
    profile_export.add_argument("file", help="Destination JSON file")
    profile_export.add_argument("--overwrite", action="store_true")
    profile_import = profile_commands.add_parser(
        "import", help="Preview or replace learned parameters and interests"
    )
    profile_import.add_argument("file", help="Preference JSON file")
    profile_import.add_argument(
        "--apply",
        action="store_true",
        help="Create a recovery file and apply the replacement",
    )
    profile_set = profile_commands.add_parser("set")
    profile_set.add_argument("--interest", action="append", required=True)
    video = commands.add_parser("video", help="Change watched or dismissed state")
    video.add_argument(
        "action",
        choices=("watched", "unwatched", "dismiss", "restore"),
    )
    video.add_argument("identifier", help="Canonical video URL or ID from the digest")
    queue = commands.add_parser(
        "queue", help="Inspect saved pending, watched, dismissed and feedback state"
    )
    queue.add_argument("--platform", choices=("youtube", "bilibili"))
    serve = commands.add_parser(
        "serve", help="Open a loopback-only recommendation and feedback dashboard"
    )
    serve.add_argument("--port", type=int, default=8765)
    dashboard = commands.add_parser(
        "dashboard", help="Open, inspect, or stop this profile's local dashboard"
    )
    dashboard.add_argument(
        "operation", choices=("open", "status", "stop"), default="open", nargs="?"
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Preferred local port; use a free port when occupied",
    )
    dashboard.add_argument(
        "--no-browser",
        action="store_true",
        help="Return the dashboard URL without opening a browser",
    )
    login = commands.add_parser(
        "login",
        help="Open an isolated Playwright browser for user-controlled platform sign-in",
    )
    login.add_argument("platform", choices=("bilibili",))
    login.add_argument("--timeout", type=int, default=600)
    login.add_argument(
        "--confirm",
        action="store_true",
        help="Verify sign-in after completing it in the open isolated browser",
    )
    evidence = commands.add_parser(
        "evidence",
        help="Prepare bounded captions/frames and compare quality assessments",
    )
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    prepare = evidence_commands.add_parser(
        "prepare",
        help="Resolve one playable video/part and prepare timestamped evidence",
    )
    prepare.add_argument("url")
    worker_arguments(prepare)
    prepare.add_argument(
        "--media", help="Explicit local video file instead of downloading"
    )
    prepare.add_argument(
        "--captions", help="Explicit local timestamped JSON3/JSON/VTT/SRT captions"
    )
    prepare.add_argument(
        "--use-browser",
        action="store_true",
        help="Use the user-authenticated isolated Bilibili profile",
    )
    prepare.add_argument("--max-mib", type=int, default=128)
    prepare.add_argument("--max-seconds", type=float, default=1200)
    prepare.add_argument("--asr-model", choices=("tiny", "base", "small"))
    assess = evidence_commands.add_parser(
        "assess", help="Use the ChatGPT-authenticated Codex CLI for bounded scorecards"
    )
    assess.add_argument("run_id")
    assess.add_argument(
        "--mode", choices=("baseline", "augmented", "both"), default="both"
    )
    frame = evidence_commands.add_parser(
        "frame",
        help="Add a targeted view before augmented assessment, within the eight-image cap",
    )
    frame.add_argument("run_id")
    frame.add_argument("--at", type=float, required=True, dest="timestamp")
    frame.add_argument("--reason", required=True)
    frame.add_argument("--long-edge", type=int, default=768)
    captions = evidence_commands.add_parser(
        "captions",
        help="Refresh native captions while preserving already-assessed inputs",
    )
    captions.add_argument("run_id")
    evidence_commands.add_parser(
        "pilot-report",
        help="Compare paired evidence assessments and report actual resource usage",
    )
    discover = commands.add_parser(
        "discover",
        help="Collect curated live search, creator and Bilibili Home candidates",
    )
    discover.add_argument("platform", choices=("youtube", "bilibili"))
    worker_arguments(discover)
    discover.add_argument("--bank", help="Optional versioned JSON query bank")
    discover.add_argument("--round", type=int, dest="round_number")
    discover.add_argument("--max-requests", type=int, default=100)
    discover.add_argument("--max-seconds", type=float, default=600)
    discover.add_argument("--creators", type=int, default=4)
    discover.add_argument("--creator-pages", type=int, default=2)
    discover.add_argument(
        "--curated",
        action="store_true",
        help="Use the fixed query bank instead of hierarchical exploration",
    )
    discover.add_argument(
        "--seed", type=int, help="Reproducible random seed for existing-topic selection"
    )
    discover.add_argument("--max-planning-calls", type=int, default=2)
    commands.add_parser(
        "topics",
        help="Inspect the persistent exploration map without changing preferences",
    )
    queries = commands.add_parser(
        "queries", help="Inspect the curated query bank and deterministic round plan"
    )
    queries.add_argument("--bank")
    queries.add_argument(
        "--platform", choices=("youtube", "bilibili"), default="youtube"
    )
    queries.add_argument("--round", type=int, default=0, dest="round_number")
    queries.add_argument(
        "--export", help="Create an editable copy of the bank at a new JSON file"
    )
    report = commands.add_parser(
        "report", help="Write standalone JSON, Markdown and trace reports"
    )
    report.add_argument("run_id")
    continuation = commands.add_parser(
        "continue",
        help="Resume a paused job without resetting its budget or random state",
    )
    continuation.add_argument("run_id")
    return result
