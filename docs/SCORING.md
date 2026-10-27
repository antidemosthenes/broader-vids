# Phase 4 scoring and consistency contract

Phase 4 learns conditional approval and structured response-category probabilities. It does not claim to recover an objective, universal video-quality score, unseen subject preferences or uniquely identifiable weights from insufficient observations. Existing scorecards and published lists remain historical records.

## Scores and priors

Hard eligibility remains separate: verified playable duration at most 90 minutes, existing evidence quality at least 60/100, no excluded translation tag, and the existing watched/dismissed, pending-capacity and unfamiliar-content rules. Feedback cannot override these rules.

The initial content index has explicit weights: substance 40%, directness 25%, observed original contribution 25%, purposeful visuals 10%, within 95 content points. Length adds at most five points, rising linearly to one hour and then plateauing. These are transparent design priors, not empirically established optimal weights.

For assessed criterion grades, `u_j = grade_j / 4`. An unknown criterion uses an explicit uncertain prior mean of **0.5**, with a separate missingness predictor; it cannot borrow a high holistic grade from unrelated criteria. Its saved grade remains unknown. With `d = min(duration / 3600, 1)`,

\[
 Q_0=95\sum_j w_j u_j+5d.
\]

The original holistic score and evidence identity are preserved. `Q_0` is a derived, versioned scoring prior. The initial interest probabilities are 0.75 for an onboarding-related domain, 0.35 for a known unrelated domain and 0.50 when domains are unknown. Query-domain tags remain imperfect predictors, not verified semantic labels.

## Bayesian updates

There are separate interest and quality Bernoulli likelihoods. For channel `c`,

\[
 y_{ic}\sim\operatorname{Bernoulli}(\sigma(o_{ic}+x_{ic}^{T}\beta_c)),
 \quad \beta_{cj}\sim N(0,s_j^2).
\]

Offsets are `logit(I_0)` and `logit(Q_0 / 100)`. Their finite baseline clipping avoids infinite offsets; it does not bound the learned probability. Coefficients have full support on the real line. Prior standard deviations are 0.35 for global, platform, duration, quality and creator corrections, and 0.60 for domain/topic corrections. Creator effects therefore shrink strongly after one observation. These are fixed, finite regularization strengths; they do not increase with sample size.

Legacy interest features include averaged domain memberships, finer query concepts, platform, length and creator. Multiple tags form one covariate vector for **one** label, not several independent observations. Quality uses the four centered criterion predictors, their missingness flags, duration, platform and provenance indicators. No waveform, voice-origin or background-audio feature is used. Audience level is a separate, evidence-referenced descriptor; old evidence without it remains unknown.

Each head includes its offset itself as a learnable predictor: a correction coefficient of minus one can cancel the baseline completely. The overall head also receives all five component values, so a different preference for popularity, recency or diversity is representable rather than permanently embedded in the offset. These coefficients have full-support priors.

An unexplained overall vote has its own likelihood; it is not copied into either channel. The weighted base score is

\[
 B=0.60(100p_Q)+0.25(100p_I)+0.05E+0.05R+0.05D.
\]

An overall calibration head uses `logit(B / 100)` as its offset and learns unrestricted context corrections. When overall labels exist, the ranking score is its posterior predictive approval percentage. Otherwise it is `B`. This makes the original weights an initial policy inside the overall model, **not a permanent blend that prevents learning a different preference**. Overall offsets are recomputed from the current interest/quality models for both training and prediction. This is a conditional empirical-Bayes calibration stage, not a claim of an exact joint posterior over all three heads. Its consistency requires the component offsets to stabilize uniformly on the covered feature support.

The display separates original evidence quality, the explicit criterion prior, learned channel probabilities, weighted base, overall adjustment, uncertainty and snapshot identity. Log-odds corrections are additive in the statistical model; they are not misrepresented as fixed percentage criterion weights.

## Mathematical prerequisite

