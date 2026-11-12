# Scoring, calibration, and mathematics

Broader Vids learns conditional preferences from explicit feedback.
It does not estimate an objective quality score for every video.
Its conclusions apply to the retrieved videos and feature contexts that receive feedback.

Claude Code uses the same scoring formulas and thresholds as Codex.
The selected provider and model can change generated assessments.
No host selection changes the saved scoring formulas.
## Terms

| Term | Meaning |
| --- | --- |
| Prior | A model assumption before the current observations |
| Posterior | The distribution after the model includes those observations |
| Predictor | A measured or derived input to a model |
| Likelihood | The probability model for observed feedback |
| Holdout | Feedback reserved for testing before training |
| Calibration | Comparison between predicted responses and observed responses |
| Provenance | Information about a video's source and the uploader's contribution |
| Ordinal response | An ordered category such as Low, Medium, or High |

## 1. Eligibility

Eligibility checks operate separately from learned preferences.
The engine requires a verified playable duration greater than sixty seconds and at most ninety minutes.
The saved evidence quality must reach 60 points out of 100.
For new assessments without a usable transcript, subtract ten points from quality after adding the duration prior, with a floor of zero.
The same deduction applies to the learned quality component during ranking.
After the learned final score, subtract 20 × clamp((300 − t) / 240, 0, 1), where t is seconds.
This deduction is 15 at two minutes, 10 at three, 5 at four, and 0 at five minutes.
New rounds use ranking policy 4, with a maximum short-video deduction of 20 points.
Saved policy-2 rounds retain the previous ten-point maximum. Policy-1 rounds retain no duration deduction.
Policy 4 also subtracts five final-score points for portrait videos with verified height greater than width.
The engine adds this deduction to the short-video deduction after learned scoring.
A square or landscape video receives zero portrait deduction. Unknown dimensions also give zero deduction.
Eligibility uses evidence quality before these final-score deductions.
Policy 3 applies the twenty-point short-video rule.
Clamp the adjusted score at zero.
The existing evidence quality gate and screening rules do not change.
Audio quality is not a criterion.
Captions and frames support the four existing content criteria.
Only rubric-5 evidence is eligible for reuse or new recommendation work.
Unsupported evidence remains unchanged. It requires a new evidence run.
By default, the exact Bilibili tag `视频翻译` excludes a video from final selection.
Watched and dismissed videos cannot enter a new final list.

A learned preference cannot override these checks.
An unavailable video remains an access failure.
It does not receive an invented low-quality label.

## 2. Community response and screening

YouTube uses likes divided by views.
Bilibili uses likes, coins, and favorites divided by views.
These measures belong to community response, separate from content quality.
Before admission to the 100-video buffer, both platforms require views >= 100, likes >= 3, and likes/views >= 0.01.
Bilibili additionally requires coins/views >= 0.001 and favorites/views >= 0.002.
Missing required metrics fail admission.
These hard floors use raw rates before the smoothing described below.

For metric count $c$, views $v$, and platform baseline $b$, the adjusted rate is

$$
\widehat r=\frac{c+1000b}{v+1000}.
$$

The baseline is the middle sorted rate when at least ten valid platform observations exist.
Otherwise, initial baselines are 0.03 for likes, 0.01 for coins, and 0.02 for favorites.
The implementation limits the baseline below by $10^{-6}$.

The metric score is

$$
 e=100\sigma\left(\operatorname{clip}\left(\log\frac{\max(\widehat r,10^{-12})}{b},-8,8\right)\right),
 \qquad \sigma(z)=\frac{1}{1+e^{-z}}.
$$

The rate-score helper assigns 50 to a missing count or zero denominator.
Such entries fail the hard admission floors and cannot enter a new recommendation shortlist.
Bilibili averages its three metric scores with equal weights.
These correlated metrics do not count as independent evidence of quality.

