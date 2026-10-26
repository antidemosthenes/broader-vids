# Project audit — October 25, 2026

Follow-up: [the October 26 concurrency audit](validation/CONCURRENCY_AUDIT.md) documents alpha.9 fixes and current verification. The results below describe the earlier structural audit and alpha.6 release.

The later [storage and memory audit](validation/STORAGE_MEMORY_AUDIT.md) defines alpha.10 retention, bounded transcription and applied workspace cleanup.

Scope: the active Phase 1–3 implementation, repository contents eligible for GitHub, module boundaries, local dashboard, job recovery, persistence and local/npm packaging. Already-ignored caches, environments, media and prior build artifacts were excluded from the cleanup inventory. Phase 4 was not started.

October 26 clarification: the obsolete local handover was subsequently removed; its maintained results and constraints are in the validation documents, architecture and plan. The temporary root `AGENTS.md` was also removed at the user's request. Subagent authorization concerns the plugin's runtime AI and is recorded in its skill and runtime design, not as permission to delegate repository development.

## Repository and structure

- Removed rejected random-ID sampling, historical catalog import/sampling, experiment orchestration, convergence diagnostics and their obsolete commands/tests. Historical data in existing databases is preserved, and the research notes remain under `docs/research/`.
- Grouped the active pipeline into `cli`, `discovery`, `evidence`, `recommendations` and `web` packages. Extracted canonical identity and parsing utilities, reduced the policy module to the active policy, and separated collection/assessment from reports and selection from job orchestration.
- Split the dashboard into HTML, CSS and JavaScript, formatted Python and web sources, and removed unused imports and duplicate report implementations.
- Grouped measured reports under `docs/validation/`, updated internal links and removed retired commands from current instructions.
- Ignored the machine-specific handover, formatter cache, dependency-install directory and local environment files. Preserved existing ignored files. Optional requirement locks, UV/npm locks, manifests, tests and maintained design/validation documents remain eligible for GitHub.
- Prepared alpha.6 so the reorganized distribution uses a new external runtime instead of reusing an older installed version. See [the architecture](ARCHITECTURE.md).

## Confirmed defects and corrections

| Area | Previous behavior | Correction |
| --- | --- | --- |
| Child-job limits | A reused evidence job could retain a larger time/media allowance than its parent had left | Pass remaining invocation time and new-byte allowance through preparation, extraction and assessment; preserve original cumulative limits |
| Temporary failures | Temporary collection failure could advance the cursor and publish the candidate as permanently unavailable | Persist retryable failure state, pause the parent and retain the candidate/cursor |
| Transcription recovery | A paused standalone preparation forgot the selected ASR model | Persist that choice in the checkpoint and reuse it on continue |
| Model response freshness | A prior response file could satisfy an otherwise empty new response | Remove old output before invoking the model; reject missing fresh output |
| Worker lifetime | A timed-out launcher could leave its descendant worker running, and inherited pipes could delay cleanup | Run model/ASR workers with bounded file-backed input/output and terminate their owned process tree on timeout or interruption; report insufficient cleanup permissions explicitly |
| Publication contention | Media hash validation ran while holding the SQLite write lock | Validate immutable evidence first; retain atomic membership checks and publication inside the transaction |
| Database reads | Each new connection repeated schema-writing work; feedback retrieval made a query per video | Skip migration writes for the current schema; add lookup indexes and use a single feedback query |
| Feedback input | A malformed non-string feedback kind could raise an uncaught exception | Validate the type before membership checks and reject invalid actions cleanly |
| Feedback draft | Background refresh could replace an unsaved feedback category | Retain local category drafts and avoid replacing active controls during refresh |
| Local dashboard | Embedded assets required permissive inline-script/style policy | Serve explicit static assets with a same-origin content security policy |
| State paths | Relative roots could produce evidence receipts dependent on the later working directory | Resolve storage roots to absolute paths |
| Launcher options | Repeated `--data-dir` options could choose a runtime different from Python's final state argument | Match argparse's last-option precedence |
| Rebuilt local package | Reinstalling a wheel rebuilt at the same version/path could reuse stale UV cache contents | Install from a private content-addressed wheel path so changed bytes get a new cache identity, including offline setup; verify the installed modules after repacking |
| Quality interval | The reported quality interval omitted the duration bonus included in the quality score | Apply the same fixed duration contribution to both interval bounds |
| Unrecognized input | Malformed query-bank or count-response shapes could produce attribute/type errors | Validate object/list shapes and return controlled errors |
| Unchanged profile | Reapplying identical interests invalidated compatible checkpoints by increasing the revision | Make identical explicit onboarding updates idempotent |

Regression tests reproduce the failure behavior for temporary evidence, forgotten transcription settings and stale model output. Additional checks exercise migration preservation, concurrent database opening, publication validation outside the write lock, malformed input, quality intervals and option precedence.

## Verification

Local Python tests, Node launcher tests, Ruff, web formatting, skill validation and package verification are run after the refactor. Browser verification uses separate temporary state for action tests and read-only access for the real twenty-card demo. The existing discovery/query-bank provenance and cached selected evidence are reused; another expensive discovery/model pass is unnecessary for these structural changes.

The fresh core installation is checked from an unrelated directory with spaces. The wheel and tarball must include the current modules/assets and matching manifests, omit removed implementations, and contain no private state or machine-specific handover. `scripts/verify_package.py` makes these checks repeatable.

Final local results:

- **60 Python tests and five Node launcher tests passed**, with no skipped tests in the normal Windows executor. Tests for deleted research implementations were retired; active duration, HTTP and migration coverage was retained and recovery/concurrency regressions were added.
- Ruff checks/formatting, Prettier checks and the skill validator passed.
- Headless Edge verified the real twenty-card list, four quality criteria, mobile width, external assets/content security policy and no JavaScript errors. Isolated browser tests verified watch/vote/dismiss persistence and preservation of an unsaved feedback category across refresh.
- The before/after private snapshot confirmed identical selections, explicit interests and user action history. The original discovery-bank SHA still matches, schema 5 created its pre-upgrade backup, and the live Phase 3 gate still passes.
- Alpha.6 core installation passed from an unrelated directory containing spaces and a state path containing `&`, with evidence dependencies absent and package bytes unchanged. The installed CLI, profile/gate commands and static resources load correctly.
- Wheel/tarball verification checks source parity, documentation links, consistent versions and private-state exclusions. The local handover is ignored and omitted from the package.

The process-tree regression passed in the normal Windows executor. The restricted test sandbox denies tree termination; that environment is reported explicitly rather than treated as successful cleanup. GitHub Actions is configured for future runs and has not been executed remotely during this audit.

## Remaining product limits

The quality score is provisional; criterion weights and learned preferences remain Phase 4 design/calibration work. Discovery covers curated platform listings rather than all videos. Platform access can fail or change, and a small fixture suite does not prove future API compatibility or recommendation satisfaction. Opt-in scheduling, host activation and broader installation validation remain later milestones. The audit neither fits a user model nor publishes a package.
