"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { withSetup } = require("./runtime-lock.cjs");

const MARKER = ".broader-vids-profile.json";
const FORMAT = "broader-vids.profile";
const KEEP_RESET = new Set([
  MARKER,
  ".launcher",
  "runtimes",
  "wheels",
  "models",
  "access",
  "launcher.json",
]);
const MANAGED = new Set([
  ...KEEP_RESET,
  ".dashboard",
  ".reset-pending",
  "onboarding.json",
  "backups",
  "evidence",
  "recommendations",
  "discovery",
  "feedback",
  "runs",
  "archives",
  "broader-vids.sqlite3",
  "broader-vids.sqlite3-wal",
  "broader-vids.sqlite3-shm",
  "broader-vids.sqlite3-journal",
]);

function inside(parent, child) {
  const relative = path.relative(parent, child);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative))
  );
}

function ensureMarker(root) {
  const file = path.join(root, MARKER);
  if (fs.existsSync(file)) return;
  const temporary = path.join(root, `.${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(
      temporary,
      JSON.stringify({ format: FORMAT, version: 1 }),
      { flag: "wx", mode: 0o600 },
    );
    fs.renameSync(temporary, file);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function readSmall(file) {
  if (fs.lstatSync(file).isSymbolicLink() || fs.statSync(file).size > 4096)
    throw Error("Profile identity metadata is invalid.");
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function recognized(root) {
  const marker = path.join(root, MARKER);
  if (fs.existsSync(marker)) {
    const value = readSmall(marker);
    return value && value.format === FORMAT && value.version === 1;
  }
  const config = path.join(root, "onboarding.json");
  const database = path.join(root, "broader-vids.sqlite3");
  if (
    !fs.existsSync(config) ||
    !fs.existsSync(database) ||
    fs.lstatSync(database).isSymbolicLink()
  )
    return false;
  const value = readSmall(config);
  const header = Buffer.alloc(16);
  const descriptor = fs.openSync(database, "r");
  try {
    fs.readSync(descriptor, header, 0, header.length, 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return (
    value &&
    value.version === 1 &&
    ["youtube", "bilibili", "both"].includes(value.default_platform) &&
    header.toString("binary") === "SQLite format 3\0"
  );
}

function validateRoot(root, packageRoot) {
  root = path.resolve(root);
  const protectedPaths = [
    path.parse(root).root,
    os.homedir(),
    os.tmpdir(),
    process.env.USERPROFILE,
    process.env.LOCALAPPDATA,
    process.env.APPDATA,
    process.env.WINDIR,
  ]
    .filter(Boolean)
    .map((item) => path.resolve(item));
  if (
    protectedPaths.some((item) => inside(root, item)) ||
    inside(root, path.resolve(packageRoot))
  )
    throw Error(
      "Data removal cannot target a system directory, user home, or a directory containing the plugin.",
    );
  for (let current = root; ; current = path.dirname(current)) {
    if (fs.existsSync(current) && fs.lstatSync(current).isSymbolicLink())
      throw Error(
        "The data directory and its parents cannot be symbolic links or junctions.",
      );
    if (path.dirname(current) === current) break;
  }
  for (const name of [".launcher", ".dashboard"]) {
    const control = path.join(root, name);
    if (fs.existsSync(control) && fs.lstatSync(control).isSymbolicLink())
      throw Error(
        "Profile control directories cannot be symbolic links or junctions.",
      );
  }
  if (
    fs.existsSync(root) &&
    (!fs.statSync(root).isDirectory() || !recognized(root))
  )
    throw Error(
      "The target is not a recognized Broader Vids profile. No files were removed.",
    );
  return root;
}

function managed(name) {
  return (
    MANAGED.has(name) ||
    /^broader-vids\.before-[A-Za-z0-9-]+\.sqlite3(?:-wal|-shm|-journal)?$/.test(
      name,
    )
  );
}

function sizeOf(target) {
  let files = 0,
    bytes = 0;
  const pending = [target];
  while (pending.length) {
    const current = pending.pop();
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink() || stat.isFile()) {
      files += 1;
      bytes += stat.size;
    } else if (stat.isDirectory()) {
      for (const name of fs.readdirSync(current))
        pending.push(path.join(current, name));
    } else
      throw Error(
        "A managed directory contains an unsupported special file. No files were removed.",
      );
  }
  return { files, bytes };
}

function preview(root, operation, packageRoot) {
  if (!["reset", "clean"].includes(operation))
    throw Error("Use reset or clean.");
  root = validateRoot(root, packageRoot);
  const result = {
    operation,
    data_directory: root,
    status: "preview",
    dry_run: true,
    files: 0,
    bytes: 0,
    remove: [],
    preserve: [],
    unknown: [],
    blockers: [],
  };
  if (!fs.existsSync(root)) return { ...result, status: "absent" };
  for (const name of fs.readdirSync(root)) {
    if (/^[a-f0-9]{32}\.lock$/.test(name)) {
      result.blockers.push(`Active or uncertain engine lock: ${name}`);
      continue;
    }
    if (!managed(name)) {
      result.unknown.push(name);
      continue;
    }
    if (operation === "reset" && KEEP_RESET.has(name)) {
      result.preserve.push(name);
      continue;
    }
    result.remove.push(name);
    const size = sizeOf(path.join(root, name));
    result.files += size.files;
    result.bytes += size.bytes;
  }
  const controls = path.join(root, ".launcher");
  if (fs.existsSync(controls)) {
    for (const name of fs.readdirSync(controls)) {
      if (
        name === "setup.json" ||
        name === "gate.lock" ||
        name.endsWith(".use.json")
      )
        result.blockers.push(`Active or uncertain launcher operation: ${name}`);
    }
  }
  if (fs.existsSync(path.join(root, "access", "bilibili", "browser.lock")))
    result.blockers.push("Finish the active Bilibili browser operation.");
  if (operation === "clean" && result.unknown.length)
    result.blockers.push(
      "Move unknown files out of this directory before Clean.",
    );
  return result;
}

function removeOwned(root, name) {
  const target = path.join(root, name);
  if (path.dirname(target) !== root || !managed(name))
    throw Error("A removal target is outside the managed profile scope.");
  fs.rmSync(target, {
    recursive: true,
    force: false,
    maxRetries: 3,
    retryDelay: 100,
  });
}

function resetFiles(root, names) {
  const pending = path.join(root, ".reset-pending");
  if (fs.existsSync(pending)) removeOwned(root, ".reset-pending");
  const selected = names.filter((name) => name !== ".reset-pending");
  if (!selected.length) return;
  fs.mkdirSync(pending);
  const moved = [];
  try {
    selected.sort(
      (a, b) =>
        Number(!a.startsWith("broader-vids.sqlite3")) -
        Number(!b.startsWith("broader-vids.sqlite3")),
    );
    for (const name of selected) {
      fs.renameSync(path.join(root, name), path.join(pending, name));
      moved.push(name);
    }
  } catch (error) {
    for (const name of moved.reverse()) {
      try {
        fs.renameSync(path.join(pending, name), path.join(root, name));
      } catch {
        /* Keep staged files for explicit recovery. */
      }
    }
    if (fs.readdirSync(pending).length === 0) fs.rmdirSync(pending);
    throw error;
  }
  removeOwned(root, ".reset-pending");
}

function apply(root, operation, packageRoot, confirmation) {
  if (
    !confirmation ||
    !path.isAbsolute(confirmation) ||
    path.relative(path.resolve(root), path.resolve(confirmation)) !== ""
  )
    throw Error(
      "Supply --confirm-path with the exact absolute data directory shown by the preview.",
    );
  const initial = preview(root, operation, packageRoot);
  if (initial.status === "absent")
    return { ...initial, status: "absent", dry_run: false };
  if (initial.blockers.length) throw Error(initial.blockers.join(" "));
  root = initial.data_directory;
  withSetup(root, () => {
    validateRoot(root, packageRoot);
    ensureMarker(root);
    const names = fs.readdirSync(root);
    if (operation === "clean" && names.some((name) => !managed(name)))
      throw Error("The directory changed. Preview Clean again.");
    if (operation === "reset") resetFiles(root, initial.remove);
    else {
      for (const name of initial.remove.filter(
        (name) => name !== ".launcher" && name !== MARKER,
      ))
        removeOwned(root, name);
      if (fs.existsSync(path.join(root, MARKER))) removeOwned(root, MARKER);
    }
  });
  if (operation === "clean") {
    try {
      fs.rmdirSync(path.join(root, ".launcher"));
      fs.rmdirSync(root);
    } catch (error) {
      return {
        ...initial,
        status: "partial",
        dry_run: false,
        error:
          "Managed data was removed, but the directory is not empty or is in use. Preview again before another action.",
        code: error.code,
      };
    }
  }
  return {
    ...initial,
    status: "completed",
    dry_run: false,
    next_action:
      operation === "reset"
        ? "Run the setup skill to choose interests or import preferences. Installed tools and Bilibili sign-in remain."
        : "The plugin remains installed. Run the setup skill before using it again.",
  };
}

module.exports = { preview, apply, ensureMarker, validateRoot, MARKER };
