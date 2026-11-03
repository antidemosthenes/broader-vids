import { state, record, draftKey } from "./state.js";
import { el, button, link, platformName } from "./ui.js";

export function createReview(item, actions) {
  const id = item.canonical_id;
  const root = el("div", "review-content");
  const title = link(item.title, item.url, "review-title");
  const creatorName = el("p", "muted");
  const reason = el(
    "p",
    "review-reason",
    item.quality_evidence?.summary ||
      (item.score != null
        ? "Selected for your recommendation list."
        : "An extra example for your feedback."),
  );
  root.append(title, creatorName, reason);
  const forms = [];
  for (const [dimension, label] of [
    ["topic_interest", "Specific topic"],
    ["depth", "Depth for this video"],
    ["quality_level", "Video quality"],
    ["domain_interest", "Broader subject"],
  ]) {
    const form = el("form", "rating-form");
    const fieldset = el("fieldset", "rating-group");
    fieldset.append(el("legend", "", label));
    const key = draftKey(id, dimension);
    const target = el("input", "text-input");
    const suggestions = el("datalist");
    suggestions.id = `suggestions-${dimension}-${id.replace(/[^a-zA-Z0-9]/g, "_")}`;
    const level = el("select", "select-input");
    const radios = [];
    let source = "ai";
    if (dimension !== "quality_level") {
      const targetLabel = el(
        "label",
        "field-label",
        dimension === "domain_interest"
          ? "Subject"
          : dimension === "depth"
            ? "Topic for depth feedback"
            : "Topic",
      );
      target.type = "text";
      target.maxLength = 120;
      target.setAttribute("list", suggestions.id);
      target.placeholder =
        dimension === "domain_interest"
          ? "Optional broader subject"
          : "Specific topic";
      targetLabel.append(target);
      fieldset.append(targetLabel, suggestions);
    }
    if (dimension === "depth") {
      const levelLabel = el("label", "field-label", "Video level");
      for (const [value, text] of [
        ["unknown", "Unknown"],
        ["introductory", "Introductory"],
        ["intermediate", "Intermediate"],
        ["advanced", "Advanced"],
      ])
        level.add(new Option(text, value));
      levelLabel.append(level);
      fieldset.append(levelLabel);
    }
    const options = el("div", "rating-options");
    const values =
      dimension === "depth"
        ? [
            ["deeper", "Too basic"],
            ["suitable", "About right"],
            ["lighter", "Too advanced"],
          ]
        : [
            ["high", "High"],
            ["medium", "Medium"],
            ["low", "Low"],
          ];
    for (const [value, text] of values) {
      const option = el("label", "rating-option");
      const input = el("input");
      input.type = "radio";
      input.name = key;
      input.value = value;
      option.append(input, el("span", "", text));
      options.append(option);
      radios.push(input);
    }
    fieldset.append(options);
    const footer = el("div", "rating-footer");
    const message = el("span", "field-status");
    message.setAttribute("role", "status");
    const read = () => ({
      value: radios.find((input) => input.checked)?.value || "",
      target: target.value,
      level: dimension === "depth" ? level.value : "unknown",
      level_source: source,
    });
    const remember = () => {
      state.drafts.set(key, read());
      if (!state.busy.has(key)) state.notes.delete(key);
      updateFooter();
    };
    radios.forEach((input) => input.addEventListener("input", remember));
    target.addEventListener("input", remember);
    level.addEventListener("change", () => {
      source = "user";
      remember();
    });
    const save = button(
      "Save",
      () => form.requestSubmit(),
      "button primary small",
    );
    const clear = button(
      "Clear",
      () =>
        actions.rate(id, dimension, { ...read(), value: "clear" }, key, read()),
      "button quiet small",
    );
    footer.append(message, clear, save);
    fieldset.append(footer);
    form.append(fieldset);
    form.onsubmit = (event) => {
      event.preventDefault();
      const payload = read();
      if (!payload.value) {
        state.notes.set(key, { kind: "error", text: "Choose a rating." });
        updateFooter();
        return;
      }
      actions.rate(id, dimension, payload, key, payload);
    };
    function updateFooter() {
      const note = state.notes.get(key);
      message.textContent =
        (state.drafts.has(key) && !state.busy.has(key) && note?.kind !== "error"
          ? "Unsaved"
          : note?.text) ||
        (state.drafts.has(key)
          ? "Unsaved"
          : record(id).details?.[dimension]
            ? "Saved"
            : "Not rated");
      message.className = `field-status ${note?.kind || ""}`;
      save.disabled = state.busy.has(key);
      clear.disabled =
        state.busy.has(key) ||
        (!state.drafts.has(key) && !record(id).details?.[dimension]);
    }
    function update() {
      const data = record(id);
      const context = data.feedback_context || {};
      const saved = data.details?.[dimension] || {};
      const prior = state.drafts.get(key) || saved;
      const choices =
        dimension === "domain_interest"
          ? context.domains || []
          : context.topics || [];
      const focus = document.activeElement;
      const editing = fieldset.contains(focus);
      if (!editing || !state.drafts.has(key)) {
        radios.forEach(
          (input) => (input.checked = input.value === prior.value),
        );
        target.value = prior.target ?? choices[0] ?? "";
        source = prior.level_source === "user" ? "user" : "ai";
        level.value =
          source === "user"
            ? prior.level
            : context.audience_level || prior.level || "unknown";
      }
      if (suggestions.dataset.choices !== JSON.stringify(choices)) {
        suggestions.dataset.choices = JSON.stringify(choices);
        suggestions.replaceChildren(
          ...choices.map((value) => new Option(value, value)),
        );
      }
      updateFooter();
    }
    root.append(form);
    forms.push({ update });
  }
  const creator = el("div", "creator-rule");
  const creatorLabel = el("label", "toggle-label");
  const toggle = el("input");
  toggle.type = "checkbox";
  creatorLabel.append(
    toggle,
    el("span", "", "Exclude this creator from future rounds"),
  );
  const explanation = el("p", "muted compact");
  explanation.setAttribute("role", "status");
  creator.append(creatorLabel, explanation);
  toggle.onchange = () => actions.creator(id, toggle.checked);
  root.append(
    creator,
    button("Score details", () => actions.score(item), "button details-button"),
  );
  return {
    element: root,
    update() {
      const data = record(id);
      creatorName.textContent =
        data.creator?.name ||
        data.creator?.creator_id ||
        platformName(item.platform);
      forms.forEach((form) => form.update());
      const busy = state.busy.has(
        `creator:${data.creator?.platform}:${data.creator?.creator_id}`,
      );
      const note = state.notes.get(`creator:${id}`);
      if (!busy) toggle.checked = !!data.creator?.excluded;
      toggle.disabled =
        busy || !data.creator?.creator_id || !!data.creator?.imported;
      explanation.textContent =
        (note?.kind === "error"
          ? note.text
          : note?.text
            ? `${note.text}. Applies to the next new round.`
            : "") ||
        (data.creator?.imported
          ? "Managed on Bilibili."
          : !data.creator?.creator_id
            ? "Creator ID is unavailable."
            : "Applies to the next new round.");
    },
  };
}
