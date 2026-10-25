# Deepvids implementation plan

Status: Phases 1 and 2 are implemented and verified on `probe-phase-2`; see [discovery verification](PHASE1_VALIDATION.md), [the evidence trial](PHASE2_VALIDATION.md) and the [roadmap](PHASES.md). Phase 3 has not started. Earlier sampling and dated-catalog routes remain superseded research.

The user accepted curated keyword search mixed with creators on YouTube, adding isolated-session Home on Bilibili. The [research design](PROBE_DISCOVERY.md) is merged below. Historical catalogs remain test fixtures and cannot satisfy readiness.

## Product decisions

- Build a local, publicly installable Codex plugin first, with a shared engine for a later Claude Code adapter.
- Produce separate lists of 10 YouTube and 10 Bilibili videos per daily batch or additional round, selected from 50 inspection candidates per platform.
- Keep previously recommended videos in a persistent pending queue until explicitly marked watched or dismissed. Pending recommendations have guaranteed places in the next inspection list and can be recommended again.
- Record watched state, dismissal and editable thumbs-up/down feedback separately; absence of watching or feedback is not a negative preference observation.
- Validate manual runs first; later offer opt-in daily scheduling through Windows Task Scheduler.
- Present numbered links, explanations, confidence, and feedback instructions in Codex; save Markdown digests locally.
- Ship shared configurable defaults; keep personal profiles, credentials, histories, models and caches outside the package.
- Require both platforms to pass live discovery and evidence-access validation before releasing v1.
- Bound each invocation to 60 minutes and checkpoint incomplete work; an explicit continue command resumes it.
- Once scheduling is enabled, replace unfinished older daily jobs on the next date while retaining their observations, recommendation history and unresolved pending recommendations.

## Phase 1: curated live discovery

Use anonymous public web listings without inherited personal cookies or account history. YouTube uses focused search and creator video tabs with continuation. Logged-out Home is excluded as a candidate source; its bootstrap page only supplies the public web client version. Bilibili uses public Home/search in a fresh in-memory cookie jar. Parse rendered cards and supported serialized state as data, never executing page scripts. These public web schemas are unofficial interfaces and can change.

Bilibili's direct recommendation API returned code 62011 and signed creator uploads returned HTTP 412 in preliminary checks. Those routes are stopped. The implemented fallback searches discovered creator names and retains only returned owner-ID matches. Label it `creator_search`: a search-index subset, not complete archive enumeration. Full archive access remains unverified; this limitation does not prevent collecting candidates from accepted public search/Home sources.

Maintain an editable, versioned bank of specific concepts and meaningful cross-domain connections, with purpose, domains, aliases and English/Chinese variants. The initial broad development curriculum has 12 families: six intersections, four deep topics and two horizon topics. It is not an inferred personal profile. Examples include Banach spaces and finance, entropy and inference, music and Fourier analysis, and materials and history. No model API is needed; future assisted suggestions must be checked against actual results.

Interleave intents and rotate family order/query variants across rounds. Collect a bounded metadata buffer before selecting 50 inspection candidates. Within each source/family bucket, prefer title/description anchor matches and local result rank, then select round-robin across buckets with a maximum of three per creator. Preserve niche opportunities regardless of global popularity or how many queries return the same video. Intent tags and anchors are hints, not verified relevance or quality. The initial buffer cap is 1,000 distinct videos, with at most 40 retained per returned page; report truncation.

Persist candidate/creator observations, all source memberships, response hashes, request IDs, bank hash, round, publication labels and pagination state in SQLite. Write buffer JSON, shortlist JSON and linked Markdown. A new round rotates the query plan; `continue` preserves pending work and remaining budget. Completed-run continuation tokens are retained for inspection, not automatically reused indefinitely.

Default limits: 100 HTTP attempts and 600 seconds, at least one second between requests, 4 MiB limits on compressed and decompressed responses. Overrides cannot exceed 1,000 attempts or 3,600 seconds. Stop on access errors or unknown schemas; never infer empty results from failed access. Temporary visitor cookies are discarded between invocations. No authentication, full-video playback or media download is needed in Phase 1.

Completion requires three distinct completed rounds of one query-bank version per platform. Each needs at least 100 source-receipted candidates, 50 distinct preview candidates, search and creator expansion, all three intents, at least six observed query families and at least three intersection preview opportunities. Bilibili additionally requires Home candidates. Historical fixtures and synthetic tests cannot substitute for live evidence in the reference workspace.