Infinite feedback **by itself** is insufficient. Repeated edits to one video's rating are one observation, not independent evidence. If subjects are never rated, or all observed creators always appear with the same topic, their separate effects cannot be identified. No inference algorithm can remove that ambiguity without additional assumptions or observations.

For a precise sufficient contract, consider a fixed model version and a finite covered set of feature contexts. Assume:

1. Preferences are stationary, and distinct rated videos supply conditionally independent Bernoulli observations with probabilities strictly between zero and one.
2. Each covered context is rated with a positive limiting frequency; missing ratings do not select outcomes after conditioning on the modeled features.
3. The true conditional log-odds are representable by the channel's offset and feature functions.
4. The target parameter is identifiable on that support. With redundant columns, the identifiable parameter is the vector of logits/predictions, not an arbitrary decomposition into correlated coefficients.
5. The prior is proper, has positive density around the true identified parameter, and has fixed finite precision. For the overall head, its estimated offsets converge uniformly to their true limiting offsets.

Then, for every positive `epsilon`, the exact posterior puts probability tending to one on an `epsilon` neighborhood of the true identified parameter. In particular, the posterior predictive probabilities converge to the user's probabilities on the covered support.

Proof sketch for this implementation: normalized log likelihood converges to a weighted sum of Bernoulli cross-entropies. Its difference from the true parameter is the negative weighted sum of Bernoulli KL divergences. Positive context frequencies and identifiability make that difference strictly negative away from the truth. The fixed Gaussian log prior divided by the number of observations tends to zero and its density is positive near the truth. Standard finite-dimensional GLM posterior-consistency conditions therefore yield concentration. For redundant features, apply the argument on the identifiable design span; null-space coefficients retain prior uncertainty. A uniformly vanishing plug-in offset error leaves the limiting overall likelihood contrast unchanged.

This is a sufficient-conditions argument, **not an unconditional guarantee for arbitrary adaptive retrieval, nonstationary users, growing feature spaces or selectively supplied feedback**. Model misspecification can yield a best-fitting approximation rather than the actual preference. Adding infinitely many one-off creators does not supply infinitely many observations about each creator. General high-dimensional or adaptive-design guarantees require additional assumptions and are not asserted here. The implementation reports information rank where computed and does not call uncertain coefficients converged.

Routine inference rebuilds from all current, versioned training labels using MAP and a Laplace approximation. It does not repeatedly reuse an approximate posterior as the prior for the same historical data. Newton solves use a full Hessian for small models and matrix-free, preconditioned conjugate gradients for larger ones, with no fixed feature/observation truncation. The optimization stopping criteria control residuals or decrease with sample size. Under regular identifiable fixed-dimensional conditions, MAP converges and the inverse-information covariance shrinks; the Laplace approximation is asymptotically valid. Finite-sample approximation quality is separately tested against numerical integration. Neither passing simulations nor thirty ratings proves an infinite-sample theorem.

There is no fixed cap on learned correction magnitude, no recency forgetting and no capped effective sample count. Unknown-feature priors remain uncertain. Heuristic evidence-sensitivity intervals are reported separately: persistent artificial measurement noise is not convolved into the point prediction, which would otherwise introduce an asymptotic bias absent from the training likelihood.

## Ratings, evaluation and release

Interest, quality and overall ratings are independent editable channels. Replacing or clearing a channel rebuilds its observation set; another channel is not erased. Watch/dismiss actions and missing ratings never become preference labels. Reasons remain user-provided text and are not silently reclassified by a model.

The calibration workflow reserves thirty previously unrated verified videos, fifteen per platform, balancing available familiarity/quality strata and limiting creator repetition. It excludes creators already present in current legacy or structured training data, caps repeated retrieval-family combinations and favors uncovered available domains. This is retrieval diversity, not complete semantic deduplication. Whole known creators are assigned together to training or holdout, and ordinary feedback from a reserved holdout creator is also withheld. New labels use frozen feature snapshots.

