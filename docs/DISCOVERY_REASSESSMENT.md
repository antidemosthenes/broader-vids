# Discovery reassessment — October 24, 2026

Historical decision: this stop was superseded by the user's accepted curated-search/creator design. See [the current plan](PLAN.md) and [Phase 1 verification](PHASE1_VALIDATION.md). The old datasets and random-ID route remain rejected.

Phase 1 is incomplete, and work stops at discovery under the current requirements. ChinaOpen and YouNiverse are rejected as recommendation sources because of their age. Their imports and draws remain test fixtures. Phase 2 has not started; no replacement is integrated.

The previous completion statement verified import and sampling mechanics, but did not establish an acceptable discovery population. The CLI gate now excludes rejected sources even with verified downloads and 100-item draws. Download time does not change the underlying data's age.

## Replacement search

The search found newer research releases and commercial services, but no verified freely accessible, broad, regularly refreshed video-ID catalog covering both platforms. This is a search result, not proof none exists. Release dates alone do not establish current video coverage.

| Source | Finding | Assessment |
| --- | --- | --- |
| [TubeCensus](https://zenodo.org/records/18267682), [maintainer documentation](https://github.com/blast-cu/tubecensus) | Released January 2026; over 100 million channel identifiers from Wayback. Maintainers state URL coverage ends in 2023. | Historical channel frame rather than current video catalog. Current-upload collection would omit newer creators and need a different sampling design. |
| [TubeStats / TokStats](https://www.cogitatiopress.com/mediaandcommunication/article/view/12085), [code](https://github.com/iDPI-Umass/tubestats-www) | A 2026 publication describes ongoing random-sample research. | Promising YouTube lead; current raw-ID availability, sample dates and automation remain unverified. Dashboard yielded no extractable dataset links and full-text download timed out. No Bilibili counterpart established. |
| [Novada YouTube dataset](https://www.novada.com/youtube-dataset/) | Vendor advertises 17 billion indexed URLs, continuous refresh and bulk/API delivery. | Commercial lead; coverage and refresh claims unverified. Need a quote, dated sample and selection-method audit. No purchase made. |
| [Bright Data Bilibili dataset](https://brightdata.com/products/datasets/bilibili) | Vendor offers snapshots and refresh plans; states pricing starts at $250 per 100K records. | Paid lead without verified broad coverage. Inventory counts differ between indexed versions and the page; do not infer coverage from them. No purchase made. |
| [BILIBILI data, May 2025](https://figshare.com/articles/dataset/BILIBILI_data/29067839) | 300 records across eight categories for studying dissemination and visual presentation. | Newer but small and research-selected; no ongoing refresh established. |
| [bilibili-masterpieces](https://huggingface.co/datasets/wencan2024/bilibili-masterpieces) | Approximately 11.3K records; curated works from prominent early creators. | Creator and curation bias; no verified current discovery frame. |

ChinaOpen additionally mismatches the intended content: its [paper](https://arxiv.org/html/2305.05880v2) reports uploads May 2010–September 2021, durations 3–608 seconds, median 27 seconds, and filters excluding face-only and text-heavy content. YouNiverse's [documentation](https://github.com/epfl-dlab/YouNiverse) places its crawl in October–November 2019. Refreshing engagement metadata would not restore missing newer uploads.

## Guess until 100 valid IDs

Independent uniform guesses with replacement are valid rejection sampling if the domain covers the target and the availability oracle distinguishes absent videos from access errors. A syntactically valid ID does not establish an existing accessible video. For domain size D and N accessible IDs:

`P(hit) = N / D`

`E(requests for 100 hits) = 100 * D / N`

Distinct hits cost slightly more. These are illustrative population assumptions, not estimates of current platform sizes or observed benchmarks. They assume one conclusive request per guess and ignore throttling and retries.

| Proposal | Assumed accessible population | Expected requests for 100 hits | Time at one request/second |
| --- | ---: | ---: | ---: |
| YouTube canonical IDs, D = 2^64 | 100 billion | 18.45 billion | About 585 years |
| Bilibili numeric IDs, D = 2^51 − 1 | 10 billion | 22.52 million | About 261 days |

The [YouTube sampling paper](https://journalqd.org/article/view/4066) documents the 64-bit space and sparse occupancy. Its method groups effective guesses through search behavior; that does not make individual URL guessing efficient. Our earlier prefix experiments found no matching IDs. Bilibili's domain follows a [third-party AV/BV encoder](https://github.com/Colerar/abv/blob/main/src/lib.rs), not an official allocation or completeness guarantee.

Smaller Bilibili ranges or neighboring IDs may yield more hits because allocation is structured. Restricting to old low IDs would exclude modern allocations and reintroduce an age cutoff. A stratified or adaptive method needs verified occupied ranges, known selection probabilities and independent coverage evidence. The earlier MCMC run encountered an access challenge during warmup and established neither mixing nor coverage. Challenges remain unresolved, not nonexistent videos.

No unbounded guessing loop was implemented or launched. A tiny zero-hit pilot cannot usefully estimate these low rates. No new platform-ID probes were made in this reassessment; the figures are calculations.

## Decision

Stop at Phase 1 as authorized if discovery is inefficient. Preserve the engine, checkpoints, fixtures and research. Do not implement later phases against the rejected catalogs.

Reopening needs a verified refreshed catalog, an efficient validated sampler for both platforms, or an explicit decision to accept a different discovery population. Live category/search/creator collection could produce fresh candidates efficiently but has selection bias requiring an explicit design change. It is not silently substituted. Older videos remain eligible in a refreshed catalog: freshness means new content can enter, not that recommendations must favor recent uploads.
