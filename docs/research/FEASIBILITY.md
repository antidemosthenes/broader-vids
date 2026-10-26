# Sampling feasibility findings

Archived research: these findings explain rejected discovery approaches. Commands and module references describe the historical prototype. See [the current architecture](../ARCHITECTURE.md) for the supported runtime.

Historical platform-wide sampling checks from October 24, 2026. These methods did not pass their original gate. The later historical catalog route was rejected because of source age; Phase 1 remains incomplete. See [the reassessment](DISCOVERY_REASSESSMENT.md) and [current roadmap](../PHASES.md). The observations below remain research evidence for the earlier methods.

## Live observations

| Check | Result |
| --- | --- |
| YouTube seed 20261024, 10 prefix queries | 11 HTTP attempts, zero matching IDs |
| YouTube seed 20261025, 100 prefix queries | 101 HTTP attempts, 244 returned search IDs, zero matching IDs, 78 pages with continuation markers |
| YouTube seed 20261026, 100 prefix queries | 101 HTTP attempts, 251 returned search IDs, zero matching IDs, 87 pages with continuation markers |
| YouTube known-ID search control | Requested public video was found; general identifier search was accessible |
| Bilibili public reference probes | Historical and 2025 multipart/part metadata were accessible |
| Bilibili four-chain experiment | 23 HTTP attempts, 30 transitions across four chains, paused on platform code `-403` before retained sampling |

YouTube's published prefix method produced no usable candidates in these checks. Search returned unrelated identifiers, and many response pages offered continuation. The current adapter records that truncation rather than treating the first page as exhaustive. Further investigation needs an independently verified working prefix control or a different discovery method.

Bilibili's `-403` was returned inside a successful HTTP response. Its meaning for the proposed ID was ambiguous, so the run preserved the pending proposal and paused. It was not accepted as evidence that the video did not exist. No automatic retry, account access or challenge workaround followed that response. An earlier initialization attempt also encountered an unavailable reference archive; the subsequent experiment used four verified starting archives spanning historical and 2025 identifiers.

No independent broad-catalog coverage evidence or transcript/frame access validation has been established. Mixing diagnostics cannot be evaluated from the interrupted warmup. These findings apply to the tested methods and access configuration.

## Reproducible artifacts

Local experiment state is in `.localdata/deepvids.sqlite3`. Each run has `report.json`, `report.md` and `trace.jsonl` under `.localdata/runs/`; raw results are ignored by Git.

- YouTube smoke: `5bcfc4e4403845e6b7b63025f9994a12`
- YouTube larger sessions: `dc188976b94543fb934df8737fe6cdb7`, `f47af4e4f2574561867efac2e82bf182`
- Known-ID control: `d036ee7b3da5463bb1e417d86a92755d`
- Bilibili interrupted chain: `92386bc421a7400ca2940a095d6c7947`

Use `deepvids --data-dir .localdata report RUN_ID` or `gate` to inspect the evidence. A continuation retains the remaining session budget, random state and unresolved proposal; retrying the paused platform response requires a deliberate new invocation.

## Implemented and verified

- Anonymous request/time bounds, redirect stops and three-state existence classification.
- Fixed prefix plans, multipart-weighted Metropolis transitions and retained self-loops.
- SQLite checkpoints, transactional trace/cursor updates, exclusive run leases and exact random-state resumption.
- Standalone reports, finite-domain stationarity checks and rank-normalized split/folded R-hat plus bulk ESS diagnostics.
- Duration eligibility: exactly 90 minutes is allowed; greater durations are excluded downstream, with a rising/plateau soft prior.
- 20 Python tests and three Node tests passed; skill frontmatter validated.
- Packed-artifact setup and repeated setup passed from an unrelated working directory with spaces in paths, using an external UV runtime.
- The installed CLI passed simulation and blocked recommendations; package file hashes were unchanged after execution.
- Actual Codex host activation and daily scheduling have not been exercised.

## Next milestone

Resolve discovery feasibility before building scoring, Bayesian posterior updates, media assessment, feedback learning or scheduling. Both platforms remain required for v1. The next discovery method must carry its own coverage argument and access validation; the current gate provides no override that relabels search or trending results as validated platform-wide random sampling.
