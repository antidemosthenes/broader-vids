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

export function sendStatus(result, test = false) {
  if (result.outcome === "sent")
    return test
      ? "ntfy accepted the test message."
      : "ntfy accepted the recommendation list.";
  if (result.outcome === "busy") return "A delivery is already in progress.";
  if (result.outcome === "inactive")
    return "Turn on notifications in Preferences.";
  if (result.outcome === "topic_changed")
    return "The active topic changed. Test it again.";
  if (result.outcome === "stale")
    return "The recommendation list changed. Try again.";
  if (result.outcome === "unavailable")
    return "This list is unavailable to send.";

  const accepted = result.accepted_parts || 0;
  const detail =
    result.outcome === "uncertain"
      ? "Delivery could not be confirmed."
      : result.outcome === "canceled"
        ? "Delivery stopped because notification settings changed."
        : "Delivery failed.";
  const count =
    result.total_parts && !test
      ? ` ntfy confirmed ${accepted} of ${result.total_parts} parts.`
      : "";
  const retry =
    accepted || result.outcome === "uncertain"
      ? " Sending again may send a duplicate."
      : "";
  return detail + count + retry;
}

export function requestDelivery(action, body) {
  return request(`${endpoint}/${action}`, body, {
    unconfirmedMessage: sendStatus({ outcome: "uncertain" }),
  });
}

export function createNotifications(changed) {
  const find = (id) => document.getElementById(`notifications-${id}`);
  const form = find("form"),
    toggle = find("enabled"),
    topic = find("topic"),
    activeAddress = find("active-address"),
    topicUrl = find("topic-url"),
    save = find("save"),
    test = find("test"),
    clear = find("clear"),
    reload = find("reload"),
    status = find("status"),
    history = find("history"),
    deliveries = find("deliveries");
  let saved,
    reading,
    saving = false,
    testing = false,
    testingDestination,
    retrying = "",
    revision = 0,
    historyKey = "",
    testStatusDestination,
    statusRevision = 0;

  function setStatus(message, destination) {
    statusRevision++;
    testStatusDestination = destination;
    status.textContent = message;
  }

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
    test.hidden = !hasTopic || !saved.enabled;
    test.setAttribute("aria-disabled", String(testing || saving));
    test.textContent =
      testing && testingDestination === saved?.destination_id
        ? "Testing…"
        : "Test";
    save.disabled = clear.disabled = saving;
    reload.disabled = Boolean(reading);
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
    if (
      testStatusDestination &&
      testStatusDestination !== value.destination_id
    ) {
      testStatusDestination = undefined;
      status.textContent = "";
    }
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
    reading = request(endpoint, {});
    if (!saved) setStatus("Loading notifications…");
    controls();
    try {
      const value = await reading;
      if (current !== revision) return;
      if (!saved) setStatus("");
      reload.hidden = true;
      show(value);
    } catch (error) {
      if (!saved) {
        setStatus(error.message);
        reload.hidden = false;
      }
    } finally {
      reading = undefined;
      controls();
    }
  }

  async function configure(enabled, destination, message, clearDraft) {
    if (!saved || saving) return;
    const focused = document.activeElement;
    let savedChange = false;
    revision++;
    saving = true;
    setStatus("Saving notifications…");
    controls();
    try {
      const value = await request(endpoint, { enabled, topic: destination });
      toggle.checked = value.enabled;
      if (clearDraft) topic.value = "";
      show(value);
      changed(value.enabled && value.has_topic);
      savedChange = true;
      setStatus(message);
    } catch (error) {
      if (!enabled && destination === null) toggle.checked = saved.enabled;
      setStatus(error.message);
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
    setStatus("Retrying delivery…");
    showDeliveries();
    try {
      const value = await request(`${endpoint}/retry`, { run_id: runId });
      if (current === revision) {
        show(value);
        const row = value.deliveries.find((row) => row.run_id === runId);
        setStatus(deliveryLabels[row?.status] || "Delivery status updated.");
      }
    } catch (error) {
      if (current === revision) setStatus(error.message);
    } finally {
      retrying = "";
      showDeliveries();
      if (current !== revision) refresh();
    }
  }

  test.onclick = async () => {
    if (testing || saving || !saved?.enabled) return;
    const current = ++revision;
    const destinationId = saved.destination_id;
    testing = true;
    testingDestination = destinationId;
    setStatus("Sending test message…", destinationId);
    const currentStatus = statusRevision;
    controls();
    try {
      const result = await requestDelivery("test", {
        destination_id: destinationId,
      });
      if (current !== revision || currentStatus !== statusRevision) return;
      if (
        result.outcome !== "topic_changed" &&
        result.outcome !== "inactive" &&
        saved?.destination_id !== destinationId
      )
        return;
      setStatus(
        sendStatus(result, true),
        result.outcome === "topic_changed" || result.outcome === "inactive"
          ? undefined
          : destinationId,
      );
      if (result.outcome === "topic_changed" || result.outcome === "inactive") {
        await reading?.catch(() => {});
        if (current === revision) await refresh();
      }
    } catch (error) {
      if (
        current === revision &&
        saved?.destination_id === destinationId &&
        currentStatus === statusRevision
      )
        setStatus(error.message, destinationId);
    } finally {
      testing = false;
      testingDestination = undefined;
      controls();
    }
  };
  reload.onclick = refresh;
  toggle.onchange = () => {
    controls();
    if (!toggle.checked && saved.enabled) {
      configure(false, null, "Notifications turned off.", false);
    } else {
      setStatus(toggle.checked ? "Save to turn on notifications." : "");
      if (toggle.checked && !saved.has_topic) topic.focus();
    }
  };
  topic.oninput = () => {
    setStatus("");
    controls();
  };
  clear.onclick = () => configure(false, "", "Saved topic cleared.", true);
  form.onsubmit = (event) => {
    event.preventDefault();
    if (save.hidden) return;
    const value = topic.value.trim();
    if (!value && !saved.has_topic && toggle.checked) {
      setStatus("Enter an ntfy topic to turn on notifications.");
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
