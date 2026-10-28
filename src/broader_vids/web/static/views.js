import { state, record, selectedId } from "./state.js";
import { el, button, link, number, dateLabel, platformName } from "./ui.js";

export function createVideoRow(item, index, actions) {
  const id = item.canonical_id;
  const row = el("li", "video-row");
  row.dataset.id = id;
  const rank = el("span", "row-rank", String(index + 1).padStart(2, "0"));
  const content = el("div", "row-content");
  const title = link(item.title, item.url, "video-title");
  const metadata = el("div", "row-meta");
  const creator = el("span", "row-creator");
  const duration = el("span", "row-duration");
  const status = el("span", "row-review-state");
  metadata.append(creator, duration, status);
  content.append(title, metadata);
  const controls = el("div", "row-controls");
  const score = el("span", "row-score", number(item.score));
  score.title = "Recommendation score / 100";
  score.setAttribute(
    "aria-label",
    `Recommendation score: ${number(item.score)} out of 100`,
  );
  const buttons = el("div", "row-actions");
  const watched = button(
    "Mark watched",
    () => actions.video(id, record(id).watched ? "unwatched" : "watched"),
    "button quiet small",
  );
  const dismiss = button(
    "Dismiss",
    () => actions.video(id, record(id).dismissed ? "restore" : "dismiss"),
    "button quiet small",
  );
  const rate = button(
    "Rate",
    () => actions.select(id),
    "button rate-button small",
  );
  const error = el("span", "row-error inline-error");
  error.setAttribute("role", "status");
  buttons.append(watched);
  if (state.view === "recommendations") buttons.append(dismiss);
  buttons.append(rate);
  controls.append(score, buttons, error);
  row.append(rank, content, controls);
  return {
    element: row,
    update() {
      const data = record(id);
      row.classList.toggle("selected", selectedId() === id);
      row.classList.toggle("dismissed", !!data.dismissed);
      row.setAttribute("aria-current", selectedId() === id ? "true" : "false");
      creator.textContent =
        data.creator?.name ||
        data.creator?.creator_id ||
        platformName(item.platform);
      const seconds =
        item.quality_evidence?.duration_seconds || item.duration_seconds;
      duration.textContent = seconds
        ? `${Math.round(seconds / 60)} min`
        : "Duration unavailable";
      status.textContent = data.dismissed
        ? "Dismissed"
        : Object.keys(data.details || {}).length
          ? "Reviewed"
          : "";
      watched.textContent = data.watched ? "Watched ✓" : "Mark watched";
      watched.setAttribute("aria-pressed", String(!!data.watched));
      dismiss.textContent = data.dismissed ? "Restore" : "Dismiss";
      watched.disabled = dismiss.disabled = state.busy.has(`video:${id}`);
      const note = state.notes.get(`video:${id}`);
      error.textContent = note?.kind === "error" ? note.text : "";
      rate.setAttribute(
        "aria-expanded",
        String(selectedId() === id && state.reviewOpen),
      );
    },
  };
}

