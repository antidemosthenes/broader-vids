# Deepvids implementation plan

Status: Phases 1–3 are implemented and verified on `probe-phase-3`. See [discovery verification](validation/PHASE1_VALIDATION.md), [the evidence trial](validation/PHASE2_VALIDATION.md), [the manual demo](validation/PHASE3_VALIDATION.md) and the [roadmap](PHASES.md). The local gate verifies complete 50-candidate/ten-pick runs on both platforms. Bayesian fitting and scheduling have not started. Earlier sampling and dated-catalog routes remain superseded research.

The user accepted curated keyword search mixed with creators on YouTube, adding isolated-session Home on Bilibili. The [research design](research/PROBE_DISCOVERY.md) is merged below. Historical catalog implementations have been removed; their research notes remain archived.

## Product decisions

- Build a local, publicly installable Codex plugin first, with a shared engine for a later Claude Code adapter.
- Produce separate lists of 10 YouTube and 10 Bilibili videos per daily batch or additional round, selected from 50 inspection candidates per platform.
- Keep previously recommended videos in a persistent pending queue until explicitly marked watched or dismissed. Pending recommendations have guaranteed places in the next inspection list and can be recommended again.
- Record watched state, dismissal and editable thumbs-up/down feedback separately; absence of watching or feedback is not a negative preference observation.
- Validate manual runs first; later offer opt-in daily scheduling through Windows Task Scheduler.
- Present numbered links, explanations, confidence, and feedback instructions in Codex; save Markdown digests locally.
- Ship shared configurable defaults; keep personal profiles, credentials, histories, models and caches outside the package.
- Require both platforms to pass live discovery and evidence-access validation before releasing v1.
- Bound the complete two-platform recommendation batch to 30 minutes and checkpoint incomplete work; an explicit continue command resumes it.
- Once scheduling is enabled, replace unfinished older daily jobs on the next date while retaining their observations, recommendation history and unresolved pending recommendations.

## Phase 1: curated live discovery

Use anonymous public web listings without inherited personal cookies or account history. YouTube uses focused search and creator video tabs with continuation. Logged-out Home is excluded as a candidate source; its bootstrap page only supplies the public web client version. Bilibili uses public Home/search in a fresh in-memory cookie jar. Parse rendered cards and supported serialized state as data, never executing page scripts. These public web schemas are unofficial interfaces and can change.

Bilibili's direct recommendation API returned code 62011 and signed creator uploads returned HTTP 412 in preliminary checks. Those routes are stopped. The implemented fallback searches discovered creator names and retains only returned owner-ID matches. Label it `creator_search`: a search-index subset, not complete archive enumeration. Full archive access remains unverified; this limitation does not prevent collecting candidates from accepted public search/Home sources.

Maintain an editable, versioned bank of specific concepts and meaningful cross-domain connections, with purpose, domains, aliases and English/Chinese variants. The current version 2 development curriculum has 17 families: seven intersections, eight deep topics and two horizon topics. It is not an inferred personal profile. Examples include Banach spaces and finance, entropy and inference, music and Fourier analysis, and materials and history. No model API is needed; future assisted suggestions must be checked against actual results.

Interleave intents and rotate family order/query variants across rounds. Collect a bounded metadata buffer before selecting 50 inspection candidates. Within each source/family bucket, prefer title/description anchor matches and local result rank, then select round-robin across buckets with a maximum of three per creator. Preserve niche opportunities regardless of global popularity or how many queries return the same video. Intent tags and anchors are hints, not verified relevance or quality. The initial buffer cap is 1,000 distinct videos, with at most 40 retained per returned page; report truncation.

Persist candidate/creator observations, all source memberships, response hashes, request IDs, bank hash, round, publication labels and pagination state in SQLite. Write buffer JSON, shortlist JSON and linked Markdown. A new round rotates the query plan; `continue` preserves pending work and remaining budget. Completed-run continuation tokens are retained for inspection, not automatically reused indefinitely.

Default limits: 100 HTTP attempts and 600 seconds, at least one second between requests, 4 MiB limits on compressed and decompressed responses. Overrides cannot exceed 1,000 attempts or 3,600 seconds. Stop on access errors or unknown schemas; never infer empty results from failed access. Temporary visitor cookies are discarded between invocations. No authentication, full-video playback or media download is needed in Phase 1.

Completion requires three distinct completed rounds of one retrieval-policy or query-bank version per platform. Each needs at least 100 source-receipted candidates, 50 distinct preview candidates, search and creator expansion, at least six observed query/topic families, and policy-specific coverage checks: fixed-bank rounds require all three intents and at least three intersection preview opportunities; hierarchical rounds require a validated plan and completed searches for every scheduled concept. Bilibili additionally requires Home candidates. Historical fixtures and synthetic tests cannot substitute for live evidence in the reference workspace.

