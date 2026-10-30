"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function busy(message) {
  const error = new Error(message);
  error.code = "RUNTIME_BUSY";
  return error;
}

function alive(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return true;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code !== "ESRCH";
  }
}

function read(file) {
  try {
    const record = JSON.parse(fs.readFileSync(file, "utf8"));
    if (
      !record ||
      typeof record !== "object" ||
      !Number.isSafeInteger(record.pid) ||
      record.pid <= 0
    )
      throw busy(
        "A runtime lock has an invalid owner. Check the operation before repairing the lock.",
      );
    return record;
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw busy(
      "A runtime lock is unreadable. Check its owner before repairing the lock.",
    );
  }
}

function gate(root, task) {
  const folder = path.join(root, ".launcher");
  fs.mkdirSync(folder, { recursive: true });
  const file = path.join(folder, "gate.lock");
  let descriptor;
  const deadline = performance.now() + 1000;
  while (descriptor === undefined) {
    try {
      descriptor = fs.openSync(file, "wx", 0o600);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      if (performance.now() >= deadline)
        throw busy(
          "Another launcher operation holds the runtime gate. Retry, or check the gate owner before repairing a stale lock.",
        );
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
    }
  }
  try {
    fs.writeFileSync(descriptor, JSON.stringify({ pid: process.pid }));
    return task(folder);
  } finally {
    fs.closeSync(descriptor);
    fs.unlinkSync(file);
  }
}

function activeSetup(folder) {
  const file = path.join(folder, "setup.json");
  const record = read(file);
  if (!record) return false;
  return true;
}

function release(file, token) {
  if (read(file)?.token === token) fs.unlinkSync(file);
}

function withRuntimeUse(root, task) {
  const token = randomUUID();
  const file = gate(root, (folder) => {
    if (activeSetup(folder))
      throw busy(
        "Runtime installation is active. Wait for setup to finish, then retry.",
      );
    const file = path.join(folder, `${token}.use.json`);
    fs.writeFileSync(file, JSON.stringify({ pid: process.pid, token }), {
      flag: "wx",
      mode: 0o600,
    });
    return file;
  });
  try {
    return task();
  } finally {
    release(file, token);
  }
}

function withSetup(root, task) {
  const token = randomUUID();
  const file = gate(root, (folder) => {
    if (activeSetup(folder))
      throw busy(
        "Another runtime installation is active. Retry after it finishes.",
      );
    for (const name of fs
      .readdirSync(folder)
      .filter((name) => name.endsWith(".use.json"))) {
      const file = path.join(folder, name);
      const record = read(file);
      if (record)
        throw busy(
          "A launcher command holds this profile. Finish it before setup. If its parent exited, check for surviving children before repairing the lock.",
        );
    }
    if (
      fs.readdirSync(root).some((name) => /^[a-f0-9]{32}\.lock$/.test(name)) ||
      fs.existsSync(path.join(root, "access", "bilibili", "browser.lock"))
    ) {
      throw busy(
        "An engine or Bilibili browser operation holds this profile. Finish it before setup.",
      );
    }
    const session = read(path.join(root, ".dashboard", "server.json"));
    if (session && alive(session.pid))
      throw busy(
        "A dashboard server uses this profile. Stop it with dashboard stop before setup.",
      );
    const file = path.join(folder, "setup.json");
    fs.writeFileSync(file, JSON.stringify({ pid: process.pid, token }), {
      flag: "wx",
      mode: 0o600,
    });
    return file;
  });
  try {
    return task();
  } finally {
    release(file, token);
  }
}

function setupInProgress(root) {
  return !!read(path.join(root, ".launcher", "setup.json"));
}

module.exports = { withRuntimeUse, withSetup, setupInProgress };
