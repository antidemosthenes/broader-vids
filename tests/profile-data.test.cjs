"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { preview, apply, ensureMarker } = require("../bin/profile-data.cjs");
const { withRuntimeUse } = require("../bin/runtime-lock.cjs");
const packageRoot = path.resolve(__dirname, "..");

function fixture(task) {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "broader-vids-data-"));
  const root = path.join(parent, "profile with spaces");
  fs.mkdirSync(root);
  ensureMarker(root);
  for (const name of [
    "runtimes",
    "wheels",
    "models",
    "access/bilibili/profile",
    "backups",
    "evidence",
    "recommendations",
    "discovery",
    "feedback",
    "runs",
    "archives",
  ]) {
    const folder = path.join(root, name);
    fs.mkdirSync(folder, { recursive: true });
    fs.writeFileSync(path.join(folder, "fixture.txt"), name);
  }
  fs.writeFileSync(
    path.join(root, "broader-vids.sqlite3"),
    "private preferences and histories",
  );
  fs.writeFileSync(
    path.join(root, "onboarding.json"),
    JSON.stringify({
      version: 1,
      default_platform: "both",
      completed_at: "fixture",
    }),
  );
  try {
    task(root, parent);
  } finally {
    assert.equal(path.dirname(path.resolve(parent)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(parent).startsWith("broader-vids-data-"));
    fs.rmSync(parent, { recursive: true, force: true });
  }
}

test("reset preview is read-only and reset keeps tools and Bilibili sign-in", () =>
  fixture((root) => {
    const report = preview(root, "reset", packageRoot);
    assert.equal(report.dry_run, true);
    assert.ok(report.bytes > 0);
    assert.ok(fs.existsSync(path.join(root, "broader-vids.sqlite3")));
    assert.throws(() => apply(root, "reset", packageRoot), /confirm-path/);
    assert.throws(
      () => apply(root, "reset", packageRoot, path.dirname(root)),
      /confirm-path/,
    );
    assert.equal(apply(root, "reset", packageRoot, root).status, "completed");
    for (const name of [
      "broader-vids.sqlite3",
      "onboarding.json",
      "evidence",
      "backups",
      "recommendations",
      "archives",
      ".reset-pending",
    ])
      assert.equal(fs.existsSync(path.join(root, name)), false);
    for (const name of [
      "runtimes",
      "wheels",
      "models",
      "access/bilibili/profile",
    ])
      assert.ok(fs.existsSync(path.join(root, name, "fixture.txt")));
    assert.equal(apply(root, "reset", packageRoot, root).status, "completed");
  }));

test("clean removes the profile while preserving the installed plugin and outside exports", () =>
  fixture((root, parent) => {
    const exported = path.join(parent, "preferences.json");
    fs.writeFileSync(exported, "user export");
    const report = preview(root, "clean", packageRoot);
    assert.ok(report.remove.includes("runtimes"));
    assert.equal(apply(root, "clean", packageRoot, root).status, "completed");
    assert.equal(fs.existsSync(root), false);
    assert.equal(fs.readFileSync(exported, "utf8"), "user export");
    assert.ok(fs.existsSync(path.join(packageRoot, "plugin.json")));
    assert.equal(apply(root, "clean", packageRoot, root).status, "absent");
    assert.equal(fs.existsSync(root), false);
  }));

test("unknown files survive reset and block complete removal", () =>
  fixture((root) => {
    const unknown = path.join(root, "my-important-export.json");
    fs.writeFileSync(unknown, "keep");
    assert.ok(preview(root, "clean", packageRoot).blockers.length);
    assert.throws(
      () => apply(root, "clean", packageRoot, root),
      /unknown files/,
    );
    apply(root, "reset", packageRoot, root);
    assert.equal(fs.readFileSync(unknown, "utf8"), "keep");
  }));

