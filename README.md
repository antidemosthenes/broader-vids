# Deepvids

Independent YouTube and Bilibili recommendations, built in five verified phases.

The project implements curated discovery, bounded caption/frame quality assessment and the manual recommendation workflow for YouTube and Bilibili.

**Current stage:** Phases 1–3 are complete in the reference workspace. The live demo has 10 YouTube and 10 Bilibili picks, selected from 50 inspection candidates per platform. See [Phase 3 verification](docs/validation/PHASE3_VALIDATION.md), [the roadmap](docs/PHASES.md) and [the plan](docs/PLAN.md). `gate` checks local evidence; a fresh installation starts at Phase 1. Scorecards are provisional. Bayesian fitting and daily scheduling have not started.


The recommendation funnel collects fresh candidates in batches of approximately 100, screens cheaply into 50 inspection candidates per platform, then uses quality evidence and full scoring to select 10 YouTube and 10 Bilibili videos. Unwatched recommendations retain guaranteed places in later inspection lists until marked watched or dismissed, reusing cached assessments. Watched status and thumbs-up/down feedback are separate. At 50 pending on a platform, fresh admissions wait for queue space.

## Manual recommendation demo

Use one combined command for both platforms and a single 30-minute allowance:

```powershell
uv run --no-sync deepvids --data-dir .localdata recommend both --use-browser --workers 4 --effort low
```

Both platform pipelines overlap, with at most two media-preparation processes and four independent GPT-6.1 Sol sessions across the batch. `--effort medium` is supported. Downloads prefer a small 240–480p stream and fall back only within 720p on the short edge, including portrait video; a higher-resolution-only video remains pending. Query planning can use AI; metadata screening does not. Continuation preserves the original time, model and byte allowances. A deadline checkpoints unfinished work and does not guarantee twenty complete recommendations from an uncached hundred-video inspection workload. See [measured performance and limits](docs/validation/PERFORMANCE_VALIDATION.md).

Continue a combined run with its **batch ID**, rather than either platform child ID. Alpha.9 hardens worker ownership, cancellation, atomic receipts, resource accounting and interrupted-run recovery; see [the concurrency audit](docs/validation/CONCURRENCY_AUDIT.md).

Alpha.10 removes full videos after augmented assessment while retaining verified captions, frames and scorecards for reuse. Unfinished jobs keep their media. `cleanup` previews older disposable evidence; `cleanup --apply` removes it. Optional transcription processes ten-minute chunks instead of holding a full video's audio in memory. See [storage and memory verification](docs/validation/STORAGE_MEMORY_AUDIT.md).

Bilibili metadata now includes tags and description in the same request as counts. The exact `视频翻译` tag excludes a fresh candidate before the 50-video list and blocks a pending entry from final selection without evicting it. `中配`, YouDub, source YouTube links and uploader repost declarations are separately recorded originality clues. Source links alone are not proof of copying, and none of these rules detects synthetic speech. New originality reviews can reuse verified media/captions/frames with a separately hashed metadata context; historical numeric scores are preserved. The dashboard shows these disclosures and flags historical excluded entries.

Complete the discovery/evidence prerequisites reported by `gate`, and install the evidence extra (plus ASR if wanted). Set explicit interests once; feedback is stored with revisions and will support learning in Phase 4.

```powershell
uv --cache-dir .cache/uv sync --extra evidence --extra asr
uv run --no-sync deepvids --data-dir .localdata profile set --interest mathematics --interest travel --interest food
uv run --no-sync deepvids --data-dir .localdata recommend both --use-browser --asr-model base --workers 4 --effort low
uv run --no-sync deepvids --data-dir .localdata serve
```

Open `http://127.0.0.1:8765` for video links, scores and watched/dismiss/feedback controls. The server binds only to loopback. Every recommendation run also saves `recommendations/RUN_ID/digest.md` and `report.json` outside the package. `--discovery-run RUN_ID` explicitly reuses a saved platform discovery buffer; omit it for fresh discovery.

The dashboard uses Python's `ThreadingHTTPServer`; the npm command is a launcher for that same Python engine. To stop a foreground server, press **Ctrl+C** in its terminal. Closing a browser tab does not stop it. For a background server on Windows, inspect `Get-NetTCPConnection -LocalPort 8765 -State Listen` and the matching Python command line, then use `Stop-Process -Id SERVER_PID` with that process ID. An external launcher may also need to be stopped. Stopping the dashboard preserves saved recommendations and feedback; run `serve` again to reopen it.

