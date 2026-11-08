import { esc } from "./ui.js";

export function scoreDetails(item, weights) {
  const names = {
    quality: "Quality",
    interest: "Interest",
    engagement: "Community response",
    recency: "Recency",
    diversity: "Creator diversity",
  };
  const num = (value) =>
    typeof value === "number" ? value.toFixed(2) : "Unavailable";
  const rows = Object.entries(names)
    .map(([key, name]) => {
      const weight = weights[key],
        score = item.components?.[key];
      return (
        "<tr><td>" +
        name +
        "</td><td>" +
        num(score) +
        "</td><td>" +
        (typeof weight === "number"
          ? (weight * 100).toFixed(0) + "%"
          : "Unavailable") +
        "</td><td>" +
        num(
          typeof score === "number" && typeof weight === "number"
            ? score * weight
            : null,
        ) +
        "</td></tr>"
      );
    })
    .join("");
  let html =
    '<p class="note">Scores belong to this recommendation round. New feedback affects later rounds.</p><h4 class="score-heading">Final score / 100</h4><table><thead><tr><th>Component</th><th>Score / 100</th><th>Weight</th><th>Points</th></tr></thead><tbody>' +
    rows +
    "</tbody></table>";
  if (item.ranking_adjustments?.version === 2) {
    const adjustment = item.ranking_adjustments;
    html +=
      '<h4 class="score-heading">Short-video adjustment</h4><table><tbody><tr><th scope="row">Before deduction</th><td>' +
      num(adjustment.score_before_duration_penalty) +
      ' / 100</td></tr><tr><th scope="row">Short-video deduction</th><td>−' +
      num(adjustment.short_video_penalty) +
      ' points</td></tr><tr><th scope="row">Final score</th><td>' +
      num(adjustment.final_score) +
      " / 100</td></tr></tbody></table>";
  }
  if (item.selection_diversity) {
    html +=
      '<p class="note">Topic diversity: ' +
      (item.selection_diversity.relaxed
        ? "A repeat was needed to fill this list from the available assessed videos."
        : "No detected overlap with earlier picks in this list.") +
      "</p>";
  }
  if (item.learning) {
    const learning = item.learning;
    html +=
      '<h4 class="score-heading">Personal calibration</h4><p class="note">Weighted base: ' +
      num(learning.base_score) +
      "; overall calibration: " +
      num(learning.overall_adjustment) +
      " points. Legacy components predict approval; structured feedback uses the category and suitability indices below. The overall model can override its initial policy prior as evidence grows; it does not change hard exclusions.</p>";
    html +=
      '<p class="note">Initial content weights: substance 40%, directness 25%, original contribution 25%, purposeful visuals 10%, applied within 95 content points; length adds at most 5 prior points. Unknown grades use an uncertain 0.5 prior with separate missingness predictors; they do not inherit the holistic score. Bayesian corrections act on log-odds, so the learned score is not a fixed linear sum of criterion grades.</p>';
    const prediction = learning.components?.quality;
    const ordinal = learning.components?.quality_ordinal;
    if (ordinal)
      html +=
        '<p class="note">Three-level quality prediction: ' +
        esc(
          Object.entries(ordinal.probabilities)
            .map(
              ([name, probability]) =>
                name + " " + num(100 * probability) + "%",
            )
            .join(" · "),
        ) +
        ". Its expected score uses Low=0, Medium=50, High=100.</p>";
    const scope = learning.components?.interest_scope;
    if (scope && scope.scope !== "legacy")
      html +=
        '<p class="note">Interest uses your ' +
        esc(scope.scope.replaceAll("_", " ")) +
        " judgment for " +
        esc(scope.matches.join(", ")) +
        ". Narrow judgments do not update parent-subject preferences.</p>";
    if (scope?.depth)
      html +=
        '<p class="note">Depth suitability for ' +
        esc(scope.depth.level) +
        " content: " +
        num(100 * scope.depth.suitability) +
        "%. This adjusts interest fit, not quality.</p>";
    if (prediction) {
      html +=
        '<p class="note">Quality approval: ' +
        num(100 * prediction.probability) +
        "%; approximate parameter interval: " +
        prediction.interval90.map((v) => num(v * 100)).join("–") +
        "%. Assessed criterion coverage: " +
        num(100 * learning.coverage) +
        "%.</p>";
      html +=
        '<p class="note">Quality corrections in log-odds: ' +
        esc(
          Object.entries(prediction.logit_contributions)
            .map(([name, value]) => name + " " + num(value))
            .join(" · "),
        ) +
        ".</p>";
    }
  }
  const provenance = item.originality_evidence;
  if (provenance?.signals?.length) {
    html +=
      '<h4 class="score-heading">Originality and source disclosures</h4><p class="note">' +
      esc(
        provenance.signals
          .map(
            (s) =>
              s.kind +
              ": " +
              (Array.isArray(s.value) ? s.value.join(", ") : s.value),
          )
          .join(" · "),
      ) +
      '</p><p class="note">These metadata clues do not verify authorship or detect synthetic speech. Historical content scores are not silently recalculated.</p>';
  }
  if (provenance?.excluded) {
    html +=
      '<p class="note">A tag rule excludes this video from future final lists. This historical recommendation remains visible. Watching and dismissal remain separate actions.</p>';
  }
  const quality = item.quality_breakdown;
  html += '<h4 class="score-heading">Inside quality / 100</h4>';
  if (quality) {
    html +=
      '<p class="note">Content judgment: ' +
      num(quality.content_quality) +
      " / 95 + length bonus: " +
      num(quality.duration_prior) +
      " / 5 = " +
      num(item.quality_evidence.quality) +
      " / 100.</p>";
    html +=
      '<p class="note">Length adds 5 × min(minutes / 60, 1) quality points. It reaches its maximum at 60 minutes and stays flat through 90 minutes; videos over 90 minutes are excluded. At the 60% quality weight, length adds at most 3 final-score points.</p>';
    html +=
      '<p class="note">The content judgment is holistic: the criteria below have no separate numerical weights and their grades are not added together.</p>';
    const labels = {
      substance: "Substance and explanatory depth",
      directness: "Directness / low filler",
      originality: "Observed original contribution",
      visuals: "Purposeful visuals / stock and memes",
    };
    for (const [key, label] of Object.entries(labels)) {
      const c = quality.criteria[key];
      if (!c) continue;
      html +=
        '<div class="criterion"><strong>' +
        label +
        ": " +
        (c.status === "assessed" ? esc(c.grade) + " / 4" : "Unknown") +
        "</strong><p>" +
        esc(c.interpretation) +
        '</p><p class="note">Confidence: ' +
        esc(c.confidence) +
        ". " +
        esc((c.limitations || []).join(" ")) +
        "</p></div>";
    }
    html +=
      '<p class="note">Overall rationale: ' + esc(quality.rationale) + "</p>";
  } else {
    html +=
      '<p class="note">Detailed scorecard unavailable. No criterion weights can be inferred from the overall score.</p>';
  }
  html += '<h4 class="score-heading">Community response</h4>';
  const features = Object.entries(item.engagement_score?.features || {}),
    metricNames = {
      likes: "Likes/views",
      coins: "Coins/views",
      favorites: "Favorites/views",
    };
  for (const [key, value] of features) {
    const rate =
      value.raw_rate === null
        ? "Unavailable"
        : (value.raw_rate * 100).toFixed(2) + "%";
    html +=
      '<p class="note">' +
      esc(metricNames[key] || key) +
      ": " +
      rate +
      " → normalized score " +
      num(value.score) +
      " / 100. " +
      (features.length === 3
        ? "One third of community response."
        : "All of community response.") +
      "</p>";
  }
  html +=
    '<p class="note">Rates are shrunk toward a platform baseline using 1,000 prior views, then normalized. Missing rates use a neutral score of 50/100. In the final score YouTube likes have a maximum 5-point contribution; Bilibili likes, coins and favorites each have a maximum 1.67-point contribution. These rates are separate from the content judgment.</p><p class="note">Before detailed assessment, screening combines community response, title-anchor support, and available preference estimates within protected topic lanes. The video details show the topic-fit evidence. Quality scores are estimates.</p>';
  return html;
}
