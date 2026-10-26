# Architecture

Deepvids has one Python engine, a thin npm launcher and a Codex skill. The CLI and optional local dashboard use the same SQLite state and application services. Phase 4 learning and Phase 5 scheduling are not implemented.

```text
bin/deepvids.cjs             npm setup and Python process launcher
skills/deepvids/SKILL.md     conversation and presentation instructions
src/deepvids/
  cli/                      arguments and command dispatch
  discovery/                curated queries, platform sources, collection, provenance
  exploration/              domain graph, weighted selection, bounded AI proposals and search activation
  evidence/                 bounded preparation process, media/captions, ASR, assessment and reports
  recommendations/          metadata provenance, screening/ranking, shared worker pool, batches and digests
  web/                      loopback server and separate HTML/CSS/JavaScript assets
  data/queries.json         versioned query bank; byte identity is part of provenance
  preferences.py            explicit interests, watch/dismiss state, feedback revisions
  identity.py               canonical platform/video/part identities
  policy.py                 fixed ranking defaults and duration rules
  parsing.py                safe extraction of platform JSON objects
  http.py                   request/time bounds, endpoint scope and request receipts
  processes.py              bounded model/ASR workers and owned-tree cleanup
  windows_job.py            native Windows job ownership and startup gate
  owned_process.py          bootstrap that waits for Windows ownership before execution
  storage.py                SQLite persistence, migrations, job leases
  readiness.py              phase completion from verified local evidence
  maintenance.py            dry-run and applied evidence compaction
tests/                      offline behavior and regression tests
scripts/                    reproducible distribution verification
docs/
  PLAN.md, PHASES.md         current design and milestones
  ARCHITECTURE.md, AUDIT.md  implementation map and review findings
  validation/               measured phase results
  research/                 historical findings behind rejected approaches
python/                     generated wheels and committed optional dependency locks
```

## Request flow

```mermaid
flowchart LR
  A[Codex skill or CLI] --> B[Hierarchical or curated discovery]
  B --> C[About 100 fresh candidates]
  C --> D[Community response and topic screening]
  P[Guaranteed pending recommendations] --> E[50 inspection slots per platform]
  D --> E
  E --> F[Verified captions and bounded frames]
  F --> G[Quality eligibility and full ranking]
  G --> H[10 YouTube and 10 Bilibili picks]
  H --> I[Digest and optional local dashboard]
  I --> J[Independent watch, dismissal and feedback actions]
  J --> P
```

Discovery sources supply metadata; they do not decide content quality. Evidence services resolve the playable unit, enforce the 90-minute exclusion, collect timestamped inputs and ask the ChatGPT-authenticated Codex CLI for a structured assessment. Recommendation services own screening, budgets, pending-slot reservations and publication. The web layer renders application state and submits validated actions; it has no ranking algorithm of its own.

`recommendations/ranking.py` contains selection policy. `quality.py` validates cached evidence and derives quality, including the separate duration prior. `service.py` orchestrates checkpointed jobs and atomic publication. `reports.py` renders digests and checks recommendation readiness. Evidence preparation and pilot reporting have corresponding separate modules.

The content score remains a provisional holistic judgment using substance, directness, original contribution and visuals. Quality adds at most five duration points. Final ranking weights remain 60/25/5/5/5. No preference fitting occurs when feedback is stored.

## Persistence and concurrency

Private state lives outside the installed package, selected by `--data-dir`, `DEEPVIDS_DATA_DIR`, or the platform default. Store roots are resolved to absolute paths. Media, captions, frames and model responses stay in that private directory; they are not included in GitHub or npm artifacts.

`evidence/retention.py` releases full videos after validated augmented assessment, retaining caption/frame hashes, scorecards and a source-bound release receipt. Unfinished and baseline-only jobs retain media for resumption. Cached judgments and metadata reviews reuse the compact bundle; no implicit redownload occurs. Cleanup uses the same evidence token lease, records intent before unlinking and rejects path escapes or links. `cleanup` previews older disposable media; `cleanup --apply` applies that policy. See [storage and memory verification](validation/STORAGE_MEMORY_AUDIT.md).

Schema 7 retains the exploration graph and existing application tables, adding indexed run-kind/platform, evidence-cache, playable-identity and pending-state lookups. Upgrades from schemas 1–6 create `deepvids.before-v7.sqlite3` first and preserve existing rows, historical tables and earlier backups. A current-schema connection performs no schema-writing transaction.

