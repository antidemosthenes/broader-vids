# Scoring, calibration, and mathematics

Deepvids learns conditional preferences from explicit feedback.
It does not estimate an objective quality score for every video.
Its conclusions apply to the retrieved videos and feature contexts that receive feedback.

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
The engine requires a verified duration above zero and at most ninety minutes.
The saved evidence quality must reach 60 points out of 100.
The exact Bilibili tag `视频翻译` excludes a video from final selection.
Watched and dismissed videos cannot enter a new final list.

A learned preference cannot override these checks.
An unavailable video remains an access failure.
It does not receive an invented low-quality label.

## 2. Community response and screening

YouTube uses likes divided by views.
Bilibili uses likes, coins, and favorites divided by views.
These measures belong to community response, separate from content quality.

For metric count \(c\), views \(v\), and platform baseline \(b\), the adjusted rate is

\[
\widehat r=\frac{c+1000b}{v+1000}.
\]

The baseline is the middle sorted rate when at least ten valid platform observations exist.
Otherwise, initial baselines are 0.03 for likes, 0.01 for coins, and 0.02 for favorites.
The implementation limits the baseline below by \(10^{-6}\).

The metric score is

\[
 e=100\sigma\left(\operatorname{clip}\left(\log\frac{\max(\widehat r,10^{-12})}{b},-8,8\right)\right),
 \qquad \sigma(z)=\frac{1}{1+e^{-z}}.
\]

A missing count or zero view denominator receives a neutral score of 50.
Bilibili averages its three metric scores with equal weights.
These correlated metrics do not count as independent evidence of quality.

Initial screening uses 70% community response and 30% title-anchor support.
An anchor is a query term found in the returned metadata.
The engine balances topic groups instead of selecting only the first search results.
Pending recommendations keep guaranteed places within the fifty inspection slots.

## 3. Initial quality and interest

The assessment uses four content criteria:

| Criterion | Initial content weight | Meaning |
| --- | ---: | --- |
| Substance | 40% | Useful explanations or substantive content |
| Directness | 25% | Relevant content without avoidable repetition or filler |
| Original contribution | 25% | Evidence of the uploader's own contribution |
| Purposeful visuals | 10% | Visual material that supports the content |

An assessed criterion has an integer grade from zero to four.
Its normalized value is \(u_j=\mathrm{grade}_j/4\).
An unknown criterion uses a prior mean of 0.5 and a separate missingness predictor.
Its stored grade remains unknown.
It does not inherit a high grade from another criterion.

For duration \(t\) seconds, the derived initial quality index is

\[
d=\min(t/3600,1),\qquad Q_0=95\sum_j w_j u_j+5d.
\]

Length contributes at most five points.
The contribution rises to one hour and then remains constant.
These initial weights express a design choice, not a fitted optimum.
The saved holistic evidence score remains a separate historical observation.

The initial interest probability \(I_0\) is 0.75 for a domain related to the user's stated interests.
It is 0.35 for a known unrelated domain and 0.50 when domains are unknown.
Search-domain labels describe retrieval context.
They do not prove the video's semantic topic.

Source links, repost declarations, `中配`, and YouDub disclosures inform originality assessment.
A source link alone does not prove copying.
An unfamiliar copyright enum remains uninterpreted.
The model must distinguish source teaching quality from the uploader's contribution.

## 4. Binary feedback models

Existing interest and quality votes use separate Bernoulli models.
An overall vote trains a third model.
The engine does not copy an overall vote into the other two models.

For channel \(c\),

\[
y_{ic}\sim\operatorname{Bernoulli}(\sigma(o_{ic}+x_{ic}^{T}\beta_c)),
\qquad \beta_{cj}\sim N(0,s_j^2).
\]

The initial offsets are \(\operatorname{logit}(I_0)\) and \(\operatorname{logit}(Q_0/100)\).
Numerical clipping keeps offsets finite.
It does not limit the final learned probability.
Each model includes its offset as a predictor, so learning can cancel that offset.

Prior standard deviations are 0.60 for domain and topic corrections.
They are 0.35 for the other implemented corrections.
These proper Gaussian priors have full support and fixed finite precision.
They do not impose a permanent correction limit.

Interest predictors include domains, topics, creator, platform, and duration.
Quality predictors include the four criteria, missingness indicators, duration, platform, and source indicators.
Several tags form one predictor vector for one observed judgment.
They do not create duplicate observations.

The engine rebuilds each model from current training labels.
An edit replaces one effective observation.
It does not repeatedly use the previous posterior as a new prior for the same data.