Initial screening uses 70% community response and 30% title-anchor support.
An anchor is a query term found in the returned metadata.
Collection scans batches of up to 100 unique candidates.
The eligible pool targets 71 familiar and 29 exploratory retrievals.
Each batch fills only the remaining slots in its own group, using the highest screening scores.
Later batches target deficient groups and preserve source attribution through search continuations.
Duplicates and rejected candidates do not fill slots.
This balance applies to collection, before inspection selection.
Screening policy 5 combines 80% of the metadata screening score with 20% learned interest.
The metadata score retains its 70% community-response and 30% title-anchor weights.
The learned term uses topic-specific interest when verified title or tag context names that topic.
Otherwise, it uses a broad-subject estimate when available.
Unknown context receives a neutral value of 50.
Repeated mentions of one topic count once, including mentions shared by the title and tags.
Each topic also counts once when averaging verified depth estimates.
The screening model uses verified video context and does not infer quality.
It uses audience depth only when evidence or the user verifies the video level.
It does not use speculative quality estimates.
Screening policy 5 retains the 71 familiar and 29 exploratory eligible-pool targets.
It also retains thirty-five inspection slots and balances query coverage.
Inspection selection balances query coverage across thirty-five slots.
Each slot favors the candidate whose retrieval queries have the fewest existing slots.
For several query origins, the largest query count applies.
The applicable screening score and canonical ID resolve ties.
A missing query uses its retrieval family when available. Unknown sources share no invented group.
This rule permits repeats when fewer queries can supply candidates.
Pending entries compete on the same score without guaranteed slots.
A creator exclusion removes eligibility without creating negative feedback.
Creator IDs determine these exclusions.
Account names serve only as labels.
Unresolved creators remain eligible.
Each round retains a fixed creator-rule snapshot for screening, assessment scheduling, and final selection.
Rule changes apply to later rounds rather than changing an active or resumed round.
Manual and account-specific imported rules remain separate from learning parameters.
Policy 5 saves one learning-model reference when the round first executes.
Screening, resumed work, previews, and final selection use that reference.
Feedback saved during a round changes the model used by a later round.
Unsupported round policies cannot resume. Current rounds retain their saved settings.

## 3. Initial quality and interest

The assessment uses four content criteria:

| Criterion | Initial content weight | Meaning |
| --- | ---: | --- |
| Substance | 40% | Useful explanations or substantive content |
| Directness | 25% | Relevant content without avoidable repetition or filler |
| Original contribution | 25% | Evidence of the uploader's own contribution |
| Purposeful visuals | 10% | Visual material that supports the content |

Rubric 5 judges substance and sampled focus against the video's purpose.
Travel films can provide substance through concrete places, activities, and distinctive visual detail.
Crafts and games can provide substance through visible execution, development, or outcomes.
Instructional videos still need evidence of their explanations.
A software demonstration does not establish that it teaches the underlying algorithm.
Substance includes explanations needed to understand an instructional video.
For other video types, judge substantial content against the video's purpose.
A visual demonstration or performance does not need to teach a lesson.
Silent frames cannot establish musical performance quality.
Temporal gaps reduce confidence and widen the interval.
They do not automatically prevent assessment of sampled relevance and focus.
A title card alone or unrelated scenes can still leave the overall score unknown.
Unknown sound, factual accuracy, and full-video pacing remain explicit limitations.
This outcome is an assessment limitation, not a download failure or a low quality rating.
A course label alone does not reduce quality.
A short, complete explanation can have high quality.
Visual assessment includes text readability, useful contrast, framing, and legible diagrams.
Simple slides and phone recordings can satisfy these checks.
Caption evidence cannot establish visual quality or AI generation.
Creator reputation does not guarantee a high grade.
Existing scorecards remain stored. New work and cache reuse require rubric 5.

An assessed criterion has an integer grade from zero to four.
Its normalized value is $u_j=\mathrm{grade}_j/4$.
An unknown criterion uses a prior mean of 0.5 and a separate missingness predictor.
Its stored grade remains unknown.
It does not inherit a high grade from another criterion.

For duration $t$ seconds, the derived initial quality index is

$$
d=\min(t/3600,1),\qquad Q_0=95\sum_j w_j u_j+5d.
$$

Length contributes at most five points.
The contribution rises to one hour and then remains constant.
These initial weights express a design choice, not a fitted optimum.
The saved holistic evidence score remains a separate historical observation.
After assessment, a separate text-only call aligns up to 35 videos with saved interests.
It uses the assessed title, specific and broad labels, and summary.
It ignores retrieval origin and accepts only medium- or high-confidence matches.
Accepted matches supply canonical broad-root features for learned lookup.
Assessed topic labels remain unchanged.

Mapping policy 2 classifies the video's central subject.
A crime match can include suspected, alleged, or unresolved crimes when investigation is central to the content.
The match does not confirm an offense or establish guilt.
A brief poisoning warning in a pet-care video need not match crime.
Mapping confidence describes topic relevance, not the probability that an allegation is true.
The rationale must preserve uncertainty about allegations.
These instructions do not change quality grades or existing user feedback.

