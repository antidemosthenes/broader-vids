# Hierarchical exploration — October 26, 2026

Alpha.7 implements the approved growing domain map before Phase 4 learning. The reference twenty recommendations and their evidence are preserved. Recovery of the twenty historical Bilibili evidence failures remains deferred.

## Offline verification

Thirteen exploration regressions cover cold-start shortfalls, activation from successful search receipts and anchor matches, profile independence, checkpoint continuation without regeneration, bounded failed generation, alias rejection, refinement/multiple-parent inheritance, reproducible sampling without ancestor duplication, profile-change handling, shared parent model budgets and free reuse of finished planning. Migration tests cover schema 5 → 6 with preserved profiles and a pre-upgrade backup. The existing discovery, evidence, ranking, queue, feedback and package checks remain applicable.

The complete suite passed **75 Python tests and five Node launcher tests** in the normal Windows executor, with no skips. Ruff, web formatting and the skill validator passed. The live database upgraded to schema 6 with a pre-upgrade backup; an exact comparison of all pre-existing application tables confirmed preservation of runs, requests, registry entries, profiles, video state, feedback, exposures and count snapshots. Seven onboarding nodes were initialized separately. The Phase 3 gate still passes, and the restarted dashboard still presents twenty cards.

The alpha.7 wheel and npm archive passed source/asset parity, documentation-link, manifest-version and private-state checks. Fresh offline core-only setup passed from an unrelated directory with spaces and an external state path containing `&`. The installed `topics` command and a zero-planning-call discovery checkpoint worked without optional evidence dependencies or package-directory mutations.

## Live smoke checks

Checks used isolated private state under `.cache/exploration-live-a7`, with the reference interests copied into a fresh profile. They did not modify production recommendations, feedback or evidence, and downloaded no video media. Each round allowed one planning call, 35 HTTP attempts and 300 seconds. The existing ChatGPT-authenticated Codex CLI generated queries; platform retrieval remained anonymous.

| Platform | Existing concepts | New proposals | Observed candidates | Preview candidates | HTTP attempts | Active seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 7 | 5 | 405 | 50 | 21 | 51.62 |
| Bilibili | 12 | 5 | 702 | 50 | 25 | 74.07 |

Both runs completed all source-receipt and policy coverage checks. The first round started from seven onboarding concepts; its five additions became available to the second round, which selected twelve existing concepts. All ten additions received anchor-supported activation, producing seventeen active nodes and twenty-nine query observations. Neither generation nor activation changed profile interests.

Example new roots included animal behavior and historical linguistics on YouTube, then archaeology and horticulture on Bilibili. Queries were more specific than their labels; for example, the animal-behavior query sought experiments on crows recognizing human faces. These examples demonstrate retrieval, not verified semantic orthogonality or content quality.

Two generation calls reported 30,369 input tokens, including 8,960 cached input tokens, and 3,185 output tokens. They submitted no images and used no API-key fallback. Counters include Codex CLI overhead and do not imply a fixed future cost. Generation took 30.98 and 40.44 seconds respectively. Private prompts, validation outcomes and reported usage are saved with each discovery job.

Run IDs are `b8ebde8eb04d45db88ab1495a0276e4f` (YouTube) and `12d4a2cb547843eaa688d49bae89fe78` (Bilibili), in the isolated smoke-check database. Their successful results do not count as three production readiness rounds for the new policy.

## Limits

Activation requires an actual successful response and a title/description anchor match; it does not certify relevance or video quality. Novelty explanations are AI judgments, and alias normalization cannot prove semantic nonduplication. Unconfirmed topics are retained but excluded from active selection. Refinement is local and bounded; it is not an exhaustive subject taxonomy. A planning context exceeding 192 KiB pauses for review. The five-new target is independent of the existing unfamiliar-video quotas, and cold starts or exhausted proposal budgets can return fewer topics.

See [the policy](../PLAN.md#growing-domain-hierarchy--implemented-october-26), [the architecture](../ARCHITECTURE.md), and [usage examples](../../README.md#phase-1-curated-live-candidates).
