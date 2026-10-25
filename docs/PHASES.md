# Usable demo phases

We finish and verify one phase before beginning the next. The CLI's `gate` command reports the current state from local artifacts.

| Phase | Deliverable | Completion check |
| --- | --- | --- |
| 1. Candidate discovery | Establish an accepted current catalog or efficient validated sampler for each platform | Both platforms produce 100 distinct candidates from an accepted population, with documented coverage, dates, cost and selection rules; historical fixtures cannot pass |
| 2. Evidence and quality assessment | Resolve current videos/parts, prepare captions or local transcripts and distributed frames, produce Codex quality scorecards | Real videos from both catalogs have evidence-backed scorecards with unknowns represented honestly, within the resource budget |
| 3. Manual recommendation demo | Private onboarding profile, repeated 100-candidate scans, 25/25 shortlist of 50, deterministic scoring, 20 recommendations/platform, saved digests and more runs | End-to-end manual recommendations obey duration rules, conditional discovery minimum and exact-ID history across rounds |
| 4. Bayesian feedback learning | Preference and quality posterior updates, reason routing, feedback revisions, uncertainty and calibration | Approximately 30 separately rated development videos and held-out checks support the updates; corrections do not double-count votes |
| 5. Daily installable demo | Opt-in Windows scheduling, date rollover, budget/recovery behavior, fresh local/npm installations | Complete daily workflow works for both platforms, including interruptions and upgrades, with personal state outside the package |

The CLI, SQLite checkpoints, reports, synthetic sampler checks and package launcher are completed preparation for Phase 1. The earlier platform-wide sampling experiments are preserved as research evidence; they do not establish current source suitability.

Phase 3 is the first usable recommendation demo. Phase 5 completes the daily plugin workflow. Phase 1 does not generate recommendations or infer Bayesian parameters.

The historical fixture exercise used the complete ChinaOpen URL-list snapshot and a bounded YouNiverse metadata prefix. The YouNiverse prefix is a development catalog, not a uniform sample of the full source. Full local-file imports are supported separately. ChinaOpen links without a part query identify the default first part; other parts are outside that linked catalog until explicitly enumerated in Phase 2.

## Reference workspace status — October 24, 2026

The earlier fixture checks passed: the ChinaOpen response contained 52,173 distinct linked videos; the bounded YouNiverse preview contained 2,000 records. A seeded 100-item draw was saved for each platform. Source receipts, hashes and selection scopes are retained in `.localdata`; no video media or model inference was used.

Phase 1 is incomplete. Both historical sources are now rejected and excluded from readiness. Work stops at discovery; Phase 2 has not started. See [the reassessment](DISCOVERY_REASSESSMENT.md).
