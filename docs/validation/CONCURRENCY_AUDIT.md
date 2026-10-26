# Concurrency audit — October 26, 2026

Alpha.9 reviews the recently added parallel recommendation pipeline, including cancellation, evidence ownership, SQLite writes, resource reservations, batch creation and crash recovery. It does not start Phase 4 or change the ranking policy.

## Findings and fixes

| Defect | Resulting behavior |
| --- | --- |
| Cancellation could arrive during semaphore acquisition and still admit work | Check cancellation and deadline both before waiting and after acquiring the slot; always release acquired slots |
| Preparation released its evidence lease before assessment, permitting another client to modify the same bundle | One parent-owned token lease spans both stages; delegated preparation/assessment validates that token and cannot remove the parent's lock |
| A preparation receipt could save an outdated copy of the whole run over a newer assessment | Update only the receipt and token-specific byte usage in a short SQLite write transaction |
| Global counter differences could charge another client's work to this recommendation | Tag assessment attempts and preparation receipts with the dispatch token; recover only owned usage and reconcile reservations once |
| Cached preparation or an existing unknown judgment could unnecessarily require new media/model allowance | Reserve zero new bytes for warm evidence and zero calls for an existing judgment; open Bilibili authentication only when media needs downloading |
| Batch creation could leave a root or one platform child after a failure | Create the root, both children and their membership in one transaction, with rollback on failure |
| Running a batch child directly could bypass its coordinator's shared allowance | Store and validate parent ownership; continuation must use the batch ID |
| An interruption between platform submissions could leave an admitted child running | Protect submission as well as result collection; propagate shared cancellation and wait for owned cleanup before the final checkpoint |
| A launcher could exit while leaving descendants running, or the parent could die before cleanup | On Windows, gate command startup until its bootstrap belongs to a kill-on-close Job Object; terminate remaining members on normal exit, cancellation or timeout |
| An abrupt interruption could lose elapsed time since the last save | Persist an active-attempt wall-clock marker; conservatively recover missing time without resetting or double-charging the allowance |
| Killed preparation could omit its elapsed time | Preserve elapsed preparation time in the parent while the evidence lease remains held |
| Each continuation could subtract shutdown headroom from the saved limit again | Keep the configured limit unchanged and apply invocation headroom only to the current execution |

Shared cancellation also stops subsequent metadata requests and publication. Dispatch records an absolute deadline, so time waiting in a queue or for a shared slot consumes the same allowance. A pending candidate retains completed results from other workers. A batch whose children already completed can recover its result without new work, even after its deadline.

The Windows implementation follows Microsoft's [Job Objects lifecycle](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) and [kill-on-close extended limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information). Job/event handles are non-inheritable. The bootstrap starts the real command only after assignment, preventing the child-start-before-ownership race. Cleanup checks for zero active job processes before releasing handles. POSIX workers retain isolated process groups.

## Verification

- The full offline suite passes **107 Python tests**, including native Windows process cleanup and real fixture-based FFmpeg preparation; no tests are skipped in this Windows run.
- Twenty repeated scheduler/storage runs passed **440 test executions**, covering bounded overlap, exact accounting, cancellation races, out-of-order results, leases, receipt interleaving, failed batch creation, interruption during submission and continuation. Three repeated native process runs passed **18 test executions**, covering timeout, cancellation, successful launcher exit, parent death and isolation from a parallel peer.
- Node launcher tests, Ruff, web formatting, skill validation, wheel/tarball verification and a fresh offline core installation are checked with the release artifacts.
- The installed wheel is exercised from an unrelated directory, including the new owned-process bootstrap and UTF-8 input/output.

These are offline regressions and local process-lifecycle checks. No new model calls, video downloads or retries of the twenty deferred Bilibili evidence failures are needed. Existing recommendation selections, interests and user actions are preserved. The historical model-overlap benchmark remains in [performance verification](PERFORMANCE_VALIDATION.md); it is not a full uncached hundred-video timing result.

## Recovery limits

An interrupted preparation with no final receipt conservatively charges its reserved bytes and labels the count as an upper bound. A crash-time marker conservatively includes the unobserved wall-clock gap, capped at the configured allowance; a backward clock consumes the remaining allowance. This can pause a run early, but cannot silently restore resources.

Lock files left after an abrupt process death remain busy until the operator verifies that no owner is active. Legacy PID-only lock files are also left intact. The engine does not guess that another worker's lock is stale. Existing older batch records without explicit child ownership must be replaced by a new batch rather than resumed with unverifiable membership. No database schema migration is required.

Platform/network behavior, host subscription throttling and future scheduling remain outside these offline checks. A thirty-minute allowance still checkpoints unfinished work; it does not guarantee twenty assessed recommendations for every cold workload.