Known YouTube durations above 90 minutes are excluded from previews. Bilibili listing duration may describe a multipart archive; playable-part eligibility remains unknown until Phase 2. The user reports full Bilibili playback requires login: configure that explicitly in Phase 2, without reusing personal cookies for Phase 1.

Evaluate curation with narrow, interdisciplinary, older and smaller-creator reference examples, holding some out of query construction. Measure retrieval into the buffer separately from selection into the 50. Regression checks cover niche preservation and source isolation; semantic usefulness and substantive quality require the Phase 2 evidence pilot. Global uniformity and exhaustive platform recall are not goals.

### Curated reference allocation

The original reference runs used fixed curated retrieval. That path remains available through `discover --curated` or an explicit `--bank`; a fresh installation without onboarding also uses it. `discovery/queries.py::query_plan` schedules one query per family per complete round and rotates its variants across rounds. It does not accept the user profile or enforce a related/unrelated search quota. The shipped bank lives in `src/deepvids/data/queries.json` (inside the Python wheel for npm installations). With onboarding and no explicit curated override, new discovery/recommendation runs use the hierarchical policy below.

For the reference user's interests (travel, food, mathematics, physics, computer science, games and music), 12 of the 17 scheduled keyword queries have overlapping domain tags and five do not: approximately 71% related and 29% unrelated search effort on either platform. The five unrelated families are causal health, materials/history, language change, restoration and field observation. Intersection intent is independent of familiarity: a math/finance query is related because mathematics overlaps the profile. These are query-tag classifications, not verified video-content labels.

| Stage | Allocation or measured result |
| --- | --- |
| Keyword searches | 12 related / 5 unrelated for the reference profile; fixed bank, no adaptive quota |
| Creator expansion | Default up to four creators, two pages each; no separate familiarity quota |
| Bilibili Home | One anonymous initial page; no profile-based allocation |
| First 100 recommendation candidates | Round-robin by query family, no familiarity quota; live YouTube 70/30 and Bilibili 65/35 related/unrelated |
| 50 inspection candidates | Aim for 25/25 when available; guaranteed pending entries take precedence; both reference runs achieved 25/25 |
| Final ten per platform | At least two unrelated when enough qualifying candidates exist; both reference lists contained eight related and two unrelated |

`recommendations/ranking.py::related` uses normalized domain-tag overlap, including inherited creator-search tags. Candidates with unknown domains, including Home-only records, count as unrelated. A video can therefore be familiar despite that label, or inherit an inaccurate topic label. Screening balances the two lanes and rotates query families; it falls back to available candidates if a lane is exhausted. The final two-unrelated rule is a minimum, not a cap. Semantic topic verification remains future work. See [the follow-up measurements](validation/PHASE3_FOLLOWUP.md).

### Growing domain hierarchy — implemented October 26

The user approved a persistent multilevel domain map. Level 1 starts with explicit interests such as mathematics and physics. Each new discovery round targets 12 existing concepts plus five AI-proposed new concepts. Search new concepts provisionally, activate them after useful retrieval, and refine individual branches when broad novelty becomes difficult: mathematics → algebra/topology → algebraic geometry. Splits are useful rough subdivisions rather than a complete taxonomy. Existing discovery checkpoints retain their original plans; completed reference results are not relabeled as hierarchical runs.

Implemented rules:

- Store the domain map separately from the user-interest profile. Record whether a node came from onboarding, subdivision or exploratory generation. Adding or searching a node establishes coverage history, not a positive preference label. Uniform selection from an ever-growing map would progressively dilute the original interests; select existing nodes with explicit interest coverage, recent-use cooldowns and opportunities for underexplored nodes.
- Use distinct concepts without replacement for the 12 existing-topic slots. Broad labels can seed narrower executable queries; avoid spending several slots on a parent and nearly identical descendants. Interpret 12 existing plus five novel as a target number of semantic topics per fresh discovery round, with platform-specific query variants. A checkpoint continuation resumes that plan rather than generating five more on every process restart. Pending capacity and invocation budgets can reduce actual fresh work.
- Define novelty relative to the selected topics and recent retrieval history, and check synonyms against the full map. Strict irrelevance to every historical ancestor is not sustainable: algebraic geometry is new within mathematics but still related to a mathematics interest. Keep novelty/depth and profile familiarity as separate labels. Do not count a new mathematics subdomain as unrelated merely because its exact label is absent from the profile.
- Refine branches locally when repeated bounded proposals are duplicates, too similar or insufficiently useful; do not require the whole map to enter level 2 at once. Allow concepts to have multiple parents, preserving intersections such as Fourier analysis under mathematics and music. Parent links and a depth indicator support navigation without pretending that every subject has a unique universal level.
- Let the host AI propose a bounded set of candidates with a short explanation of novelty and useful English/Chinese search terms. Validate canonical names, aliases, parent relations and nonduplication before retrieval. Keep proposals pending; activate a node after a successful search yields plausible relevant material. Failed access and empty results remain distinct observations. When five usable additions cannot be established within budget, record a shortfall and reuse existing eligible nodes rather than force meaningless novelty or unlimited refinement.
- Preserve explicit retrieval provenance: selected node IDs, parent links, query variants, origin, proposal/review history, novelty explanation, results, requests and cost. Generation/search yield updates the coverage map; explicit user feedback updates preferences through the planned learning model. The existing 25/25 inspection target, conditional final minimum of two unrelated videos and pending guarantees remain separate policies unless the user changes them.

