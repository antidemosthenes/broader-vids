# Deepvids implementation plan

Status: Phase 1 is incomplete and work stops at discovery after rejection of the dated sources. Phase 2 has not started. See [the reassessment](DISCOVERY_REASSESSMENT.md). Follow the [five-phase roadmap](PHASES.md), completing one phase at a time. The earlier platform-wide sampling failures are recorded in [the findings](FEASIBILITY.md).

Historical catalog imports are retained only as test fixtures and cannot satisfy readiness. No replacement is integrated. Later-phase designs below remain available for a possible resumption.

## Product decisions

- Build a local, publicly installable Codex plugin first, with a shared engine for a later Claude Code adapter.
- Produce separate lists of 20 YouTube and 20 Bilibili videos per daily batch or additional round.
- Validate manual runs first; later offer opt-in daily scheduling through Windows Task Scheduler.
- Present numbered links, explanations, confidence, and feedback instructions in Codex; save Markdown digests locally.
- Ship shared configurable defaults; keep personal profiles, credentials, histories, models and caches outside the package.
- Require both platforms to pass sampling and evidence-access validation before releasing v1.
- Bound each invocation to 60 minutes and checkpoint incomplete work; an explicit continue command resumes it.
- Once scheduling is enabled, replace unfinished older daily batches on the next date, retaining observations and completed recommendation history.

## Phase 1: catalog-backed discovery

First establish accepted current discovery sources or efficient validated samplers for both platforms, auditing coverage, data dates, refresh behavior, selection bias and cost. ChinaOpen and YouNiverse import support is now only a fixture implementation. Sample linked videos uniformly without replacement from contiguous catalog ordinals. Bilibili links without a part query refer to the default first part; other parts require explicit enumeration during evidence preparation.

The first exercise uses the complete ChinaOpen URL-list response and an 8 MiB / 2,000-row YouNiverse prefix preview. The prefix is only a development catalog. Full local-file imports use a published checksum when claiming a complete source snapshot. Both platforms need at least 100 distinct linked items from an accepted discovery population and a saved draw before Phase 1 is complete; the rejected historical fixtures cannot qualify. Full-domain ID guessing is impractical within the invocation budget. Quality assessment, recommendation generation and Bayesian updates remain later phases.

## Earlier platform-wide sampling experiments

Approximate platform-wide discovery is acceptable with documented limitations and supporting evidence; exact global uniformity is not claimed.

1. Prototype the published YouTube five-letter prefix sampler, including its sixth-character hyphen restriction, without inheriting its concurrent append-until-full stopping bias.
2. Prototype Bilibili Metropolis sampling over numeric archive IDs using symmetric multiscale offsets and a global proposal; target archive mass proportional to its part count and sample one part uniformly.
3. Preserve rejection self-loops, proposal order and unresolved requests; preferences, quality, duration and engagement never alter raw discovery.
4. Run three separately seeded sessions per platform, each at most 60 minutes or 1,000 HTTP attempts. Use four dispersed Bilibili starting archives with provenance; initial states are not an unbiased sample.
5. Validate correctness against finite synthetic populations; measure effective yield, dependence, seed sensitivity, truncation, access failures and observed distributions.

The prototype domain `1 <= aid < 2**51` follows a third-party AV/BV implementation, not an official completeness guarantee. Metadata-visible Bilibili parts are only a provisional population until playback accessibility is established. YouTube search and playability responses also have access and indexing limitations.

These experiments did not pass their original feasibility gate. The later historical catalog route was also rejected because of source age. Neither route currently satisfies Phase 1. Any future platform-wide claim still requires independent coverage evidence. Current content-evidence access is tested in Phase 2 before recommendation generation.

## Phase 3 recommendation pipeline after evidence validation

For each platform, draw batches of 100 raw events, discard already recommended canonical IDs and hard-rule violations, then accumulate 50 distinct survivors: 25 profile-related and 25 unrelated. Unknown topics count as unrelated and remain eligible.

Use cheap metadata/caption proxies conservatively before inspecting all 50 with compact transcripts and distributed frames. Expand evidence for promising uncertain cases. Select 20 qualifying videos, including at least four unrelated candidates when sufficient qualifying candidates exist, then order the selected list by full score. Refill insufficient pools and checkpoint at the work limit.

| Score component | Default weight |
| --- | ---: |
| Quality | 60% |
| Interest | 25% |
| Engagement | 5% |
| Recency | 5% |
| Creator diversity | 5% |

Quality is purpose-aware: original contribution, substance, coherence, density and purposeful production. AI narration, generated visuals, stock assets, memes and translated reposts are uncertain soft predictors. Missing evidence is unknown, not an invented observation.