test("active launcher, engine, browser, and dashboard operations block changes", () =>
  fixture((root) => {
    withRuntimeUse(root, () =>
      assert.throws(
        () => apply(root, "clean", packageRoot, root),
        /launcher operation/,
      ),
    );
    const engine = path.join(root, `${"a".repeat(32)}.lock`);
    fs.writeFileSync(engine, "active");
    assert.throws(() => apply(root, "reset", packageRoot, root), /engine lock/);
    fs.unlinkSync(engine);
    const browser = path.join(root, "access/bilibili/browser.lock");
    fs.writeFileSync(browser, "active");
    assert.throws(
      () => apply(root, "reset", packageRoot, root),
      /Bilibili browser/,
    );
    fs.unlinkSync(browser);
    fs.mkdirSync(path.join(root, ".dashboard"));
    fs.writeFileSync(
      path.join(root, ".dashboard/server.json"),
      JSON.stringify({ pid: process.pid }),
    );
    assert.throws(
      () => apply(root, "clean", packageRoot, root),
      /dashboard server/,
    );
    assert.ok(fs.existsSync(path.join(root, "broader-vids.sqlite3")));
  }));

test("dangerous roots and unrecognized folders cannot be removed", () =>
  fixture((root, parent) => {
    for (const target of [
      os.homedir(),
      os.tmpdir(),
      path.parse(root).root,
      packageRoot,
      parent,
    ])
      assert.throws(() => preview(target, "clean", packageRoot));
    const unrelated = path.join(parent, "unrelated");
    fs.mkdirSync(unrelated);
    fs.writeFileSync(path.join(unrelated, "file.txt"), "untouched");
    assert.throws(
      () => apply(unrelated, "clean", packageRoot, unrelated),
      /recognized/,
    );
    assert.equal(
      fs.readFileSync(path.join(unrelated, "file.txt"), "utf8"),
      "untouched",
    );
  }));

test("managed links are unlinked without visiting outside data", (t) =>
  fixture((root, parent) => {
    const outside = path.join(parent, "outside");
    fs.mkdirSync(outside);
    fs.writeFileSync(path.join(outside, "keep.txt"), "keep");
    const link = path.join(root, "evidence/linked");
    try {
      fs.symlinkSync(
        outside,
        link,
        process.platform === "win32" ? "junction" : "dir",
      );
    } catch (error) {
      if (["EPERM", "EACCES"].includes(error.code)) {
        t.skip("Link creation is unavailable");
        return;
      }
      throw error;
    }
    apply(root, "reset", packageRoot, root);
    assert.equal(
      fs.readFileSync(path.join(outside, "keep.txt"), "utf8"),
      "keep",
    );
    const rootLink = path.join(parent, "profile-link");
    fs.symlinkSync(
      root,
      rootLink,
      process.platform === "win32" ? "junction" : "dir",
    );
    assert.throws(
      () => preview(rootLink, "clean", packageRoot),
      /symbolic links or junctions/,
    );
  }));

test("a failed staging move restores all data before deletion", () =>
  fixture((root) => {
    const rename = fs.renameSync;
    fs.renameSync = (source, destination) => {
      if (source === path.join(root, "evidence")) throw Error("Staging failed");
      return rename(source, destination);
    };
    try {
      assert.throws(
        () => apply(root, "reset", packageRoot, root),
        /Staging failed/,
      );
    } finally {
      fs.renameSync = rename;
    }
    assert.equal(
      fs.readFileSync(path.join(root, "broader-vids.sqlite3"), "utf8"),
      "private preferences and histories",
    );
    assert.ok(fs.existsSync(path.join(root, "backups/fixture.txt")));
    assert.equal(fs.existsSync(path.join(root, ".reset-pending")), false);
  }));

test("an interrupted reset retains staged files and a later reset completes removal", () =>
  fixture((root) => {
    const remove = fs.rmSync;
    fs.rmSync = (target, options) => {
      if (target === path.join(root, ".reset-pending"))
        throw Error("Removal interrupted");
      return remove(target, options);
    };
    try {
      assert.throws(
        () => apply(root, "reset", packageRoot, root),
        /Removal interrupted/,
      );
    } finally {
      fs.rmSync = remove;
    }
    assert.ok(
      fs.existsSync(path.join(root, ".reset-pending/broader-vids.sqlite3")),
    );
    assert.equal(apply(root, "reset", packageRoot, root).status, "completed");
    assert.equal(fs.existsSync(path.join(root, ".reset-pending")), false);
  }));
