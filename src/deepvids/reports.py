from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .diagnostics import diagnose
from .catalogs import SOURCES, list_catalogs
from .models import Policy
from .storage import utc_now
from .discovery import discovery_report


LIMITATIONS = {
    "youtube": [
        "Five alphabetic ID characters followed by a hyphen restrict proposal support.",
        "Case-insensitive indexed search and any continuation truncation can bias retrieval.",
        "Player status is not a download/transcript-access or whole-catalog coverage test.",
    ],
    "bilibili": [
        "The 51-bit AID domain comes from a third-party encoder, not an official completeness guarantee.",
        "Part counts target metadata-visible parts; per-part playback availability is unverified.",
        "Finite random walks can remain trapped despite an appropriate stationary target.",
        "Dispersed starting archives are not a probability sample; diagnostics cannot reveal every unseen region.",
    ],
}


def make_report(store, run_id):
    run = store.get(run_id)
    events = store.events(run_id)
    all_samples = [e["payload"] for e in events if e["kind"] == "sample"]
    samples = [e["payload"] for e in events if e["kind"] == "sample" and not e["payload"]["warmup"]]
    prefixes = [e["payload"] for e in events if e["kind"] == "prefix"]
    policy = Policy()
    counts = Counter(s["part_key"] for s in samples)
    durations = [s["duration_seconds"] for s in samples if s["duration_seconds"] is not None]
    requests = store.requests(run_id)
    result = {"schema_version": 1, "run_id": run_id, "platform": run["platform"],
              "created_at": run["created_at"], "updated_at": run["updated_at"], "status": run["status"],
              "reason": run["reason"], "settings": run["settings"], "elapsed_seconds": run["elapsed"],
              "http_attempts": len(requests), "unresolved_http_attempts": sum(r["outcome"] != "ok" for r in requests),
              "response_bytes": sum(int(r["detail"][6:]) for r in requests if r["detail"].startswith("bytes:")),
              "retained_events": len(samples), "unique_parts": len(counts),
              "total_sample_events": len(all_samples),
              "warmup_events": sum(s["warmup"] for s in all_samples),
              "unresolved_resolution_events": sum(e["kind"] == "resolution" and e["payload"]["outcome"] == "unresolved" for e in events),
              "unique_archives": len({s["archive_id"] for s in samples}),
              "repeated_states": len(samples) - len(counts),
              "prefix_queries": len(prefixes),
              "returned_search_ids": sum(p.get("returned_id_count", 0) for p in prefixes),
              "matching_search_ids": sum(len(p["matching_ids"]) for p in prefixes),
              "truncated_prefix_pages": sum(p["continuation_present"] for p in prefixes),
              "unknown_durations": sum(s["duration_seconds"] is None for s in samples),
              "over_90_minutes": sum(d > 5400 for d in durations),
              "duration_eligible_unique_parts": len({s["part_key"] for s in samples if policy.duration_eligible(s["duration_seconds"])}),
              "access_evidence": dict(Counter(s["access_evidence"] for s in samples)),
              "upload_year_counts": dict(Counter(_year(s["published_at"]) for s in samples)),
              "duration_band_counts": dict(Counter(_duration_band(s["duration_seconds"]) for s in samples)),
              "view_magnitude_counts": dict(Counter(_view_band(s["views"]) for s in samples)),
              "limitations": LIMITATIONS[run["platform"]],
              "coverage_validated": False, "content_access_validated": False,
              "gate": "blocked", "gate_reason": "Independent coverage and content-evidence access have not been established."}
    if run["settings"]["kind"] == "probe":
        result["probe_results"] = [e["payload"] for e in events if e["kind"] == "probe"]
    if run["settings"]["kind"] == "search_control":
        result["control_results"] = [e["payload"] for e in events if e["kind"] == "search_control"]
    if run["platform"] == "bilibili" and run["settings"]["kind"] == "experiment":
        chains = [[math.log2(int(s["archive_id"])) for s in samples if s["chain"] == i] for i in range(4)]
        result["log_aid_diagnostics"] = diagnose(chains)
        result["chains"] = [{"chain": i, "retained_events": len(chains[i]),
                             "completed_steps": sum(s["chain"] == i for s in all_samples),
                             "unique_archives": len({s["archive_id"] for s in samples if s["chain"] == i}),
                             "moved_steps": sum(s["moved"] for s in samples if s["chain"] == i)} for i in range(4)]
    result["observed_yield_check"] = run["status"] == "completed" and len(counts) >= 100 and run["settings"]["kind"] == "experiment"
    return result


def _year(value):
    try:
        if isinstance(value, str):
            return str(datetime.fromisoformat(value).year)
        if type(value) is int:
            return str(datetime.fromtimestamp(value, timezone.utc).year)
    except (ValueError, OverflowError, OSError):
        pass
    return "unknown"


def _duration_band(value):
    if value is None:
        return "unknown"
    for seconds, label in ((60, "0–1m"), (600, "1–10m"), (1800, "10–30m"), (3600, "30–60m"), (5400, "60–90m")):
        if value <= seconds:
            return label
    return ">90m"


