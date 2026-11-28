(() => {
  const root = document.documentElement;
  const key = "broader-vids:appearance";
  const system = matchMedia("(prefers-color-scheme: dark)");
  const apply = (theme) => {
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    else delete root.dataset.theme;
  };

  let storage;
  try {
    storage = window.localStorage;
  } catch {}
  function restore() {
    try {
      if (storage) apply(storage.getItem(key));
    } catch {}
  }

  restore();

  document.addEventListener(
    "DOMContentLoaded",
    () => {
      const button = document.getElementById("appearance-toggle");
      const label = button.querySelector("span");
      function update() {
        const current =
          root.dataset.theme || (system.matches ? "dark" : "light");
        const next = current === "dark" ? "light" : "dark";
        button.dataset.targetTheme = next;
        button.title = `Use ${next} theme`;
        button.setAttribute("aria-label", button.title);
        label.textContent = next === "light" ? "Light theme" : "Dark theme";
      }

      button.addEventListener("click", () => {
        apply(button.dataset.targetTheme);
        try {
          storage?.setItem(key, root.dataset.theme);
        } catch {}
        update();
      });
      system.addEventListener("change", update);
      if (storage) {
        window.addEventListener("storage", (event) => {
          if (
            event.storageArea !== storage ||
            (event.key !== key && event.key !== null)
          )
            return;
          restore();
          update();
        });
        restore();
      }
      update();
      button.disabled = false;
    },
    { once: true },
  );
})();