Known YouTube durations above 90 minutes are excluded from previews. Bilibili listing duration may describe a multipart archive; playable-part eligibility remains unknown until Phase 2. The user reports full Bilibili playback requires login: configure that explicitly in Phase 2, without reusing personal cookies for Phase 1.

Evaluate curation with narrow, interdisciplinary, older and smaller-creator reference examples, holding some out of query construction. Measure retrieval into the buffer separately from selection into the 50. Regression checks cover niche preservation and source isolation; semantic usefulness and substantive quality require the Phase 2 evidence pilot. Global uniformity and exhaustive platform recall are not goals.

## Earlier platform-wide sampling experiments

The following describes the superseded experiment, not current product requirements.

1. Prototype the published YouTube five-letter prefix sampler, including its sixth-character hyphen restriction, without inheriting its concurrent append-until-full stopping bias.
2. Prototype Bilibili Metropolis sampling over numeric archive IDs using symmetric multiscale offsets and a global proposal; target archive mass proportional to its part count and sample one part uniformly.
3. Preserve rejection self-loops, proposal order and unresolved requests; preferences, quality, duration and engagement never alter raw discovery.
4. Run three separately seeded sessions per platform, each at most 60 minutes or 1,000 HTTP attempts. Use four dispersed Bilibili starting archives with provenance; initial states are not an unbiased sample.
5. Validate correctness against finite synthetic populations; measure effective yield, dependence, seed sensitivity, truncation, access failures and observed distributions.

The prototype domain `1 <= aid < 2**51` follows a third-party AV/BV implementation, not an official completeness guarantee. Metadata-visible Bilibili parts are only a provisional population until playback accessibility is established. YouTube search and playability responses also have access and indexing limitations.

These experiments failed their original gate; the later catalog route was rejected because of source age. Curated live retrieval replaces both. Any future platform-wide claim still needs independent coverage evidence. Content-evidence access is tested in Phase 2.

## Phase 2: bounded evidence collection and quality assessment

Phase 2 is implemented and its six-video trial is complete: three videos per platform, with explanatory slides, restoration demonstrations, older material and an interdisciplinary science example. See [measured results and limitations](PHASE2_VALIDATION.md). The implementation supports caption-only/augmented scorecards; all-candidate frame analysis remains unscaled pending review of this trial.

### Evidence and resource limits

1. Resolve the current playable video or Bilibili part, its duration and access status. Apply the 90-minute exclusion per playable unit before expensive analysis. Configure full-video access explicitly where needed; the user's reported Bilibili login requirement is a Phase 2 dependency. Listing metadata is not proof that full content is accessible.
2. Prefer available captions; use bounded local transcription when needed and feasible. Preserve timestamps and caption/ASR provenance. Inspect compact excerpts across the video, preserving the introduction, main argument and conclusion; expand relevant sections when a judgment needs more context. Missing or incomplete speech evidence limits the assessment.
3. Start visual inspection with four frames near 10%, 35%, 65% and 90% of the playable duration. Deduplicate near-identical frames locally. Timestamp every retained frame, preserve aspect ratio and initially resize to a maximum 768-pixel long edge. These are adjustable trial defaults, not a promise of sufficient coverage for every format.
4. Add up to four more frames or targeted higher-resolution views only to resolve a concrete uncertainty, such as unreadable slide text or a possible mismatch between narration and visuals. The initial trial cap is eight submitted images per video, including replacement views. Do not repeatedly inspect nearly identical slides. A contact sheet may simplify presentation, but its image/token cost must be measured rather than assumed lower.
5. Assess voice characteristics only if audio can actually be inspected with suitable local or available subscription tools. Captions, ASR text and frames do not establish whether narration is human or synthetic. If this capability or adequate evidence is absent, narration type remains unknown; do not introduce a paid API merely to fill the field.
6. Cache evidence and assessments by video/part, media identity, extraction settings and rubric version. Keep the existing 60-minute invocation limit and checkpoint progress. Record download volume, extraction/transcription time, submitted image counts, text size, model calls and token usage where available. Label token estimates separately from observed usage; subscription limits are not assumed unlimited.

Implementation: yt-dlp resolves/downloads one playable unit; FFmpeg verifies actual duration and extracts frames; Pillow fingerprints frames; optional faster-whisper transcribes locally in a timeout-bounded worker. The worker supplies FFmpeg-decoded PCM directly to avoid the observed faster-whisper/PyAV decoder incompatibility. Defaults are 128 MiB media and 1,200 seconds per job, with at most two model calls and eight submitted images. Explicit targeted views can be added before augmented assessment, with a stated uncertainty and at most a 1536-pixel long edge.