The initial interest probability $I_0$ is 0.75 for a domain related to the user's stated interests.
It is 0.35 for a known unrelated domain and 0.50 when domains are unknown.
Search-domain labels describe retrieval context.
They do not prove the video's semantic topic.

New AI topic labels must be short subject names, with at most 120 characters and 12 words.
Fresh assessments and reads of saved scorecards omit unusable topic labels before validation.
The filter removes obvious citations, instructions, control characters, and editing notes from the label lists.
The engine retains the raw model response and preserves numeric quality scores, saved ratings, and explicit user targets.
Remaining schema or evidence errors use the existing bounded assessment fix workflow.
This check detects common formatting errors. It does not prove that a topic correctly describes the video.

Source links, repost declarations, `中配`, and YouDub disclosures inform originality assessment.
A source link alone does not prove copying.
An unfamiliar copyright enum remains uninterpreted.
The model must distinguish source teaching quality from the uploader's contribution.

## 4. Logistic components

Current ratings use categorical topic, subject, depth, and quality responses.
The quality model factors its categorical likelihood into logistic events.
Each event uses a predictor vector and a proper Gaussian prior:

$$
y_i\sim\operatorname{Bernoulli}(\sigma(o_i+x_i^T\beta)),
\qquad \beta_j\sim N(0,s_j^2).
$$

Numerical clipping keeps offsets finite.
It does not limit the final learned probability.
An offset also enters as a predictor, so learning can cancel that offset.
Prior standard deviations are 0.60 for domain and topic corrections and 0.35 for other implemented corrections.
These priors have full support and fixed finite precision.

Quality predictors include criteria, missingness indicators, duration, platform, and source indicators.
Several tags form one predictor vector for one judgment.
They do not create duplicate observations.
The engine rebuilds models from current training labels.
An edit replaces one effective observation.

The current interface uses four independent structured ratings.
Binary upvote and downvote commands and migration tools are not available.
Model version 8 retains its portable parameter groups for exact preference transfer.

## 5. Full ranking

The initial weighted score is

$$
B=0.60Q+0.25I+0.05E+0.05R+0.05D.
$$

All components use a scale from zero to 100.
Without structured feedback, quality and interest use their initial priors.
The next section defines the structured replacements.
$E$ is community response.
For age $a$ in years, recency is $R=100/(1+a/10)$.
A missing publication date gives $R=50$.

Creator diversity is $D=100/(1+n+0.1h)$.
Here, $n$ counts this creator in the developing list.
The value $h$ counts the creator in recent recommendation exposures.
Randomness enters discovery rather than adding a random final-score bonus.
This score component measures creator diversity.
Screening policy 5 also applies a separate diversity rule when it constructs each platform list.
For each remaining candidate, count earlier picks that share a retrieval query, primary narrow topic, or secondary-school course format.
Choose the lowest count first, then the highest full score.
Primary labels match after normalization, including possessives and multiword phrase containment.
Broad subject labels do not create a match.
High-school and Gaokao markers come from titles and metadata tags.
Missing labels do not create a match.
A positive overlap count means the rule relaxed to fill the list from the assessed pool.
The saved `selection_diversity` field records that case.
This selection rule changes no quality grade, component weight, or preference posterior.
It adds no model calls. It cannot guarantee semantic uniqueness when labels or query coverage are incomplete.
Different videos about the same subject can therefore recur.
A subject match alone does not establish repeated content or equal explanatory depth.

Current profiles rank with $B$, using learned structured quality and interest.
The initial component weights remain a ranking policy.
An empty internal overall model does not change this score.
Historical snapshots can retain an older overall adjustment.
Published historical lists retain their saved scores.
Later runs use the current model.

Provisional previews use the round's saved ranking policy and validated interest mappings.
The short-video deduction applies once when the saved policy requires it.
Previews create no recommendation history.

## 6. Structured feedback

Topic interest, broad-subject interest, depth fit, and video quality are independent dimensions.
Low, Medium, and High represent observed categories.
Blank means no observation.
Medium interest means indifference.
Medium quality means acceptable quality.
An empty field is not a Medium response.
The dashboard shows Not rated yet as a placeholder.
Use Clear to remove a saved dimension.

