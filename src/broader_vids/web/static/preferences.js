import { get, request } from "./api.js";

export function createPreferences({ busy, changed, getProfile }) {
  const find = (id) => document.getElementById(`preferences-${id}`);
  const input = document.getElementById("interests-input"),
    interestStatus = document.getElementById("interests-status"),
    file = find("file"),
    status = find("status");
  let profile,
    dirty = false,
    active = false,
    visible = false,
    text,
    preview;
  function controls() {
    input.disabled = active;
    document.getElementById("interests-save").disabled = active || !profile;
    document.getElementById("interests-reload").disabled = active;
    for (const id of ["choose", "file"]) find(id).disabled = active;
    find("import").disabled = active || !preview;
    find("export").disabled = active || dirty;
    find("export-warning").hidden = !dirty;
    find("draft-warning").hidden = !dirty || !preview;
  }
  function applyProfile(value) {
    profile = value;
    input.value = value.interests.join("\n");
    input.setCustomValidity("");
    dirty = false;
    changed(value);
    controls();
  }
  function resetTransfer(clearFile = true) {
    text = preview = undefined;
    if (clearFile) file.value = "";
    find("filename").textContent = "No file selected";
    find("preview").hidden = true;
    find("summary").replaceChildren();
    status.textContent = "";
    controls();
  }
  function showPreview(value) {
    preview = value;
    const summary = find("summary");
    summary.replaceChildren();
    for (const [label, content] of [
      ["Interests", value.interests.join(", ") || "None"],
      ["Model parameters", value.model_parameters.toLocaleString()],
      [
        "Preference groups",
        Object.values(value.preference_cells)
          .reduce((a, b) => a + b, 0)
          .toLocaleString(),
      ],
    ]) {
      const term = document.createElement("dt"),
        detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = content;
      summary.append(term, detail);
    }
    find("preview").hidden = false;
    controls();
  }
  async function operation(target, message, task) {
    if (active) return;
    active = true;
    busy(true);
    target.textContent = message;
    controls();
    try {
      await task();
    } catch (error) {
      target.textContent = error.message;
    } finally {
      active = false;
      if (!visible) resetTransfer();
      controls();
      busy(false);
    }
  }
  function loadInterests() {
    return operation(interestStatus, "Loading saved interests…", async () => {
      applyProfile(await get("/api/interests"));
      interestStatus.textContent = "";
    });
  }
  input.oninput = () => {
    dirty = input.value !== (profile?.interests || []).join("\n");
    input.setCustomValidity("");
    interestStatus.textContent = "";
    controls();
  };
  document.getElementById("interests-reload").onclick = loadInterests;
  document.getElementById("interests-form").onsubmit = (event) => {
    event.preventDefault();
    if (active || !profile) return;
    const interests = input.value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    if (
      !interests.length ||
      interests.length > 40 ||
      interests.some((name) => Array.from(name).length > 80)
    ) {
      input.setCustomValidity(
        "Enter 1–40 interests, each with 1–80 characters.",
      );
      input.reportValidity();
      return;
    }
    operation(interestStatus, "Saving interests…", async () => {
      const saved = await get("/api/interests");
      if (
        saved.revision > profile.revision &&
        JSON.stringify(saved.interests) === JSON.stringify(profile.interests)
      )
        profile = saved;
      applyProfile(
        await request("/api/interests", {
          interests,
          revision: profile.revision,
        }),
      );
      interestStatus.textContent =
        "Saved. New recommendation rounds will use these interests.";
    });
  };
  find("choose").onclick = () => {
    file.value = "";
    file.click();
  };
  file.onchange = () => {
    if (active) return;
    const selected = file.files[0];
    resetTransfer(false);
    if (!selected) {
      status.textContent = "";
      return;
    }
    find("filename").textContent = selected.name;
    operation(status, "Checking preference file…", async () => {
      if (selected.size > 8 * 1024 * 1024)
        throw Error("The preference file must not exceed 8 MiB.");
      text = (await selected.text()).replace(/^\uFEFF/, "");
      showPreview(
        await request("/api/preferences/preview", { file_text: text }),
      );
      status.textContent =
        "File is compatible. Check the summary before importing.";
    });
  };
  find("import").onclick = () => {
    if (active || !preview || text === undefined) return;
    const selectedText = text,
      revision = preview.revision;
    operation(status, "Checking preferences…", async () => {
      const current = await request("/api/preferences/preview", {
        file_text: selectedText,
      });
      if (current.revision !== revision) {
        showPreview(current);
        status.textContent =
          "Preferences changed since you chose this file. Check the summary, then select Import preferences again.";
        return;
      }
      status.textContent = "Replacing learned preferences…";
      const result = await request("/api/preferences/import", {
        file_text: selectedText,
        revision,
      });
      applyProfile(result.profile || (await get("/api/interests")));
      resetTransfer();
      interestStatus.textContent =
        "Imported interests are ready. Edit them here, then select Save.";
      status.textContent = `Preferences imported. Recovery file: ${result.backup_path}`;
      if (visible) {
        find("page").scrollTop = 0;
        input.focus({ preventScroll: true });
      }
    });
  };
  find("export").onclick = () => {
    if (dirty) return;
    operation(find("export-status"), "Preparing preference file…", async () => {
      const result = await request("/api/preferences/export", {});
      const url = URL.createObjectURL(
        new Blob([result.file_text], { type: "application/json" }),
      );
      const link = document.createElement("a");
      try {
        link.href = url;
        link.download = `broader-vids-preferences-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.append(link);
        link.click();
      } finally {
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      find("export-status").textContent =
        "Preference file ready. Your browser manages the download.";
    });
  };
  controls();
  return {
    open() {
      if (visible) return;
      visible = true;
      if (!dirty) {
        const saved = getProfile?.();
        if (saved) applyProfile(saved);
        loadInterests();
      }
    },
    close() {
      if (!visible) return;
      visible = false;
      if (!active) resetTransfer();
    },
  };
}