Expand **Score details** on a card to see weighted final contributions, the content/length split, saved criterion grades and engagement ratios. Content quality uses substance, directness, original contribution and purposeful visuals in a holistic judgment out of 95, plus up to five length points. Criterion grades do not have separate numerical weights.

Screening uses smoothed likes/views on YouTube and likes/views, coins/views and favorites/views on Bilibili, with missing counts neutral. `--use-browser` enables the isolated Bilibili session for count enrichment and content evidence, without adding it to anonymous discovery. Content quality uses verified captions/frames. Topic fit initially uses inherited exploration or curated domain tags rather than a fitted preference model.

Defaults per platform are a shared 30-minute batch limit, 160 metadata requests, up to 52 total model calls (two planning plus fifty quality assessments) and 1 GiB media; fresh discovery has its separate bounded request budget and shares the job's elapsed-time limit. Use `--max-mib`, `--max-requests`, `--max-assessments` and `--max-seconds` to set explicit bounds. Valid cached evidence costs no new assessment call. Limits and failures checkpoint work; `continue RUN_ID` retains the original budgets. `publish RUN_ID --allow-incomplete` can explicitly publish an assessed subset as a **partial** digest; it does not pass Phase 3 completion. A new round may reuse cached evidence with a new explicit budget.

```powershell
uv run --no-sync deepvids --data-dir .localdata video watched "youtube:VIDEO_ID"
uv run --no-sync deepvids --data-dir .localdata video up "youtube:VIDEO_ID" --kind interest --reason "Useful explanation"
uv run --no-sync deepvids --data-dir .localdata video dismiss "bilibili:BVID:p1"
uv run --no-sync deepvids --data-dir .localdata queue
```

IDs above are placeholders; copy a canonical ID from a digest. `unwatched`, `restore` and `clear` reverse watch state, dismissal and vote independently. Votes can be revised. Clicking a link or leaving a recommendation unwatched never creates an implicit preference label.

The earlier platform-wide random-ID methods did not pass their feasibility checks. The historical catalog and random-ID implementations have been removed; see [the earlier findings](docs/research/FEASIBILITY.md) and [catalog research](docs/research/SAMPLING_DATABASES.md).

## Local development

Use Python 3.12+ through UV:

```powershell
uv sync
uv run --no-sync deepvids doctor
uv run --no-sync python -m unittest discover -s tests -v
```

If UV's default cache is unavailable, set `UV_CACHE_DIR` to a writable directory or use `uv --cache-dir .cache/uv ...`.

## Phase 1: curated live candidates

With an onboarding profile, new discovery and recommendation rounds now use a persistent domain hierarchy. Each round targets **12 existing concepts plus five new concepts**. Weighted random selection retains interest coverage, favors less-used topics and reduces immediate repetition. The existing ChatGPT-authenticated Codex CLI supplies concrete English/Chinese search queries and novel-topic proposals; local refinements inherit their parent domains. Exploration never adds an inferred like to your profile.

The first round can have fewer than twelve existing concepts: seven initial interests give seven existing topics plus up to five additions. Successful anonymous searches with title/description anchor matches activate additions for later rounds; unmatched results remain unconfirmed. Novelty and semantic relevance are provisional AI judgments. The existing 25/25 inspection target and conditional minimum of two unrelated final videos remain separate rules.

```powershell
uv run --no-sync deepvids --data-dir .localdata topics
uv run --no-sync deepvids --data-dir .localdata discover youtube --seed 42
uv run --no-sync deepvids --data-dir .localdata discover bilibili
# Use the original fixed bank explicitly:
uv run --no-sync deepvids --data-dir .localdata discover youtube --curated
```