New assessments provide cited content topics and audience levels.
A separate bounded labeling job can update older videos from retained captions.
Search queries do not populate rating topics.
The user can edit each label or audience level.

Ratings provide the observations used for learning.
Tag exclusions remain separate explicit rules.
Applied tag rules operate during screening and final selection.
They require matching observed tags.
The user can remove an exclusion.

### Topic and broad-subject interest

Each user-selected topic or subject uses a separate Dirichlet categorical model.
For initial mean $b$, its Low/Medium/High prior is

$$
\alpha=2\left((1-b)^2,\ 2b(1-b),\ b^2\right).
$$

Use $b=0.75$ for an onboarding-related topic or subject and $b=0.35$ otherwise.
Current distinct-video category counts $n$ give posterior $\operatorname{Dirichlet}(\alpha+n)$.
The interest index is $100[P(\mathrm{High})+0.5P(\mathrm{Medium})]$.

The earliest explicit topic or depth action anchors the topic's parent domains.
Edits and clears preserve that anchor.
An explicit onboarding change can change the prior.
A new dislike cannot move the prior by changing the parent association.

A topic judgment does not update parent, sibling, global, or creator dislikes.

Canonical alignment contributes to broad-domain lookup. It does not replace assessed specific or broad labels.
Lookup also retains raw broad labels so existing subject ratings and learned coefficients remain usable.
New rounds do not inherit final interest membership from retrieval domains when content labels are missing.

Manual per-video topic and domain feedback contributes at its own scope.
Matching specific-topic scores take precedence over broad-root scores.

Depth feedback then adjusts interest for the assessed topic and audience level.
Direct feedback for that level takes precedence over transferred depth fit.

Several matching topic scores use an average.
Custom topic labels transfer only to matching normalized labels.

### Depth fit

Depth uses an exact categorical model for each observed topic and audience level.
The responses are want deeper, suitable, and want lighter.
Their Dirichlet prior is $(0.5,1,0.5)$.
For an observed level, $s=P(\mathrm{suitable})$ multiplies interest odds by $s/(1-s)$.
Its prior mean of 0.5 is neutral.

For a level without direct feedback, a separate directional model transfers information within that topic.
Encode introductory, intermediate, and advanced as $l=0,1,2$.
A latent preferred level has prior $\mu\sim N(1,1.5^2)$.
With $w=0.75$, its likelihood is

$$
P(\mathrm{deeper}\mid\mu,l)=\sigma(\mu-l-w),
\quad P(\mathrm{lighter}\mid\mu,l)=\sigma(l-\mu-w),
$$

$$
P(\mathrm{suitable}\mid\mu,l)=1-P(\mathrm{deeper})-P(\mathrm{lighter}).
$$

Adaptive one-dimensional quadrature approximates this posterior.
The transfer adds $\operatorname{logit}(s_{post})-\operatorname{logit}(s_{prior})$ to interest log odds.
Thus, no feedback gives no adjustment.
Want deeper shifts preference upward, while want lighter shifts it downward.
Direct categorical feedback takes precedence when that candidate level receives observations.
The directional model does not add duplicate categorical counts.

This transfer is a ranking policy, not a calibrated joint probability model.
Its fixed noise width cannot represent every possible response distribution.
The exact categorical consistency result applies to directly observed topic-level groups.
No unconditional convergence claim applies to transferred scores at an unobserved level.

The engine clips depth suitability to $10^{-12}$ through $1-10^{-12}$ before conversion to log odds.
Depth does not change quality or domain interest.
Unknown audience levels retain feedback but do not adjust future scores.
The model supplies a cited level when the evidence supports one.
The user can override that level.
The engine distinguishes an AI estimate from an explicit user level.
An explicit Unknown choice prevents the AI estimate from overriding that judgment.
Later labels can resolve a pending AI level.
Errors in estimated levels can still bias depth predictions.
Duration does not establish audience level.

### Ordinal quality

The quality model uses two logistic stages:

$$
a=P(\mathrm{Medium\ or\ High}),\quad b=P(\mathrm{High}\mid\mathrm{not\ Low}).
$$

The category probabilities are $(1-a,\ a(1-b),\ ab)$.
A Low response supplies only the first-stage event.
Medium and High responses also supply the conditional second-stage event.
This is one categorical likelihood, not duplicate votes or fractional Bernoulli labels.