def _view_band(value):
    if type(value) is not int or value < 0:
        return "unknown"
    if value == 0:
        return "0"
    return f"10^{int(math.log10(value))}"


def write_report(store, run_id):
    report = make_report(store, run_id)
    root = store.root / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    target = root / "report.json"
    _atomic(target, json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    lines = [f"# {report['platform']} sampling experiment", "", f"Run: `{run_id}`",
             f"Status: **{report['status']}**", f"Reason: {report['reason']}", "",
             f"HTTP attempts: {report['http_attempts']}", f"Active seconds: {report['elapsed_seconds']:.2f}",
             f"Retained events: {report['retained_events']}", f"Distinct parts: {report['unique_parts']}",
             f"Repeated states: {report['repeated_states']}", "", "## Release gate", "",
             f"**{report['gate']}**: {report['gate_reason']}", "", "## Limitations", ""]
    lines.extend(f"- {item}" for item in report["limitations"])
    _atomic(root / "report.md", "\n".join(lines) + "\n")
    trace = "".join(json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n" for e in store.events(run_id))
    _atomic(root / "trace.jsonl", trace)
    return target


def _atomic(path, body):
    temp = Path(str(path) + ".tmp")
    temp.write_text(body, encoding="utf-8")
    temp.replace(path)


def release_gate(store):
    platforms = {}
    catalogs = list_catalogs(store)
    phase_one_complete = True
    for platform in ("youtube", "bilibili"):
        discoveries = [discovery_report(store, r["id"]) for r in store.list_runs()
                       if r["platform"] == platform and r["settings"].get("kind") == "discovery"]
        verified_discoveries = [r for r in discoveries if r["status"] == "completed" and all(r["checks"].values())]
        discovery_rounds = {}
        for report in verified_discoveries:
            discovery_rounds.setdefault(report["settings"]["bank_sha256"], set()).add(report["settings"]["round"])
        runs = [r for r in store.list_runs() if r["platform"] == platform and r["settings"]["kind"] == "experiment"]
        reports = [make_report(store, r["id"]) for r in runs]
        passing = [r for r in reports if r["observed_yield_check"]]
        independent_seeds = len({r["settings"]["seed"] for r in passing})
        local = [c for c in catalogs if c["platform"] == platform and c["status"] == "ready" and c["item_count"] >= 100]
        exercised = []
        rejected = []
        for catalog in local:
            source = SOURCES.get(catalog["source"])
            if source is None or not source.discovery_eligible:
                rejected.append(catalog["id"])
                continue
            manifest = catalog["manifest"]
            receipt = manifest.get("receipt") or {}
            provenance = catalog["scope"] == "source_snapshot" or (
                receipt.get("sha256") == manifest["sha256"] and receipt.get("source_url") == manifest["source_url"])
            draws = store.db.execute("SELECT COUNT(*) FROM catalog_draws WHERE catalog_id=? AND sample_size>=100", (catalog["id"],)).fetchone()[0]
            if provenance and draws:
                exercised.append(catalog["id"])
        verified_round_count = max((len(rounds) for rounds in discovery_rounds.values()), default=0)
        discovery_ready = verified_round_count >= 3
        phase_one_complete &= discovery_ready
        reasons = [] if discovery_ready else ["Need three distinct completed rounds of one curated query bank, each with 100 observed candidates, 50 shortlist candidates and verified source coverage."]
        if rejected:
            reasons.append("Historical or unapproved catalogs are test fixtures and cannot satisfy discovery readiness.")
        reasons.extend(["Current content-evidence access and quality scorecards are not implemented.",
                        "Recommendation generation, Bayesian learning and scheduling remain later phases."])
        platforms[platform] = {"experiment_count": len(runs), "yield_passing_distinct_seeds": independent_seeds,
                               "catalog_count": len(local), "exercised_catalogs": exercised, "ineligible_catalogs": rejected,
                               "discovery_runs": [{k: r[k] for k in ("run_id", "status", "candidate_count", "checks")} for r in discoveries],
                               "verified_discovery_rounds": verified_round_count,
                               "candidate_discovery_ready": discovery_ready, "status": "discovery_ready" if discovery_ready else "blocked", "reasons": reasons}
    names = ("Candidate discovery", "Evidence and quality assessment", "Manual recommendation demo",
             "Bayesian feedback learning", "Daily installable demo")
    phases = [{"phase": i, "name": name, "status": "complete" if i == 1 and phase_one_complete else
               "next" if i == 2 and phase_one_complete else "blocked" if i == 1 else "not_started"}
              for i, name in enumerate(names, 1)]
    return {"generated_at": utc_now(), "ready": False, "sampling_population": "Curated live search and creator listings; Bilibili additionally uses isolated-session Home",
            "current_phase": 2 if phase_one_complete else 1, "phase_one_complete": bool(phase_one_complete),
            "phases": phases, "platforms": platforms,
            "next_step": "Begin evidence and quality assessment after reviewing Phase 1 artifacts." if phase_one_complete else
                         "Complete and inspect curated discovery runs for both platforms; see docs/PLAN.md."}
