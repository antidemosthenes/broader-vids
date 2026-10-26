# Usable demo phases

Finish and verify one phase before starting the next. `deepvids gate` reports readiness from local source receipts and checkpoints.

| Phase | Deliverable | Completion check |
| --- | --- | --- |
| 1. Curated live discovery | Versioned query bank, live search/creators, Bilibili Home, registry and candidate previews | Three distinct completed rounds per platform, each with 100 observed candidates, 50 preview candidates and required source/intent coverage |
| 2. Evidence and quality assessment | Six-video trial: captions/transcripts, four initial frames with at most eight submitted images per video, explicit quality rubric | Both platforms have usable content access, evidence-backed scorecards, verified per-part durations and a caption-only versus augmented benefit/cost report; see PLAN.md |
| 3. Manual recommendation demo | Onboarding, metadata enrichment and engagement-ratio screening into 50/platform including guaranteed pending entries, quality/full scores, 10 recommendations/platform, digests, watched/dismissed actions, stored votes and additional rounds | Workflow verifies count coverage/shrinkage, duration, conditional two-unrelated minimum, pending capacity/carryover, watched/dismissed suppression, evidence reuse and independent watch/vote state |
| 4. Bayesian feedback learning | Preference/quality posterior updates, reason routing, revisions and calibration | Approximately 30 separately rated development videos and held-out checks support updates; revisions replace prior observations |
| 5. Daily installable demo | Opt-in scheduling, rollover preserving pending recommendations, recovery and Windows local/npm installation | Daily workflow works on both platforms with private state outside installed packages; day changes preserve watched/feedback/pending state |

Phase 3 is the first recommendation demo. Phase 5 completes the daily workflow. Phase 1 previews are candidates, not quality-ranked recommendations.

The October 26 exploration extension is implemented before Phase 4: onboarded runs use a growing domain map and bounded AI planning, while the curated path remains explicit. See [hierarchical exploration verification](validation/EXPLORATION_VALIDATION.md). This extension does not enable Bayesian feedback fitting or scheduling.

Current output target is 10 YouTube and 10 Bilibili recommendations. Previously recommended videos keep guaranteed inspection slots until marked watched or dismissed and can reappear in the final lists. With 50 pending on one platform, fresh admissions wait for queue space. Feedback and watched state are separate; unwatched is not negative feedback. See PLAN.md for the funnel and capacity rules.

## Reference workspace status — October 25, 2026


**Phases 1–3 are complete in the reference workspace. Phase 4 has not started.** The local gate verified complete 50-candidate/10-recommendation runs on both platforms with intact selected evidence; partial digests do not pass it. Bayesian fitting and scheduling remain future work. See [discovery verification](validation/PHASE1_VALIDATION.md), [the six-video evidence trial](validation/PHASE2_VALIDATION.md), [the manual demo](validation/PHASE3_VALIDATION.md) and [the implementation plan](PLAN.md).

YouTube uses curated search and channel video tabs. Bilibili adds isolated-session public Home. Bilibili creator expansion uses public search with returned owner-ID verification because direct archive access was blocked; it is explicitly a partial index view.

Random video/user IDs and ChinaOpen/YouNiverse are outside the active path. Historical artifacts cannot pass the current gate. A fresh installation must establish its own three successful rounds per platform.