Selection uses a saved 63-bit random seed, sampling without replacement. At least six existing slots are drawn from interest-related branches when available; remaining draws use the whole eligible map. Related nodes have weight 3 versus 1, recent nodes are multiplied by 0.2, and all weights are divided by `sqrt(1 + previous search count)`. The recent window is 24 query observations across platforms. Ancestor/descendant pairs cannot occupy separate existing slots in one round. A cold start with seven interests can therefore have seven existing concepts, growing toward twelve after successful additions. Selection targets and deficiencies are reported rather than filled with duplicates.

The ChatGPT-authenticated Codex JSON runner generates bilingual queries and up to five additions, without images, tools or API-key fallback. It has at most two attempts per discovery round, each capped at 120 seconds and the remaining invocation time. Attempts, prompts, hashes, token usage and validation failures persist before continuation. A second attempt may refine active branches after a shortfall; refinement is also allowed initially after at least five recent new-topic queries have no anchor matches. New roots are preferred outside current interests and recent coverage; duplicate names/aliases are rejected against the entire map. Semantic novelty is an AI judgment, explicitly unverified. Context over 192 KiB pauses for map review rather than silently dropping historical aliases.

Successful anonymous source receipts and at least one title/description anchor match activate an addition. Empty or unmatched successful searches leave it unconfirmed; access failures leave its query pending. Neither outcome changes preferences. Unconfirmed nodes retain their provenance but are not sampled as active topics. Query plans interleave new concepts with existing concepts before creator expansion. `topics` inspects the private map; `discover --seed N` permits reproducible selection, and `--max-planning-calls 0..2` bounds generation. `continue` preserves selections, generated queries, attempts, source cursors and budgets.

Recommendation jobs share their model allowance with exploration: at most two planning attempts count against the configured `--max-assessments` total before new video assessments. Reports distinguish planning calls, assessment calls and their total. Reused discovery buffers consume no new planning calls in the current recommendation invocation. At 50 pending entries, discovery remains skipped. Evidence recovery for the historical 20 Bilibili failures is deferred at the user's request. Bayesian fitting and scheduling have not started.

## Phase 2: bounded evidence collection and quality assessment

Phase 2 is implemented and its six-video trial is complete: three videos per platform, with explanatory slides, restoration demonstrations, older material and an interdisciplinary science example. See [measured results and limitations](validation/PHASE2_VALIDATION.md). The implementation supports caption-only/augmented scorecards; all-candidate frame analysis remains unscaled pending review of this trial.

### Evidence and resource limits

1. Resolve the current playable video or Bilibili part, its duration and access status. Apply the 90-minute exclusion per playable unit before expensive analysis. Configure full-video access explicitly where needed; the user's reported Bilibili login requirement is a Phase 2 dependency. Listing metadata is not proof that full content is accessible.
2. Prefer available captions; use bounded local transcription when needed and feasible. Preserve timestamps and caption/ASR provenance. Inspect compact excerpts across the video, preserving the introduction, main argument and conclusion; expand relevant sections when a judgment needs more context. Missing or incomplete speech evidence limits the assessment.
3. Start visual inspection with four frames near 10%, 35%, 65% and 90% of the playable duration. Deduplicate near-identical frames locally. Timestamp every retained frame, preserve aspect ratio and initially resize to a maximum 768-pixel long edge. These are adjustable trial defaults, not a promise of sufficient coverage for every format.
4. Add up to four more frames or targeted higher-resolution views only to resolve a concrete uncertainty, such as unreadable slide text or a possible mismatch between caption content and visuals. The initial trial cap is eight submitted images per video, including replacement views. Do not repeatedly inspect nearly identical slides. A contact sheet may simplify presentation, but its image/token cost must be measured rather than assumed lower.
5. Cache evidence and assessments by video/part, media identity, extraction settings and rubric version. Keep the 30-minute recommendation-batch limit and checkpoint progress. Record download volume, extraction/transcription time, submitted image counts, text size, model calls and token usage where available. Label token estimates separately from observed usage; subscription limits are not assumed unlimited.

Implementation: yt-dlp resolves/downloads one playable unit; FFmpeg verifies actual duration and extracts frames; Pillow fingerprints frames; optional faster-whisper transcribes locally in a timeout-bounded worker. The worker supplies FFmpeg-decoded PCM directly to avoid the observed faster-whisper/PyAV decoder incompatibility. Defaults are 128 MiB media and 1,200 seconds per job, with at most two model calls and eight submitted images. Explicit targeted views can be added before augmented assessment, with a stated uncertainty and at most a 1536-pixel long edge.