The quality index is $Q=100[0.5P(\mathrm{Medium})+P(\mathrm{High})]$.
It represents expected ordinal utility, not a binary approval percentage.
Initial stage offsets use the quality prior when the retired binary model has no observations.
Learned corrections can cancel these offsets.
An empty compatibility model supplies the baseline without retaining binary votes.

## 7. Calibration and evaluation

The calibration cohort contains thirty previously unrated videos, fifteen from each platform.
Selection balances available groups and limits repeated creators and retrieval families.
It excludes creators already present in active training feedback.
This does not guarantee semantic diversity across all possible subjects.

Known creators stay entirely in training or holdout.
Feedback from other videos by a reserved holdout creator also waits for release.
The engine saves evaluation before it releases reserved labels into training.
It releases a completed review with at least 26 judgments in each main channel.
An incomplete reservation also expires after sixty distinct newly rated videos.
Expiration does not establish successful calibration.

Reports include Brier score, log loss, prior comparisons, and creator-group bootstrap intervals.
Binary reliability bins compare predicted and observed response frequencies.
Categorical responses use multiclass losses.
The small-sample bootstrap does not guarantee nominal interval coverage.

The development check requires:

1. A thirty-video cohort and at least 26 judgments per main channel.
2. At least ten judgments per channel and platform.
3. At least eighteen training judgments for each active main response type.
4. At least eight holdout judgments with two examples in each of two response categories.
5. At least six holdout creator groups for each checked response type.
6. No detected Brier degradation relative to the prior.

The check includes mixed binary and structured response types separately.
Only known-level depth feedback can establish known-level depth coverage.
The separate overall model is outside this main-channel check.
Wide intervals do not prove equivalence or improvement.

Read-only status does not fit a missing model.
Changed labels or effective features invalidate affected frozen evaluations.
A new response type requires new holdout coverage.
Training on released labels does not replace the earlier test result.

Evidence maintenance adds derived feature records after source checks.
Corrections include the current missing-caption deduction, which replaces any earlier deduction.
They preserve original ratings, scorecards, and captured features.
Incomplete source metadata can pause quality fitting.
Maintenance uses retained frames and captions without downloading replacement media.

## 8. Transfer of learned parameters

Preference file format 4 remains unchanged. Coverage summary version 2 requires an updated plugin.

Preference format 4 transfers interests, model version 8 parameters, watched video IDs, a bounded exploration summary, and manual creator exclusions.

The engine rejects other preference formats before import changes the profile.


The included starter preset preserves interests, parameters, and parameter uncertainty from the supplied export.
It clears watched IDs, creator exclusions, and exploration progress.
Import replaces manual creator lists independently of learned parameters. These rules never create negative ratings.
Blacklist sync combines the account-specific blacklist with the current manual rules.
The exploration summary contains at most 1,024 hierarchy nodes, aggregate counts, and relative round ages.
It contains no individual video history.
Logistic channels retain parameter names, posterior means, and the full covariance or precision matrix.
Sparse fits aggregate their precision matrix before export. They do not export per-observation feature rows.
Categorical groups retain Dirichlet concentration parameters and aggregate observation counts.
Depth models retain their posterior summaries and a three-by-three table of level/response counts per topic.
These counts are sufficient statistics for the implemented depth likelihood. They contain no video identities or chronology.
The watched-video ID list contains no titles, ratings, or recommendation records.
Coverage summary version 2 adds aggregate, per-platform, and broad-root use counters.


The new plugin accepts version 1 summaries. Older plugin releases do not read version 2 summaries.
A version 1 import starts new counters at zero and preserves old aggregate counts.
Merge uses maximum counter values, so repeated imports do not add duplicate use.
Import merges those IDs into the target profile's watched state to prevent exact repeats.
The target profile keeps its local ratings, video lists, tag rules, and account-specific blacklist copies.
Quality calibration retains both continuation-stage models.
Transfers exclude per-video topic and level corrections, test cohorts, and validation claims.
Local saved corrections remain visible in the dashboard.
They continue to label local videos without adding their old ratings to training.

For a logistic parameter vector, import uses the saved Gaussian approximation as a proper prior:

$$
\beta\sim N(m_0,\Sigma_0),\qquad
\log p(\beta\mid D_{\mathrm{new}})
=-\tfrac12(\beta-m_0)^\top\Sigma_0^{-1}(\beta-m_0)
+\sum_{i\in D_{\mathrm{new}}}\log p(y_i\mid x_i,\beta)+C.
$$

