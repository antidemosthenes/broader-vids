import { request } from "./api.js";

export function createPreferenceTransfer({ busy, changed }) {
  const find = (id) => document.getElementById(`preferences-${id}`);
  const dialog = find("dialog"),
    file = find("file"),
    status = find("status");
  let active = false,
    text,
    preview;
  function reset() {
    text = preview = undefined;
    find("preview").hidden = true;
  }
  function showPreview() {
    const summary = find("summary");
    summary.replaceChildren();
    for (const [label, value] of [
      ["Interests", preview.interests.join(", ") || "None"],
      ["Model parameters", preview.model_parameters.toLocaleString()],
      [
        "Preference groups",
        Object.values(preview.preference_cells)
          .reduce((a, b) => a + b, 0)
          .toLocaleString(),
      ],
    ]) {
      const term = document.createElement("dt"),
        detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value;
      summary.append(term, detail);
    }
    find("preview").hidden = false;
  }
  async function operation(message, task) {
    if (active) return;
    active = true;
    busy(true);
    status.textContent = message;
    for (const name of ["export", "import", "file", "close", "choose"])
      if (find(name)) find(name).disabled = true;
    try {
      await task();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      active = false;
      busy(false);
      for (const name of ["export", "import", "file", "close", "choose"])
        if (find(name)) find(name).disabled = false;
    }
  }
  find("open").onclick = () => dialog.showModal();
  find("open").textContent = "Import/Export";
  find("title").textContent = "Import/Export";
  if (find("choose"))
    find("choose").onclick = () => {
      file.value = "";
      file.click();
    };
  if (find("check")) find("check").hidden = true;
  find("close").onclick = () => dialog.close();
  dialog.addEventListener("cancel", (event) => {
    if (active) event.preventDefault();
  });
  find("export").onclick = () =>
    operation("Preparing preference file…", async () => {
      const result = await request("/api/preferences/export", {});
      const url = URL.createObjectURL(
        new Blob([result.file_text], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `broader-vids-preferences-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent =
        "Preference file ready. Your browser manages the download.";
    });
  file.onchange = () => {
    reset();
    const selected = file.files[0];
    if (find("filename"))
      find("filename").textContent = selected?.name || "No file selected";
    if (!selected) {
      status.textContent = "";
      return;
    }
    operation("Checking preference file…", async () => {
      if (selected.size > 8 * 1024 * 1024)
        throw Error("The preference file must not exceed 8 MiB.");
      text = (await selected.text()).replace(/^\uFEFF/, "");
      preview = await request("/api/preferences/preview", { file_text: text });
      showPreview();
      status.textContent =
        "File is compatible. Check the summary before importing.";
    });
  };
  find("import").onclick = () => {
    if (!preview || text === undefined) return;
    operation("Checking preferences…", async () => {
      const current = await request("/api/preferences/preview", {
        file_text: text,
      });
      if (current.revision !== preview.revision) {
        preview = current;
        showPreview();
        status.textContent =
          "Preferences changed since you chose this file. Check the summary, then select Import preferences again.";
        return;
      }
      status.textContent = "Replacing learned preferences…";
      const result = await request("/api/preferences/import", {
        file_text: text,
        revision: preview.revision,
      });
      reset();
      file.value = "";
      if (find("filename")) find("filename").textContent = "No file selected";
      status.textContent = `Preferences imported. Recovery file: ${result.backup_path}`;
      changed();
    });
  };
}