export function createExclusions(actions) {
  const platform = state.platform;
  const kind = state.exclusionTab;
  const root = el("div", "exclusions-content");
  let connection;
  if (platform === "bilibili") {
    connection = el("section", "connection-panel");
    connection.append(el("h2", "section-title", "Bilibili connection"));
    const values = el("dl", "connection-fields");
    const status = el("dd");
    const account = el("dd");
    const last = el("dd");
    for (const [label, content] of [
      ["Sync status", status],
      ["Account", account],
      ["Last successful sync", last],
    ]) {
      const field = el("div");
      field.append(el("dt", "", label), content);
      values.append(field);
    }
    const footer = el("div", "connection-actions");
    const sync = button("Sync blacklist", actions.sync, "button small");
    const remote = link(
      "Manage on Bilibili ↗",
      "https://account.bilibili.com/account/blacklist",
      "subtle-link",
    );
    const error = el("p", "inline-error");
    const fallback = el("p", "muted compact");
    footer.append(sync, remote);
    connection.append(values, footer, error, fallback);
    root.append(connection);
    connection.update = () => {
      const data = state.exclusions.bilibili_sync || {};
      status.textContent =
        data.status === "complete"
          ? "Synced"
          : data.status === "failed"
            ? "Failed"
            : "Not synced";
      status.className = `sync-state ${data.status === "complete" ? "success" : data.status === "failed" ? "error" : ""}`;
      if (account.dataset.id !== (data.account_id || "")) {
        account.replaceChildren(
          data.account_id
            ? link(
                data.account_id,
                `https://space.bilibili.com/${data.account_id}`,
              )
            : el("span", "muted", "Not verified"),
        );
        account.dataset.id = data.account_id || "";
      }
      last.textContent = dateLabel(data.last_success);
      last.title = data.last_success
        ? `${dateLabel(data.last_success, true)} · ${data.last_success}`
        : "Never";
      sync.disabled = state.busy.has("sync");
      sync.textContent = sync.disabled ? "Syncing…" : "Sync blacklist";
      error.textContent =
        state.notes.get("sync")?.kind === "error"
          ? state.notes.get("sync").text
          : data.status === "failed"
            ? data.error || "Sync could not complete."
            : "";
      fallback.textContent = error.textContent
        ? "Saved rules remain active."
        : "";
    };
  }
  const tabs = el("div", "subtabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Exclusion type");
  for (const [value, label] of [
    ["creators", "Creators"],
    ["tags", "Tags"],
  ]) {
    const tab = button(label, () => actions.exclusionTab(value), "subtab");
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(kind === value));
    tab.tabIndex = kind === value ? 0 : -1;
    tab.onkeydown = (event) => {
      if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        actions.exclusionTab(kind === "creators" ? "tags" : "creators");
      }
    };
    tabs.append(tab);
  }
  root.append(tabs);
  const toolbar = el("div", "management-toolbar");
  const search = el("input", "text-input search-input");
  search.type = "search";
  search.placeholder = `Search ${kind}`;
  search.setAttribute("aria-label", `Search ${kind}`);
  const searchKey = `${platform}:${kind}`;
  search.value = state.search.get(searchKey) || "";
  search.oninput = () => {
    state.search.set(searchKey, search.value);
    updateList();
  };
  const count = el("span", "muted");
  toolbar.append(search, count);
  let input, add, formStatus;
  if (kind === "tags") {
    if (platform === "youtube")
      root.append(
        el(
          "p",
          "muted tag-help",
          "Matches creator-supplied keyword tags when available, rather than visible #hashtags.",
        ),
      );
    const form = el("form", "tag-form");
    const label = el(
      "label",
      "field-label",
      `Tag to exclude on ${platformName(platform)}`,
    );
    input = el("input", "text-input");
    input.required = true;
    input.maxLength = 120;
    input.placeholder = "Exact metadata tag";
    input.value = state.tagDrafts.get(platform) || "";
    input.oninput = () => state.tagDrafts.set(platform, input.value);
    label.append(input);
    add = el("button", "button primary add-tag-button", "Add tag");
    add.type = "submit";
    formStatus = el("span", "field-status");
    formStatus.setAttribute("role", "status");
    form.append(label, add, formStatus);
    form.onsubmit = (event) => {
      event.preventDefault();
      actions.addTag(platform, input.value);
    };
    root.append(form);
  }
  const list = el("div", "management-list");
  let signature = "";
  let rowUpdates = [];
  root.append(toolbar, list);
  function updateList() {
    const entries = state.exclusions[kind]?.[platform] || [];
    const query = (state.search.get(searchKey) || "")
      .trim()
      .toLocaleLowerCase();
    const filtered = entries.filter((entry) =>
      `${entry.name || ""} ${entry.creator_id || ""} ${entry.tag || ""}`
        .toLocaleLowerCase()
        .includes(query),
    );
    count.textContent = `${filtered.length} ${kind}`;
    const next = JSON.stringify(filtered);
    if (next === signature) {
      rowUpdates.forEach((update) => update());
      return;
    }
    signature = next;
    const restoreFocus = list.contains(document.activeElement);
    rowUpdates = [];
    list.replaceChildren();
    if (!filtered.length)
      list.append(
        el(
          "p",
          "empty-message",
          query
            ? "No matching rules."
            : `No ${kind === "creators" ? "creator" : "tag"} exclusions on ${platformName(platform)}.`,
        ),
      );
    for (const entry of filtered) {
      const row = el("div", "management-row");
      const info = el("div", "management-identity");
      if (kind === "creators")
        info.append(
          link(entry.name || entry.creator_id, entry.url, "creator-name"),
          el("span", "muted small-text", entry.creator_id),
        );
      else info.append(el("strong", "", entry.tag));
      const sources = el("div", "rule-sources");
      for (const source of entry.sources || [entry.source])
        sources.append(
          el(
            "span",
            "source-badge",
            source === "bilibili_blacklist"
              ? "Bilibili"
              : source === "builtin"
                ? "Built-in"
                : "Manual",
          ),
        );
      row.append(info, sources);
      const action = el("div", "rule-actions");
      row.append(action);
      const removable =
        kind === "tags" ? entry.editable : entry.sources.includes("manual");
      if (removable) {
        const key =
          kind === "tags"
            ? `tag:${platform}:${entry.tag}`
            : `removecreator:${platform}:${entry.creator_id}`;
        const remove = button(
          kind === "creators" && entry.imported
            ? "Remove manual rule"
            : "Remove",
          () => actions.removeRule(kind, platform, entry),
          "button danger small",
        );
        const error = el("span", "inline-error rule-error");
        error.setAttribute("role", "status");
        action.append(remove);
        row.append(error);
        const update = () => {
          remove.disabled = state.busy.has(key);
          const note = state.notes.get(key);
          error.textContent = note?.kind === "error" ? note.text : "";
        };
        rowUpdates.push(update);
        update();
      } else action.append(el("span", "muted small-text", "Read-only"));
      list.append(row);
    }
    if (restoreFocus) search.focus({ preventScroll: true });
  }
  return {
    element: root,
    update() {
      connection?.update();
      if (input) {
        if (document.activeElement !== input || !state.tagDrafts.has(platform))
          input.value = state.tagDrafts.get(platform) || "";
        const key = `addtag:${platform}`;
        add.disabled = state.busy.has(key);
        add.textContent = add.disabled ? "Saving…" : "Add tag";
        const note = state.notes.get(key);
        formStatus.textContent = note?.text || "";
        formStatus.className = `field-status ${note?.kind || ""}`;
      }
      updateList();
    },
  };
}
