"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { cleanupRuntimeStorage } = require("../bin/runtime-storage.cjs");
const { cacheWheel, wheelDigest } = require("../bin/broader-vids.cjs");
const { ensureMarker } = require("../bin/profile-data.cjs");
const { withSetup, withRuntimeUse } = require("../bin/runtime-lock.cjs");

function fixture(action) {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "broader-vids-storage-"),
  );
  try {
    const root = path.join(directory, "profile");
    const plugin = path.join(directory, "plugin");
    fs.mkdirSync(root);
    fs.mkdirSync(plugin);
    ensureMarker(root);
    const runtime = (version) => {
      const target = path.join(root, "runtimes", version);
      fs.mkdirSync(target, { recursive: true });
      fs.writeFileSync(path.join(target, "pyvenv.cfg"), "fixture");
      return target;
    };
    const wheel = (version, content) => {
      const source = path.join(
        plugin,
        `broader_vids-${version}-py3-none-any.whl`,
      );
      fs.writeFileSync(source, content);
      return cacheWheel(source, root);
    };
    runtime("1.0.0");
    const currentWheel = wheel("1.0.0", "current wheel");
    const currentDigest = wheelDigest(currentWheel);
    const cleanup = (apply = false) =>
      withSetup(root, () =>
        cleanupRuntimeStorage(root, plugin, "1.0.0", currentDigest, { apply }),
      );
    action({ root, directory, plugin, runtime, wheel, currentWheel, cleanup });
  } finally {
    assert.equal(
      path.dirname(path.resolve(directory)),
      path.resolve(os.tmpdir()),
    );
    assert.ok(path.basename(directory).startsWith("broader-vids-storage-"));
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test("runtime cleanup previews and removes only superseded managed assets", () =>
  fixture(({ root, runtime, wheel, currentWheel, cleanup }) => {
    const old = runtime("0.9.0");
    const alpha = runtime("1.0.0-alpha.17");
    const newer = runtime("1.1.0");
    const oldWheel = wheel("0.9.0", "old wheel");
    const oldBuild = wheel("1.0.0", "same-version obsolete build");
    const newerWheel = wheel("1.1.0", "newer wheel");
    const flat = path.join(
      root,
      "wheels",
      "broader_vids-0.1.0a17-py3-none-any.whl",
    );
    fs.writeFileSync(flat, "legacy cache");
    const unknown = path.join(root, "runtimes", "0.8.0");
    fs.mkdirSync(unknown);
    fs.writeFileSync(path.join(root, "onboarding.json"), "saved configuration");
    fs.mkdirSync(path.join(root, "access"));
    fs.writeFileSync(path.join(root, "access", "session"), "keep credentials");
    fs.mkdirSync(path.join(root, "backups"));
    fs.writeFileSync(
      path.join(root, "backups", "recovery.json"),
      "keep recovery",
    );
    const before = cleanup();
    assert.equal(before.status, "preview");
    assert.equal(before.candidates.length, 5);
    assert.ok(fs.existsSync(old));
    assert.ok(fs.existsSync(oldWheel));
    assert.equal(before.removed.length, 0);
    const after = cleanup(true);
    assert.equal(after.status, "completed");
    assert.equal(after.removed.length, 5);
    for (const target of [old, alpha, oldWheel, oldBuild, flat])
      assert.equal(fs.existsSync(target), false);
    for (const target of [newer, newerWheel, unknown, currentWheel])
      assert.ok(fs.existsSync(target));
    assert.equal(
      fs.readFileSync(path.join(root, "onboarding.json"), "utf8"),
      "saved configuration",
    );
    assert.equal(
      fs.readFileSync(path.join(root, "access", "session"), "utf8"),
      "keep credentials",
    );
    assert.equal(
      fs.readFileSync(path.join(root, "backups", "recovery.json"), "utf8"),
      "keep recovery",
    );
    assert.deepEqual(cleanup(true).removed, []);
  }));

test("runtime cleanup blocks active work and retries an interrupted deletion", (t) =>
  fixture(({ root, runtime, cleanup, currentWheel }) => {
    const old = runtime("0.9.0");
    const flat = path.join(
      root,
      "wheels",
      "broader_vids-0.9.0-py3-none-any.whl",
    );
    fs.writeFileSync(flat, "legacy wheel");
    withRuntimeUse(root, () =>
      assert.throws(() => cleanup(true), /holds this profile/),
    );
    assert.ok(fs.existsSync(old));
    const remove = fs.rmSync;
    const mock = t.mock.method(fs, "rmSync", (target, options) => {
      if (
        ["runtimes", "wheels"].some(
          (kind) => path.dirname(target) === path.join(root, kind),
        )
      )
        throw Object.assign(Error("Fixture sharing violation"), {
          code: "EBUSY",
        });
      return remove(target, options);
    });
    const first = cleanup(true);
    assert.equal(first.status, "partial");
    assert.equal(first.issues.length, 2);
    assert.equal(first.issues[0].code, "EBUSY");
    assert.match(first.issues[0].path, /\.obsolete-/);
    assert.ok(fs.existsSync(currentWheel));
    mock.mock.restore();
    const recovered = cleanup(true);
    assert.equal(recovered.status, "completed");
    assert.equal(recovered.removed.length, 2);
    assert.deepEqual(fs.readdirSync(path.join(root, "wheels")), [
      path.basename(path.dirname(currentWheel)),
    ]);
    assert.deepEqual(fs.readdirSync(path.join(root, "runtimes")), ["1.0.0"]);
  }));

test("runtime cleanup cannot traverse linked storage or nested junctions", () =>
  fixture(({ root, directory, runtime, cleanup, currentWheel }) => {
    const outside = path.join(directory, "outside");
    fs.mkdirSync(outside);
    const sentinel = path.join(outside, "keep.txt");
    fs.writeFileSync(sentinel, "outside data");
    const old = runtime("0.9.0");
    fs.symlinkSync(
      outside,
      path.join(old, "linked"),
      process.platform === "win32" ? "junction" : "dir",
    );
    assert.equal(cleanup(true).status, "completed");
    assert.equal(fs.readFileSync(sentinel, "utf8"), "outside data");
    fs.rmSync(path.dirname(currentWheel), { recursive: true });
    fs.rmdirSync(path.join(root, "wheels"));
    fs.symlinkSync(
      outside,
      path.join(root, "wheels"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const refused = cleanup(true);
    assert.equal(refused.status, "partial");
    assert.equal(refused.issues[0].path, "wheels");
    assert.equal(fs.readFileSync(sentinel, "utf8"), "outside data");
  }));
