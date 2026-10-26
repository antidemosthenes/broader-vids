# Phase 3 manual demo — October 25, 2026

Current verification is recorded in [the project audit](../AUDIT.md). The following phase measurements describe the original alpha.5 demo. The audited alpha.6 implementation preserves its twenty selections and evidence while using the reorganized active pipeline and regression suite.


The manual workflow is implemented and verified in alpha.5. `deepvids gate` reports Phases 1–3 complete in the reference workspace, with Phase 4 next; a fresh installation still starts at discovery. Bayesian posterior fitting and scheduling have not started.

## Implemented workflow

Explicit onboarding interests drive familiar/unfamiliar query-tag matching. Fresh curated discovery returned 555 YouTube and 782 Bilibili candidates from query bank version 2. Each demo screens a fair 100-candidate buffer into 50 inspection candidates, then uses verified quality evidence and 60/25/5/5/5 scoring for up to ten picks. A conditional minimum of two unfamiliar picks and creator-diversity scoring apply at final selection. These are provisional, evidence-limited quality estimates, not calibrated predictions of satisfaction.

Count snapshots have source receipts, observation time and video/archive scope. YouTube exposed exact like counts for 92 of the 100 observed candidates; missing metrics stayed neutral. The anonymous Bilibili count API returned HTTP 412 and that route stopped. The explicitly authorized isolated session then returned views/likes/coins/favorites for all 100 candidates. No cookies were printed, exported to another service or included in model prompts. Anonymous discovery remains separate from account access.

Each rate is shrunk toward a platform baseline with 1,000 prior views. The three normalized Bilibili rates have equal weights. Screening uses engagement and title-anchor support within protected query-family/familiarity lanes; fresh candidates compete for places left by guaranteed pending entries. Content-quality assessments use the independent Phase 2 rubric and do not include the engagement bonus again.

The initial anonymous-screened Bilibili job was stopped and retained as a paused research run before publication. Its completed evidence is reused when eligible. The replacement job screens with authenticated count snapshots; the two jobs are not mislabeled as separate successful recommendation rounds.

## State and controls

The SQLite schema stores onboarding revisions, watched/dismissed flags, immutable action history, recommendation exposures and metadata snapshots. Votes are editable without marking watched or dismissed. Missing feedback and unwatched status never become negative labels. Bayesian updates are not active yet.

Unwatched recommendations receive guaranteed next-run inspection slots, reuse valid quality evidence and can be recommended again. At 50 pending, no new inspection places are admitted. Restoring an overflow preserves records and requires queue resolution before publication. Watched/dismissed videos are excluded at screening and checked again during publication. A write transaction protects exposure/state commits while manual actions remain available during evidence collection.

`serve` binds only to loopback and provides video links, scores, watched/dismiss/like/dislike controls and access to older pending entries. Actions require a same-origin local token; unknown hosts/origins are rejected. JSON and Markdown digests persist outside the package and can be regenerated from committed state.

## Verification

- 70 Python tests and three Node launcher tests passed, covering shrinkage, count identity/scope, screening balance, pending guarantees, independent/revised feedback, cache reuse, capacity, concurrent actions, transaction rollback, request guards and migration preservation.
- A headless Edge browser test used a temporary two-platform fixture to click watched, vote and dismissal controls, reload and verify persistence. It checked mobile width and reported no JavaScript errors. No synthetic feedback was added to the real user profile.
- The skill validator passed. The alpha.5 package passed fresh core-only installation with optional dependencies confirmed absent, explicit hash-checked evidence/ASR installation from the local cache, profile/gate commands and dashboard-resource loading from an unrelated working directory with spaces. Execution preserved package file hashes. All 70 Python tests also passed against the installed wheel on Python 3.13, in addition to the source environment on Python 3.14.
- A private SQLite copy of the completed live demo was used to test both platforms: mark one watched, dismiss another, vote on a third, and build the next 50. Each retained all eight remaining pending entries and their evidence references, suppressed the watched/dismissed pair and preserved the independent vote. Older update dates did not expire pending entries. These validation actions did not modify production feedback.