Verified evidence corrections are append-only derived feature revisions. They preserve original scorecards, rating snapshots, comments and labels, while replacing the effective quality evidence used for fitting. Changed evidence invalidates affected evaluations. Complete Bilibili provenance metadata is required before quality training; an unknown numeric copyright enum is retained without interpreting it or discarding otherwise valid metadata. `learn repair-evidence` uses retained verified frames/captions within explicit bounds and never downloads replacement media or invents ratings.

After a thirty-video review with at least 26 ratings in each main channel, evaluation is frozen **before** holdout labels are released into training; these need not be the same 26 videos. A reservation also expires after sixty distinct newly rated videos, so incomplete calibration cannot withhold a creator's feedback indefinitely. Release alone does not pass the Phase 4 gate. All its labels can then inform preferences. Another unseen cohort can be requested with `calibrate start --new`. Revisions invalidate a frozen evaluation whose label identities changed; evaluation is never recomputed on already-trained holdouts and presented as independent.

Reports include held-out Brier score, log loss, prior comparison and paired loss differences resampled by whole creator groups. Reliability-bin observed intervals use the same cluster bootstrap and are omitted when not informative. These small-sample intervals do not guarantee nominal coverage. The development gate requires a thirty-video cohort, at least 26 independent ratings per main channel, ten per channel/platform, eighteen training and eight held-out labels with response variation and at least six held-out creator groups for each active main response type. Blanks are abstentions, not negative or Medium labels. Three-level ratings have separate multiclass Brier/log-loss reports; legacy binary validation cannot certify a newly added response type. Mixed legacy and structured heads are checked separately, as is known-level depth when active. A detectably worse Brier score fails the gate. Wide intervals do not prove equivalence or improvement. Metrics apply to explicitly rated retrieved videos, with no unbiased platform-wide claim. The small overall-approval head is outside this main-channel gate.

Read-only status and gate checks do not fit missing models. A frozen receipt records the pre-release tested snapshot; later fitting on released labels cannot replace that held-out result. Newly activated response types require a fresh unseen cohort rather than inheriting certification from a binary receipt.

## Scoped feedback and depth — October 27 extension

Four independent structured dimensions are editable in both lists:

| Dimension | Responses | Scope |
| --- | --- | --- |
| Specific-topic interest | Low / Medium / High | One explicitly confirmed topic |
| Broad-subject interest | Low / Medium / High | One explicitly chosen subject |
| Depth fit | Too elementary / About right / Too advanced | One topic and observed audience level |
| Video quality | Low / Medium / High | Verified quality features |

Medium is an actual response category. Blank is missing information. Free text is preserved as a note; it does not silently create additional votes. A specific-topic or depth clarification supersedes the same video's ambiguous legacy interest/overall observations during fitting, while preserving their original rows and comments. A three-level quality judgment supersedes that video's legacy quality observation. Clearing the clarification restores the earlier observation; clear the earlier vote separately to abstain from it too. Revisions replace one effective observation per video/dimension.

Specific and broad interest use separate Dirichlet categorical posteriors. For a prior mean `b` (0.75 for an onboarding-related topic/subject, otherwise 0.35), the Low/Medium/High prior is `alpha = 2*((1-b)^2, 2*b*(1-b), b^2)`. A topic's parent-domain association is anchored by its earliest explicit topic/depth action and persists across edits and clears; new votes cannot change that association and move the prior. Explicit onboarding changes can revise the prior. Add the current distinct-video category counts to obtain the exact posterior. The expected interest score is `P(High) + 0.5*P(Medium)`. Topic labels are confirmed retrieval suggestions or user-entered labels, not automatic semantic truth. Matching currently uses normalized labels; a custom label is known for its rated video and transfers only to candidates with the same label.

