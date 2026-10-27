# Usable demo phases

Finish and verify one phase before starting the next. `deepvids gate` reports readiness from local source receipts and checkpoints.

| Phase | Deliverable | Completion check |
| --- | --- | --- |
| 1. Curated live discovery | Versioned query bank, live search/creators, Bilibili Home, registry and candidate previews | Three distinct completed rounds per platform, each with 100 observed candidates, 50 preview candidates and required source/intent coverage |
| 2. Evidence and quality assessment | Six-video trial: captions/transcripts, four initial frames with at most eight submitted images per video, explicit quality rubric | Both platforms have usable content access, evidence-backed scorecards, verified per-part durations and a caption-only versus augmented benefit/cost report; see PLAN.md |
| 3. Manual recommendation demo | Onboarding, metadata enrichment and engagement-ratio screening into 50/platform including guaranteed pending entries, quality/full scores, 10 recommendations/platform, digests, watched/dismissed actions, stored votes and additional rounds | Workflow verifies count coverage/shrinkage, duration, conditional two-unrelated minimum, pending capacity/carryover, watched/dismissed suppression, evidence reuse and independent watch/vote state |
| 4. Bayesian feedback learning | Preference/quality posterior updates, reason routing, revisions and calibration | Approximately 30 separately rated development videos and held-out checks support updates; revisions replace prior observations |
| 5. Locally installed Codex plugin and daily demo | Desktop-only skills plugin, external Python engine, actual Codex installation, daily job semantics, opt-in desktop scheduling and local distribution artifacts | Complete 5A–5D below; actual installed-plugin and scheduled runs work with external private state; public publication is separate |

Phase 3 is the first recommendation demo. Phase 5 completes the daily workflow. Phase 1 previews are candidates, not quality-ranked recommendations.

## Phase 5 delivery sequence — planned, not implemented

The deliverable is a **desktop-only Codex plugin that contains the Deepvids skill and invokes the local engine**. npm is an optional distribution route for the launcher, wheel and plugin files. The desktop app's Scheduled feature supplies the recurring trigger; the skill supplies the method, and the engine supplies durable daily state. `/goal` is not the daily clock. The dashboard and generated digests stay local. See the decisions, limitations and source references in [PLAN.md](PLAN.md#phase-5-product-installation-and-distribution-decisions).

| Step | Work | Acceptance before moving on |
| --- | --- | --- |
| 5A. Install and exercise locally | Finish portable manifest/catalog and guided setup; install from a local marketplace into Codex; verify source and packed-file paths | In a fresh local Codex conversation, the installed plugin activates, locates its bundled engine, completes setup, produces linked results and saves explicit feedback to isolated external state. Test an additional request, access failure, unrelated prompt and recovery. No internet publication is needed. Confirm how to select the existing demo state without copying or overwriting it. |
| 5B. Daily engine workflow | Add profile/date daily identity, timezone handling, serialized daily/additional runs, carryover, rollover and recovery | Duplicate triggers do not create duplicate daily batches; manual and scheduled calls share state/leases; one current-date catch-up follows downtime; unfinished older daily orchestration is superseded without losing observations or pending entries. Same-job continuation preserves budgets. Test day changes, restart, timezone boundaries and pause/resume. |
| 5C. Opt-in native schedule | Configure a desktop scheduled task invoking the installed skill in a local project with the selected external data root | First run on demand and then by a real scheduled trigger; verify the host can activate the installed plugin, access the same state/session and use authenticated model workers. Verify inspect/pause/remove and failure reporting. The computer must be on and the desktop app running. If host support is unavailable, report the blocker and agree on an alternative before claiming completion. |
| 5D. Local release candidate | Build local plugin ZIP and npm tarball from one versioned payload; verify clean Windows installation, upgrades and documentation | Install from local files with lifecycle scripts disabled; verify spaces/unrelated working directories/read-only package paths, external state preservation and exclusion of private data. The user can test the complete plugin before approving any public release. Record measured live runtime/completion yield, not only mocked test success. |

