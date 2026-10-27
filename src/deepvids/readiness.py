from deepvids.discovery.service import discovery_report
from deepvids.evidence.reports import pilot_report
from deepvids.recommendations.reports import recommendation_readiness
from deepvids.storage import utc_now


def release_gate(store):
    evidence = pilot_report(store, write=False)
    recommendations = recommendation_readiness(store)
    platforms = {}
    for platform in ("youtube", "bilibili"):
        discoveries = [
            discovery_report(store, run["id"])
            for run in store.iter_runs(kind="discovery", platform=platform)
        ]
        rounds = {}
        for report in discoveries:
            if report["status"] == "completed" and all(report["checks"].values()):
                rounds.setdefault(report["settings"]["bank_sha256"], set()).add(
                    report["settings"]["round"]
                )
        count = max((len(values) for values in rounds.values()), default=0)
        reasons = []
        if count < 3:
            reasons.append(
                "Need three distinct completed rounds of one hierarchical policy or curated query bank, each with verified source coverage, 100 candidates and 50 preview candidates."
            )
        if evidence["verified_videos"][platform] < 3:
            reasons.append(
                "Need three verified playable videos with valid caption-only and augmented scorecards on this platform."
            )
        platforms[platform] = {
            "candidate_discovery_ready": count >= 3,
            "verified_discovery_rounds": count,
            "content_evidence_ready": evidence["verified_videos"][platform] >= 3,
            "verified_evidence_videos": evidence["verified_videos"][platform],
            "discovery_runs": [
                {
                    key: report[key]
                    for key in ("run_id", "status", "candidate_count", "checks")
                }
                for report in discoveries
            ],
            "reasons": reasons,
        }
    phase_one = all(value["candidate_discovery_ready"] for value in platforms.values())
    phase_two = phase_one and evidence["phase_two_complete"]
    phase_three = phase_two and recommendations["phase_three_complete"]
    from deepvids.learning.calibration import evaluate

    try:
        learning = evaluate(store, fit=False) if phase_three else None
    except (ValueError, OSError) as error:
        learning = {"phase_four_complete": False, "reasons": [str(error)]}
    phase_four = bool(phase_three and learning["phase_four_complete"])
    current = (
        5
        if phase_four
        else 4
        if phase_three
        else 3
        if phase_two
        else 2
        if phase_one
        else 1
    )
    names = (
        "Candidate discovery",
        "Evidence and quality assessment",
        "Manual recommendation demo",
        "Bayesian feedback learning",
        "Daily installable demo",
    )
    complete = {1: phase_one, 2: phase_two, 3: phase_three, 4: phase_four}
    return {
        "generated_at": utc_now(),
        "ready": False,
        "current_phase": current,
        "manual_recommendations_available": phase_two,
        "phase_one_complete": phase_one,
        "phase_two_complete": phase_two,
        "phase_three_complete": phase_three,
        "phase_four_complete": phase_four,
        "learning": learning,
        "verified_recommendation_runs": recommendations["verified_runs"],
        "platforms": platforms,
        "phases": [
            {
                "phase": index,
                "name": name,
                "status": "complete"
                if complete.get(index)
                else "next"
                if index == current
                else "not_started",
            }
            for index, name in enumerate(names, 1)
        ],
        "next_step": (
            "Validate opt-in scheduling and deployment in Phase 5 when requested."
            if phase_four
            else "Rate the saved calibration cohort separately for interest and quality, then run learn evaluate."
            if phase_three
            else "Run and inspect manual recommendations on both platforms."
            if phase_two
            else "Prepare and assess the six-video evidence pilot."
            if phase_one
            else "Complete three curated discovery rounds per platform; see docs/REFERENCE.md."
        ),
    }
