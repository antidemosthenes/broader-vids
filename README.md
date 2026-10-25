# Deepvids

Independent YouTube and Bilibili recommendations, built in five verified phases.

The `probe-phase-2` branch implements curated discovery and bounded caption/frame quality assessment for YouTube and Bilibili.

**Current stage:** Phases 1 and 2 are complete in the reference workspace: three discovery rounds per platform and six paired caption-only/four-frame assessments. Phase 3 has not started. See [the evidence trial](docs/PHASE2_VALIDATION.md), [the roadmap](docs/PHASES.md) and [the plan](docs/PLAN.md). Scorecards are provisional; recommendation lists, profiles, Bayesian learning and scheduling remain later work.

The planned recommendation funnel collects fresh candidates in batches of approximately 100, screens cheaply into 50 inspection candidates per platform, then uses quality evidence and full scoring to select 10 YouTube and 10 Bilibili videos. Unwatched recommendations retain guaranteed places in later inspection lists until marked watched or dismissed, reusing cached assessments. Watched status and thumbs-up/down feedback are separate. At 50 pending on a platform, fresh admissions wait for queue space. These Phase 3 behaviors are planned, not implemented.

The earlier platform-wide random-ID methods did not pass their feasibility checks. Historical catalogs remain test fixtures and cannot satisfy discovery readiness; see [the earlier findings](docs/FEASIBILITY.md) and [catalog research](docs/SAMPLING_DATABASES.md).

## Local development

Use Python 3.12+ through UV:

```powershell
uv sync
uv run deepvids doctor
uv run deepvids simulate
uv run --no-sync python -m unittest discover -s tests -v
```

If UV's default cache is unavailable, set `UV_CACHE_DIR` to a writable directory or use `uv --cache-dir .cache/uv ...`.

## Phase 1: curated live candidates

```powershell
uv run deepvids queries --platform youtube
uv run deepvids queries --export my-queries.json
uv run deepvids --data-dir .localdata discover youtube
uv run deepvids --data-dir .localdata discover bilibili
uv run deepvids --data-dir .localdata report RUN_ID
uv run deepvids --data-dir .localdata gate
```

Run discovery three times per platform to establish local readiness. New runs rotate the curated bank; use `--bank my-queries.json` for an edited bank and `continue RUN_ID` to resume pending work within its original budget. Defaults are 100 requests and ten minutes per invocation. Reports save a larger metadata buffer and 50 linked preview candidates under the chosen data directory. Selection gives narrow/interdisciplinary query families opportunities and caps each creator at three.

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

Four 768-pixel frames are extracted initially. To inspect an uncertainty before augmented assessment, use `evidence frame RUN_ID --at SECONDS --reason "unreadable diagram" --long-edge 1536`. The job cap is eight images and two model calls, with no automatic paid API fallback. Download/time limits, hashes, recovery and resource usage are retained with the job. The assessment CLI requires an existing `codex login` using ChatGPT. Optional narration/originality judgments remain unknown when unsupported.

The npm launcher supports `setup --evidence` and `setup --asr`; these explicitly install the corresponding locked optional dependencies into its external runtime. Plain `setup` retains the dependency-free discovery core. Optional tools and model weights are not bundled in the plugin.

## Historical catalog fixtures

These commands test import and sampling mechanics only; the sources are rejected for recommendation discovery.

```powershell
uv run deepvids --data-dir .localdata catalog fetch chinaopen
uv run deepvids --data-dir .localdata catalog fetch youniverse --max-download-mib 8 --preview-rows 2000
uv run deepvids --data-dir .localdata catalog list
uv run deepvids --data-dir .localdata catalog sample CATALOG_ID --count 100 --seed 20261024
uv run deepvids --data-dir .localdata gate
```

The ChinaOpen fetch imports the complete URL-list response. YouNiverse fetches at most 8 MiB and imports the first 2,000 metadata rows as a development preview. Sampling is uniform without replacement over distinct links in the imported catalog; the preview is not a representative sample of all YouNiverse or YouTube. Source hashes, scope and selection limits are saved with each catalog and draw. The draw artifact contains the actual candidate URLs; `--show-items` also prints them.

Local imports accept ChinaOpen TSV or YouNiverse gzip JSONL files:

```powershell
uv run deepvids --data-dir .localdata catalog import youniverse C:\data\yt_metadata_en.jsonl.gz --scope source_snapshot
```

A complete YouNiverse snapshot must match its published file checksum. Imports stream records and publish atomically; malformed compressed files leave no usable partial catalog. Large full-source import performance has not been benchmarked. Each import has a one-hour processing budget. Catalog draws are raw candidates; availability, duration exclusions and quality inspection happen in later phases. ChinaOpen links default to the first part unless their URL specifies a part.

## Earlier sampling experiments

```powershell
uv run deepvids --data-dir .localdata experiment youtube --seed 20261024 --queries 10 --max-requests 30 --max-seconds 120
uv run deepvids --data-dir .localdata probe bilibili --aid 170001
uv run deepvids --data-dir .localdata search-control --video-id dQw4w9WgXcQ
uv run deepvids --data-dir .localdata experiment bilibili --seed-aid 170001 --seed-aid 205634870 --seed-aid 4550175 --seed-aid 114868162141203 --seed-provenance "Public reference IDs; dispersed coverage not established"
uv run deepvids --data-dir .localdata status
uv run deepvids --data-dir .localdata continue RUN_ID
uv run deepvids --data-dir .localdata report RUN_ID
uv run deepvids --data-dir .localdata gate
```

Example starting archives are public reference IDs, not evidence of a representative initialization; supply verified dispersed IDs and provenance for a substantive Bilibili trial. Small smoke tests do not count as full feasibility sessions.

Network work is anonymous and bounded. Access challenges, throttling and ambiguous failures pause a run. Continue retains its random state and pending query/proposal. Sampling does not inspect cookies, download media, call a model, or personalize discovery. Reports contain provisional metadata populations and known coverage limitations, never a claim of exact global uniformity.

Reports and SQLite state live under the chosen data directory. Without an override, the default is `%LOCALAPPDATA%/deepvids` on Windows. Keep experimental results out of source control.

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

The catalog also travels with the npm package; its package directory can be registered as the local source. Host activation is a separate local setup step. The Claude Code runtime adapter remains a later milestone.

Setup requires UV and installs only the bundled wheel into an external versioned environment. An npm installation also exposes `deepvids setup`. No postinstall script or model API key is used. No package has been published to npm.

## Sampling limitations

Historical catalogs inherit their collection dates, creator/language selection and other omissions. Exact local sampling does not certify platform-wide coverage or playback availability. The earlier YouTube prefix and Bilibili ID-space experiments remain available for research, with their limitations preserved. The `gate` command reports catalog readiness and the remaining phases; completing Phase 1 cannot unlock recommendations.

