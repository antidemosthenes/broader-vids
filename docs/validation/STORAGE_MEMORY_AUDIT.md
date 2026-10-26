# Storage and memory audit — October 26, 2026

Alpha.10 adds a defined media lifecycle, safe cleanup of completed evidence and bounded optional transcription. Ranking, user preferences and Phase 4 scope are unchanged.

## Retention policy

| Resource | Lifetime |
| --- | --- |
| Full video and download intermediates | Remove automatically after a validated augmented assessment; `cleanup --apply` compacts older completed evidence |
| Unfinished preparation and baseline-only full video | Keep for resumption, caption recovery and targeted frames before augmented assessment |
| Verified captions, selected frames, scorecards and provenance hashes | Keep as compact evidence for cached scoring, pending recommendations and metadata-context reviews |
| Profile, actions, exposures, exploration graph and budget receipts | Keep as durable application state and future learning inputs |
| ASR model weights and dependencies in current use | Keep for subsequent offline runs; each ASR worker exits after its task |
| Authenticated isolated browser profile | Keep for user-controlled sign-in; cleanup does not delete credentials or regular browser data |
| Temporary JSON, PCM and worker-log files | Close/delete after success or failure; named JSON writes remove partial temporary files |
| Browser feedback drafts | Keep while the video remains visible; remove entries for videos no longer in the displayed state |

The full video is a processing input, not an input sent to the assessment model. After augmented assessment, its captions and bounded frames are sufficient to reuse the saved judgment. Cleanup retains the source hash, byte count and duration, together with a release receipt bound to that descriptor. It continues to verify retained asset hashes and scorecard identities. **It cannot rehash or replay a deleted source video.** Missing media without a matching release receipt remains an error. Additional frames after release require a new explicit local-media job; no silent redownload or budget reset occurs. Clients that use compacted evidence need alpha.10 or later.

Preview and apply with the same state directory as the recommendation engine:

```powershell
uv run --no-sync deepvids --data-dir .localdata cleanup
uv run --no-sync deepvids --data-dir .localdata cleanup --apply
```

Cleanup holds the evidence lease, validates the retained bundle and assessment, and checks each deletion against its own evidence directory. It refuses symbolic links, junctions and paths outside that directory, and never deletes the original supplied local file. Active jobs are reported as busy. A persisted `release_pending` receipt allows recovery if the process stops between unlinking and its final database save. A failed cleanup preserves the saved assessment and reports the problem; running cleanup again retries it. A completed cleanup is idempotent.

Unfinished jobs are not expired automatically. Their files can still support resumption, including the twenty deferred Bilibili evidence failures. Disposable development installations were removed separately from runtime evidence; the CLI does not recursively purge arbitrary workspace caches.

## Memory and temporary-file corrections

Previously, optional ASR captured an entire decoded video in a Python byte string before checking its length: up to **345.6 MB of PCM per worker**, before model/features memory. Transcription now decodes consecutive chunks of at most ten minutes to an automatically closed temporary file, validates the sample count, and loads one NumPy buffer of at most approximately **38.4 MB**, with a small decoder allowance. Each buffer and segment iterator is released before the next chunk. Global cue timestamps are preserved; chunk boundaries can affect transcription continuity. Model and feature extraction allocations are additional, so this is not a hard whole-process RSS cap. The existing limit of two preparation processes still applies.

Recommendation cache lookup now streams only evidence matching the current inspection IDs, retaining job IDs rather than whole historical state documents. Gate/report iteration also avoids decoding all historical job states at once. Download clients explicitly close their HTTP resources. Hashing already streams one-MiB blocks, and metadata/caption responses retain their size limits.

JSON writes use unique temporary files and a bounded replacement retry under a short process-local lock, including Windows sharing conflicts. Worker logs are monitored while the process is running and stop an excessive writer rather than waiting until the invocation ends. File-backed logs and PCM are closed on exceptions; the owned-process lifecycle from [the concurrency audit](CONCURRENCY_AUDIT.md) handles descendant cleanup. Codex assessment sessions remain ephemeral.

## Verification and applied cleanup

- **126 Python tests and five Node launcher tests pass.** Retention tests cover cached reuse, immutable remaining evidence, incomplete work, busy leases, dry runs, idempotence, interrupted deletion, path/link boundaries, inherited compact bundles, client closure and failed/concurrent JSON writes.
- Ten repeated scheduler/storage/retention runs pass **390 test executions** with no failures or skips. Transcription checks use a fake model and a real FFmpeg fixture; they confirm chunk timestamps, sample limits, closed temporary files and collection of previous NumPy buffers. No model weights are loaded for these tests.
- The reference workspace cleanup removes **81 full-video files, 1,798,087,535 bytes**, retaining 26 unfinished evidence runs. Selections, scorecards, original budgets, profile, video state, action history and exposures remain unchanged. The twenty-card dashboard and Phase 3 gate still pass. A second preview finds zero remaining disposable evidence files.
- Obsolete test installations, extracted packages and benchmark media account for another **1,180,677,496 bytes** across twenty cache directories. Benchmark summaries remain available. UV pruning removes unreachable entries, and explicit removal of dependencies absent from the current environment/build requirements removes **4.6 GiB** of obsolete caches, including GPU PyTorch and retired audio-analysis dependencies. Current dependency/model caches remain available.
- Read-only browser verification confirms twenty cards, preserved active feedback drafts, removal of invisible drafts and no JavaScript errors. It submits no user actions.

Byte totals describe removed file contents; shared hard links mean they are not a precise measurement of physical free-space gain. The audit does not retry platform failures, make model calls, download videos or delete reusable models, application history, migration backups or authentication state.
