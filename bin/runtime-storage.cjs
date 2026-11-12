"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { validateRoot } = require("./profile-data.cjs");

const STAGED = /^\.obsolete-[0-9a-f-]{36}$/;
const TEMPORARY = /^\.[0-9a-f-]{36}\.tmp$/;

function versionParts(value) {
  const match =
    /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-alpha\.(0|[1-9]\d*))?$/.exec(
      value,
    );
  if (!match) return null;
  const parts = match.slice(1, 4).map(Number);
  parts.push(match[4] === undefined ? 1 : 0, Number(match[4] || 0));
  return parts.every(Number.isSafeInteger) ? parts : null;
}

function compareVersions(left, right) {
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
  }
  return 0;
}

function wheelVersion(name) {
  const match =
    /^broader_vids-(\d+\.\d+\.\d+(?:a\d+)?)-[A-Za-z0-9_.-]+\.whl$/.exec(name);
  return match ? versionParts(match[1].replace(/a(\d+)$/, "-alpha.$1")) : null;
}

function cleanupRuntimeStorage(
  root,
  packageRoot,
  version,
  digest,
  { apply = false } = {},
) {
  const current = versionParts(version);
  if (!current || !/^[a-f0-9]{64}$/.test(digest))
    throw Error("Runtime cleanup requires a valid version and wheel digest.");
  const report = {
    dry_run: !apply,
    status: "preview",
    candidates: [],
    removed: [],
    preserved: [],
    issues: [],
  };
  const issue = (target, error) =>
    report.issues.push({
      path: target,
      code: error.code || "UNSAFE_PATH",
      reason: error.message,
    });
  try {
    root = validateRoot(root, packageRoot);
  } catch (error) {
    issue(".", error);
    return { ...report, status: "partial" };
  }
  for (const kind of ["runtimes", "wheels"]) {
    const folder = path.join(root, kind);
    let entries;
    try {
      const stat = fs.lstatSync(folder);
      if (stat.isSymbolicLink() || !stat.isDirectory())
        throw Error("Runtime storage cannot be a link or a file.");
      entries = fs.readdirSync(folder);
    } catch (error) {
      if (error.code !== "ENOENT") issue(kind, error);
      continue;
    }
    for (const name of entries) {
      const relative = path.join(kind, name);
      let target = path.join(folder, name);
      try {
        const stat = fs.lstatSync(target);
        let disposable =
          STAGED.test(name) &&
          (stat.isDirectory() || (kind === "wheels" && stat.isFile()));
        if (!disposable && !stat.isSymbolicLink()) {
          if (kind === "runtimes") {
            const parsed = versionParts(name);
            disposable =
              stat.isDirectory() &&
              parsed &&
              compareVersions(parsed, current) < 0 &&
              ["pyvenv.cfg", "broader-vids-build.json"].some((marker) =>
                fs.existsSync(path.join(target, marker)),
              );
          } else if (stat.isFile()) {
            const parsed = wheelVersion(name);
            disposable = parsed && compareVersions(parsed, current) <= 0;
          } else if (
            stat.isDirectory() &&
            /^[a-f0-9]{64}$/.test(name) &&
            name !== digest
          ) {
            disposable = fs
              .readdirSync(target, { withFileTypes: true })
              .every((entry) => {
                if (!entry.isFile()) return false;
                const parsed = wheelVersion(entry.name);
                return (
                  TEMPORARY.test(entry.name) ||
                  (parsed && compareVersions(parsed, current) <= 0)
                );
              });
          }
        }
        if (!disposable) {
          report.preserved.push(relative);
          continue;
        }
        report.candidates.push(relative);
        if (!apply) continue;
        // Staging keeps interrupted deletions recognizable on the next cleanup.
        if (!STAGED.test(name)) {
          const staged = path.join(folder, `.obsolete-${randomUUID()}`);
          fs.renameSync(target, staged);
          target = staged;
        }
        if (path.dirname(path.resolve(target)) !== path.resolve(folder))
          throw Error("Runtime cleanup escaped its storage directory.");
        fs.rmSync(target, {
          recursive: true,
          force: false,
          maxRetries: 3,
          retryDelay: 100,
        });
        report.removed.push(relative);
      } catch (error) {
        issue(path.relative(root, target), error);
      }
    }
  }
  report.status = report.issues.length
    ? "partial"
    : apply
      ? "completed"
      : "preview";
  return report;
}

module.exports = { cleanupRuntimeStorage };
