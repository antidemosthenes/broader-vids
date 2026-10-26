# Phase 3 follow-up — October 25, 2026

This check reuses the saved reference jobs and request/evidence receipts. It makes no new platform requests, downloads or model calls, and does not change recommendations or feedback.

October 26 disposition: the user explicitly deferred live retesting and recovery of the 20 evidence failures to later phases. Preserve the saved outcomes and evidence; they are not a blocker for the present exploration-design discussion. The obsolete private handover was removed after confirming that its durable results, costs and operational constraints were already covered by the validation documents, audit, architecture and plan. Its stale process IDs, module paths and development-delegation restriction are not current instructions.

## Bilibili admission and evidence failures

The completed recommendation run is `71435f7a40414f19ba557882b70fdb4b`, using discovery run `ddd28e713539424b8cfb8c2a3f749e9e`.

| Stage | Recorded outcome |
| --- | --- |
| Discovery | 782 distinct observed candidates |
| Recommendation buffer | 100 candidates allocated by query-family round-robin; the other 682 were outside this run's buffer |
| Engagement enrichment | All 100 received views, likes, coins and favorites using the explicitly authorized isolated signed-in session |
| Cheap screening | 50 entered inspection; 47 deferred for priority/capacity and three excluded because the resolved part exceeded 90 minutes |
| Detailed evidence | 30 received quality scores; 20 were unavailable for assessment |
| Final selection | Ten recommendations, eight profile-related and two unrelated |

The three duration exclusions were 6,884, 7,402 and 7,774 seconds. They are hard-rule exclusions, not access failures. The 47 lower-priority candidates were not measured and found low quality. Their recorded reason is `lower_screening_priority_or_no_free_slot`.

The 20 unavailable candidates had already passed screening. Their saved failures fall into these categories:

| Saved failure | Count |
| --- | ---: |
| Requested media format unavailable | 9 |
| CDN timeout or incomplete transfer | 4 |
| Local ASR worker failure | 3 |
| Assessment returned no defensible content score from irrelevant lyric captions and sparse frames | 2 |
| Downloader produced no complete video file | 1 |
| Downloaded duration failed the full-playable-unit check | 1 |

These are evidence limitations, not twenty low quality scores. The extractor's bounded low-resolution format request, platform availability and the local extraction path need to be distinguished; a saved format error alone does not prove which platform/account restriction caused it. A duration mismatch correctly prevents an incomplete file or preview from passing as full evidence.

A confirmed selector gap was corrected during this follow-up. The previous fallback tried a combined video/audio stream after the 240–480-pixel options, but omitted unrestricted separate video plus audio. A DASH-only video with no rendition in that height range could therefore fail. The selector now also tries the lowest available separate video and audio, under the same byte/time limits. An offline regression with portrait-only 1,920-pixel video verifies this path and preserves preference for available low-resolution video. This follows [yt-dlp's documented format-selection semantics](https://github.com/yt-dlp/yt-dlp#format-selection). The original nine failures did not save available-format lists, so this is a verified defect class, not a proven diagnosis for each of those videos.

The earlier anonymous trial `88d44d438f1d4ab5939b8d0c0739922b` hit HTTP 412 at the count endpoint on its first metadata request. That trial was paused and replaced, and never published. The implementation records `metadata_route_paused`, stops further requests to that route, and can still screen with missing engagement metrics neutral. It does not silently drop every candidate lacking counts. The completed authenticated run resolved the count-access issue for its entire 100-candidate buffer. Anonymous discovery remains separate from account access.

The [audit](../AUDIT.md) changed retryable network/download handling: future transient failures pause the current candidate without advancing its cursor. Historical unavailable outcomes remain an accurate record of the original run. This change does not establish that every format, ASR or insufficient-evidence failure has been solved, and no successful recovery is claimed without a fresh attempt.

The nine metadata/format errors and four transfer errors now follow that retryable path. Generic ASR failure, missing complete output and duration mismatch are still explicit unavailable outcomes. The historical ASR worker stderr was not retained, so its precise failure cause cannot be reconstructed. The no-video case retained only an audio file; that alone does not establish whether a video exceeded its allowance or another extraction step failed. The two null quality scores correctly remain unknown. More detailed private extraction diagnostics are still needed before attributing those historical cases to specific causes.

## Exploration measurements

The version 2 bank schedules 17 keyword queries per complete platform round. For the stored seven-interest reference profile, 12 are related and five unrelated by domain-tag overlap. This is a property of the current curated bank, not a configurable or learned search-effort ratio.

The saved 100-candidate buffers contained 70 related / 30 unrelated on YouTube and 65 / 35 on Bilibili. Both 50-video inspection lists were 25 / 25, and both final ten-video lists were eight / two. See [the curated reference allocation](../PLAN.md#curated-reference-allocation) for pending-slot precedence, creator/Home retrieval and limitations of tag-based familiarity. New onboarded runs now use [the growing hierarchy](../PLAN.md#growing-domain-hierarchy--implemented-october-26); these historical measurements remain fixed-bank results.

## Dashboard lifecycle

The optional dashboard is Python's loopback-only `ThreadingHTTPServer`. The npm executable launches the Python engine. Press Ctrl+C in the foreground terminal to stop it. For a background Windows process, inspect the listening PID and command line before using `Stop-Process`; see [the README](../../README.md#manual-recommendation-demo). Browser tabs do not control the server's lifetime. Stopping the server preserves SQLite recommendations and feedback.
