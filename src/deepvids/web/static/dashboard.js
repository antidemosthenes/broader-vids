const token = document.querySelector('meta[name="deepvids-token"]').content;
const feedbackDrafts = new Map();
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
async function act(id, action, kind = "overall") {
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    let reason = "";
    if (action === "up" || action === "down") {
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
      ? "Feedback saved. Automatic preference learning is not enabled yet."
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
    (document.querySelector("select:focus") ||
      document.querySelector("button:disabled"))
  )
    return;
  const version = ++loadVersion;
  const r = await fetch("/api/state");
  if (!r.ok) throw Error("Could not load local state");
  const serialized = await r.text();
  if (version !== loadVersion) return;
  if (!force && serialized === lastState) return;
  lastState = serialized;
  const data = JSON.parse(serialized);
  for (const id of feedbackDrafts.keys()) {
    if (!Object.hasOwn(data.videos, id)) feedbackDrafts.delete(id);
  }
  document.querySelector("#interests").textContent =
    "Your interests: " +
    (data.profile?.interests.join(", ") || "Set your profile in the CLI");
  const root = document.querySelector("#content");
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
        feedbackDrafts.get(item.canonical_id) || s.feedback?.kind || "overall";
      kind.onchange = () => feedbackDrafts.set(item.canonical_id, kind.value);
      feedbackLabel.append(kind);
      card.append(feedbackLabel);
      const actions = document.createElement("div");
      actions.className = "actions";
      const vote = s.feedback?.vote;
      for (const [action, label, pressed] of [
        [
          s.watched ? "unwatched" : "watched",
          s.watched ? "Watched ✓" : "Mark watched",
          s.watched,
        ],
        [
          s.dismissed ? "restore" : "dismiss",
          s.dismissed ? "Restore" : "Dismiss",
          s.dismissed,
        ],
        [vote === "up" ? "clear" : "up", "Like", vote === "up"],
        [vote === "down" ? "clear" : "down", "Dislike", vote === "down"],
      ]) {
        const b = document.createElement("button");
        b.textContent = label;
        b.setAttribute("aria-pressed", String(!!pressed));
        b.onclick = () => act(item.canonical_id, action, kind.value);
        actions.append(b);
      }
      card.append(actions);
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
load().catch(
  (e) => (document.querySelector("#status").textContent = e.message),
);
setInterval(() => load().catch(() => {}), 10000);
