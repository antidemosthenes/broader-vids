import {
  state,
  contextKey,
  selectedId,
  entries,
  record,
  updateRecord,
  updateRating,
  applyExclusions,
  applyTagRules,
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
  secondary = false,
  preferences,
  preferencesReturnView = "recommendations";
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
  if (state.view === "preferences" && $("#preferences-page"))
    state.scrolls.set("preferences", $("#preferences-page").scrollTop);
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
  if (change.view === "preferences" && state.view !== "preferences")
    preferencesReturnView = state.view;
  rememberPosition();
  if (change.view && change.view !== state.view) route = "";
  if (mobile.open) mobile.close();
  secondary = false;
  Object.assign(state, change);
  state.reviewOpen = !isNarrow() && !!selectedId();
  render();
}
let refreshing = false,
  refreshQueued = false;
async function refresh() {
  if (document.hidden || state.busy.size) return;
  if (refreshing) {
    refreshQueued = true;
    return;
  }
  refreshing = true;
  const version = ++state.readVersion,
    generation = state.generation;
  try {
    const responses = await Promise.allSettled([
      get("/api/state"),
      get("/api/calibration"),
      get("/api/exclusions"),
    ]);
    const failure = responses.find((result) => result.status === "rejected");
    if (failure) throw failure.reason;
    const [data, calibration, exclusions] = responses.map(
      (result) => result.value,
    );
    if (
      version !== state.readVersion ||
      generation !== state.generation ||
      state.busy.size
    )
      return;
    const retained = {};
    const retain = (id) => {
      if (state.data.videos[id]) retained[id] = state.data.videos[id];
    };
    for (const key of state.drafts.keys())
      retain(key.slice(0, key.lastIndexOf("|")));
    for (const items of earlierItems.values())
      for (const item of items) retain(item.canonical_id);
    data.videos = { ...retained, ...data.videos };
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
  } finally {
    refreshing = false;
    if (refreshQueued) {
      refreshQueued = false;
      queueMicrotask(refresh);
    }
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
      `tags:${platform}`,
      { action: "add_tag", id: "all", platform, tag },
      (rules) => {
        applyTagRules(rules);
        if (state.tagDrafts.get(platform) === tag)
          state.tagDrafts.delete(platform);
      },
      `addtag:${platform}`,
    );
  },
  removeRule(kind, platform, entry) {
    const key =
      kind === "tags"
        ? `tag:${platform}:${entry.tag}`
        : `removecreator:${platform}:${entry.creator_id}`;
    mutate(
      kind === "tags" ? `tags:${platform}` : key,
      {
        action: kind === "tags" ? "remove_tag" : "remove_creator",
        id: "all",
        platform,
        tag: entry.tag,
        creator_id: entry.creator_id,
      },
      kind === "creators" ? applyExclusions : applyTagRules,
      key,
    );
  },
  switchAccount() {
    accountSwitch?.start();
  },
  confirmAccount() {
    accountSwitch?.confirm();
  },
  cancelAccount() {
    accountSwitch?.cancel();
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
  const isRules = state.view === "exclusions",
    isPreferences = state.view === "preferences";
  const title = isPreferences
    ? "Preferences"
    : state.view === "recommendations"
      ? "Recommendations"
      : isRules
        ? "Exclusions"
        : "Calibration";
  $("#page-title").textContent = title;
  $("#page-subtitle").textContent = isPreferences
    ? "Your interests and learned preferences."
    : isRules
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
  $("#workspace").hidden = isRules || isPreferences;
  $("#management").hidden = !isRules;
  $(".platform-switch").hidden = isPreferences;
  if ($("#preferences-back")) $("#preferences-back").hidden = !isPreferences;
  const preferencesPage = $("#preferences-page"),
    enteringPreferences = isPreferences && preferencesPage?.hidden;
  if (preferencesPage) preferencesPage.hidden = !isPreferences;
  if (isPreferences) {
    if (enteringPreferences)
      preferencesPage.scrollTop = state.scrolls.get("preferences") || 0;
    preferences?.open();
    return;
  }
  preferences?.close();
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
  const run = state.data.runs[state.platform];
  const preview =
    state.view === "recommendations" &&
    !earlier.get(contextKey()) &&
    run?.provisional &&
    items.length > 0;
  $("#round-notice").hidden = !preview;
  $("#round-notice").textContent = preview
    ? `Provisional preview · ${run.assessed} of ${run.inspection} assessed. This list is not yet published. You can save ratings now.`
    : "";
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
function leavePreferences() {
  navigate({ view: preferencesReturnView });
  $("#preferences-open").focus({ preventScroll: true });
}
$("#preferences-back")?.addEventListener("click", leavePreferences);
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    state.view === "preferences" &&
    !document.querySelector("dialog[open]")
  ) {
    event.preventDefault();
    leavePreferences();
  }
});
if ($("#preferences-page")) {
  import("./preferences.js")
    .then(({ createPreferences }) => {
      preferences = createPreferences({
        getProfile() {
          return state.data.profile;
        },
        busy(active) {
          state.generation++;
          if (active) state.busy.add("preferences");
          else {
            state.busy.delete("preferences");
            refresh();
          }
        },
        changed(profile) {
          if (profile && state.data) state.data.profile = profile;
        },
      });
      $("#preferences-open").disabled = false;
      if (state.view === "preferences") preferences.open();
    })
    .catch(() => {
      $("#preferences-open").disabled = false;
      $("#preferences-open").onclick = () => {
        $("#status").textContent = "Restart the dashboard to use Preferences.";
      };
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
  const root = document.documentElement,
    compact = isNarrow(),
    mobileNavigation =
      innerWidth <= 700 ||
      innerWidth / parseFloat(getComputedStyle(root).fontSize) < 700 / 15;
  const changed =
    root.hasAttribute("data-compact") !== compact ||
    root.hasAttribute("data-mobile") !== mobileNavigation;
  root.toggleAttribute("data-compact", compact);
  root.toggleAttribute("data-mobile", mobileNavigation);
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
if ($("#token-usage-dialog")) {
  import("./token-usage.js")
    .then(({ createTokenUsage }) => createTokenUsage())
    .catch(() => {
      $("#token-usage-open").onclick = () => {
        $("#status").textContent =
          "Restart the dashboard to enable token usage reports.";
      };
    });
}
let accountSwitch;
state.accountSwitch = {
  active: false,
  can_start: false,
  message: "Loading account controls…",
};
import("./bilibili-account.js")
  .then(({ createBilibiliAccount }) => {
    accountSwitch = createBilibiliAccount({ changed: render, refresh });
  })
  .catch(() => {
    state.accountSwitch = {
      active: false,
      can_start: false,
      message: "Restart the dashboard to enable account switching.",
    };
    render();
  });
setInterval(refresh, 15000);
refresh();