Bilibili login uses an isolated persistent Playwright Edge/Chrome profile and the normal site entry, followed by explicit user confirmation and a verified login check. This flow was informed by [bili-watch](https://github.com/Carlown/bili-watch/blob/master/app.py). Session cookies are used only in memory by the platform fetcher and are not printed, exported for third-party services or included in model input. Native captions are requested explicitly; the adapter supports inline SRT, `ai-zh` tracks and timestamped player JSON. [bilibiliCCHelper](https://github.com/indefined/UserScripts/blob/master/bilibiliCCHelper/bilibiliCCHelper.user.js) helped verify the direct player-caption route. Tampermonkey is not required for extraction after sign-in. Kedou's public page was researched, but the implementation does not depend on it.

Caption-recovery follow-up, source-reviewed but not implemented: [Bili SubBatch 6.9.17](https://greasyfork.org/en/scripts/589638-bili-subbatch-loop-bilibili/code) adds ordinary player-v2 and dm-view queries after the WBI player query, plus an AI-subtitle URL lookup for a listed AI track with no URL. The installed yt-dlp extractor only uses the WBI subtitle-list route and drops tracks without URLs. Consider bounded recovery before local ASR when that route yields no usable captions; distinguish unavailable access from successful empty results, retain endpoint provenance, and stop on authentication/security failures. Validate each additional route live before claiming coverage. Cache bodies by video, resolved CID and language; signed track URLs must not become durable cache keys. Browser runtime metadata is an optional later fallback, with CID matching required. See the [source comparison](validation/PHASE2_VALIDATION.md#additional-subtitle-source-review).

The official Codex CLI uses the existing ChatGPT login, image attachments, a strict output schema, a read-only sandbox, disabled shell/browser tools and no API-key fallback. References must name actual supplied cues/frames, and failed calls consume the trial budget. Native-caption refresh preserves already-assessed inputs; completed scorecards are reused after hash verification. Numerical estimates remain provisional, with a separate local duration prior of at most five points.

Use a caption-only assessment as the baseline, then add visual evidence within these limits and record which judgments changed, which unknowns were resolved and the additional resource cost. This is a small incremental-benefit trial, not proof of a calibrated quality model. Do not scale frame analysis to all candidates until its benefit and cost have been reviewed. Sparse frames cannot establish the absence of filler or visual reuse throughout a video, and a compact transcript can omit important context.

### Quality criteria

Quality is assessed against the video's purpose. The following criteria preserve the user's preferences; they do not make a production style a universal quality rule.

| Criterion | Preferred evidence | Limits on the judgment |
| --- | --- | --- |
| Substance and relevance | Useful explanation, meaningful depth, relevant examples and support for the main claims | A title, popularity or length alone does not establish substance or factual accuracy |
| Directness and coherence | Clear progression, to-the-point delivery, little irrelevant material or needless repetition | Context, demonstrations and necessary repetition are not automatically filler; excerpts cannot establish whole-video density |
| Original contribution | Original reasoning, demonstrations, observations or purposeful synthesis with attribution | A few frames or captions cannot prove authorship, plagiarism or asset provenance; translated reposts are a soft predictor |
| Visual presentation | Purposeful visuals; prefer original demonstrations and explanatory material over unnecessary stock/meme montage | Static slides, diagrams and talking-head presentation can serve substantive content well; suspected stock or generated assets need evidence |
| Duration prior | A small initial preference rising through 60 minutes and flat from 60 to 90 minutes | At most five quality points; not proof that a longer video is better. More than 90 minutes is excluded; exactly 90 is eligible, per Bilibili part |

For each criterion, store observations separately from interpretation, with timestamps, evidence references, coverage limitations and confidence. Unsupported properties remain unknown rather than receiving an invented neutral or zero score. Retain a provisional overall quality estimate on a 0–100 scale with its uncertainty and rationale when evidence supports one; otherwise mark the video unassessed and request more evidence within budget. Do not imply numerical calibration before the development review.

The existing overall ranking weights remain quality 60%, interest 25%, engagement 5%, recency 5% and creator diversity 5%. The initial quality eligibility threshold remains 60/100, subject to explicit pilot review. Popularity, recency and personal interest must not be counted again as evidence of quality.

The current implementation asks for one holistic content judgment out of 95; the 0–4 criterion grades are supporting observations, not a weighted sum. It then adds `5 * min(duration_minutes / 60, 1)` length points. Thus content contributes at most 57 points and length at most three points to the final 100-point ranking. The dashboard exposes the saved criterion grades, rationale, content/length arithmetic and each final ranking contribution. Fixed numerical weights for substance, directness, originality and visuals are not implemented. Introducing them requires a new explicit rubric/version and cache compatibility policy; do not attribute invented weights to existing holistic scorecards.

Rubric version 2 contains only substance, directness, original contribution and visual presentation. Original scorecards remain reusable when retired criteria were unassessed; current reports and dashboard views show only active criteria. Reuse preserves the original numeric judgment, evidence hashes and model-call receipts.

### Deliverable and completion check

Produce evidence bundles and structured scorecards for the six-video trial, with a human-readable report comparing caption-only and augmented judgments and their resource costs. Verify playable-part durations, cache/recovery behavior and truthful handling of inaccessible or incomplete evidence on both platforms. Inspect the evidence behind favorable and unfavorable judgments, including narrow/interdisciplinary relevance. Completion requires usable content access and evidence-backed assessments on both platforms; a listing-only or failed-access record cannot substitute. Record unresolved originality questions without blocking every other assessable criterion.

Phase 2 does not implement the 10-video-per-platform recommendation lists, watched/pending workflow, user-profile fitting, Bayesian updates or scheduling. Those remain subsequent phases.

## Phase 3 recommendation pipeline after evidence validation

Run the following funnel separately for YouTube and Bilibili, on both daily and additional runs. The output target is 10 videos per platform, not 20 per platform. A batch of approximately 100 fresh distinct observed candidates is an initial collection target, not a hard maximum or a guarantee of 50 survivors; refill within the invocation budget when needed.

1. Load all previously recommended videos still pending: neither watched nor dismissed. Reserve one place for each in the 50-video inspection list, deduplicating rediscoveries into that existing record. These videos bypass fresh-candidate screening and compete with new candidates at full scoring.
2. Collect fresh candidates through curated search, creator expansion and Bilibili Home. Exclude watched or dismissed IDs and known hard-rule violations. Enrich inexpensive metadata with engagement counts and use the normalized engagement ratios below as primary early quality proxies, alongside relevance, duration and available cached observations, to discard candidates confidently expected to score poorly and fill the remaining inspection places. This is a conservative prefilter, not detailed quality measurement: unknown quality is not zero, popularity/age alone cannot establish quality, and low familiar-topic interest must not eliminate protected unfamiliar/intersection opportunities. Record rejection reasons and retain samples for false-negative audits. No compulsory media downloads or model quality calls belong to this stage.
3. Form at most 50 distinct inspection candidates from the guaranteed pending set plus the screened new set. Aim for 25 profile-related and 25 unrelated when the guaranteed entries permit it; allocate remaining slots toward the deficient group. Guaranteeing pending entries takes precedence over this target and the usual creator cap, with any imbalance reported. Unknown topics remain eligible as unrelated. Nonrecommended shortlist entries stay cached and may compete for fresh slots later, but do not receive guaranteed places.
4. Measure quality for new inspection candidates using the Phase 2 evidence policy, expanding uncertain cases within budget. Reuse valid cached evidence and quality assessments for pending videos rather than repeating the paired pilot experiment. Rescore every candidate against the current profile and policy. Keep missing evidence explicit and checkpoint unfinished assessment instead of substituting a fabricated quality score.
5. Apply quality eligibility and full-score selection to produce up to 10 qualifying videos, with at least two unrelated candidates when sufficient qualifying candidates exist. Creator diversity considers the developing slate. Return fewer than 10 with a reason if evidence, eligibility or work limits prevent completion; never relax hard rules merely to fill the list. Persist selection and pending-state changes atomically with the digest.

The Phase 1 preview selector remains retrieval scaffolding. The separate Phase 3 engine implements ratio screening, pending-slot reservations and quality-ranked recommendations without relabeling existing preview lists. Initial topic fit uses explicit onboarding interests and inherited exploration/curated domain tags, not a fitted profile or verified semantic content labels.

Implementation: SQLite schema 7 retains onboarding, video state, append-only user actions, exposure history and count snapshots and adds the exploration graph and search observations. Older schema versions are migrated, with a pre-migration backup when upgrading an existing database. `profile`, `recommend`, `video`, `queue`, `publish` and loopback-only `serve` expose the manual workflow. Platform job leases serialize competing recommendation jobs; watched/feedback actions remain usable during collection, and publication rechecks current state under a SQLite write transaction. A changed profile or newly restored pending entry may require a new run. Digests can be regenerated from committed state if file export is interrupted.

Each platform defaults to 160 metadata requests, up to 52 total model calls (at most two planning and fifty quality assessments), and 1 GiB media; both platforms share one 1,800-second batch allowance. Fresh discovery uses its separately recorded bounded request budget inside the same elapsed-time limit. Per-video evidence preparation is limited to the remaining time/bytes, at most 600 seconds and 128 MiB; assessments use one augmented call, reusing valid cached scorecards. Exhaustion checkpoints the cursor without resetting budgets. Explicit `publish RUN_ID --allow-incomplete` can save a provisional assessed-subset digest; a partial publication cannot satisfy Phase 3 readiness. New rounds reuse evidence while admitting new candidates within pending capacity.

### Performance and metadata originality policy

`recommend both --use-browser` coordinates the two platforms with one cumulative 1,800-second allowance. At most two local preparation processes and four independent GPT-6.1 Sol model workers run across that batch. Default reasoning is low; medium is an explicit option. The engine overlaps media preparation with model assessment, reserves model/byte allowances before dispatch, reconciles interrupted reservations once, and saves out-of-order completed results without advancing past a retryable gap. Metadata requests retain platform pacing. AI is used for the approved keyword-planning step and detailed quality assessment; cheap screening itself is deterministic.

Preparation runs in deadline-bounded owned process trees; isolated-session cookies are passed through an in-memory OS pipe, never a temporary credential file or command line. Model workers use the existing ChatGPT-authenticated CLI without API-key fallback. A cleanup margin is reserved before the batch limit. At deadline, incomplete work remains checkpointed; no claim that all 100 inspection videos or both final ten-video lists finished is made without matching saved results. A new round can reuse verified evidence. Cached metadata and scoring inputs remain versioned separately from mutable feedback.

Prefer low-resolution streams, with 720 pixels as the maximum short edge for newly downloaded video. There is no unrestricted high-resolution fallback. Existing verified higher-resolution files may be reused without another download. Four initial frames remain sufficient for the current bounded rubric; ASR is optional caption recovery. FFmpeg decoding and each ASR process use bounded CPU threads.

After a validated augmented assessment, delete the full video and obsolete download intermediates, preserving captions, selected frames, scorecards and a source-bound release receipt for cached scoring. Keep unfinished and baseline-only media for resumption or targeted frames. Missing source media without a release receipt remains invalid. Additional frames after release require an explicit new local-media job rather than an automatic download. `cleanup` previews older eligible files; `cleanup --apply` uses evidence leases and path checks to remove them. Optional ASR decodes at most ten minutes per chunk and releases each audio buffer before the next chunk. Preserve reusable models, authentication state, profile and action history. See [the implemented retention and memory policy](validation/STORAGE_MEMORY_AUDIT.md).

The exact Bilibili tag `视频翻译` is a hard metadata exclusion before fresh screening. Guaranteed pending entries retain their queue records but cannot be selected while excluded. Title `中配`, YouDub mentions/tags, linked source YouTube videos and uploader repost declarations are evidence for the original-contribution criterion. They are not automatic proof of synthetic narration, copyright infringement, missing authorization or absent original contribution. In particular, an external reference link by itself is not copying. The model receives cited M... metadata observations together with captions and frames, and must assess any substantive added explanation. These signals add no separate AI-voice score or fixed numerical penalty.

The combined Bilibili detail endpoint supplies archive counts, playable parts, title, description and tags in one request. Unknown tags remain unknown, not a verified empty list. Snapshots from the earlier metadata version are refreshed before screening. Changed substantive provenance gets a new evidence-context identity and may require a fresh quality call while reusing verified assets. Published historical scorecards are not silently rewritten. See [verification and measured performance](validation/PERFORMANCE_VALIDATION.md).

### Engagement ratios before the 50-video list

Use the following explicit rate features as important, inexpensive evidence of expected quality in fresh-candidate screening:

| Platform | Features |
| --- | --- |
| Bilibili | Likes / total views; coins / total views; favorites / total views |
| YouTube | Likes / total views |

Store raw nonnegative counts, retrieval timestamp, source, count scope and availability independently of derived rates. Numerator and denominator must describe the same video/archive and observation snapshot; do not mix archive totals with part-specific views. Bilibili archive-wide rates can inform a part's prior, but must be labeled shared archive evidence rather than an independently measured part rating. These are event rates, not necessarily proportions of distinct viewers or independent votes.

Do not rank by raw ratios alone. Start with empirical-Bayes rate shrinkage, `adjusted_rate = (count + k * cohort_rate) / (views + k)`, using a separately estimated baseline and prior exposure strength for each metric. This is a Gamma-Poisson working model with views as exposure; repeated viewing and correlated audience actions require conservative uncertainty/overdispersion treatment rather than an exact binomial interpretation. For example, one like in ten views should not automatically outrank 5,000 likes in 100,000 views. Zero/unknown views make the observed rate unavailable, not infinite or zero quality. Small audiences remain uncertain and eligible for protected exploration.

The initial implementation uses platform baselines, estimated by median observed rate when at least ten valid snapshots exist, otherwise explicit provisional defaults. Prior exposure strength is initially 1,000 views. Map shrunk rates relative to the baseline through a bounded logistic log-rate transform. Bilibili's composite gives equal weights to the three normalized metrics; YouTube uses the normalized like rate. Screening combines 70% community response and 30% title-anchor support within protected query-family/familiarity lanes. It retains creator caps and logs deferred/rejected IDs. Broad content-purpose/duration cohorts are a later calibration improvement, not currently fitted. Do not sum raw percentages or treat correlated likes/coins/favorites as three independent confirmations. No universal raw-rate cutoff should eliminate whole topics or unfamiliar candidates. Counts can reflect audience selection, calls to action or manipulation, so even a strong rate is not proof of substance or originality.

Use this community-response score for screening before expensive quality assessment, and once in the final engagement component. Preserve the existing final weights: quality 60%, interest 25%, engagement 5%, recency 5%, creator diversity 5%. The content-quality score remains based on the Phase 2 rubric and does not receive these same engagement points again. Thus ratios have a substantial role in allocating the 50 inspection opportunities while their final additive contribution remains the configured 5%; increasing that weight would be a separate policy change. Later quality-label validation can test whether the screening prior predicts quality, independently of user-interest feedback.

Collection is implemented as a bounded metadata-only enrichment stage over the fresh buffer before selecting the 50. YouTube watch-page metadata supplies exact views and available like counts; Bilibili's view endpoint supplies archive statistics and page/CID resolution. Snapshots are cached for 24 hours with response receipts. `--use-browser` explicitly supplies scoped isolated-session cookies to Bilibili metadata/content requests, labeled `isolated_browser_http`; discovery remains anonymous. An access error stops that metadata route and is recorded; missing counts stay neutral. No full evidence preparation or media download is used to obtain counts. Pending recommendations retain their guaranteed slots regardless of refresh success.

[YouTube's resource documentation](https://developers.google.com/youtube/v3/docs/videos#statistics) defines view/like counts; an optional Data API adapter would need explicit API configuration and quota accounting. No API key was used in this demo. The [Bilibili yt-dlp extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) demonstrates views/likes extraction from video metadata; our separate adapter also retains coins/favorites. The live trial found the anonymous Bilibili count API blocked with HTTP 412, whereas the explicitly authenticated profile returned all four counts for 100 candidates. Platform counting-definition changes require versioned observations and refreshed baselines, not an upload-date bonus.

### Watched state, feedback and pending capacity

Maintain a stable record per canonical playable video with separate watched state, dismissal state, feedback revision and recommendation exposures. Every digest item provides a stable reference for marking watched, undoing watched, dismissing, restoring and giving or revising thumbs-up/down feedback. Opening a link is not proof of watching. A vote does not implicitly mark the video watched or dismissed; the user can combine the actions explicitly. A watched video with no vote produces no preference label. An unwatched video with no vote remains unlabeled regardless of its age or number of exposures.

On first recommendation, an unwatched, undismissed video becomes pending. Remaining pending is sufficient for guaranteed inclusion in the next 50 on the next day or any additional run; it does not guarantee inclusion in the final 10. Previously exposed items may reappear and must be labeled as pending carryover. Watched and dismissed videos are suppressed across runs until the user explicitly reverses that state. Reversal restores pending status for a previously recommended item, subject to reported capacity handling. Feedback remains available for later Bayesian learning independently of these queue transitions.

The guaranteed set can consume the inspection list. For example, 30 pending entries leave 20 fresh inspection slots; 50 leave none. In the latter case, rerank the existing pending set and explain that new admissions require watching or dismissing entries. Do not silently expire, evict or turn pending videos into dislikes. Admit new recommendations only when the resulting pending set remains within 50 per platform; repeated exposures do not increase its size. If restored/imported state exceeds 50, preserve every record and pause final publication until the user chooses which entries to dismiss or defer; never silently break the guarantee. Newly unavailable or hard-ineligible pending items retain a visible blocked record, do not receive an eligible score, and do not justify recommending prohibited content.

Phase 3 implements persistence and manual actions for this workflow; Phase 4 fits the Bayesian models from explicit feedback. Daily scheduling in Phase 5 reuses the same state transitions and does not reset the queue at midnight.

| Score component | Default weight |
| --- | ---: |
| Quality | 60% |
| Interest | 25% |
| Engagement | 5% |
| Recency | 5% |
| Creator diversity | 5% |

Use the Phase 2 quality rubric, evidence uncertainties, duration prior and per-playable-unit exclusion. Missing evidence remains unknown. The provisional 60/100 quality threshold is subject to the Phase 2 pilot review.

Creator diversity considers the developing slate and recent recommendations. Engagement uses the likes/view ratio on YouTube and the likes/view, coins/view and favorites/view ratios on Bilibili, with the shrinkage, normalization and missing-data rules above; raw total views are not a second popularity reward. Use a slow recency decay. Never add a random final-score bonus.

Canonical YouTube video IDs and Bilibili archive/part IDs define deduplication and watched/dismissed suppression across all runs; AV and BV aliases normalize to the same archive. Prior recommendation alone no longer causes exclusion: pending carryovers are allowed repeated exposure. Reposts with different IDs receive originality judgments. Reuse valid cached evidence for both pending and never-recommended candidates and rescore against the current profile. This supersedes the earlier blanket prohibition on repeated recommendations in discovery research documents.

## Bayesian learning design

Keep content observations, learned distributions and fixed policy separate.

- Represent user interests, creator preferences, depth and presentation preferences using Bayesian logistic regression with hierarchical effects and informative, revisable priors from onboarding.
- Retain posterior uncertainty. Sparse creator preferences shrink toward the general prior rather than becoming confident after one vote.
- Learn feature-quality relationships from separately labeled quality feedback, including the development calibration set. Translated reposts start as a hypothesis, not a proven cause.
- Preserve the configured rising/plateau duration shape initially; its strength can be inferred within the configured nonnegative bound. The 90-minute cap remains a hard rule.
- Route reason-specific feedback to the appropriate model. An unexplained vote informs overall preference and does not independently establish poor quality or disinterest in every topic. Missing feedback is unlabeled.
- Use MAP estimation with a Laplace approximation for routine local posterior updates; rebuild from stored feedback batches so edits replace earlier observations. Check the approximation against a posterior-sampling reference during validation where needed.
- Keep embeddings and Codex feature extraction fixed initially. Version observations and retain their evidence uncertainty.
- Keep the 60/25/5/5/5 weights, target 25/25 shortlist subject to pending-slot guarantees, conditional minimum of two unrelated videos in each final 10, and hard exclusions fixed until explicitly changed.
- Version query curation and retrieval allocation separately from Bayesian profile updates. Preserve independent horizon/intersection effort. Votes are conditional on exposure; missing feedback cannot establish that a query family is poor.

Current discovery does not sample random video/user IDs. Bayesian inference estimates preference/quality parameters after evidence collection; it cannot recover material omitted by retrieval.

## Runtime architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the implemented package boundaries. Use a UV-managed Python core, SQLite and a dependency-free npm launcher. Explicit setup installs a bundled wheel into a versioned external runtime; npm lifecycle scripts are unnecessary. Keep state under `%LOCALAPPDATA%/deepvids`, with configurable data roots.

The engine owns discovery, evidence, deterministic scoring, feedback, profiles, history, job state and migrations. The Codex skill owns conversation and presentation. Add a Claude adapter later; defer MCP until needed.

The user authorizes the plugin's runtime AI to use subagents automatically for independent exploration and video analysis when host support and a time benefit exist. This is runtime authorization, not repository-development authorization. All child work shares the parent invocation's budgets and provenance requirements; the parent validates results and coordinates engine writes and publication. The current isolated `codex exec` assessment worker disables tools, so enabling delegation within that worker would require an explicit execution-path change; skill instructions alone do not implement it.

Use yt-dlp, FFmpeg and optional faster-whisper; prefer captions, otherwise bounded local transcription. Use authenticated official `codex exec`, image attachments and validated structured output with the existing subscription. Never copy OAuth credentials or silently switch to API billing. Begin anonymously; account access is explicitly configured by the user.

Save profile/history, watched/dismissed state, pending membership, recommendation exposures, observations, feedback revisions, run state, retrieval provenance and policy versions transactionally. Treat transcripts and descriptions as untrusted data. Deduplicate IDs within each list and serialize daily/additional publication so concurrent jobs cannot exceed pending capacity or admit watched/dismissed entries; repeated exposure of an existing pending item is intentional.

## Validation and milestones

1. Curated live discovery, protected narrow/interdisciplinary opportunities, creator expansion, provenance and three verified rounds per platform.
2. Evidence and quality assessment for discovered videos, including explicit Bilibili playback access and per-part durations.
3. Onboarding and the complete manual recommendation funnel: fresh collection and cheap screening, 50 inspection candidates including guaranteed pending entries, evidence/full scoring, 10 recommendations per platform, digests, watched/dismissed actions, stored feedback and more rounds.
4. Bayesian learning and roughly 30 development videos rated separately for interest and quality, with held-out checks and rejected-candidate audits.
5. Validate scheduling and Windows local/npm installations with scripts disabled, unrelated working directories, spaces in paths, read-only package directories and upgrades preserving history.

Track quality agreement, satisfaction for familiar/unfamiliar topics, first-pass false negatives, engagement-count coverage and ratio calibration, new versus pending exposures, prohibited watched/dismissed repeats, queue occupancy, runtime, subscription usage, downloaded bytes, transcription time and completion yield. Test small/zero denominators, missing metrics, mismatched archive/part scope, extreme rates, snapshot caching and request limits, plus carryover after a day change and additional runs, full pending capacity, independent watch/vote actions, undo/revisions, cache reuse and concurrent publication. Do not evaluate on feedback already used for fitting.

## Research and reuse

- YouTube sampler and MIT attribution: https://github.com/iDPI-Umass/youtubescripts
- Sampling evidence and limitations: https://journalqd.org/article/view/4066 and https://www.cogitatiopress.com/mediaandcommunication/article/view/12085
- AV/BV implementation domain: https://github.com/Colerar/abv
- Metropolis construction: https://www.stat.berkeley.edu/~aldous/RWG/Book_Ralph/Ch11.S2.html
- Bayesian/hierarchical regression: https://mc-stan.org/docs/stan-users-guide/regression.html
- Audit selected workflow patterns from OpenBiliClaw and Worthwatch; retain official Codex execution rather than copying authentication code.