Hierarchical discovery stores selected concepts, random seed, profile revision, generated bilingual queries, provisional additions and model-attempt receipts in the discovery checkpoint. `exploration/domains.py` manages graph records and weighted selection; `proposals.py` defines and validates the AI response; `planning.py` coordinates bounded generation and receipt-backed activation. The existing authenticated Codex JSON runner is reused without images or API-key fallback. Generation finishes before any source requests, and the anonymous discovery transport stays separate from evidence access. Search receipts and title-anchor matches activate provisional nodes without modifying preferences. Multi-parent descendants inherit the union of ancestor interest domains.

Platform and job leases serialize recommendation runs and profile edits. User watch/dismiss/feedback actions use a short SQLite write transaction and remain independent of those long-running leases. Publication validates media before acquiring its database write lock, then rechecks current profile and queue membership and atomically saves the slate, pending entries and exposures. A failed publication cannot leak rolled-back draft state.

An evidence token lease spans preparation and assessment. A delegated worker verifies the token without releasing its parent's lease. Preparation receipts update only their own JSON fields in a short transaction. Model attempts and byte receipts carry dispatch tokens, so reconciliation charges only owned work. Cached preparation reserves no new bytes, and an existing unknown judgment needs no additional model call.

The pending queue never expires entries silently. At fifty pending videos per platform, new admissions wait for space. Watch, dismissal and votes are independent and reversible. Revisions preserve action history. Feedback lookup uses one indexed query rather than a query per video.

## Recovery and resource limits

Every source request and assessment attempt consumes its recorded allowance. Continue retains the original budgets. Parent recommendation jobs pass their remaining time and media allowance into reused child jobs; a child cannot consume an older, larger allowance. Preparation retains its ASR choice across interruptions.

Model/ASR processes use file-backed input/output to avoid inherited-pipe waits, with bounded log reads. Timeout/interruption terminates only the invocation's owned process tree. A local executor must permit that cleanup; restricted process permissions are reported as a failure.

Optional ASR decodes at most ten minutes into a temporary PCM file and loads one bounded audio buffer at a time; process exit releases its model. Job iteration filters current inspection identities and retains IDs instead of complete historical state documents. Download clients close their network resources, atomic JSON writes clean partial files, and running-worker log limits are checked during execution.

On Windows, a gated bootstrap starts commands only after assignment to a native kill-on-close Job Object. Cleanup removes remaining descendants even after a successful launcher exit, and parent death closes the job handle. POSIX workers use isolated process groups. Preparation uses the same ownership mechanism with an in-memory input pipe. Cancellation is checked after slot acquisition, before additional HTTP requests and before publication.

Batch creation is atomic, and platform children record their parent batch ID. Continue the batch ID to retain shared concurrency and time limits; a child cannot run independently. `recommendations/timing.py` checkpoints active attempts and conservatively recovers unobserved elapsed time after a crash. Normal continuation keeps the configured allowance unchanged and applies shutdown headroom only to the current invocation. See [the concurrency audit](validation/CONCURRENCY_AUDIT.md) for verification and recovery limits.

Retryable access, extraction and assessment failures leave the current candidate pending and preserve its cursor. Content that cannot be assessed is reported explicitly; it does not receive a fabricated low score. Previous model output files are cleared before a new attempt, so an incomplete response cannot reuse stale output. Cached original scorecards can be reused only when retired fields were unassessed; current views expose the active rubric.

## Distribution and development

The npm launcher installs the bundled wheel into a versioned external UV environment. Evidence/ASR dependencies are explicit, pinned and hash checked. Core imports remain dependency-free. The runtime exposes only current discovery, evidence and recommendation commands; rejected sampling and catalog implementations have been removed.

Ruff and Prettier are development tools. Unit tests use temporary state and mocked platform/model responses, with real FFmpeg extraction where installed. They do not consume a model subscription or sign into a platform. GitHub Actions runs the offline tests and distribution checks; live platform access and model-output quality remain separate validation activities.

The batch coordinator overlaps the two platform services under one 30-minute allowance. The evidence scheduler reserves model and byte allowances before dispatch, uses two preparation slots and up to four shared GPT-6.1 Sol sessions, and retains out-of-order results at retryable gaps. Preparation subprocesses receive scoped session cookies through memory pipes; model inputs contain only verified evidence and public provenance clues. Exact translation tags are screening exclusions, and metadata-based original-contribution reviews receive separately hashed context while reusing verified media assets. See [performance and provenance verification](validation/PERFORMANCE_VALIDATION.md).