`topics` inspects the saved map without generating proposals or changing interests. Plans and seeds are checkpointed once per new round; `continue` does not generate another five topics after a completed planning step. Discovery permits at most two planning calls, each capped at 120 seconds and the remaining job time; `--max-planning-calls` can lower that limit. In recommendation jobs, planning calls share the total `--max-assessments` model allowance with video assessment. Reports separate both kinds of call and their total. A supplied finished discovery buffer costs no new generation. With no profile, discovery retains the fixed bank for initial setup. See [the hierarchy policy](docs/PLAN.md#growing-domain-hierarchy--implemented-october-26).

```powershell
uv run --no-sync deepvids queries --platform youtube
uv run --no-sync deepvids queries --export my-queries.json
uv run --no-sync deepvids --data-dir .localdata discover youtube
uv run --no-sync deepvids --data-dir .localdata discover bilibili
uv run --no-sync deepvids --data-dir .localdata report RUN_ID
uv run --no-sync deepvids --data-dir .localdata gate
```

Run discovery three times per platform to establish local readiness. Fixed-bank runs rotate the curated bank; use `--bank my-queries.json` for an edited bank and `continue RUN_ID` to resume pending work within its original budget. Hierarchical runs retain their own policy identity and generated plans. Defaults are 100 requests and ten minutes per invocation. Reports save the plan, planning usage, a larger metadata buffer and 50 linked preview candidates under the chosen data directory. Selection gives query/topic families opportunities and caps each creator at three.

YouTube uses public keyword search and creator video tabs. Bilibili adds Home in an isolated temporary session. Its creator fallback keeps only public search results whose owner ID matches the discovered creator; this is not a complete archive. Direct Bilibili archive/API access was blocked in preliminary checks. Phase 1 never reads personal browser cookies or downloads media. Full Bilibili video access and per-part duration checks belong to Phase 2.

## Phase 2: bounded evidence trial

```powershell
uv sync --extra evidence
# Add --extra asr only for optional local transcription.
uv run --no-sync python -m deepvids --data-dir .localdata login bilibili
# Complete sign-in in the isolated browser, then confirm in another terminal:
uv run --no-sync python -m deepvids --data-dir .localdata login bilibili --confirm
uv run --no-sync python -m deepvids --data-dir .localdata evidence prepare "https://www.youtube.com/watch?v=VIDEO_ID"
uv run --no-sync python -m deepvids --data-dir .localdata evidence assess RUN_ID --mode both
uv run --no-sync python -m deepvids --data-dir .localdata evidence pilot-report
```

For Bilibili preparation, use a canonical URL with an explicit `?p=1` (or the requested part) and `--use-browser`. The session belongs to the isolated profile; no personal browser profile or exported cookie file is needed. If Edge/Chrome is absent, install Playwright Chromium explicitly with `uv run --no-sync playwright install chromium`.

Native captions are preferred, including inline SRT and `ai-zh` tracks. Use `evidence captions RUN_ID` to refresh native captions without redownloading media; assessed inputs remain unchanged. Missing speech can use `--asr-model base` after installing the `asr` extra. `--media FILE --captions FILE` supports explicit local input, with local provenance recorded separately. Local files do not substitute for network-verified reference-pilot readiness.

Four 768-pixel frames are extracted initially. To inspect an uncertainty before augmented assessment, use `evidence frame RUN_ID --at SECONDS --reason "unreadable diagram" --long-edge 1536`. The job cap is eight images and two model calls, with no automatic paid API fallback. Download/time limits, hashes, recovery and resource usage are retained with the job. The assessment CLI requires an existing `codex login` using ChatGPT. Originality judgments remain unknown when unsupported.

The npm launcher supports `setup --evidence` and `setup --asr`; these explicitly install the corresponding locked optional dependencies into its external runtime. Plain `setup` retains the dependency-free discovery core. Optional tools and model weights are not bundled in the plugin.

## Local and npm packaging

```powershell
uv build --out-dir python
npm pack --ignore-scripts
```

The tarball contains the wheel, launcher and Codex skill. To use a packed/local package:

```powershell
node bin/deepvids.cjs setup
node bin/deepvids.cjs doctor
```

After building the wheel, register the local source in Codex:

```powershell
codex plugin marketplace add C:\path\to\deepvids
codex plugin add deepvids@deepvids-local
```

The plugin marketplace manifest also travels with the npm package; its package directory can be registered as the local source. Host activation is a separate local setup step. The Claude Code runtime adapter remains a later milestone.

Setup requires UV and installs only the bundled wheel into an external versioned environment. An npm installation also exposes `deepvids setup`. No postinstall script or model API key is used. No package has been published to npm.


## Architecture and development checks

Read [the architecture](docs/ARCHITECTURE.md), [the audit](docs/AUDIT.md) and [the phase roadmap](docs/PHASES.md). Historical discovery research lives in `docs/research/`; its retired sampling/catalog commands are no longer shipped.

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
uv run --no-sync python -m unittest discover -s tests
npm ci --ignore-scripts
npm test
npm run check:web
```
