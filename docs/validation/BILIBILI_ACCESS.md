# Bilibili metadata access investigation

Verified October 26, 2026, America/New_York, against `BV1ig87zeE9q` using the reference workspace's existing isolated login.

## Findings

| Route | Observed result |
| --- | --- |
| Anonymous ordinary HTTPS request to `/x/web-interface/view/detail` | HTTP 412 |
| Existing isolated browser session, `/x/web-interface/nav` | HTTP 200; `isLogin=true` |
| Existing isolated browser session, combined metadata endpoint | HTTP 200; API code 0 |
| Normal video page in the existing isolated browser | HTTP 200; matching video identity and embedded metadata |
| Application `PublicHttp` with the same scoped session | Successful combined metadata response |
| New `metadata URL --use-browser` command | Completed; one tracked metadata request; complete snapshot persisted |

The anonymous failure did not establish that the video was inaccessible through the application. The original diagnostic did not use the existing authenticated route. An earlier separate tags probe also used an unsupported endpoint; production and the fixed diagnostic use the combined `/x/web-interface/view/detail` response for counts, description and tags. No proxy, challenge bypass, new login or retry loop was needed. Anonymous access can still be rejected; this change does not promise platform-wide availability.

The verified video metadata contains source `https://www.youtube.com/watch?v=GAkVaD1ihi4`, uploader `copyright=2` (repost declaration), and tags `数学`, `课程`, `视频`. It does not have the exact `视频翻译` hard-exclusion tag. The source/repost disclosures are originality evidence; they do not independently establish synthetic narration. The refreshed reference snapshot is attached to metadata diagnostic `e924b96f26f149518276f21b33fc62b3`, request 383.

## Corrections

- Share the existing scoped authenticated request path between recommendation enrichment and the metadata-only command; keep discovery anonymous.
- Restore the HTTP client's prior cookies and transport marker after the authenticated scope, including error paths.
- Explain a Bilibili HTTP 412 with authenticated-session guidance, without automatically switching identities or repeatedly requesting the blocked route.
- Clear the previous success receipt before each new request so a failed read cannot appear to reuse it.
- Require description, nonempty title, uploader authorship declaration and a recognized tags list before caching a newly collected Bilibili metadata response as complete. A valid empty description or tags list is permitted.
- Invalidate recent but incomplete historical Bilibili snapshots during enrichment.
- Pause a metadata failure before advancing its cursor or changing to the assessment phase. Pending-video metadata refresh failures also pause and retry safely.
- Preserve the last successful snapshot on failed standalone refresh. Record diagnostic outcome and successful replacement snapshot atomically.

## Verification and boundaries

Ten targeted offline tests cover authenticated collection, source/repost fields, secret-free persistence, missing login without anonymous fallback, HTTP 412 without retries, old-snapshot preservation, incomplete metadata, cookie cleanup, stale receipt removal, cache invalidation and fresh/pending cursor recovery. The complete Python suite passes 165 tests.

The live command fetched metadata only. It did not download media, call an AI model, alter watched/dismissed/vote state, regenerate the calibration cohort or change its frozen feature snapshots and historical assessments. Historical scores therefore still need a separate provenance-aware review; restoring access alone does not recompute them. The twenty previously deferred Bilibili evidence failures were not retried.

To repeat this bounded check:

```powershell
uv run --no-sync deepvids --data-dir .localdata metadata "https://www.bilibili.com/video/BV1ig87zeE9q" --use-browser
```

Exit code 0 means the snapshot was saved; 2 means access or validation remains unresolved. A fresh metadata diagnostic has its own one-request, sixty-second allowance. It does not reset a recommendation job's budgets. Rerun the diagnostic after correcting access; use `continue RUN_ID` for a paused recommendation or its owning batch ID for a combined run.
