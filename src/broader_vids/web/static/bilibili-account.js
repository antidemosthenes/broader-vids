import { state } from "./state.js";
import { get, request } from "./api.js";

export function createBilibiliAccount({ changed, refresh }) {
  let version = 0,
    timer,
    pending = false,
    loading = false,
    finished;
  state.accountSwitch = { status: "idle", active: false, can_start: false };
  function schedule() {
    clearTimeout(timer);
    if (!document.hidden)
      timer = setTimeout(load, state.accountSwitch.active ? 1000 : 15000);
  }
  function update(value) {
    const next = { ...value, pending };
    if (JSON.stringify(state.accountSwitch) !== JSON.stringify(next)) {
      state.accountSwitch = next;
      changed();
    }
    const result = `${value.id}:${value.status}`;
    if (
      !value.active &&
      ["completed", "failed", "cleanup_pending"].includes(value.status) &&
      result !== finished
    ) {
      finished = result;
      refresh();
    }
  }
  async function load() {
    if (document.hidden || pending || loading) return schedule();
    loading = true;
    const current = ++version;
    try {
      const value = await get("/api/bilibili/account", {
        allowFailedStatus: true,
      });
      if (current === version && !pending) update(value);
    } catch (error) {
      if (current === version)
        update({ ...state.accountSwitch, error: error.message });
    } finally {
      loading = false;
      schedule();
    }
  }
  async function operation(action) {
    if (pending) return;
    pending = true;
    ++version;
    update({ ...state.accountSwitch, error: "" });
    try {
      update(
        await request(
          "/api/bilibili/account",
          { operation: action },
          { allowFailedStatus: true },
        ),
      );
    } catch (error) {
      update({ ...state.accountSwitch, error: error.message });
    } finally {
      pending = false;
      state.accountSwitch.pending = false;
      changed();
      schedule();
    }
  }
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden) load();
  });
  load();
  return {
    start: () => operation("start"),
    confirm: () => operation("confirm"),
    cancel: () => operation("cancel"),
  };
}
