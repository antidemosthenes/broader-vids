# Deepvids

Independent YouTube and Bilibili recommendations, built in five verified phases.

**Current stage:** Phase 1 is incomplete and work stops at discovery. ChinaOpen and YouNiverse were rejected as dated; no suitable current replacement has been verified for both platforms. Phase 2 has not started. See [the reassessment](docs/DISCOVERY_REASSESSMENT.md). See [the phase roadmap](docs/PHASES.md) and [implementation plan](docs/PLAN.md).

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