The browser fixture verifies controls, not live recommendation quality, discovery coverage or Phase 3 readiness. The local gate requires complete 50-candidate/ten-pick runs with verified selected evidence on both platforms. An explicitly published assessed-subset digest remains partial and cannot pass the gate. Unavailable or incomplete evidence stays explicit; no substitute score is generated.

## Completed live results

| Platform | Fresh buffer | Inspection list | Quality assessed | Evidence unavailable | Below quality threshold | Final picks | Unfamiliar picks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 100 | 50 | 44 | 6 | 1 | 10 | 2 |
| Bilibili | 100 | 50 | 30 | 20 | 8 | 10 | 2 |

Both inspection lists contained 25 familiar and 25 unfamiliar candidates. All 50 were processed on each platform; unavailable evidence was explicitly recorded rather than assigned a low score. All twenty selected videos have verified, network-resolved playable media, usable captions/ASR, valid augmented scorecards and durations within 90 minutes. The saved selected evidence passes the current hash, identity and rubric checks. Five additional frame spot checks across both platforms supported the recorded visual observations; they did not establish authorship or full-video pacing.

Completed runs are `2c4ac4885350429fb1f43c9ffdb3e716` (YouTube) and `71435f7a40414f19ba557882b70fdb4b` (Bilibili). Their private artifacts are `.localdata/recommendations/RUN_ID/digest.md` and `report.json`; `.localdata/recommendations/demo.md` combines both lists, and `demo-summary.json` records the measurements. The dashboard presents the same committed selections with independent watched/feedback controls. First-publication pending counts were ten per platform.

| Platform | Active job minutes | Recorded new media bytes | New completed model calls | Submitted images | Reported input tokens | Reported output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 33.33 | 1,599,698,166 | 43 | 172 | 907,418 | 47,000 |
| Bilibili | 43.97 | 454,808,393 | 26 | 104 | 615,712 | 25,755 |

The two jobs overlapped. The successful jobs used 69 new completed Codex calls and 276 images; cached scorecards were reused. Input counters include Codex CLI/system overhead and are not isolated image charges. No API-key model billing was used. The roughly 1.91 GiB recorded media total excludes metadata/browser traffic, reused downloads/model weights and the earlier stopped anonymous-screened Bilibili trial. That earlier trial retains its own counters. Development-chat work and the five audit image views are outside these CLI counters.

This first uncached 50-per-platform pass is resource intensive. The demo establishes working manual selection, access, caching and feedback state; it does not establish calibrated quality, satisfaction or inexpensive daily cold-start operation. Missing/unclear speech evidence and media access caused substantially more unassessed Bilibili cases. Feedback remains stored for Phase 4, not used for automatic preference fitting yet.

## Run the demo

Use the same external data directory for all commands:

```powershell
uv run deepvids --data-dir .localdata serve
uv run deepvids --data-dir .localdata gate
uv run deepvids --data-dir .localdata profile show
uv run deepvids --data-dir .localdata queue
```

The dashboard URL is printed by `serve` (default `http://127.0.0.1:8765`). Start subsequent rounds with `recommend youtube` and `recommend bilibili --use-browser`, optionally adding `--asr-model base`. Current defaults are 60 minutes, 160 metadata requests, 50 new assessment calls and 1 GiB media per job. Set a suitable explicit media limit for a first uncached run; preparation of all 50 may exceed that default. Fresh discovery has a separately recorded bounded request budget inside the same time limit. `continue RUN_ID` preserves budgets. `report RUN_ID` regenerates artifacts. `publish RUN_ID --allow-incomplete` explicitly publishes only the assessed subset as partial.
# Score transparency follow-up — October 25, 2026

The local dashboard now reads saved scorecards to display the holistic content score out of 95, length bonus out of five, supporting criterion grades/limitations and final weighted component contributions. No scores, recommendations or feedback were changed, and no new assessment calls were made. The criterion grades have no separate numerical weights.

All twenty selected videos reconciled numerically. Fourteen recommendation tests and JavaScript syntax checking passed. Read-only headless Edge verification confirmed twenty cards, score details, preserved expansion after refresh, mobile fit and no JavaScript errors. Alpha.5 wheel/tarball contents were rebuilt and checked against current source; the archive contains nineteen intended entries and no private state.