Each named topic has its own likelihood and prior. Its observations are **not** added to a parent domain, global intercept, creator or sibling topic. Explicit broad-subject feedback can inform matching descendants at prediction time; a matching specific-topic posterior takes precedence. This deliberately gives zero upward propagation for a clearly scoped dislike, rather than merely using a smaller negative domain weight. Existing unexplained legacy votes keep their former interpretation until clarified or cleared.

Depth uses a separate Dirichlet posterior for `(topic, observed audience level)` and responses `(want deeper, suitable, want lighter)`, with prior `(0.5,1,0.5)`. Let `s=P(suitable)`. For a known matching level, suitability changes interest odds by `s/(1-s)`; its prior mean 0.5 is neutral. Depth never changes quality or the subject-interest posterior. An introductory mismatch does not establish a preference for every advanced video. Unknown audience levels retain the feedback but do not adjust future candidate scores. New scorecards can supply a cited introductory/intermediate/advanced assessment; a user can explicitly provide the observed level for a reviewed video. No level is inferred from duration or substance grade.

Three-level quality uses a continuation-ratio categorical likelihood with two Gaussian-prior logistic heads: `a=P(Medium or High)` and `b=P(High | not Low)`. The probabilities are `(1-a, a*(1-b), a*b)`. A Low response observes only the first Bernoulli event; Medium and High also observe their conditional second event. This factorization is one categorical likelihood, not two duplicate votes or a fractional Bernoulli label. The ranking quality index is `100*(0.5*P(Medium)+P(High))`; it is explicitly an expected ordinal utility rather than an approval percentage. Initial stage offsets use the legacy quality posterior (or `Q_0` without legacy labels), via the categorical prior above, and can be fully cancelled by learned coefficients. This conditional empirical-Bayes transfer preserves earlier quality learning when the first ordinal rating arrives; it does not turn legacy thumbs into invented category observations. Numerical offset clipping is not a permanent bound on learned probabilities. Legacy binary quality remains available separately and supplies the component when no ordinal quality training data exists.

For a fixed covered topic/level group with stationary independent categorical responses, the Dirichlet posterior is `Dirichlet(alpha+n)`. The empirical proportions converge almost surely to the category probabilities, the fixed prior contribution vanishes, and posterior variance tends to zero. This proves group prediction consistency under coverage; it does not prove generalization to an unrated child topic or an unknown level. The ordinal quality model inherits the logistic consistency conditions separately for its two conditional heads, with positive frequency of the second-stage event, correct model specification and uniformly stabilizing plug-in legacy offsets. Sparse feedback, misspecification and selective abstention remain limitations. There is no permanent shrinkage floor or observation cap.

Averaging several matching topic scores and multiplying by depth-suitability odds are ranking policies, not a theorem identifying their combination with true joint approval probability. Each named group's own distinct-video sample size must grow for its consistency result to apply. The current user's deliberate abstentions may depend on unmodeled difficulty, so the legacy model's missing-at-random condition is not established by the completed review.

## References

- [Stan regression models](https://mc-stan.org/docs/stan-users-guide/regression.html): logistic regression and hierarchical shrinkage.
- [Stan multinomial and Dirichlet-multinomial distributions](https://mc-stan.org/docs/functions-reference/multinomial-distribution.html): categorical counts and positive Dirichlet parameters.
- [Miller, JMLR 2021](https://www.jmlr.org/papers/v22/20-469.html): conditions for concentration, asymptotic normality and Laplace validity, including GLMs; the conditions must be checked rather than inferred from the word Bayesian.
- [Kasprzak, Giordano and Broderick, JMLR 2025](https://www.jmlr.org/beta/papers/v26/24-0619.html): finite-sample Laplace approximation accuracy is a separate concern.
- [Stan predictive checks](https://mc-stan.org/docs/stan-users-guide/posterior-predictive-checks.html): prior and posterior predictive assessment.
- [Li et al., WSDM](https://arxiv.org/abs/1003.5956): offline recommendation evaluation needs appropriate logging assumptions; the current curated data does not establish those assumptions.
