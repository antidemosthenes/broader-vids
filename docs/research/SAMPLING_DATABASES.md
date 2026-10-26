# Existing video catalogs for sampling

Archived research: these findings explain rejected discovery approaches. Commands and module references describe the historical prototype. See [the current architecture](../ARCHITECTURE.md) for the supported runtime.

Historical research: the current implementation uses curated live discovery; see [the plan](../PLAN.md) and [verification](../validation/PHASE1_VALIDATION.md).

Researched October 24, 2026. ChinaOpen and YouNiverse were subsequently rejected as dated. The [reassessment](DISCOVERY_REASSESSMENT.md) supersedes the earlier recommendation below. Phase 1 is incomplete and work stops here.

## Public datasets

| Catalog | Contents and access | Sampling limitations |
| --- | --- | --- |
| [Archive Team YouTube dislikes / ClickHouse mirror](https://clickhouse.com/docs/get-started/sample-datasets/youtube-dislikes) | Roughly 4.6 billion historical records; public JSONLines/Zstandard shards include video IDs, titles, descriptions, upload/fetch dates and engagement. ClickHouse documents both Internet Archive and public S3 sources. | A 2021 crawl, not today's complete catalog. Audit unique IDs, crawl selection, duplicates, invalid records and current availability. The documentation gives slightly different aggregate row counts; verify rather than assuming a precise distinct-video count. |
| [YouNiverse](https://github.com/epfl-dlab/YouNiverse) | 72,924,794 videos from roughly 137,000 English-speaking channels. [Zenodo](https://zenodo.org/records/4650046) provides metadata; the English compressed metadata file is listed as 13.6 GB. | Crawled October–November 2019. Channel selection used more than 10,000 subscribers and more than ten videos; English filtering and creator-size selection exclude much of the long tail. |
| [ChinaOpen-50K](https://ruc-aimc-lab.github.io/ChinaOpen/) | Nominally 50K Bilibili videos, with [a directly accessible ID/URL list](https://raw.githubusercontent.com/JewelChen2019/ChinaOpen/main/ChinaOpen-50K-url.txt), titles/tags, annotations and optional extracted frames. | A curated 2023 research release with quality/content filtering. It does not establish platform-wide representativeness. The URL file has more than 50K lines, so count distinct canonical IDs on import; current playability and part lists are unverified. |
| [vRetention](https://github.com/flowtele/vRetention) | Downloadable YouTube/Bilibili metadata and retention-curve files; repository lists a Box alternative when Git LFS quota is exceeded. The [paper](https://yihchun.com/papers/vretention.pdf) reports 30,137 Bilibili videos from October 2022–May 2023 across 16 categories. | Popular-video selection and a bounded collection period. Useful for access/retention experiments, not a whole-catalog sampling reference or a ground-truth quality label. |

Availability checks: the ChinaOpen raw URL list was retrievable. A metadata-only request to the ClickHouse public S3 mirror returned HTTP 200 and listed `youtube/original/files/youtubedislikes_20211127161229_18654868.1637897329_vid.json.zst` at 89,850,633 compressed bytes; this verifies an available shard listing, not the completeness of the advertised collection. No bulk dataset was downloaded, and no selected videos were probed in this research round.

## Additional leads

- [Common Crawl's URL index](https://index.commoncrawl.org/) is free, queryable by URL pattern and backed by downloadable indexes; its [overview](https://commoncrawl.org/overview) lists regularly updated snapshots. It is a possible source for building a deduplicated cross-platform ID catalog. Video-platform coverage has not been measured, and web-link/crawl selection creates its own bias. Use the columnar index for bulk work rather than flooding the public URL-index service.
- The [WWW 2017 Bilibili study](https://archives.iw3c2.org/www2017/proceedings/companion/p1477.pdf) describes an exhaustive historical crawl containing 2,858,844 videos as of May 2016 and points to `sites.google.com/view/bilibildataset`. The dataset landing page was not retrievable through the research tool, so current download availability is unverified.
- [Shorter Is Different](https://arxiv.org/html/2410.16058v1) describes 4.7 million Bilibili videos uploaded July 29–August 12, 2024, retaining videos viewed at least once during their first week. The paper describes restricted offline data access; no public catalog download was established.
- [Novada](https://www.novada.com/youtube-dataset/) advertises a paid continuously refreshed index of 17 billion YouTube URLs. This is a vendor claim; coverage, acquisition method, quote and sample access have not been independently validated.
- [Bright Data's Bilibili offering](https://brightdata.com/products/datasets/bilibili) displayed about 3.4K records and a $250 minimum order when inspected. It is not evidence of a broad platform catalog and does not fit the current no-new-paid-services default.

## Earlier architectural recommendation (superseded)

Catalog-backed discovery was selected for Phase 1. A declared catalog makes candidate selection reproducible and avoids spending nearly all requests on nonexistent IDs. Its statistical target is the catalog's distinct linked videos, with source dates and selection rules retained.

For a prototype, ChinaOpen supplies a small immediately accessible Bilibili catalog, while YouNiverse supplies a manageable documented YouTube metadata source. The Archive Team mirror is the stronger broad historical YouTube lead, with substantial preprocessing and storage needs. Broader Bilibili coverage remains unestablished.

Import provenance, snapshot dates, original selection rules and known omissions. Deduplicate canonical IDs across catalogs before sampling. Distinguish archive IDs from Bilibili playable parts and resolve current part counts. Sample locally, then refresh availability/duration and gather content evidence only for selected candidates. Keep ambiguous access responses separate from unavailable videos.

For a large source, a first-N-row download is not a random sample. A one-pass reservoir requires a complete stream; shard sampling requires known row counts and appropriate weighting. Avoid claiming uniformity from random file selection or from a mixture of overlapping catalogs without correcting their selection probabilities.
