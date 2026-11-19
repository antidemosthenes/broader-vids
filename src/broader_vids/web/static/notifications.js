import { request } from "./api.js";
import { button, dateLabel, el, platformName } from "./ui.js";

const endpoint = "/api/notifications";
const deliveryLabels = {
  pending: "Waiting to send",
  sending: "Sending",
  sent: "Sent",
  failed: "Delivery failed",
  uncertain: "Delivery unconfirmed",
  canceled: "Canceled",
};

export function createNotifications() {
  const find = (id) => document.getElementById(`notifications-${id}`);
  const form = find("form"),
    toggle = find("enabled"),
    topic = find("topic"),
    activeAddress = find("active-address"),
    topicUrl = find("topic-url"),
    save = find("save"),
    clear = find("clear"),
    reload = find("reload"),
    status = find("status"),
    history = find("history"),
    deliveries = find("deliveries");
  let saved,
    reading = false,
    saving = false,
    retrying = "",
    revision = 0,
    historyKey = "";

  function controls() {
    const hasTopic = Boolean(saved?.has_topic);
    toggle.disabled = !saved || saving;
    topic.disabled = toggle.disabled;
    topic.required = toggle.checked && !hasTopic;
    find("topic-settings").hidden =
      !toggle.checked && !hasTopic && !topic.value;
    save.hidden =
      !saved || (!topic.value.trim() && toggle.checked === saved.enabled);
    clear.hidden = !hasTopic;
    save.disabled = clear.disabled = saving;
    reload.disabled = reading;
  }

  function showDeliveries() {
    const rows = saved.deliveries;
    const key = JSON.stringify([rows, saved.enabled, retrying]);
    if (key === historyKey) return;
    historyKey = key;
    const focused = deliveries.contains(document.activeElement)
      ? document.activeElement.dataset.runId
      : undefined;
    let nextFocus;
    deliveries.replaceChildren();
    history.hidden = !rows.length;
    for (const row of rows) {
      const item = el("li", "notification-delivery");
      const summary = el("div");
      const platform = platformName(row.platform);
      const round = el("strong", "", platform);
      if (row.published_at && Number.isFinite(Date.parse(row.published_at)))
        round.append(` · ${dateLabel(row.published_at)}`);
      const label =
        retrying === row.run_id
          ? "Retrying…"
          : deliveryLabels[row.status] || "Status unavailable";
      summary.append(round, el("p", "muted small-text", label));
      if (row.status === "uncertain" && row.can_retry)
        summary.append(
          el("p", "note small-text", "Retrying may send a duplicate."),
        );
      item.append(summary);
      if (row.can_retry && saved.enabled) {
        const retry = button(
          "Retry",
          () => retryDelivery(row.run_id),
          "button quiet",
        );
        retry.dataset.runId = row.run_id;
        retry.setAttribute("aria-label", `Retry ${round.textContent} delivery`);
        retry.setAttribute("aria-disabled", String(Boolean(retrying)));
        item.append(retry);
        if (focused === row.run_id) nextFocus = retry;
      }
      deliveries.append(item);
    }
    if (focused) (nextFocus || status).focus({ preventScroll: true });
  }

  function show(value) {
    if (!saved || toggle.checked === saved.enabled)
      toggle.checked = value.enabled;
    saved = value;
    const address = saved.enabled ? saved.topic_url || "" : "";
    if (topicUrl.textContent !== address) topicUrl.textContent = address;
    activeAddress.hidden = !address;
    controls();
    showDeliveries();
  }

  async function refresh() {
    if (reading || saving || retrying) return;
    const current = revision;
    reading = true;
    if (!saved) status.textContent = "Loading notifications…";
    controls();
    try {
      const value = await request(endpoint, {});
      if (current !== revision) return;
      if (!saved) status.textContent = "";
      reload.hidden = true;
      show(value);
    } catch (error) {
      if (!saved) {
        status.textContent = error.message;
        reload.hidden = false;
      }
    } finally {
      reading = false;
      controls();
    }
  }

  async function configure(enabled, destination, message, clearDraft) {
    if (!saved || saving) return;
    const focused = document.activeElement;
    let savedChange = false;
    revision++;
    saving = true;
    status.textContent = "Saving notifications…";
    controls();
    try {
      const value = await request(endpoint, { enabled, topic: destination });
      toggle.checked = value.enabled;
      if (clearDraft) topic.value = "";
      show(value);
      savedChange = true;
      status.textContent = message;
    } catch (error) {
      if (!enabled && destination === null) toggle.checked = saved.enabled;
      status.textContent = error.message;
    } finally {
      saving = false;
      controls();
      if (form.contains(focused) && document.activeElement === document.body) {
        (savedChange ? toggle : focused).focus({ preventScroll: true });
      }
    }
  }

  async function retryDelivery(runId) {
    if (retrying || saving || !saved.enabled) return;
    const current = ++revision;
    retrying = runId;
    status.textContent = "Retrying delivery…";
    showDeliveries();
    try {
      const value = await request(`${endpoint}/retry`, { run_id: runId });
      if (current === revision) {
        show(value);
        const row = value.deliveries.find((row) => row.run_id === runId);
        status.textContent =
          deliveryLabels[row?.status] || "Delivery status updated.";
      }
    } catch (error) {
      if (current === revision) status.textContent = error.message;
    } finally {
      retrying = "";
      showDeliveries();
      if (current !== revision) refresh();
    }
  }

  reload.onclick = refresh;
  toggle.onchange = () => {
    controls();
    if (!toggle.checked && saved.enabled) {
      configure(false, null, "Notifications turned off.", false);
    } else {
      status.textContent = toggle.checked
        ? "Save to turn on notifications."
        : "";
      if (toggle.checked && !saved.has_topic) topic.focus();
    }
  };
  topic.oninput = () => {
    status.textContent = "";
    controls();
  };
  clear.onclick = () => configure(false, "", "Saved topic cleared.", true);
  form.onsubmit = (event) => {
    event.preventDefault();
    if (save.hidden) return;
    const value = topic.value.trim();
    if (!value && !saved.has_topic && toggle.checked) {
      status.textContent = "Enter an ntfy topic to turn on notifications.";
      topic.focus();
      return;
    }
    configure(
      toggle.checked,
      value || null,
      toggle.checked ? "" : "Topic saved.",
      true,
    );
  };
  controls();
  return { refresh };
}
