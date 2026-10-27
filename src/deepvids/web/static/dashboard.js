const token = document.querySelector('meta[name="deepvids-token"]').content;
const feedbackDrafts = new Map();
let calibrationIds = new Set();
let lastState = null;
let loadVersion = 0;
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function feedbackControls(id, state, context = {}, expanded = false) {
  const panel = document.createElement("details");
  panel.className = "feedback-editor";
  panel.open = expanded;
  const summary = document.createElement("summary");
  summary.textContent = "Rate topic, depth and quality";
  panel.append(summary);
  const help = document.createElement("p");
  help.className = "note";
  help.textContent =
    "Each row is independent. Medium means moderate interest or acceptable quality; blank means no judgment. A specific-topic dislike or depth mismatch does not downvote its broader subject. Topic suggestions come from retrieval; correct the label when needed.";
  panel.append(help);
  for (const [dimension, title] of [
    ["topic_interest", "This specific topic"],
    ["depth", "Depth fit for this topic"],
    ["quality_level", "Video quality"],
    ["domain_interest", "Broader subject (optional)"],
  ]) {
    const saved = state.details?.[dimension] || {};
    const draftKey = id + "|" + dimension;
    const prior = feedbackDrafts.get(draftKey) || saved;
    const row = document.createElement("div");
    row.className = "feedback-row";
    const label = document.createElement("label");
    label.textContent = title;
    const choice = document.createElement("select");
    choice.setAttribute("aria-label", title);
    const options =
      dimension === "depth"
        ? [
            ["", "No judgment"],
            ["deeper", "Too elementary — I want more depth"],
            ["suitable", "About right"],
            ["lighter", "Too advanced — I want a lighter explanation"],
          ]
        : [
            ["", "No judgment"],
            ["high", "High"],
            ["medium", "Medium"],
            ["low", "Low"],
          ];
    for (const [value, text] of options) choice.add(new Option(text, value));
    choice.value = prior.value || "";
    label.append(choice);
    row.append(label);
    const target = document.createElement("input");
    if (dimension !== "quality_level") {
      const suggestions =
        dimension === "domain_interest"
          ? context.domains || []
          : context.topics || [];
      const list = document.createElement("datalist");
      list.id = "targets-" + dimension + "-" + id.replace(/[^a-zA-Z0-9]/g, "_");
      for (const suggestion of suggestions)
        list.append(new Option(suggestion.replaceAll("-", " "), suggestion));
      target.setAttribute("list", list.id);
      target.setAttribute(
        "aria-label",
        dimension === "domain_interest" ? "Broad subject" : "Specific topic",
      );
      target.placeholder =
        dimension === "domain_interest"
          ? "Choose or enter a broad subject"
          : "Confirm or enter the specific topic";
      target.maxLength = 120;
      target.value =
        prior.target ?? (suggestions.length === 1 ? suggestions[0] : "");
      row.append(target, list);
    }
    const level = document.createElement("select");
    if (dimension === "depth") {
      level.setAttribute("aria-label", "This video's level");
      for (const [value, text] of [
        ["unknown", "This video's level: unknown"],
        ["introductory", "This video is introductory"],
        ["intermediate", "This video is intermediate"],
        ["advanced", "This video is advanced"],
      ])
        level.add(new Option(text, value));
      level.value = prior.level || context.audience_level || "unknown";
      row.append(level);
      const note = document.createElement("small");
      note.textContent =
        "Depth affects suitability, not quality. Unknown levels are saved for review and do not guess the level of future videos.";
      row.append(note);
    }
    const note = document.createElement("input");
    note.setAttribute("aria-label", title + " comment");
    note.placeholder = "Optional comment for this judgment";
    note.maxLength = 1000;
    note.value = prior.note || "";
    row.append(note);
    const read = () => ({
      value: choice.value,
      target: target.value,
      level: dimension === "depth" ? level.value : "unknown",
      note: note.value,
    });
    for (const input of [choice, target, level, note])
      input.addEventListener("input", () =>
        feedbackDrafts.set(draftKey, read()),
      );
    const save = document.createElement("button");
    save.textContent = "Save " + title.toLowerCase();
    save.onclick = () => saveFeedback(id, dimension, read(), draftKey);
    row.append(save);
    panel.append(row);
  }
  const legacy = Object.entries(state.ratings || {}).map(
    ([kind, rating]) =>
      `${kind}: ${rating.vote}${rating.reason ? " — " + rating.reason : ""}`,
  );
  if (legacy.length) {
    const text = document.createElement("p");
    text.className = "note legacy-feedback";
    text.textContent =
      "Earlier feedback (preserved): " +
      legacy.join(" · ") +
      ". Structured clarification takes precedence for that video. Free-text comments remain notes; use the fields above to specify their learning effect.";
    panel.append(text);
    const clearActions = document.createElement("div");
    clearActions.className = "actions";
    for (const kind of Object.keys(state.ratings || {})) {
      const clear = document.createElement("button");
      clear.textContent = "Clear earlier " + kind + " vote";
      clear.onclick = () => act(id, "clear", kind, false);
      clearActions.append(clear);
    }
    panel.append(clearActions);
  }
  return panel;
}
async function saveFeedback(id, dimension, feedback, draftKey) {
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Deepvids-Token": token,
      },
      body: JSON.stringify({
        id,
        action: "rate",
        kind: dimension,
        ...feedback,
        value: feedback.value || "clear",
      }),
    });
    const value = await response.json();
    if (!response.ok) throw Error(value.error || "Feedback could not be saved");
    if (
      !feedbackDrafts.has(draftKey) ||
      JSON.stringify(feedbackDrafts.get(draftKey)) === JSON.stringify(feedback)
    )
      feedbackDrafts.delete(draftKey);
    document.querySelector("#status").textContent =
      "Saved this judgment. Other ratings and watched status are unchanged. Specific-topic and depth judgments do not train parent-subject dislikes.";
    await load(true);
  } catch (error) {
    document.querySelector("#status").textContent = error.message;
  } finally {
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}
function scoreDetails(item, weights) {
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
    '<details><summary>Score details · weights and quality breakdown</summary><h4 class="score-heading">Final score / 100</h4><table><thead><tr><th>Component</th><th>Score / 100</th><th>Weight</th><th>Points</th></tr></thead><tbody>' +
    rows +
    "</tbody></table>";
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
      '<p class="note">Excluded from future recommendations: 视频翻译 tag. This historical recommendation is retained; watched, dismissed and pending states remain separate.</p>';
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
    '<p class="note">Rates are shrunk toward a platform baseline using 1,000 prior views, then normalized. Missing rates use a neutral score of 50/100. In the final score YouTube likes have a maximum 5-point contribution; Bilibili likes, coins and favorites each have a maximum 1.67-point contribution. These rates are separate from the content judgment.</p><p class="note">Before detailed assessment, screening uses 70% community response + 30% title-anchor support within protected topic lanes. Topic fit currently uses query tags. Quality estimates remain provisional.</p></details>';
  return html;
}
async function act(id, action, kind = "overall", askReason = true) {
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    let reason = "";
    if (askReason && (action === "up" || action === "down")) {
      reason = prompt(
        "Optional feedback reason (Cancel leaves the vote unchanged):",
        "",
      );
      if (reason === null) return;
    }
    const r = await fetch("/api/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Deepvids-Token": token,
      },
      body: JSON.stringify({ id, action, reason, kind }),
    });
    const v = await r.json();
    if (!r.ok) throw Error(v.error || "Action failed");
    document.querySelector("#status").textContent = [
      "up",
      "down",
      "clear",
    ].includes(action)
      ? "Feedback saved for this category. The Bayesian model updates from training ratings; reserved evaluation ratings stay held out."
      : "Saved. Your next run will use this state.";
    await load(true);
  } catch (e) {
    document.querySelector("#status").textContent = e.message;
  } finally {
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}
async function load(force = false) {
  if (
    !force &&
    (document.querySelector("select:focus, input:focus, textarea:focus") ||
      document.querySelector("button:disabled"))
  )
    return;
  const version = ++loadVersion;
  const r = await fetch("/api/state");
  if (!r.ok) throw Error("Could not load local state");
  const serialized = await r.text();
  if (version !== loadVersion) return;
  await loadLearning(version);
  if (version !== loadVersion) return;
  if (!force && serialized === lastState) return;
  lastState = serialized;
  const data = JSON.parse(serialized);
  for (const key of feedbackDrafts.keys()) {
    const id = key.split("|")[0];
    if (!Object.hasOwn(data.videos, id) && !calibrationIds.has(id))
      feedbackDrafts.delete(key);
  }
  document.querySelector("#interests").textContent =
    "Your interests: " +
    (data.profile?.interests.join(", ") || "Set your profile in the CLI");
  const root = document.querySelector("#content");
  const openedFeedback = new Set(
    [...root.querySelectorAll(".card[data-id]")]
      .filter((card) => card.querySelector(".feedback-editor")?.open)
      .map((card) => card.dataset.id),
  );
  const opened = new Set(
    [...root.querySelectorAll(".card[data-id]")]
      .filter((c) => c.querySelector("details")?.open)
      .map((c) => c.dataset.id),
  );
  root.replaceChildren();
  for (const platform of ["youtube", "bilibili"]) {
    const run = data.runs[platform],
      section = document.createElement("section");
    section.className = "platform";
    section.innerHTML =
      "<h2>" +
      { youtube: "YouTube", bilibili: "Bilibili" }[platform] +
      '</h2><p class="meta">' +
      data.pending[platform] +
      "/50 pending · " +
      (run
        ? esc(run.status) +
          " · " +
          run.processed +
          "/" +
          run.inspection +
          " inspected · " +
          run.assessed +
          " scored"
        : "No recommendations yet") +
      "</p>";
    const grid = document.createElement("div");
    grid.className = "grid";
    for (const item of run?.selected || []) {
      const s = data.videos[item.canonical_id] || {},
        card = document.createElement("article");
      card.className = "card";
      card.dataset.id = item.canonical_id;
      const q = item.quality_evidence;
      card.innerHTML =
        '<span class="badge">' +
        (item.related ? "Familiar" : "Discovery") +
        (item.originality_evidence?.excluded
          ? '</span><span class="badge">Excluded: 视频翻译'
          : "") +
        '</span><span class="badge">' +
        (item.carryover ? "Carryover" : "New") +
        '</span><h3><a target="_blank" rel="noopener noreferrer" href="' +
        esc(item.url) +
        '">' +
        esc(item.title) +
        '</a></h3><p class="meta">' +
        (q.duration_seconds / 60).toFixed(1) +
        " min · Score " +
        item.score.toFixed(1) +
        " · Quality " +
        q.quality.toFixed(1) +
        '</p><p class="summary">' +
        esc(q.summary) +
        "</p><small>" +
        esc(item.canonical_id) +
        "</small>" +
        scoreDetails(item, run.weights);
      card.querySelector("details").open = opened.has(item.canonical_id);
      const editor = feedbackControls(item.canonical_id, s, s.feedback_context);
      editor.open = openedFeedback.has(item.canonical_id);
      card.append(editor);
      const watchActions = document.createElement("div");
      watchActions.className = "actions";
      for (const [action, text] of [
        [
          s.watched ? "unwatched" : "watched",
          s.watched ? "Watched ✓" : "Mark watched",
        ],
        [
          s.dismissed ? "restore" : "dismiss",
          s.dismissed ? "Restore" : "Dismiss",
        ],
      ]) {
        const button = document.createElement("button");
        button.textContent = text;
        button.onclick = () => act(item.canonical_id, action);
        watchActions.append(button);
      }
      card.append(watchActions);
      const feedbackLabel = document.createElement("label");
      feedbackLabel.textContent = "Feedback about ";
      const kind = document.createElement("select");
      for (const [value, text] of [
        ["overall", "Overall"],
        ["interest", "My interests"],
        ["quality", "Video quality"],
      ]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        kind.append(option);
      }
      kind.value =
        feedbackDrafts.get(item.canonical_id) ||
        (["interest", "quality", "overall"].includes(s.feedback?.kind)
          ? s.feedback.kind
          : "overall");
      kind.onchange = () => feedbackDrafts.set(item.canonical_id, kind.value);
      feedbackLabel.append(kind);
      const legacyControls = document.createElement("details");
      const legacySummary = document.createElement("summary");
      legacySummary.textContent = "Overall vote and legacy thumbs";
      legacyControls.append(legacySummary, feedbackLabel);
      const actions = document.createElement("div");
      actions.className = "actions";
      const vote = s.ratings?.[kind.value]?.vote;
      for (const [action, label, pressed] of [
        [vote === "up" ? "clear" : "up", "Like", vote === "up"],
        [vote === "down" ? "clear" : "down", "Dislike", vote === "down"],
      ]) {
        const b = document.createElement("button");
        b.textContent = label;
        b.setAttribute("aria-pressed", String(!!pressed));
        if (label === "Like" || label === "Dislike") {
          b.dataset.vote = label === "Like" ? "up" : "down";
          b.onclick = () =>
            act(
              item.canonical_id,
              s.ratings?.[kind.value]?.vote === b.dataset.vote
                ? "clear"
                : b.dataset.vote,
              kind.value,
            );
        } else {
          b.onclick = () => act(item.canonical_id, action, kind.value);
        }
        actions.append(b);
      }
      kind.onchange = () => {
        feedbackDrafts.set(item.canonical_id, kind.value);
        actions
          .querySelectorAll("button[data-vote]")
          .forEach((button) =>
            button.setAttribute(
              "aria-pressed",
              String(s.ratings?.[kind.value]?.vote === button.dataset.vote),
            ),
          );
      };
      legacyControls.append(actions);
      card.append(legacyControls);
      grid.append(card);
    }
    if (!run?.selected.length)
      grid.innerHTML =
        '<p class="empty">Run a recommendation batch in the CLI to fill this list.</p>';
    section.append(grid);
    const chosen = new Set((run?.selected || []).map((i) => i.canonical_id));
    const backlog = (data.pending_items[platform] || []).filter(
      (i) => !chosen.has(i.canonical_id),
    );
    if (backlog.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent =
        "Unwatched from earlier lists (" + backlog.length + ")";
      details.append(summary);
      for (const item of backlog) {
        const row = document.createElement("p");
        const link = document.createElement("a");
        link.href = item.url;
        link.textContent = item.title;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
        for (const [action, label] of [
          ["watched", "Mark watched"],
          ["dismiss", "Dismiss"],
        ]) {
          const button = document.createElement("button");
          button.textContent = label;
          button.onclick = () => act(item.canonical_id, action);
          row.append(button);
        }
        details.append(row);
      }
      section.append(details);
    }
    if (run?.issues) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent =
        run.unavailable +
        " candidates had unavailable evidence; " +
        run.issues +
        " access notes. Quality estimates are provisional.";
      section.append(note);
    }
    root.append(section);
  }
}
async function loadLearning(version) {
  const [statusResponse, calibrationResponse] = await Promise.all([
    fetch("/api/learning"),
    fetch("/api/calibration"),
  ]);
  if (!statusResponse.ok || !calibrationResponse.ok) return;
  const [status, items] = await Promise.all([
    statusResponse.json(),
    calibrationResponse.json(),
  ]);
  if (version !== loadVersion) return;
  calibrationIds = new Set(items.map((entry) => entry.item.canonical_id));
  const interestCount = items.filter(
    (item) =>
      item.ratings.interest?.vote ||
      item.details?.topic_interest ||
      item.details?.domain_interest,
  ).length;
  const qualityCount = items.filter(
    (item) => item.ratings.quality?.vote || item.details?.quality_level,
  ).length;
  document.querySelector("#learning-summary").textContent =
    `${status.current ? "Model current" : "Model needs fitting"} · ${interestCount}/${items.length || 30} interest and ${qualityCount}/${items.length || 30} quality reviews, counted independently. ${Object.values(status.structured_ratings || {}).reduce((a, b) => a + b, 0)} structured judgments saved. ${status.pending_depth_level || 0} depth judgments await a known video level for future matching. ${status.quality_review_pending || 0} videos await provenance-aware quality review; saved ratings need not be repeated. ${status.empirically_calibrated ? "Held-out checks passed for evaluated response types; sparse or new dimensions remain provisional." : "Personal calibration is provisional until held-out checks pass. Blanks remain abstentions."}`;
  const root = document.querySelector("#calibration-cards");
  const opened = new Set(
    [...root.querySelectorAll(".calibration-card")]
      .filter((card) => card.querySelector(".feedback-editor")?.open)
      .map((card) => card.dataset.id),
  );
  root.replaceChildren();
  if (!items.length) {
    root.textContent = "Create a saved cohort with: deepvids calibrate start";
    return;
  }
  for (const entry of items) {
    const article = document.createElement("article");
    article.className = "calibration-card";
    article.dataset.id = entry.item.canonical_id;
    const title = document.createElement("a");
    title.href = entry.item.url;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    title.textContent = `${entry.item.platform} · ${entry.item.title}`;
    article.append(title);
    article.append(
      feedbackControls(
        entry.item.canonical_id,
        entry,
        entry.feedback_context,
        opened.has(entry.item.canonical_id),
      ),
    );
    const watched = document.createElement("button");
    watched.textContent = entry.watched ? "Watched ✓" : "Mark watched";
    watched.onclick = () =>
      act(entry.item.canonical_id, entry.watched ? "unwatched" : "watched");
    article.append(watched);
    root.append(article);
  }
}
load().catch(
  (e) => (document.querySelector("#status").textContent = e.message),
);
setInterval(() => load().catch(() => {}), 10000);