Duration contributes an initial soft prior of at most five quality points: rise linearly through 60 minutes, plateau through 90 minutes. Exclude individual playable videos longer than 90 minutes; exactly 90 minutes is eligible. Apply the rule per Bilibili part, not per combined archive. The initial quality eligibility threshold is 60/100, subject to explicit pilot review.

Creator diversity considers the developing slate and recent recommendations. Normalize engagement within platforms, shrink noisy small-count signals, and treat missing metrics neutrally. Use a slow recency decay. Never add a random final-score bonus.

Canonical YouTube video IDs and Bilibili archive/part IDs define repeat suppression across all runs; AV and BV aliases normalize to the same archive. Reposts with different IDs receive originality judgments. Reuse cached evidence for never-recommended candidates and rescore against the current profile.

## Bayesian learning design

Keep content observations, learned distributions and fixed policy separate.

- Represent user interests, creator preferences, depth and presentation preferences using Bayesian logistic regression with hierarchical effects and informative, revisable priors from onboarding.
- Retain posterior uncertainty. Sparse creator preferences shrink toward the general prior rather than becoming confident after one vote.
- Learn feature-quality relationships from separately labeled quality feedback, including the development calibration set. AI narration and translated reposts start as hypotheses, not proven causes.
- Preserve the configured rising/plateau duration shape initially; its strength can be inferred within the configured nonnegative bound. The 90-minute cap remains a hard rule.
- Route reason-specific feedback to the appropriate model. An unexplained vote informs overall preference and does not independently establish poor quality or disinterest in every topic. Missing feedback is unlabeled.
- Use MAP estimation with a Laplace approximation for routine local posterior updates; rebuild from stored feedback batches so edits replace earlier observations. Check the approximation against a posterior-sampling reference during validation where needed.
- Keep embeddings and Codex feature extraction fixed initially. Version observations and retain their evidence uncertainty.
- Keep the 60/25/5/5/5 weights, 25/25 shortlist, conditional discovery minimum and hard exclusions fixed until explicitly changed.
- Tune raw-discovery proposal parameters only during dedicated sampling experiments; freeze them during retained sampling. User feedback never personalizes raw discovery.

The Bilibili discovery chain samples video IDs; Bayesian inference estimates model parameters. These are separate uses of probability and neither replaces the other.

## Runtime architecture after the gate

Use a UV-managed Python core, SQLite and a dependency-free npm launcher. Explicit setup installs a bundled wheel into a versioned external runtime; npm lifecycle scripts are unnecessary. Keep state under `%LOCALAPPDATA%/deepvids`, with configurable data roots.

The engine owns discovery, evidence, deterministic scoring, feedback, profiles, history, job state and migrations. The Codex skill owns conversation and presentation. Add a Claude adapter later; defer MCP until needed.

Use yt-dlp, FFmpeg and optional faster-whisper; prefer captions, otherwise bounded local transcription. Use authenticated official `codex exec`, image attachments and validated structured output with the existing subscription. Never copy OAuth credentials or silently switch to API billing. Begin anonymously; account access is explicitly configured by the user.

Save profile/history, observations, feedback revisions, run state, sampling provenance and policy versions transactionally. Treat transcripts and descriptions as untrusted data. Ensure concurrent daily/additional jobs cannot publish duplicate IDs.

## Validation and milestones

1. Catalog-backed candidate discovery, source provenance and reproducible draws; preserve the earlier sampling research.
2. Evidence collection and quality assessment for real videos from both catalogs.
3. Onboarding and the complete manual recommendation funnel, digests, history and more rounds.
4. Bayesian learning and roughly 30 development videos rated separately for interest and quality, with held-out checks and rejected-candidate audits.
5. Validate scheduling and Windows local/npm installations with scripts disabled, unrelated working directories, spaces in paths, read-only package directories and upgrades preserving history.

Track quality agreement, satisfaction for familiar/unfamiliar topics, first-pass false negatives, repeats, runtime, subscription usage, downloaded bytes, transcription time and completion yield. Do not evaluate on feedback already used for fitting.

## Research and reuse

- YouTube sampler and MIT attribution: https://github.com/iDPI-Umass/youtubescripts
- Sampling evidence and limitations: https://journalqd.org/article/view/4066 and https://www.cogitatiopress.com/mediaandcommunication/article/view/12085
- AV/BV implementation domain: https://github.com/Colerar/abv
- Metropolis construction: https://www.stat.berkeley.edu/~aldous/RWG/Book_Ralph/Ch11.S2.html
- Bayesian/hierarchical regression: https://mc-stan.org/docs/stan-users-guide/regression.html
- Audit selected workflow patterns from OpenBiliClaw and Worthwatch; retain official Codex execution rather than copying authentication code.