Bilibili login uses an isolated persistent Playwright Edge/Chrome profile and the normal site entry, followed by explicit user confirmation and a verified login check. This flow was informed by [bili-watch](https://github.com/Carlown/bili-watch/blob/master/app.py). Session cookies are used only in memory by the platform fetcher and are not printed, exported for third-party services or included in model input. Native captions are requested explicitly; the adapter supports inline SRT, `ai-zh` tracks and timestamped player JSON. [bilibiliCCHelper](https://github.com/indefined/UserScripts/blob/master/bilibiliCCHelper/bilibiliCCHelper.user.js) helped verify the direct player-caption route. Tampermonkey is not required for extraction after sign-in. Kedou's public page was researched, but the implementation does not depend on it.

Caption-recovery follow-up, source-reviewed but not implemented: [Bili SubBatch 6.9.17](https://greasyfork.org/en/scripts/589638-bili-subbatch-loop-bilibili/code) adds ordinary player-v2 and dm-view queries after the WBI player query, plus an AI-subtitle URL lookup for a listed AI track with no URL. The installed yt-dlp extractor only uses the WBI subtitle-list route and drops tracks without URLs. Consider bounded recovery before local ASR when that route yields no usable captions; distinguish unavailable access from successful empty results, retain endpoint provenance, and stop on authentication/security failures. Validate each additional route live before claiming coverage. Cache bodies by video, resolved CID and language; signed track URLs must not become durable cache keys. Browser runtime metadata is an optional later fallback, with CID matching required. See the [source comparison](PHASE2_VALIDATION.md#additional-subtitle-source-review).

The official Codex CLI uses the existing ChatGPT login, image attachments, a strict output schema, a read-only sandbox, disabled shell/browser tools and no API-key fallback. Narration identity is forced unknown without audio evidence, references must name actual supplied cues/frames, and failed calls consume the trial budget. Native-caption refresh preserves already-assessed inputs; completed scorecards are reused after hash verification. Numerical estimates remain provisional, with a separate local duration prior of at most five points.

Use a caption-only assessment as the baseline, then add visual evidence within these limits and record which judgments changed, which unknowns were resolved and the additional resource cost. This is a small incremental-benefit trial, not proof of a calibrated quality model. Do not scale frame analysis to all candidates until its benefit and cost have been reviewed. Sparse frames cannot establish the absence of filler or visual reuse throughout a video, and a compact transcript can omit important context.

### Quality criteria

Quality is assessed against the video's purpose. The following criteria preserve the user's preferences; they do not make a production style a universal quality rule.

| Criterion | Preferred evidence | Limits on the judgment |
| --- | --- | --- |
| Substance and relevance | Useful explanation, meaningful depth, relevant examples and support for the main claims | A title, popularity or length alone does not establish substance or factual accuracy |
| Directness and coherence | Clear progression, to-the-point delivery, little irrelevant material or needless repetition | Context, demonstrations and necessary repetition are not automatically filler; excerpts cannot establish whole-video density |
| Original contribution | Original reasoning, demonstrations, observations or purposeful synthesis with attribution | A few frames or captions cannot prove authorship, plagiarism or asset provenance; translated reposts are a soft predictor |
| Narration | Prefer human narration under the user's stated taste; assess clarity and whether speech serves the explanation | Synthetic narration is an uncertain soft predictor, not a hard exclusion; leave its type unknown without adequate audio evidence |
| Visual presentation | Purposeful visuals; prefer original demonstrations and explanatory material over unnecessary stock/meme montage | Static slides, diagrams and talking-head presentation can serve substantive content well; suspected stock or generated assets need evidence |
| Duration prior | A small initial preference rising through 60 minutes and flat from 60 to 90 minutes | At most five quality points; not proof that a longer video is better. More than 90 minutes is excluded; exactly 90 is eligible, per Bilibili part |

For each criterion, store observations separately from interpretation, with timestamps, evidence references, coverage limitations and confidence. Unsupported properties remain unknown rather than receiving an invented neutral or zero score. Retain a provisional overall quality estimate on a 0–100 scale with its uncertainty and rationale when evidence supports one; otherwise mark the video unassessed and request more evidence within budget. Do not imply numerical calibration before the development review.

The existing overall ranking weights remain quality 60%, interest 25%, engagement 5%, recency 5% and creator diversity 5%. The initial quality eligibility threshold remains 60/100, subject to explicit pilot review. Popularity, recency and personal interest must not be counted again as evidence of quality.

### Deliverable and completion check

Produce evidence bundles and structured scorecards for the six-video trial, with a human-readable report comparing caption-only and augmented judgments and their resource costs. Verify playable-part durations, cache/recovery behavior and truthful handling of inaccessible or incomplete evidence on both platforms. Inspect the evidence behind favorable and unfavorable judgments, including narrow/interdisciplinary relevance. Completion requires usable content access and evidence-backed assessments on both platforms; a listing-only or failed-access record cannot substitute. Record unresolved narration/originality questions without blocking every other assessable criterion.

Phase 2 does not implement the 10-video-per-platform recommendation lists, watched/pending workflow, user-profile fitting, Bayesian updates or scheduling. Those remain subsequent phases.

## Phase 3 recommendation pipeline after evidence validation

Run the following funnel separately for YouTube and Bilibili, on both daily and additional runs. The output target is 10 videos per platform, not 20 per platform. A batch of approximately 100 fresh distinct observed candidates is an initial collection target, not a hard maximum or a guarantee of 50 survivors; refill within the invocation budget when needed.

1. Load all previously recommended videos still pending: neither watched nor dismissed. Reserve one place for each in the 50-video inspection list, deduplicating rediscoveries into that existing record. These videos bypass fresh-candidate screening and compete with new candidates at full scoring.
2. Collect fresh candidates through curated search, creator expansion and Bilibili Home. Exclude watched or dismissed IDs and known hard-rule violations. Use inexpensive metadata and available cached observations to discard candidates confidently expected to score poorly and fill the remaining inspection places. This is a conservative prefilter, not detailed quality measurement: unknown quality is not zero, popularity/age alone cannot establish quality, and low familiar-topic interest must not eliminate protected unfamiliar/intersection opportunities. Record rejection reasons and retain samples for false-negative audits. No compulsory media downloads or model quality calls belong to this stage.
3. Form at most 50 distinct inspection candidates from the guaranteed pending set plus the screened new set. Aim for 25 profile-related and 25 unrelated when the guaranteed entries permit it; allocate remaining slots toward the deficient group. Guaranteeing pending entries takes precedence over this target and the usual creator cap, with any imbalance reported. Unknown topics remain eligible as unrelated. Nonrecommended shortlist entries stay cached and may compete for fresh slots later, but do not receive guaranteed places.
4. Measure quality for new inspection candidates using the Phase 2 evidence policy, expanding uncertain cases within budget. Reuse valid cached evidence and quality assessments for pending videos rather than repeating the paired pilot experiment. Rescore every candidate against the current profile and policy. Keep missing evidence explicit and checkpoint unfinished assessment instead of substituting a fabricated quality score.
5. Apply quality eligibility and full-score selection to produce up to 10 qualifying videos, with at least two unrelated candidates when sufficient qualifying candidates exist. Creator diversity considers the developing slate. Return fewer than 10 with a reason if evidence, eligibility or work limits prevent completion; never relax hard rules merely to fill the list. Persist selection and pending-state changes atomically with the digest.

The existing Phase 1 preview selector is retrieval scaffolding; it does not yet implement learned screening, pending-slot reservations or quality-ranked recommendations. Phase 3 adds these behaviors without relabeling existing preview lists.

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

Creator diversity considers the developing slate and recent recommendations. Normalize engagement within platforms, shrink noisy small-count signals, and treat missing metrics neutrally. Use a slow recency decay. Never add a random final-score bonus.

Canonical YouTube video IDs and Bilibili archive/part IDs define deduplication and watched/dismissed suppression across all runs; AV and BV aliases normalize to the same archive. Prior recommendation alone no longer causes exclusion: pending carryovers are allowed repeated exposure. Reposts with different IDs receive originality judgments. Reuse valid cached evidence for both pending and never-recommended candidates and rescore against the current profile. This supersedes the earlier blanket prohibition on repeated recommendations in discovery research documents.

## Bayesian learning design

Keep content observations, learned distributions and fixed policy separate.

- Represent user interests, creator preferences, depth and presentation preferences using Bayesian logistic regression with hierarchical effects and informative, revisable priors from onboarding.
- Retain posterior uncertainty. Sparse creator preferences shrink toward the general prior rather than becoming confident after one vote.
- Learn feature-quality relationships from separately labeled quality feedback, including the development calibration set. AI narration and translated reposts start as hypotheses, not proven causes.
- Preserve the configured rising/plateau duration shape initially; its strength can be inferred within the configured nonnegative bound. The 90-minute cap remains a hard rule.
- Route reason-specific feedback to the appropriate model. An unexplained vote informs overall preference and does not independently establish poor quality or disinterest in every topic. Missing feedback is unlabeled.
- Use MAP estimation with a Laplace approximation for routine local posterior updates; rebuild from stored feedback batches so edits replace earlier observations. Check the approximation against a posterior-sampling reference during validation where needed.
- Keep embeddings and Codex feature extraction fixed initially. Version observations and retain their evidence uncertainty.
- Keep the 60/25/5/5/5 weights, target 25/25 shortlist subject to pending-slot guarantees, conditional minimum of two unrelated videos in each final 10, and hard exclusions fixed until explicitly changed.
- Version query curation and retrieval allocation separately from Bayesian profile updates. Preserve independent horizon/intersection effort. Votes are conditional on exposure; missing feedback cannot establish that a query family is poor.

Current discovery does not sample random video/user IDs. Bayesian inference estimates preference/quality parameters after evidence collection; it cannot recover material omitted by retrieval.

## Runtime architecture after the gate

Use a UV-managed Python core, SQLite and a dependency-free npm launcher. Explicit setup installs a bundled wheel into a versioned external runtime; npm lifecycle scripts are unnecessary. Keep state under `%LOCALAPPDATA%/deepvids`, with configurable data roots.

The engine owns discovery, evidence, deterministic scoring, feedback, profiles, history, job state and migrations. The Codex skill owns conversation and presentation. Add a Claude adapter later; defer MCP until needed.

Use yt-dlp, FFmpeg and optional faster-whisper; prefer captions, otherwise bounded local transcription. Use authenticated official `codex exec`, image attachments and validated structured output with the existing subscription. Never copy OAuth credentials or silently switch to API billing. Begin anonymously; account access is explicitly configured by the user.

Save profile/history, watched/dismissed state, pending membership, recommendation exposures, observations, feedback revisions, run state, retrieval provenance and policy versions transactionally. Treat transcripts and descriptions as untrusted data. Deduplicate IDs within each list and serialize daily/additional publication so concurrent jobs cannot exceed pending capacity or admit watched/dismissed entries; repeated exposure of an existing pending item is intentional.

## Validation and milestones

1. Curated live discovery, protected narrow/interdisciplinary opportunities, creator expansion, provenance and three verified rounds per platform.
2. Evidence and quality assessment for discovered videos, including explicit Bilibili playback access and per-part durations.
3. Onboarding and the complete manual recommendation funnel: fresh collection and cheap screening, 50 inspection candidates including guaranteed pending entries, evidence/full scoring, 10 recommendations per platform, digests, watched/dismissed actions, stored feedback and more rounds.
4. Bayesian learning and roughly 30 development videos rated separately for interest and quality, with held-out checks and rejected-candidate audits.
5. Validate scheduling and Windows local/npm installations with scripts disabled, unrelated working directories, spaces in paths, read-only package directories and upgrades preserving history.

Track quality agreement, satisfaction for familiar/unfamiliar topics, first-pass false negatives, new versus pending exposures, prohibited watched/dismissed repeats, queue occupancy, runtime, subscription usage, downloaded bytes, transcription time and completion yield. Test carryover after a day change and additional runs, full pending capacity, independent watch/vote actions, undo/revisions, cache reuse and concurrent publication. Do not evaluate on feedback already used for fitting.

## Research and reuse

- YouTube sampler and MIT attribution: https://github.com/iDPI-Umass/youtubescripts
- Sampling evidence and limitations: https://journalqd.org/article/view/4066 and https://www.cogitatiopress.com/mediaandcommunication/article/view/12085
- AV/BV implementation domain: https://github.com/Colerar/abv
- Metropolis construction: https://www.stat.berkeley.edu/~aldous/RWG/Book_Ralph/Ch11.S2.html
- Bayesian/hierarchical regression: https://mc-stan.org/docs/stan-users-guide/regression.html
- Audit selected workflow patterns from OpenBiliClaw and Worthwatch; retain official Codex execution rather than copying authentication code.

