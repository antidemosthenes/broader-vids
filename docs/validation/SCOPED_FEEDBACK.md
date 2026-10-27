# Scoped calibration feedback

Historical extension checkpoint. See the [subsequent Phase 4 audit](PHASE4_AUDIT.md) for current schema/model versions, fixes and development validation results.

Implemented October 27, 2026. Python alpha.12 / npm alpha.12, SQLite schema 9, learning model version 4 and evidence rubric 3.

## User workflow

Both recommendation and calibration cards have the same **Rate topic, depth and quality** editor. Specific-topic interest, broad-subject interest and video quality accept High, Medium or Low. Depth accepts Too elementary, About right or Too advanced and an optional observed audience level. The target topic or subject must be confirmed; retrieval labels are suggestions and can be corrected.

Every save changes one dimension. A blank is abstention, and Medium is an actual response. Watch/dismiss remain separate. Old thumbs and comments are retained and individually clearable; an explicit clarification takes precedence during fitting without deleting the old record. Clearing that clarification restores the old observation, so an earlier vote must also be cleared if the user wants to abstain from it.

Old ratings are not converted into invented three-level judgments. There is no need to repeat the completed review. Users can clarify the few dislikes whose scope or level was ambiguous. Free-text comments remain notes; structured fields identify the intended learning effect.

## Learning behavior

- Specific and broad interests have separate categorical posteriors per named target. Topic observations do not train parent domains, siblings, global effects or creator dislikes.
- Depth learns a separate categorical distribution for each topic and observed audience level. It changes suitability, not quality. Unknown levels are retained without guessing future video levels or preferences.
- Three-level quality uses two conditional logistic likelihoods whose product is one categorical likelihood. Medium is not a fractional Bernoulli vote, and one video is not counted twice.
- New scorecards can report cited audience-level evidence in the existing assessment call. Earlier cards lacking that field remain valid with an unknown level; their grades and histories are preserved.
- Pending provenance review prevents stale quality features from training quality/overall heads or certifying validation after new source/repost metadata is discovered. Ratings remain intact; reviewing evidence is application work, not a reason to request duplicate user ratings.
- Structured feedback respects the existing creator-grouped holdout. Revisions, changes to versioned features and provenance-review flags invalidate affected frozen evaluation identities.
- Evaluation counts main channels independently. The development check keeps eighteen training and eight held-out labels per evaluated response type, with both-platform coverage and response variation, rather than requiring thirty paired votes. New categorical responses have separate multiclass Brier and log-loss checks.

The exact priors, categorical factorization, consistency conditions and limits are in [SCORING.md](../SCORING.md#scoped-feedback-and-depth--october-27-extension). Convergence requires coverage and appropriate model assumptions; neither a small cohort nor passing tests proves personal calibration.

## Verification

The complete suite passes 181 Python tests and five launcher tests. Ruff, Prettier, skill validation and browser checks pass.

Offline tests verify parent/sibling isolation, explicit broad-versus-specific precedence, depth/quality separation, unknown-level handling, legacy clarification/clear behavior, one-observation revision semantics, three-category probability normalization and recovery, Dirichlet variance contraction, malformed-input rejection, holdout exclusion, schema-eight migration, independent abstention counts, provenance guards and cited audience-level validation.

Headless Edge exercised real HTTP actions against temporary state: save a specific-topic dislike, save an introductory/deeper mismatch, save Medium quality, preserve and clear an earlier interest vote, verify the same values in calibration, and clear one dimension while retaining the rest. No action marked a video watched. Mobile width fit and JavaScript error checks passed. These tests submitted zero actions against production state.

The personal database migration makes a pre-v9 backup and retains ratings, notes, watch/dismiss state, recommendation history and frozen calibration features. Existing topic concentration and unresolved provenance assessments are not silently repaired by relabeling data. No live video download, AI assessment call, new calibration cohort or retry of the twenty deferred Bilibili evidence failures is part of this change.

The reference migration was compared row-for-row with its pre-v9 backup: profile, all user actions, 69 current legacy ratings (27 interest, 30 quality, 12 overall), video state, calibration membership, frozen evaluations, exposure history and metadata snapshots are unchanged. No structured rating was manufactured. The retained cohort has 27 interest and 30 quality reviews; its current validation blocker is one provenance-aware evidence review, rather than the three intentional interest blanks.