## 5. Full ranking

The initial weighted score is

\[
B=0.60Q+0.25I+0.05E+0.05R+0.05D.
\]

All components use a scale from zero to 100.
Without ordinal feedback, \(Q=100p_Q\) and \(I=100p_I\) use the learned binary predictions.
The next section defines the structured replacements.
\(E\) is community response.
For age \(a\) in years, recency is \(R=100/(1+a/10)\).
A missing publication date gives \(R=50\).

Creator diversity is \(D=100/(1+n+0.1h)\).
Here, \(n\) counts this creator in the developing list.
The value \(h\) counts the creator in recent recommendation exposures.
Randomness enters discovery rather than adding a random final-score bonus.

When overall training labels exist, an overall logistic model calibrates \(B\).
Its offset is \(\operatorname{logit}(B/100)\).
Its predictors include all component values and context corrections.
Its final score is the predicted overall approval percentage.
Otherwise, the final score is \(B\).

The overall stage recomputes component offsets for training and prediction.
This is conditional empirical Bayes, not an exact joint posterior over every model.
Its consistency requires uniformly stabilizing component offsets.
Published historical lists retain their saved scores.
Later runs use the current model.

## 6. Structured feedback

Topic interest, broad-subject interest, depth fit, and video quality are independent dimensions.
Low, Medium, and High represent observed categories.
Blank means no observation.
Comments remain notes unless the user supplies an explicit structured judgment.

A topic or depth clarification excludes that video's old interest and overall votes from fitting.
An ordinal quality judgment excludes that video's old binary quality vote from fitting.
The engine preserves the original rows and comments.
Clearing the clarification restores the earlier effective observation.
Clear that earlier vote separately to abstain from it too.

### Topic and broad-subject interest

Each user-selected topic or subject uses a separate Dirichlet categorical model.
For initial mean \(b\), its Low/Medium/High prior is

\[
\alpha=2\left((1-b)^2,\ 2b(1-b),\ b^2\right).
\]

Use \(b=0.75\) for an onboarding-related topic or subject and \(b=0.35\) otherwise.
Current distinct-video category counts \(n\) give posterior \(\operatorname{Dirichlet}(\alpha+n)\).
The interest index is \(100[P(\mathrm{High})+0.5P(\mathrm{Medium})]\).

The earliest explicit topic or depth action anchors the topic's parent domains.
Edits and clears preserve that anchor.
An explicit onboarding change can change the prior.
A new dislike cannot move the prior by changing the parent association.

A topic judgment does not update parent, sibling, global, or creator dislikes.
A matching specific-topic posterior takes precedence over a broad-subject posterior.
Several matching topic scores use an average.
Custom topic labels transfer only to matching normalized labels.

### Depth fit

Depth uses a separate model for each topic and observed audience level.
The responses are want deeper, suitable, and want lighter.
Their Dirichlet prior is \((0.5,1,0.5)\).
For \(s=P(\mathrm{suitable})\), the ranking multiplies interest odds by \(s/(1-s)\).
The prior mean \(s=0.5\) is neutral.

Depth does not change the quality or domain-interest posterior.
Unknown audience levels retain feedback but do not adjust future candidate scores.
Audience level requires cited evidence or an explicit user choice.
Duration and substance grade do not establish audience level.

Topic averaging and depth odds are ranking policies.
They do not establish a true joint approval probability.

### Ordinal quality

The quality model uses two logistic stages:

\[
a=P(\mathrm{Medium\ or\ High}),\quad b=P(\mathrm{High}\mid\mathrm{not\ Low}).
\]

The category probabilities are \((1-a,\ a(1-b),\ ab)\).
A Low response supplies only the first-stage event.
Medium and High responses also supply the conditional second-stage event.
This is one categorical likelihood, not duplicate votes or fractional Bernoulli labels.

The quality index is \(Q=100[0.5P(\mathrm{Medium})+P(\mathrm{High})]\).
It represents expected ordinal utility, not a binary approval percentage.
Initial stage offsets transfer the legacy quality prediction without inventing category labels.
Learned corrections can cancel these offsets.
The binary quality model remains separate.

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
They preserve original ratings, comments, scorecards, and captured features.
Incomplete source metadata can pause quality fitting.
Maintenance uses retained frames and captions without downloading replacement media.

## 8. Consistency assumptions

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

For a fixed categorical group, the exact posterior is \(\operatorname{Dirichlet}(\alpha+n)\).
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
