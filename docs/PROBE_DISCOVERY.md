# Discovery alternatives on the probe branch

Research date: October 24, 2026. Branch: `probe`.

This document records the research and accepted design without platform-wide random video-ID sampling. It has now been merged into [the implementation plan](PLAN.md), with [Phase 1 live verification](PHASE1_VALIDATION.md). Historical alternatives and proposed experiments below remain research context; the merged plan defines the implemented scope. Phase 2 and the daily recommender are not implemented.

## Confirmed direction

The user confirmed these choices after the initial research:

- YouTube: curated keyword retrieval and creator archives. Exclude logged-out Home from the chosen route; the user confirmed it is unavailable in their environment.
- Bilibili: the same retrieval sources plus Home in an isolated private browsing session, which the user confirmed works. This observation does not yet establish a stable direct API.
- Broad subject queries alone are insufficient. The user's example is an interdisciplinary video about Banach spaces and finance that could rank poorly for both broad subjects. Candidate generation must deliberately seek narrow concepts and intersections before forming the 50-video shortlist.

Private browsing isolates existing browsing state rather than guaranteeing zero cookies: [Chrome documents temporary cookies and site data during Incognito sessions](https://support.google.com/chrome/answer/95464?hl=en). The intended condition is logged out, with no inherited personal cookies or history; normal temporary site state is distinct from account authentication.

## Proposed curated query design

This design addresses the confirmed concern; its exact allocation and calibration remain proposals.

1. **Maintain a versioned topic map and query bank.** Represent broad domains, specific concepts, meaningful cross-domain connections, aliases, languages and prerequisite/depth tags. Give each query an explicit discovery purpose. Do not generate all pairs of subjects indiscriminately, and do not rely solely on the user's currently favored topics.
2. **Search intersections directly.** For the user's example, illustrative queries include `Banach spaces finance`, `functional analysis mathematical finance`, `巴拿赫空间 金融` and `泛函分析 金融应用`. These are search hypotheses, not claims that matching videos exist or that the phrases describe identical content. Broader and narrower variants should complement one another. A video's keywords may omit the user's exact terminology.
3. **Protect query diversity before the shortlist.** Reserve retrieval effort and provisional shortlist opportunities for specific concepts, cross-domain connections, unfamiliar topics and older archives. Do not merge every result and select the top 50 by platform rank, total views or the number of queries in which it appears. A niche result appearing in one focused query must remain eligible. Interdisciplinary content can be profile-related or unrelated; these are separate labels and must not replace the existing discovery requirement.
4. **Keep more than 50 cheap candidates.** Merge metadata from multiple focused queries and creator expansion into a larger, bounded buffer. Form the 50 evidence candidates from that buffer with creator limits and the protected discovery purposes. Fifty is the expensive-inspection budget, not a first-arrival cutoff. Refill when access, duration or evidence checks remove candidates.
5. **Use focused follow-up retrieval.** When a promising result introduces an unfamiliar term or creator, add bounded searches for the term, public thematic collections and that creator's relevant back catalog. Preserve source provenance and archive cursors. Do not assume every upload by a good creator has the same quality or topic.
6. **Rotate breadth and depth.** Persist query cursors and run dates. Rotate concept families, historical windows where supported, and selected deeper result pages across days. Pagination alone is an inefficient substitute for specific queries. New-video discovery and archive coverage require separate effort; avoid endlessly revisiting the same broad top results.

Query curation can be assisted by a language model that proposes specific concepts, synonyms and plausible connections, but proposals must be checked for coherent meaning and observed retrieval results. Keep the bank inspectable and editable; models must not invent matching videos. Query variants with no results are observations, not proof the subject has no videos. Precision and breadth both matter: a very specific query can miss relevant videos too.

Evaluate this design with a small reference set of known worthwhile videos, including narrow topics, cross-domain cases, old uploads and smaller creators. Keep some reference videos out of query-bank construction. Measure whether eligible reference videos enter the metadata buffer and then survive into the 50-video shortlist. This distinguishes retrieval failures from shortlist failures. Compare against broad-query-only retrieval under the same request and inspection budget; test on additional unseen examples to avoid merely encoding reference titles.

Query scheduling and coverage rules are distinct from final ranking weights and the planned Bayesian user profile. Do not penalize a query solely because few of its videos were shown or rated. Log retrieval source, topic tags, exposure and availability separately. No finite curated bank guarantees discovery of every obscure intersection; the objective is a measurable improvement in useful coverage, not a platform-wide completeness claim.

## Assessment

The selected direction is curated topic discovery plus creator back catalogs on YouTube, adding a capped Home contribution on Bilibili. Homepage collection can supply seeds, but cannot by itself remove the platform's recommendation biases. Random user-ID approaches below remain research alternatives outside the selected route. Discovering known creators and retrieving their uploads is the more practical version of the second idea.

The new target is a useful, diverse, age-inclusive recommendation pool. We no longer need to estimate platform-wide distributions or prove uniform inclusion. We still need to measure which kinds of videos the retrieval methods omit. A perfect quality ranker cannot recover a video that never enters the pool.

## 1. Logged-out homepage collection

Collect ordinary video cards, follow the page's normal refresh or continuation mechanism, deduplicate canonical IDs and stop at a bounded target. An ordinary anonymous session can retain site-issued visitor cookies without logging into an account. That is a different experimental condition from a fresh session for every request; neither should borrow the user's authenticated browser state.

### Evidence and access

- Google documents that [cookies and other identifiers can affect personalization](https://policies.google.com/technologies/cookies?hl=en), and that even non-personalized content can depend on location, language, device and current content. Logged out does not mean unbiased or independent of session context.
- Google's [watch-history documentation](https://support.google.com/youtube/answer/95725?hl=en) describes conditions in which Home recommendations are removed. This does not mean every logged-out visit is blank. The [yt-dlp YouTube tab extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_tab.py) also contains an empty recommended-feed fallback test requiring visitor data. A completely fresh session is therefore not a dependable source until tested in the deployment environment.
- A [community-maintained Bilibili API reference](https://github.com/melon-444/bilibili-API-collect-fork/blob/master/docs/video/recommend.md) describes `/x/web-interface/index/top/rcmd` with refresh/page parameters. Its authentication notes and examples are insufficient to guarantee current anonymous API access. Browser-visible recommendations and a stable anonymous API are separate questions.
- In this investigation, the web reader extracted no recommendation cards from YouTube's homepage. Bilibili's homepage redirected that reader to a browser-version notice. Direct Bilibili API URLs were not accessible through the reader. These observations describe the research tool, not a controlled browser trial or proof that either site is unavailable to the user.

### Merits and limits

Advantages: no huge identifier space; potentially several existing video links per response; fresh creator discovery; little onboarding. Refreshing can increase the candidate count.

Limits: session and geographic effects, repeated cards, hidden ranking policy, popularity and potentially recency bias, plus promoted placements. Fifty raw cards are not fifty unique eligible videos, and refreshes are not independent draws. If a response has b cards and an observed new-eligible fraction r, a rough local projection is `remaining_target / (b * r)` responses; r can decline as the feed saturates. No hit rate or request count has been measured here.

The claim that this method excludes all older videos is too strong: no hard age cutoff has been established. The concern is underexposure. Giving recency only 5% in our score cannot undo an age bias in candidate retrieval. Repeated refreshes alone do not solve this.

Separate marked ad cards from ordinary videos. A promoted placement is not proof a video lacks substance, and an unmarked ordinary video may contain sponsorship. Neither card metadata nor popularity establishes originality, narration type or content quality.

**Use:** a capped source of new candidates and creator seeds, followed by archive expansion. Do not rely on refreshing forever to reach 50 after deduplication, duration filtering and repeat suppression.

## 2. User or creator discovery

### Guessing numeric Bilibili user IDs

A real user can expose many uploads, so a successful creator lookup can amortize its cost over multiple candidates. This makes the idea worth distinguishing from random video IDs. However, existing accounts can have no public uploads, and the occupied numeric domain and fraction of productive creators have not been established. A smaller old-ID interval may improve apparent yield while excluding newer creators.

If p is the fraction of guessed IDs that lead to accessible creators with eligible public uploads, discovering k such creators takes approximately `k / p` lookup attempts, before archive retrieval and duplicate filtering. p must be measured for each declared interval; no rate is assumed here. A small pilot cannot establish whole-platform coverage.

The [Bilibili space reference](https://janson20.github.io/bilibili-api-collect-mirror/docs/user/space.html) documents creator uploads with pagination and publication, view-count and favorite-count ordering. Its [account-information reference](https://janson20.github.io/bilibili-api-collect-mirror/docs/user/info.html) describes signing and cookie requirements for detailed profiles. Prefer a directly useful uploads lookup where accessible; a separate profile lookup may add cost without yielding videos. These are unofficial descriptions, not a supported public API contract.

The current [yt-dlp Bilibili extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) implements paginated creator uploads and explicitly handles server rejections, including 412, -401 and -352. Creator discovery does not remove the access problem seen in the earlier video experiment. Stop and record unresolved access failures; do not treat them as nonexistent users or empty archives.

**Use:** an optional bounded Bilibili experiment, conditional on ordinary public access. It is not yet a replacement discovery strategy.

### Guessing YouTube users

The [YouTube channel API](https://developers.google.com/youtube/v3/docs/channels/list) looks up known IDs, handles and legacy usernames. It provides no random-user operation or documented dense numeric account range. Guessing opaque channel identifiers has no demonstrated efficiency advantage; guessing human-readable handles introduces vocabulary and language bias.

**Use known creators instead.** Google documents [retrieving a channel's uploads playlist](https://developers.google.com/youtube/v3/guides/implementation/videos), followed by [paginated playlist-item retrieval](https://developers.google.com/youtube/v3/docs/playlistItems/list). Public-data use requires API setup and quotas; personal account history need not be a discovery input. This investigation used no API key and did not benchmark these calls.

### Creator selection changes the distribution

Selecting creators equally and taking a fixed number of uploads is creator-balanced, not video-balanced. With two equally selected creators having 10 and 1,000 uploads, taking one uniformly within each archive makes a particular video from the smaller archive 100 times likelier. This can be desirable for author diversity now that global uniformity is not the goal.

A deterministic alternative is round-robin retrieval from discovered creators, with a per-creator cap. Either way, do not let prolific creators fill the entire pool, or retrieve only each creator's newest page. Old uploads may require multiple pages; backfill gradually and preserve cursors. Bilibili's documented pagination allows targeted page requests, whereas YouTube playlist traversal uses page tokens rather than arbitrary numeric page access. Avoid promising cheap arbitrary-depth access on both platforms.

## 3. Other useful routes

### Topic search across publication periods

Use a broad topic taxonomy containing both profile interests and unrelated domains. Rotate concrete subtopics, synonyms and languages. Include deliberately historical queries alongside unrestricted and recent discovery, rather than always sorting newest first.

The [YouTube search API](https://developers.google.com/youtube/v3/docs/search/list) supports video/channel/playlist results, publication bounds and several sort orders. It also warns about incomplete results with some sorts and date filters. These are retrieval controls, not exhaustive database queries. API credentials and quotas are required; the [setup documentation](https://developers.google.com/youtube/v3/getting-started) explains registration. Ordinary web search is an alternative access path but still has ranking and indexing limits.

[Bilibili community documentation](https://janson20.github.io/bilibili-api-collect-mirror/docs/search/search_request.html) describes keyword video and creator search, multiple sorts, signing and anonymous visitor-cookie checks. Arbitrary publication-window support was not verified. Where date filters are absent, use creator archives and observed publication dates; do not invent unsupported API parameters.

This is the strongest independent discovery source: it can seek substantive older material while exposing unfamiliar subjects. Its risks are keyword blind spots, ranking bias and a new profile-shaped filter bubble. Reserve explicit retrieval budget for topics outside the profile.

### Public playlists, collections and external curation

Use public thematic playlists, Bilibili collections, educational organizations' reading/watch lists and relevant public web pages as seeds. External search can discover direct video links as well as creators. Verify each result on its platform; a search snippet or a generated suggestion does not establish a valid video or quality.

These sources can surface evergreen material efficiently but inherit curator, language and popularity biases. Retain curator/source provenance and use multiple independent sources. A good playlist is a discovery aid, not a trusted quality label.

### Related-video and collaboration expansion

From good seeds, follow a limited number of related-video links, public collaborations or explicitly linked creators, then inspect their archives. The [Bilibili recommendation reference](https://github.com/melon-444/bilibili-API-collect-fork/blob/master/docs/video/recommend.md) documents related-video retrieval. This may reach older material, but related recommendations remain platform-ranked and can reinforce the starting topic. Keep expansion shallow and start new independent topic searches regularly.

### Persistent local candidate and creator registry

Build a small continuously refreshed local collection of observed public creators and videos, with source, first/last observation, publication time and archive cursors. Reuse never-recommended candidates and progressively backfill older uploads. This reduces daily discovery cost and avoids depending on an old external snapshot.

The registry is storage for observed candidates, not a census. New creators must continually enter through search, homepage and curation. Refresh failures remain unknown; lack of recent observation does not mean deletion. Repeated recommendations remain prohibited across sources and days.

## Comparison

Ratings below are reasoned expectations from interface capabilities, not measured performance scores.

| Route | Access to older material | Independence from Home ranking | Main weakness | Suggested role |
| --- | --- | --- | --- | --- |
| Logged-out Home refresh | Possible, uncontrolled | Low | Context, popularity, recency and duplication | Small seed source |
| Random Bilibili numeric users | Good after productive archive access | Potentially high | Unknown creator yield, occupied ranges and access rejection | Optional experimental arm |
| Random YouTube channel IDs/handles | Good after a hit | Potentially high | No proven efficient discovery mechanism | Do not prioritize |
| Known creators plus archive backfill | Strong within discovered creators | High during upload enumeration | Initial creator bias and deep pagination cost | Core source |
| Topic search with age-aware queries | Strong where indexed and supported | Medium; still search-ranked | Vocabulary and result truncation | Core discovery source |
| Playlists, collections and external links | Often strong | High for external curators | Curator bias and stale links | Supplemental seeds |
| Related-video expansion | Possible | Low to medium | Seed/topic feedback loops | Capped expansion |
| Mixed retrieval plus local registry | Stronger across observed sources | Mixed | More bookkeeping; no coverage guarantee | Recommended design |

## Recommended design to investigate

Use live discovery to build and refresh a creator registry. Expand discovered creators into both recent uploads and back catalogs. Merge with direct topic-search and curated-link candidates, normalize IDs, suppress prior recommendations, apply duration/access rules, then perform the existing evidence-based shortlist and scoring stages.

Use platform-specific source allocations: YouTube has curated search and creator archives; Bilibili additionally has Home. The earlier shared 10/20/20 source allocation is superseded. Exact counts should follow a bounded comparison, with protected retrieval effort for narrow concepts, meaningful intersections, unfamiliar topics and older uploads. Source allocations are distinct from quality-score weights or learned probabilities. Use unique candidates; retain all source memberships for overlaps and refill exhausted sources explicitly. Do not force low-quality results into final recommendations just to fill a source quota.

Fifty raw candidates are only a discovery benchmark. Producing 50 eligible evidence candidates and ultimately 20 recommendations can require a larger pool. Preserve the previous duration rule (>90 minutes excluded, exactly 90 allowed, Bilibili per playable part), quality rubric and cross-round history. Changes to candidate retrieval do not require replacing the planned Bayesian preference model. Feedback is conditional on retrieved/exposed content, so retain source and exposure context; unknown platform inclusion probabilities cannot be corrected away by the learner.

## Proposed next experiment

Run metadata-only comparisons on this branch before implementation decisions. No experimental runs below have been executed yet.

1. **Bilibili Home baseline:** use an isolated ordinary private session and the normal continuation/refresh behavior. Attempt 50 distinct candidates, recording emitted cards, unique IDs, creator count, ages and duplicates. YouTube Home is excluded by the confirmed direction. Never sign out the user's existing session or change their settings for the experiment.
2. **Known-creator expansion:** discover creators through a fixed broad query list, then enumerate capped recent and older uploads. Measure how many additional eligible older videos appear beyond the initial search/Home seeds. Save pagination positions and report truncated archives.
3. **Topic/age retrieval:** use the same broad topics across sessions, with available historical retrieval controls. Compare historical share and creator/topic diversity against Home. Verify current anonymous Bilibili access first; distinguish visitor cookies from account credentials.
4. **Curated-query comparison:** compare broad subjects alone against narrow concepts, meaningful intersections and creator expansion under the same request budget. Use held-out reference videos to measure retrieval into the buffer and survival into the 50-video shortlist. User-ID guessing is outside the selected route; retain it only as an unexecuted research alternative.

Provisional bounds: ten minutes and 100 total HTTP requests per platform/method session, at least one second between requests, with narrower bounds if a service requires them. Count initialization, pagination and validation calls. For Home, cap refreshes at 20 or stop after three consecutive zero-new-ID responses. Stop the affected route on access challenges. Report quota exhaustion or missing credentials as access limitations, not zero-yield results. Repeat viable arms in three sessions before treating apparent yield as dependable.

Compare time/requests to 50 distinct raw candidates; eligible yield after availability, duration and history checks; creator concentration; age distribution (under 30 days, 30 days–1 year, 1–5 years, over 5 years); topical spread; duplicate saturation; explicitly labeled promotions; and unresolved access rate. Spot-check a matched small subset from each arm for substantive quality using the same evidence rubric. New-creator counts and a high unique-video yield alone do not establish recommendation quality.

Choose a route on demonstrated useful yield, older-video access, discovery breadth and repeat-run reliability. Do not reopen the original uniform-sampling gate or advance the stopped project's phase merely because this research document exists.
