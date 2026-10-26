# Performance and originality metadata — October 26, 2026

Alpha.8 adds bounded parallel execution, a combined two-platform command, lower-resolution acquisition and metadata provenance screening. Preference learning remains outside this change.

The subsequent [alpha.9 concurrency audit](CONCURRENCY_AUDIT.md) fixes ownership, cancellation, accounting and recovery defects in this implementation. Measurements below retain their original alpha.8 scope.

## Execution policy

`recommend both --use-browser --workers 4 --effort low` starts both platform pipelines under one cumulative 1,800-second allowance. Both share two preparation slots and four model slots, including any overlapping query-planning calls. Independent ChatGPT-authenticated Codex CLI sessions use `gpt-6.1-sol`, with low reasoning by default and medium available explicitly. Official model/settings references: [Codex models](https://learn.chatgpt.com/docs/models?surface=web), [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Metadata screening remains deterministic. The approved hierarchical query-planning stage may use up to two AI calls per platform. Default per-platform allowances are 160 metadata requests, 1 GiB new media, and 52 total model calls: at most two planning plus fifty detailed assessments. The combined run therefore allows at most 2 GiB new media and 104 total calls, with shared concurrency and time limits. Lower explicit budgets are respected; dispatch reserves allowances before launching work. Interrupted preparations without a final receipt charge their reserved byte allowance conservatively and label the counter as an upper bound.

Media preparation uses owned child processes that can be terminated at deadline or cancellation. Session cookies travel only through an in-memory OS pipe, outside command arguments, logs, model prompts and temporary credential files. Model processes have bounded input/output, cancellation and owned-tree cleanup. Shutdown headroom is reserved before the deadline. Completed out-of-order work remains saved when another candidate needs retry; continuation does not reset allowances. Full pending queues still skip fresh discovery.

New downloads prefer small 240–480p streams and permit fallback only within a 720-pixel short edge, supporting portrait orientation. There is no unrestricted high-resolution fallback. A format that cannot match this policy is unavailable for the current comparison rather than a fabricated low-quality score; other candidates may proceed. Already verified larger media can be reused without downloading it again. Preparation uses at most two FFmpeg decode threads and two ASR threads per active preparation. Optional ASR remains caption recovery, not voice-origin detection.

## Measured model overlap

Two actual augmented reviews ran concurrently using isolated hard-linked copies of already verified YouTube evidence. Each submitted four frames. No new media was downloaded and no production scorecard or recommendation changed.

| Measurement | Observed result |
| --- | ---: |
| Model | GPT-6.1 Sol, low reasoning |
| First assessment time | 34.104 s |
| Second assessment time | 31.541 s |
| Sum of individual assessment times | 65.645 s |
| Concurrent wall time including local verification | 34.246 s |
| Observed overlap benefit | About 1.92× for these two reviews |
| Reported input tokens | 42,142 |
| Reported output tokens | 2,053 |
| Submitted images | 8 |
| New downloaded media | 0 bytes |

The two model attempts started about 18 ms apart and both returned valid evidence-cited scorecards. The initial benchmark fixture had a prepared state with a pending status, performed zero model calls, and was corrected before the recorded measurement. Runtime recovery now checks prepared state, and this unsuccessful setup is not counted as successful model work. Private measurements live under `.cache/parallel-model-benchmark/`.

This is a two-worker cached-evidence benchmark, not proof that every cold hundred-video inspection finishes within thirty minutes. Download speed, unavailable captions, optional transcription, subscription throttling and platform access affect completion yield. The time allowance checkpoints unfinished work; it does not certify twenty completed recommendations. The twenty historical Bilibili evidence failures were not retried.

## Dashboard and storage

Schema 7 adds indexed lookup for run kind/platform, evidence cache keys, playable identities and pending video state. The dashboard retrieves only the current platform runs and visible/pending video states rather than decoding every discovery/evidence job. It reuses a single feedback lookup, and the browser skips unchanged responses and rejects out-of-order refreshes.

On the current reference database, the old full-history decoding step alone had a median cost of **44.611 ms** over seven repetitions. The optimized complete dashboard payload took **7.776 ms** median over seven repetitions. These are different scopes: the first measures the eliminated history-decoding step, not the complete old HTTP handler. Both measurements exclude network and browser rendering.

Schema 6 → 7 migration created `deepvids.before-v7.sqlite3`. Exact before/after comparisons confirmed preservation of every pre-existing application row. Refreshing the two user-supplied examples then added metadata observations without changing profiles, feedback, watched/dismissed state, exposures or selections. All twenty historical recommendation cards remain available and the Phase 3 gate still passes.

## Verified provenance examples

The combined Bilibili detail endpoint supplied title, description, archive engagement counts, playable-part metadata and tags in one request per example using the existing isolated session.

| Example | Observed metadata | Policy outcome |
| --- | --- | --- |
| [Wildlife photography editing](https://www.bilibili.com/video/BV1VYEM6tE74) | Exact `视频翻译` tag | Excluded before fresh screening; historical pending entry remains visible but blocked from future selection |
| [Chinese-dubbed Roblox/AI video](https://www.bilibili.com/video/BV1VbnR6eEbf/) | `中配` title, YouDub tag/description disclosure, source `OfObWnrucRY` YouTube link, uploader repost declaration | Cited original-contribution evidence for detailed assessment; no automatic inference about its voice |

These facts establish disclosures, not proof of authorship, licensing or synthetic narration. Source links alone are not a hard exclusion. New substantive metadata gets a separate hashed assessment context. Existing verified assets are hard-linked, or copied when links are unavailable, into the review's own evidence directory. Metadata M... references are validated alongside caption/frame IDs. Historical numeric scorecards are not silently revised, and no AI-voice score or audio detector was added.

## Verification

**88 Python tests and five Node launcher tests passed**, with no skipped tests in the normal Windows executor. Ruff, formatting, the skill validator and wheel/tarball parity checks passed. A fresh offline core-only alpha.8 setup worked from an unrelated directory with spaces and an external state path containing `&`, with optional evidence dependencies absent and package files unchanged. Read-only headless Edge verified twenty cards, the exact-tag exclusion badge, DOM preservation on unchanged refresh, mobile fit and no JavaScript errors.

Offline regressions cover overlapping local/model stages and shared limits, bounded preparation processes, global batch deadlines, reservation recovery, conservative uncertain byte accounting, pending gaps, exact-tag exclusions, nonexclusive source-link signals, metadata evidence citations, asset reuse, low-resolution portrait selection and owned-tree cancellation. The launcher, web formatting, skill, wheel/tarball contents and fresh core-only installation are checked separately. See [the plan](../PLAN.md#performance-and-metadata-originality-policy) and [the architecture](../ARCHITECTURE.md).