Phase 5A is the next implementation step. CLI/package tests from Phase 4 do not substitute for actual host installation. No schedule is enabled merely by installing the package. Time, timezone and the target profile are selected during schedule opt-in. Only one scheduling backend is enabled per profile; Windows Task Scheduler is a possible later adapter, not an implicit second trigger.

The initial supported case is Windows local Codex execution with the existing ChatGPT-authenticated CLI and isolated Bilibili session. A web-only/cloud plugin, remote MCP service, hosted public dashboard, always-on cloud execution and Claude runtime adapter are outside Phase 5. Local platform requests and dependency installation still need internet access; "local" describes execution/state, not offline video discovery.

## Optional public release — after local Phase 5 acceptance

Public release is not required to pass Phase 5. Prepare reviewable artifacts first, then obtain explicit authorization for each selected destination, including a push to this project's GitHub remote. None is authorized by this design update.

| Channel | Artifact | Separate release decision |
| --- | --- | --- |
| GitHub | Reviewed source, skill, plugin manifests/catalog, docs and synthetic examples; optional releases | Which branch/commit and public files may be pushed |
| npm | Tested versioned launcher/wheel/plugin bundle | Package name/scope, version, visibility and registry publication |
| OpenAI public Plugins Directory | Desktop-only skills-plugin ZIP, identity and listing material | Eligibility, submission/review and final publication; a GitHub/npm release does not perform this step |

The proposed public destination is OpenAI's directory, complemented by GitHub and npm if desired. Approval is external and not guaranteed. Personal/repo marketplaces remain a private local testing/distribution option. Publishing a plugin does not publish recommendations or create a hosted service. Real preferences, ratings, history, sessions, evidence assets, private logs and backups stay out of all public artifacts. Future MCP or Claude support requires its own adapter and acceptance checks; it is not implied by the current compatibility marketplace file.

The October 26 exploration extension is implemented before Phase 4: onboarded runs use a growing domain map and bounded AI planning, while the curated path remains explicit. See [hierarchical exploration verification](validation/EXPLORATION_VALIDATION.md). This extension does not enable Bayesian feedback fitting or scheduling.

Current output target is 10 YouTube and 10 Bilibili recommendations. Previously recommended videos keep guaranteed inspection slots until marked watched or dismissed and can reappear in the final lists. With 50 pending on one platform, fresh admissions wait for queue space. Feedback and watched state are separate; unwatched is not negative feedback. See PLAN.md for the funnel and capacity rules.

## Reference workspace status — October 27, 2026


**Phases 1–4 of the usable demo are complete; Phase 5 has not started.** The existing binary interest/quality development holdout passes after the audit. This limited check does not prove superiority, and new structured heads remain empirically provisional. The reference workspace retains twenty historical recommendations and a thirty-video, two-platform calibration cohort. The user's completed review is preserved. The October 27 extension separates specific-topic preference, broad-subject preference, depth fit and three-level quality, with independent channel coverage and legitimate abstentions. Legacy thumbs and comments remain available for clarification. Simulations do not establish personal calibration, and a new response type needs its own held-out coverage. Scheduling remains Phase 5. See [the Phase 4 audit](validation/PHASE4_AUDIT.md), [scoped-feedback verification](validation/SCOPED_FEEDBACK.md), [the mathematical/scoring contract](SCORING.md) and [the implementation plan](PLAN.md).

YouTube uses curated search and channel video tabs. Bilibili adds isolated-session public Home. Bilibili creator expansion uses public search with returned owner-ID verification because direct archive access was blocked; it is explicitly a partial index view.

Random video/user IDs and ChinaOpen/YouNiverse are outside the active path. Historical artifacts cannot pass the current gate. A fresh installation must establish its own three successful rounds per platform.
