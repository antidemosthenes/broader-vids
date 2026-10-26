# Phase 1 verification — October 24, 2026

**Phase 1 is complete in the reference workspace on `probe`. Phase 2 has not started.** The current design is merged into [PLAN.md](../PLAN.md).

## Live candidate collection

Three distinct rotated rounds of the same versioned query bank completed on each platform. These are ranked public-listing collections, not independent statistical samples. Each round retained at least 100 distinct observed candidates and wrote a 50-candidate preview, with search/creator coverage and deep/intersection/horizon retrieval intents.

| Platform | Round | Unique buffer | Preview | Preview creators | HTTP requests | Active seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 0 | 449 | 50 | 40 | 21 | 20.5 |
| YouTube | 1 | 455 | 50 | 42 | 21 | 20.4 |
| YouTube | 2 | 449 | 50 | 41 | 21 | 20.4 |
| Bilibili | 0 | 550 | 50 | 41 | 22 | 32.3 |
| Bilibili | 1 | 510 | 50 | 44 | 21 | 27.6 |
| Bilibili | 2 | 521 | 50 | 44 | 20 | 26.3 |

Each preview capped each creator at three videos. Intersection query buckets supplied 18–21 preview opportunities per round; these are retrieval-intent labels, not independently confirmed interdisciplinary contents. YouTube previews included platform labels such as “6 years ago” and “7 years ago”; Bilibili previews included dated older material, such as October 2023 and October 2022. Publication labels are preserved as reported rather than converted into invented exact dates.

Run IDs:

- YouTube round 0: `0d6d509ee7d54578be0f6c9f9c8c01ed`
- YouTube round 1: `1cf66a7aeeea4b8d96be6a4df4916dd3`
- YouTube round 2: `0ce9933514014b7f9b2cc68c88f0eaa6`
- Bilibili round 0: `f2905a9ce7f048ba81787b5a7f7a174e`
- Bilibili round 1: `a2b2cf1f7fcb47628c3c8d8c93bef968`
- Bilibili round 2: `133e54b0f7204167a98535f5f6b315d5`

The reference SQLite database is `.localdata/deepvids.sqlite3`. Each run has `report.json`, `buffer.json` and `candidates.md` under `.localdata/discovery/RUN_ID/`. Request IDs, response hashes and all query/source memberships are retained. These local artifacts are ignored by Git and are not distributed in the plugin. Regenerate reports with `deepvids --data-dir .localdata report RUN_ID`.

## Access findings and limits

- YouTube keyword search and channel video tabs worked anonymously, including continuation into a second channel page. Home supplied only the public client version during bootstrap; no Home recommendation cards were collected.
- Bilibili Home and keyword search worked through isolated anonymous HTTP sessions. These are not GUI private-browser trials; the user separately confirmed private browsing works. Cookies were fresh/in-memory and were not copied from a personal browser.
- The direct Bilibili recommendation API returned code 62011; signed creator uploads returned HTTP 412. Neither rejected route was retried. Creator expansion uses ordinary public creator-name search and accepts only returned owner-ID matches. It cannot enumerate the creator's complete archive.
- Bilibili round 0 paused on an unrecognized public search layout. Inspection found serialized results, so a restricted data parser was added and the saved run resumed inside its original budget. No page JavaScript was executed. The table includes that run's original request and active-time totals; preliminary access checks and a diagnostic fetch were separate.
- Full videos were not played or downloaded. The user's reported Bilibili login requirement remains a Phase 2 dependency. Bilibili card durations do not prove playable-part duration eligibility.
- Three rounds in one environment establish current feasibility, not long-term uptime, geographic coverage, global recall or quality. Explicit semantic/quality evaluation and the 25/25 familiar/unfamiliar split require Phase 2/3 evidence and profiles.

## Verification scope

All 42 Python tests and three Node launcher tests passed. Checks cover bank validation/rotation, listing formats, owner isolation, niche shortlist opportunities, creator caps, duration boundaries, response/decompression limits, data-only parsing, provenance rejection, budget retention, interrupted recovery, migration and the three-round gate. The skill validator passed. Legacy tests are regression coverage, not evidence for the active discovery route.

Packaging verification passed for the bundled bank and alpha.3 wheel, an external runtime launched from an unrelated path with spaces, editable bank export, fresh/verified gate behavior, report generation and unchanged package file hashes after execution. No npm publication, personal browser configuration changes, login or Phase 2 implementation is included.