The implementation preserves off-diagonal correlations.
New coefficients receive the usual independent proper priors.
MAP fitting and a full Hessian update the imported Gaussian approximation.
This is sequential approximate Bayesian learning. It does not reconstruct the original non-Gaussian posterior from raw observations.
Immediately after import, predictions and parameter uncertainty match the exported approximation, subject to floating-point arithmetic.
Per-video corrections and local exclusion rules are outside this comparison.

For each categorical group, new counts add to the imported concentration vector.
Depth fitting combines imported aggregate counts with new counts and repeats its numerical integration.
Each topic uses at most nine level-response counts. This is the same likelihood as separate response rows.
Local ratings saved before import do not enter these likelihoods again.
Editing an older rating supplies one new post-import observation. Later edits replace that post-import observation.
Clearing it removes its post-import contribution.
Clearing cannot remove an unidentified historical observation embedded in the imported aggregate posterior.
Replacing the imported baseline with an earlier recovery file can restore its earlier parameters.
Repeated import replaces the baseline. It does not add two copies of the same parameters.
Frozen evaluation results cannot cross an import boundary.

The consistency assumptions below apply to a fixed imported proper prior and sufficiently many subsequent distinct-video observations.
Repeated transfers of successively approximated posteriors do not establish unconditional consistency.

## 9. Consistency assumptions

Infinite feedback alone is insufficient.
Repeated edits to one rating remain one observation.
Unobserved topics and confounded creator effects cannot become identifiable through unrelated feedback.

For a fixed model version and finite covered feature support, sufficient conditions are:

1. Distinct videos supply stationary, conditionally independent responses with probabilities strictly between zero and one.
2. Each covered context has positive limiting observation frequency. Missing labels do not select outcomes after conditioning on modeled features.
3. The true conditional logits lie within the implemented offset and feature family.
4. The prediction target is identifiable on the covered support. Redundant coefficients need not be separately identifiable.
5. The prior is proper and positive near the true target, with fixed finite precision.
6. Plug-in component offsets converge uniformly where an overall or ordinal stage uses them.

Under regular finite-dimensional conditions, the exact posterior concentrates around the true identified target.
Consequently, predictions converge on the covered support.

The normalized likelihood contrast is a negative weighted sum of Bernoulli KL divergences.
Positive coverage and identifiability separate the truth from alternatives.
The fixed Gaussian log prior divided by sample size tends to zero.
Its positive local density supplies prior mass near the truth.
The standard concentration argument then applies on the identifiable design span.
Null-space coefficients can retain prior uncertainty.
Uniformly vanishing offset errors preserve the limiting likelihood contrast.

Routine inference uses MAP with a Laplace approximation.
Small models use a full Hessian.
Larger models use matrix-free preconditioned conjugate gradients.
Prediction uses finite Gaussian quadrature.
These are numerical approximations, with conditional asymptotic justification rather than exact finite-sample posterior calculations.

For a fixed categorical group, the exact posterior is $\operatorname{Dirichlet}(\alpha+n)$.
If that group's distinct-video sample grows under stationary independent responses, empirical proportions converge and posterior variance tends to zero.
Each topic or level group needs its own coverage.
The ordinal model also requires positive second-stage frequency and well-specified conditional models.

The project does not claim unconditional consistency for growing hierarchies, changing preferences, missing predictors, selective feedback, or arbitrary adaptive retrieval.
Difficulty-related abstentions can violate the missing-label assumption.
Model misspecification can produce a best-fitting approximation instead of the actual preference.

There is no fixed observation cap, forgetting window, or learned correction cap.
Unseen features retain prior uncertainty.
Evidence-sensitivity intervals remain separate from point predictions.
Persistent artificial measurement noise does not enter the point prediction.

## References

- [Stan regression models](https://mc-stan.org/docs/stan-users-guide/regression.html).
- [Stan categorical and Dirichlet distributions](https://mc-stan.org/docs/functions-reference/multinomial-distribution.html).
- [Miller, 2021](https://www.jmlr.org/papers/v22/20-469.html): posterior concentration and Laplace conditions.
- [Kasprzak, Giordano, and Broderick, 2025](https://www.jmlr.org/beta/papers/v26/24-0619.html): finite-sample Laplace accuracy.
- [Li et al.](https://arxiv.org/abs/1003.5956): assumptions for offline recommendation evaluation.
