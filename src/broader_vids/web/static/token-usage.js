import { get } from "./api.js";
import { el, dateLabel, platformName } from "./ui.js";

export function createTokenUsage() {
  const dialog = document.getElementById("token-usage-dialog"),
    select = document.getElementById("token-usage-run"),
    status = document.getElementById("token-usage-status"),
    content = document.getElementById("token-usage-content");
  let selected,
    loading = false,
    version = 0,
    timer;
  const format = (number) => number.toLocaleString();
  const label = (run) =>
    `${dateLabel(run.created_at)} · ${run.platform === "both" ? "YouTube + Bilibili" : platformName(run.platform)} · ${run.status}`;
  function schedule() {
    clearTimeout(timer);
    if (dialog.open && !document.hidden) timer = setTimeout(load, 5000);
  }
  function render(report) {
    content.replaceChildren();
    if (!report) {
      content.append(
        el(
          "p",
          "empty-message",
          "No recommendation rounds yet. Usage will appear here when a round starts.",
        ),
      );
      return;
    }
    const totals = report.totals;
    const measured = totals.reported_calls > 0 || totals.calls === 0;
    const summary = el("dl", "usage-summary");
    for (const [name, value] of [
      ["Reported tokens", measured ? totals.total_tokens : null],
      ["Input", measured ? totals.input_tokens : null],
      ["Output", measured ? totals.output_tokens : null],
      [
        "Cached input",
        totals.cached_reported_calls ? totals.cached_input_tokens : null,
      ],
    ]) {
      const item = el("div");
      item.append(
        el("dt", "muted", name),
        el("dd", "", value == null ? "Not reported" : format(value)),
      );
      summary.append(item);
    }
    content.append(summary);
    content.append(
      el(
        "p",
        "usage-coverage",
        totals.calls
          ? `${totals.reported_calls} of ${totals.calls} model attempts reported usage.${totals.unreported_calls ? ` ${totals.unreported_calls} unreported. Totals are partial.` : ""}`
          : "No model attempts recorded for this round.",
      ),
    );
    if (
      totals.cached_reported_calls &&
      totals.cached_reported_calls < totals.reported_calls
    )
      content.append(
        el(
          "p",
          "muted compact",
          "Cached input is reported for only some attempts.",
        ),
      );
    const title = el("h3", "usage-heading", "Share of reported tokens");
    content.append(title);
    for (const stage of report.stages) {
      const row = el("section", "usage-stage");
      const header = el("div", "usage-stage-heading");
      const share =
        stage.share_percent == null
          ? "—"
          : `${stage.share_percent.toFixed(1)}%`;
      header.append(
        el("h4", "", stage.label),
        el(
          "span",
          "usage-stage-total",
          stage.calls && !stage.reported_calls
            ? "Not reported"
            : `${format(stage.total_tokens)} · ${share}`,
        ),
      );
      const bar = el("div", "usage-bar");
      bar.setAttribute("aria-hidden", "true");
      const fill = el("span");
      fill.style.width = `${stage.share_percent || 0}%`;
      bar.append(fill);
      const detail =
        stage.calls && !stage.reported_calls
          ? `Usage is missing for ${stage.calls} model ${stage.calls === 1 ? "attempt" : "attempts"}.`
          : stage.calls
            ? `${format(stage.input_tokens)} input · ${format(stage.output_tokens)} output${stage.unreported_calls ? ` · ${stage.unreported_calls} ${stage.unreported_calls === 1 ? "attempt" : "attempts"} unreported` : ""}`
            : "No Codex tokens used by this step.";
      row.append(header, bar, el("p", "muted compact", detail));
      content.append(row);
    }
    const platforms = el(
      "p",
      "muted usage-platforms",
      report.platforms
        .map(
          (item) =>
            `${platformName(item.platform)}: ${format(item.tokens)} reported tokens · ${item.status}${item.unreported_calls ? " · partial" : ""}`,
        )
        .join(" / "),
    );
    content.append(platforms, el("p", "muted usage-scope", report.scope));
    if (report.legacy)
      content.append(
        el(
          "p",
          "muted usage-scope",
          "Historical attempts use saved checkpoints. Unrecorded usage cannot be reconstructed.",
        ),
      );
  }
  async function load() {
    if (!dialog.open || document.hidden || loading) return schedule();
    loading = true;
    const current = version,
      requested = selected;
    try {
      const value = await get(
        `/api/token-usage${requested ? `?run_id=${encodeURIComponent(requested)}` : ""}`,
      );
      if (current !== version || !dialog.open) return;
      const report = value.report;
      selected = report?.run_id;
      const runs = [...value.runs];
      if (report && !runs.some((run) => run.id === selected))
        runs.push({ id: selected, ...report });
      if (select.dataset.runs !== JSON.stringify(runs)) {
        select.dataset.runs = JSON.stringify(runs);
        select.replaceChildren(
          ...runs.map((run) => new Option(label(run), run.id)),
        );
      }
      select.value = selected || "";
      select.disabled = !runs.length;
      if (content.dataset.report !== JSON.stringify(report)) {
        content.dataset.report = JSON.stringify(report);
        render(report);
      }
      status.textContent = "";
    } catch (error) {
      if (current === version && dialog.open)
        status.textContent = error.message;
    } finally {
      loading = false;
      if (current !== version && dialog.open) load();
      else schedule();
    }
  }
  document.getElementById("token-usage-open").onclick = () => {
    ++version;
    status.textContent = "Loading saved usage…";
    dialog.showModal();
    load();
  };
  document.getElementById("token-usage-close").onclick = () => dialog.close();
  select.onchange = () => {
    selected = select.value;
    ++version;
    load();
  };
  dialog.addEventListener("close", () => {
    ++version;
    clearTimeout(timer);
  });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden && dialog.open) load();
  });
}
