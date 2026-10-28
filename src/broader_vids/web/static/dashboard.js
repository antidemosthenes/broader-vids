import {
  state,
  contextKey,
  selectedId,
  entries,
  record,
  updateRecord,
  updateRating,
  applyExclusions,
} from "./state.js";
import { get, post } from "./api.js";
import { el, platformName } from "./ui.js";
import { createReview } from "./review.js";
import { createVideoRow, createExclusions } from "./views.js";
import { scoreDetails } from "./score.js";

const $ = (selector) => document.querySelector(selector);
const isNarrow = () =>
  innerWidth <= 1100 ||
  innerWidth / parseFloat(getComputedStyle(document.documentElement).fontSize) <
    74;
const listPane = $("#list-pane"),
  list = $("#video-list"),
  inspectorBody = $("#inspector-body");
const mobile = $("#review-dialog"),
  mobileBody = $("#mobile-review-body"),
  details = $("#details-dialog");
const placeholder = inspectorBody.firstElementChild;
let rows = [],
  listSignature = "",
  route = "",
  review,
  reviewId,
  management,
  managementKey,
  secondary = false;
let returnFocus,
  earlier = new Map();
const earlierItems = new Map();
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function currentEntries() {
  return state.view === "recommendations" && earlier.get(contextKey())
    ? earlierItems.get(state.platform) ||
        state.data.pending_items[state.platform]?.filter(
          (item) =>
            !state.data.runs[state.platform]?.selected.some(
              (pick) => pick.canonical_id === item.canonical_id,
            ),
        ) ||
        []
    : entries();
}
function rememberPosition() {
  state.scrolls.set(
    contextKey() + ":" + !!earlier.get(contextKey()),
    listPane.scrollTop,
  );
  state.scrolls.set(
    "management:" + state.platform + ":" + state.exclusionTab,
    $("#management").scrollTop,
  );
  if (reviewId && state.view !== "exclusions" && !secondary) {
    const parent = review?.element.parentElement;
    if (parent === inspectorBody || (parent === mobileBody && mobile.open))
      state.inspectorScrolls.set(reviewId, parent.scrollTop);
  }
}
function navigate(change) {
  rememberPosition();
  if (change.view && change.view !== state.view) route = "";
  if (mobile.open) mobile.close();
  secondary = false;
  Object.assign(state, change);
  state.reviewOpen = !isNarrow() && !!selectedId();
  render();
}
async function refresh() {
  if (document.hidden || state.busy.size) return;
  const version = ++state.readVersion,
    generation = state.generation;
  try {
    const [data, calibration, exclusions] = await Promise.all([
      get("/api/state"),
      get("/api/calibration"),
      get("/api/exclusions"),
    ]);
    if (
      version !== state.readVersion ||
      generation !== state.generation ||
      state.busy.size
    )
      return;
    data.videos = { ...state.data.videos, ...data.videos };
    state.data = data;
    state.calibration = calibration;
    applyExclusions(exclusions);
    state.ready = true;
    $("#status").textContent = "";
    render();
  } catch (error) {
    if (version === state.readVersion && generation === state.generation)
      $("#status").textContent =
        `${error.message} Saved edits remain in this tab.`;
  }
}
async function mutate(key, body, apply, noteKey = key) {
  if (state.busy.has(key)) return;
  state.busy.add(key);
  state.generation++;
  state.notes.set(noteKey, { text: "Saving…" });
  render();
  try {
    const result = await post(body);
    apply?.(result);
    state.notes.set(noteKey, {
      text: body.value === "clear" ? "Cleared" : "Saved",
    });
  } catch (error) {
    state.notes.set(noteKey, { kind: "error", text: error.message });
  } finally {
    state.busy.delete(key);
    state.generation++;
    render();
    if (!state.busy.size) await refresh();
  }
}
function updateTags(rules) {
  for (const platform of ["youtube", "bilibili"]) {
    state.exclusions.tags[platform] = [
      ...(state.exclusions.tags[platform] || []).filter(
        (rule) => !rule.editable,
      ),
      ...(rules[platform] || []).map((tag) => ({
        tag,
        source: "manual",
        editable: true,
      })),
    ];
  }
}
const actions = {
  select(id) {
    rememberPosition();
    if (!mobile.open) returnFocus = document.activeElement;
    state.selected.set(contextKey(), id);
    state.reviewOpen = true;
    secondary = false;
    render();
    if (isNarrow() && !mobile.open) mobile.showModal();
  },
  rate(id, kind, payload, key, original) {
    mutate(key, { action: "rate", id, kind, ...payload }, () => {
      updateRating(id, kind, payload);
      if (!state.drafts.has(key) || equal(state.drafts.get(key), original))
        state.drafts.delete(key);
    });
  },
  video(id, action) {
    mutate(`video:${id}`, { action, id }, () =>
      updateRecord(
        id,
        action === "watched" || action === "unwatched"
          ? { watched: action === "watched" }
          : { dismissed: action === "dismiss" },
      ),
    );
  },
  creator(id, excluded) {
    const creator = record(id).creator;
    mutate(
      `creator:${creator.platform}:${creator.creator_id}`,
      { action: "creator_rule", id, excluded },
      applyExclusions,
      `creator:${id}`,
    );
  },
  exclusionTab(value) {
    navigate({ exclusionTab: value });
    $('.subtab[aria-selected="true"]')?.focus();
  },
  addTag(platform, tag) {
    mutate(
      `addtag:${platform}`,
      { action: "add_tag", id: "all", platform, tag },
      (rules) => {
        updateTags(rules);
        if (state.tagDrafts.get(platform) === tag)
          state.tagDrafts.delete(platform);
      },
    );
  },
  removeRule(kind, platform, entry) {
    const key =
      kind === "tags"
        ? `tag:${platform}:${entry.tag}`
        : `removecreator:${platform}:${entry.creator_id}`;
    mutate(
      key,
      {
        action: kind === "tags" ? "remove_tag" : "remove_creator",
        id: "all",
        platform,
        tag: entry.tag,
        creator_id: entry.creator_id,
      },
      kind === "creators" ? applyExclusions : updateTags,
    );
  },
  sync() {
    mutate("sync", { action: "sync_blacklist", id: "all" });
  },
  score(item) {
    const content = el("div", "technical");
    content.innerHTML = scoreDetails(
      item,
      state.data.runs[item.platform]?.weights || {},
    );
    const diagnostics = el("details", "diagnostics");
    diagnostics.append(el("summary", "", "Run and model diagnostics"));
    const pre = el(
      "pre",
      "",
      JSON.stringify(
        {
          run: { ...state.data.runs[item.platform], selected: undefined },
          learning: "Loading…",
        },
        null,
        2,
      ),
    );
    diagnostics.append(pre);
    content.append(diagnostics);
    get("/api/learning")
      .then((learning) => {
        pre.textContent = JSON.stringify(
          {
            run: { ...state.data.runs[item.platform], selected: undefined },
            learning,
          },
          null,
          2,
        );
      })
      .catch((error) => {
        pre.textContent = error.message;
      });
    if (isNarrow()) {
      state.inspectorScrolls.set(reviewId, mobileBody.scrollTop);
      secondary = true;
      mobileBody.replaceChildren(content);
      mobileBody.scrollTop = 0;
      $("#mobile-review-title").textContent = "Score details";
      $("#review-back").focus();
    } else {
      $("#details-body").replaceChildren(content);
      details.showModal();
    }
  },
};
function move(delta) {
  const items = currentEntries(),
    index = items.findIndex((item) => item.canonical_id === selectedId());
  if (items[index + delta]) actions.select(items[index + delta].canonical_id);
}
function render() {
  const isRules = state.view === "exclusions";
  const title =
    state.view === "recommendations"
      ? "Recommendations"
      : isRules
        ? "Exclusions"
        : "Calibration";
  $("#page-title").textContent = title;
  $("#page-subtitle").textContent = isRules
    ? "Choose the sources you want to see."
    : state.view === "calibration"
      ? "Optional examples to refine your preferences."
      : "A little familiar. A little unexpected.";
  document.querySelectorAll("[data-view]").forEach((node) => {
    if (node.dataset.view === state.view)
      node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  document.querySelectorAll("[data-platform]").forEach((node) => {
    node.setAttribute(
      "aria-selected",
      String(node.dataset.platform === state.platform),
    );
    node.tabIndex = node.dataset.platform === state.platform ? 0 : -1;
  });
  $("#workspace").hidden = isRules;
  $("#management").hidden = !isRules;
  if (isRules) {
    const key = `${state.platform}:${state.exclusionTab}`;
    if (managementKey !== key) {
      managementKey = key;
      management = createExclusions(actions);
      $("#management").replaceChildren(management.element);
      $("#management").scrollTop =
        state.scrolls.get(
          "management:" + state.platform + ":" + state.exclusionTab,
        ) || 0;
    }
    management.update();
    return;
  }
  const items = currentEntries();
  const nextRoute = contextKey() + ":" + !!earlier.get(contextKey());
  const signature =
    nextRoute +
    ":" +
    (state.data.runs[state.platform]?.id || "") +
    ":" +
    items.map((item) => item.canonical_id).join("|");
  if (signature !== listSignature) {
    listSignature = signature;
    rows = items.map((item, index) => createVideoRow(item, index, actions));
    list.replaceChildren(...rows.map((row) => row.element));
    listPane.scrollTop = state.scrolls.get(nextRoute) || 0;
  }
  rows.forEach((row) => row.update());
  $("#list-label").textContent =
    `${platformName(state.platform)} · ${items.length} ${state.view === "calibration" ? "examples" : "videos"}`;
  const pending =
    state.data.pending_items[state.platform]?.filter(
      (item) =>
        !state.data.runs[state.platform]?.selected.some(
          (pick) => pick.canonical_id === item.canonical_id,
        ),
    ).length || 0;
  $("#pending-toggle").hidden =
    state.view !== "recommendations" ||
    (!pending && !earlier.get(contextKey()));
  $("#pending-toggle").textContent = earlier.get(contextKey())
    ? "Current picks"
    : `Earlier unwatched (${pending})`;
  $("#empty-list").hidden = !!items.length;
  $("#empty-list").replaceChildren(
    el(
      "h2",
      "",
      state.view === "calibration" ? "Room to learn" : "Your next discoveries",
    ),
    el(
      "p",
      "",
      state.view === "calibration"
        ? "Calibration is optional. Ask your assistant to add examples when you want more practice."
        : state.ready
          ? "Start a recommendation round with your assistant. Your videos will appear here."
          : "Loading saved videos…",
    ),
  );
  const item = items.find((item) => item.canonical_id === selectedId());
  if (!item) {
    reviewId = undefined;
    review = undefined;
    inspectorBody.replaceChildren(placeholder);
    return;
  }
  const reviewRoute =
    nextRoute + ":" + (state.data.runs[state.platform]?.id || "");
  const changed = reviewId !== item.canonical_id || route !== reviewRoute;
  if (changed) {
    reviewId = item.canonical_id;
    review = createReview(item, actions);
    route = reviewRoute;
  }
  review.update();
  if (!secondary) {
    const parent = isNarrow() ? mobileBody : inspectorBody;
    if (review.element.parentElement !== parent)
      parent.replaceChildren(review.element);
    if (changed) parent.scrollTop = state.inspectorScrolls.get(reviewId) || 0;
    $("#mobile-review-title").textContent = "Video review";
  }
  const index = items.indexOf(item);
  $("#review-position").textContent = `${index + 1} of ${items.length}`;
  for (const prefix of ["review", "mobile"]) {
    $(`#${prefix}-previous`).disabled = index <= 0;
    $(`#${prefix}-next`).disabled = index >= items.length - 1;
  }
}
function back() {
  if (secondary) {
    secondary = false;
    mobileBody.replaceChildren(review.element);
    mobileBody.scrollTop = state.inspectorScrolls.get(reviewId) || 0;
    $("#mobile-review-title").textContent = "Video review";
    review.element.querySelector(".details-button").focus();
  } else {
    rememberPosition();
    mobile.close();
  }
}
mobile.addEventListener("cancel", (event) => {
  event.preventDefault();
  back();
});
mobile.addEventListener("close", () => {
  state.reviewOpen = false;
  secondary = false;
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
});
$("#review-back").onclick = back;
for (const prefix of ["review", "mobile"]) {
  $(`#${prefix}-previous`).onclick = () => move(-1);
  $(`#${prefix}-next`).onclick = () => move(1);
}
$("#details-close").onclick = () => details.close();
$("#help-open").onclick = () => $("#help-dialog").showModal();
$("#help-close").onclick = () => $("#help-dialog").close();
if ($("#preferences-dialog")) {
  const { createPreferenceTransfer } = await import("./transfer.js");
  createPreferenceTransfer({
    busy(active) {
      state.generation++;
      if (active) state.busy.add("preference-transfer");
      else {
        state.busy.delete("preference-transfer");
        refresh();
      }
    },
    changed() {
      $("#status").textContent =
        "Learned preferences replaced. Your video lists and rating drafts are preserved.";
    },
  });
}
$("#pending-toggle").onclick = () => {
  rememberPosition();
  if (!earlier.get(contextKey()))
    earlierItems.set(
      state.platform,
      state.data.pending_items[state.platform]?.filter(
        (item) =>
          !state.data.runs[state.platform]?.selected.some(
            (pick) => pick.canonical_id === item.canonical_id,
          ),
      ) || [],
    );
  earlier.set(contextKey(), !earlier.get(contextKey()));
  render();
};
document
  .querySelectorAll("[data-view]")
  .forEach(
    (node) => (node.onclick = () => navigate({ view: node.dataset.view })),
  );
document.querySelectorAll("[data-platform]").forEach((node) => {
  node.onclick = () => navigate({ platform: node.dataset.platform });
  node.onkeydown = (event) => {
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      navigate({
        platform: state.platform === "youtube" ? "bilibili" : "youtube",
      });
      $('[data-platform][aria-selected="true"]').focus();
    }
  };
});
function updateLayout() {
  const compact = isNarrow();
  const changed =
    document.documentElement.hasAttribute("data-compact") !== compact;
  document.documentElement.toggleAttribute("data-compact", compact);
  if (!changed) return;
  state.reviewOpen = !compact && !!selectedId();
  if (!compact && mobile.open) mobile.close();
  secondary = false;
  render();
}
const measure = el("span", "text-measure");
measure.setAttribute("aria-hidden", "true");
document.body.append(measure);
new ResizeObserver(updateLayout).observe(measure);
window.addEventListener("resize", updateLayout);
updateLayout();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
setInterval(refresh, 15000);
refresh();
