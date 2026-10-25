# Usable demo phases

Finish and verify one phase before starting the next. `deepvids gate` reports readiness from local source receipts and checkpoints.

| Phase | Deliverable | Completion check |
| --- | --- | --- |
| 1. Curated live discovery | Versioned query bank, live search/creators, Bilibili Home, registry and candidate previews | Three distinct completed rounds per platform, each with 100 observed candidates, 50 preview candidates and required source/intent coverage |
| 2. Evidence and quality assessment | Six-video trial: captions/transcripts, four initial frames with at most eight submitted images per video, audio only where inspectable, explicit quality rubric | Both platforms have usable content access, evidence-backed scorecards, verified per-part durations and a caption-only versus augmented benefit/cost report; see PLAN.md |
| 3. Manual recommendation demo | Onboarding, 25/25 familiar/unfamiliar inspection list, scores, 20 recommendations/platform, saved digests and additional rounds | Complete manual workflow enforces duration, conditional discovery minimum and exact-ID history |
| 4. Bayesian feedback learning | Preference/quality posterior updates, reason routing, revisions and calibration | Approximately 30 separately rated development videos and held-out checks support updates; revisions replace prior observations |
| 5. Daily installable demo | Opt-in scheduling, rollover, recovery and Windows local/npm installation | Daily workflow works on both platforms with private state outside installed packages |

Phase 3 is the first recommendation demo. Phase 5 completes the daily workflow. Phase 1 previews are candidates, not quality-ranked recommendations.

## Reference workspace status — October 24, 2026

**Phase 1 is complete. Phase 2 has not started.** See [live verification](PHASE1_VALIDATION.md) and [the merged implementation plan](PLAN.md).

YouTube uses curated search and channel video tabs. Bilibili adds isolated-session public Home. Bilibili creator expansion uses public search with returned owner-ID verification because direct archive access was blocked; it is explicitly a partial index view.

Random video/user IDs and ChinaOpen/YouNiverse are outside the active path. Historical artifacts cannot pass the current gate. A fresh installation must establish its own three successful rounds per platform.
