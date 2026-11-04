#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash, randomUUID } = require("node:crypto");
const {
  withRuntimeUse,
  withSetup,
  setupInProgress,
} = require("./runtime-lock.cjs");
const { spawnSync } = require("node:child_process");

function defaultRoot(env = process.env, platform = process.platform) {
  const fallback =
    platform === "win32"
      ? path.join(env.USERPROFILE || os.homedir(), "AppData", "Local")
      : path.join(os.homedir(), ".local", "share");
  return path.join(env.LOCALAPPDATA || fallback, "broader-vids");
}

function dataRoot(args, env = process.env, selected) {
  let override;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--data-dir") override = args[++index];
    else if (args[index].startsWith("--data-dir="))
      override = args[index].slice("--data-dir=".length);
    else continue;
    if (!override || override.startsWith("--"))
      throw new Error("--data-dir requires a path");
  }
  return path.resolve(
    override || env.BROADER_VIDS_DATA_DIR || selected || defaultRoot(env),
  );
}

function selectionFile(env = process.env) {
  return path.join(defaultRoot(env), "launcher.json");
}

function selectedRoot(env = process.env) {
  const file = selectionFile(env);
  if (!fs.existsSync(file)) return undefined;
  const value = JSON.parse(fs.readFileSync(file, "utf8"));
  if (
    !value ||
    typeof value !== "object" ||
    value.version !== 1 ||
    typeof value.data_directory !== "string" ||
    !path.isAbsolute(value.data_directory)
  )
    throw new Error(
      "Invalid saved data selection; use broader-vids data reset or choose a directory with data use.",
    );
  return value.data_directory;
}

function resolvedRoot(args) {
  const explicit = args.some(
    (value) => value === "--data-dir" || value.startsWith("--data-dir="),
  );
  return dataRoot(
    args,
    process.env,
    explicit || process.env.BROADER_VIDS_DATA_DIR ? undefined : selectedRoot(),
  );
}

function selectRoot(directory, env = process.env) {
  if (!directory || directory.startsWith("--"))
    throw new Error("data use requires a directory path.");
  if (fs.existsSync(directory) && !fs.statSync(directory).isDirectory())
    throw new Error("Choose a directory, not a file.");
  const target = selectionFile(env);
  const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  try {
    fs.writeFileSync(
      temporary,
      JSON.stringify(
        { version: 1, data_directory: path.resolve(directory) },
        null,
        2,
      ),
      { flag: "wx", mode: 0o600 },
    );
    fs.renameSync(temporary, target);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function commandIndex(args) {
  let index = 0;
  while (index < args.length) {
    if (args[index] === "--data-dir") index += 2;
    else if (args[index].startsWith("--data-dir=")) index += 1;
    else return index;
  }
  return -1;
}

function validateSetupArguments(args, index) {
  for (let item = index + 1; item < args.length; item += 1) {
    const value = args[item];
    if (value === "--asr" || value === "--evidence") continue;
    if (value === "--data-dir") {
      item += 1;
      continue;
    }
    if (value.startsWith("--data-dir=")) continue;
    throw new Error(
      "Use setup with optional --evidence, --asr, and --data-dir PATH.",
    );
  }
}

function invoke(command, args) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: false,
    env: { ...process.env, BROADER_VIDS_LAUNCHER: __filename },
  });
  if (result.error)
    throw new Error(
      `Could not launch ${path.basename(command)} (${result.error.code || "unknown error"})`,
    );
  if (result.status !== 0) {
    const error = new Error(
      `${path.basename(command)} did not complete successfully.`,
    );
    error.exitCode = result.status ?? (result.signal === "SIGINT" ? 130 : 1);
    throw error;
  }
}

function cacheWheel(source, root) {
  const content = fs.readFileSync(source);
  const digest = createHash("sha256").update(content).digest("hex");
  const directory = path.join(root, "wheels", digest);
  const destination = path.join(directory, path.basename(source));
  fs.mkdirSync(directory, { recursive: true });
  if (
    !fs.existsSync(destination) ||
    !fs.readFileSync(destination).equals(content)
  ) {
    const temporary = path.join(directory, `.${randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, content, { flag: "wx", mode: 0o600 });
      fs.renameSync(temporary, destination);
    } finally {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
  }
  return destination;
}

function bundledWheel(packageRoot, version) {
  const prefix = `broader_vids-${version.replace("-alpha.", "a")}-`;
  const folder = path.join(packageRoot, "python");
  const wheels = fs.existsSync(folder)
    ? fs
        .readdirSync(folder)
        .filter((name) => name.startsWith(prefix) && name.endsWith(".whl"))
    : [];
  if (wheels.length !== 1)
    throw new Error(
      "The package needs exactly one matching bundled wheel; in a checkout run: uv build --out-dir python",
    );
  return path.join(folder, wheels[0]);
}

function wheelDigest(wheel) {
  return createHash("sha256").update(fs.readFileSync(wheel)).digest("hex");
}

function requirementDigests(packageRoot, groups) {
  return Object.fromEntries(
    groups.map((group) => [
      group,
      createHash("sha256")
        .update(
          fs.readFileSync(
            path.join(packageRoot, "python", `${group}-requirements.txt`),
          ),
        )
        .digest("hex"),
    ]),
  );
}

function validGroups(groups) {
  return (
    Array.isArray(groups) &&
    (JSON.stringify(groups) === JSON.stringify(["core"]) ||
      JSON.stringify(groups) === JSON.stringify(["core", "evidence"]) ||
      JSON.stringify(groups) === JSON.stringify(["core", "evidence", "asr"]))
  );
}

function runtimeBuildMatches(runtime, digest, packageRoot) {
  const file = path.join(runtime, "broader-vids-build.json");
  try {
    if (fs.statSync(file).size > 4096) return false;
    const receipt = JSON.parse(fs.readFileSync(file, "utf8"));
    if (
      receipt?.version !== 2 ||
      receipt.wheel_sha256 !== digest ||
      !validGroups(receipt.groups)
    )
      return false;
    const expected = requirementDigests(packageRoot, receipt.groups);
    return (
      JSON.stringify(receipt.requirements_sha256) === JSON.stringify(expected)
    );
  } catch {
    return false;
  }
}

function recordRuntimeBuild(runtime, digest, packageRoot, groups) {
  const target = path.join(runtime, "broader-vids-build.json");
  const temporary = path.join(runtime, `.build-${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(
      temporary,
      JSON.stringify({
        version: 2,
        wheel_sha256: digest,
        groups,
        requirements_sha256: requirementDigests(packageRoot, groups),
      }),
      { flag: "wx", mode: 0o600 },
    );
    fs.renameSync(temporary, target);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function main(args = process.argv.slice(2)) {
  const packageRoot = path.resolve(__dirname, "..");
  const manifest = JSON.parse(
    fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"),
  );
  const index = commandIndex(args);
  if (args[index] === "plugin") {
    if (
      index === 0 &&
      args.length === 2 &&
      ["--help", "-h"].includes(args[1])
    ) {
      console.log("Usage: broader-vids plugin install");
      return;
    }
    if (index !== 0 || args.length !== 2 || args[1] !== "install")
      throw new Error(
        "Use broader-vids plugin install without profile arguments.",
      );
    const result = require("./plugin-install.cjs").installPlugin(
      manifest.version,
      invoke,
    );
    console.log(JSON.stringify(result, null, 2));
    console.log(
      "Start a new Codex conversation and invoke the Broader Vids setup skill.",
    );
    return;
  }
  if (args[index] === "data") {
    const operation = args[index + 1] || "show";
    if (operation === "use" && args.length === index + 3)
      selectRoot(args[index + 2]);
    else if (operation === "reset" && args.length === index + 2) {
      const file = selectionFile();
      if (fs.existsSync(file)) fs.unlinkSync(file);
    } else if (operation !== "show" || args.length > index + 2)
      throw new Error("Use data show, data use PATH, or data reset.");
    console.log(
      JSON.stringify(
        { data_directory: resolvedRoot(args), selection_file: selectionFile() },
        null,
        2,
      ),
    );
    return;
  }
  const root = resolvedRoot(args);
  const runtime = path.join(root, "runtimes", manifest.version);
  const executable =
    process.platform === "win32"
      ? path.join(runtime, "Scripts", "python.exe")
      : path.join(runtime, "bin", "python");
  if (["reset", "clean"].includes(args[index])) {
    const operation = args[index];
    const values = args.slice(index + 1);
    if (values.length === 1 && ["--help", "-h"].includes(values[0])) {
      console.log(
        `Usage: broader-vids [--data-dir PATH] ${operation} [--apply --confirm-path ABSOLUTE_PATH]`,
      );
      return;
    }
    let applyChange = false,
      confirmation;
    for (let item = 0; item < values.length; item += 1) {
      if (values[item] === "--apply") applyChange = true;
      else if (values[item] === "--confirm-path") confirmation = values[++item];
      else if (values[item].startsWith("--confirm-path="))
        confirmation = values[item].slice("--confirm-path=".length);
      else
        throw new Error(
          "Use reset or clean with optional --apply --confirm-path ABSOLUTE_PATH.",
        );
    }
    const data = require("./profile-data.cjs");
    const result = applyChange
      ? data.apply(root, operation, packageRoot, confirmation)
      : data.preview(root, operation, packageRoot);
    console.log(JSON.stringify(result, null, 2));
    if (result.status === "partial") process.exitCode = 2;
    return;
  }
  if (args[index] === "setup") {
    validateSetupArguments(args, index);
    const sourceWheel = bundledWheel(packageRoot, manifest.version);
    const wheelRoot = path.dirname(sourceWheel);
    return withSetup(root, () => {
      require("./profile-data.cjs").ensureMarker(root);
      const receipt = path.join(runtime, "broader-vids-build.json");
      if (fs.existsSync(receipt)) fs.unlinkSync(receipt);
      if (!fs.existsSync(executable))
        invoke("uv", [
          "venv",
          "--allow-existing",
          "--python",
          ">=3.12",
          runtime,
        ]);
      const wheel = cacheWheel(sourceWheel, root);
      invoke("uv", [
        "pip",
        "install",
        "--python",
        executable,
        "--reinstall",
        "--no-index",
        "--no-deps",
        wheel,
      ]);
      const extras = args.includes("--asr")
        ? ["evidence", "asr"]
        : args.includes("--evidence")
          ? ["evidence"]
          : [];
      const groups = ["core", ...extras];
      for (const extra of groups) {
        invoke("uv", [
          "pip",
          "install",
          "--python",
          executable,
          "--require-hashes",
          "-r",
          path.join(wheelRoot, `${extra}-requirements.txt`),
        ]);
      }
      recordRuntimeBuild(runtime, wheelDigest(wheel), packageRoot, groups);
      console.log(
        JSON.stringify(
          {
            status: "installed",
            runtime,
            data_directory: root,
            next_action:
              "Complete the setup skill and run onboarding complete to verify readiness.",
          },
          null,
          2,
        ),
      );
    });
  }
  if (fs.existsSync(path.join(root, ".reset-pending")))
    throw new Error(
      "A reset is unfinished. Preview reset or clean, then complete that action before using this profile.",
    );
  if (setupInProgress(root)) {
    const error = new Error(
      "Runtime installation holds this profile. Wait for setup to finish. Check surviving child processes before repairing a stale installation lock.",
    );
    error.code = "RUNTIME_BUSY";
    throw error;
  }
  if (!fs.existsSync(executable)) {
    if (
      ["preflight", "recommend", "onboarding"].includes(
        args[commandIndex(args)],
      )
    ) {
      console.log(
        JSON.stringify(
          {
            status: "setup_required",
            data_directory: root,
            checks: {
              runtime: {
                status: "missing",
                remedy:
                  "Run the setup skill, or setup --evidence with this same --data-dir.",
              },
            },
          },
          null,
          2,
        ),
      );
      process.exitCode = 2;
      return;
    }
    throw new Error(
      "Broader Vids runtime is missing; run broader-vids setup --evidence with the same --data-dir first.",
    );
  }
  return withRuntimeUse(root, () => {
    const stopDashboard =
      args[index] === "dashboard" && args[index + 1] === "stop";
    if (
      !stopDashboard &&
      !runtimeBuildMatches(
        runtime,
        wheelDigest(bundledWheel(packageRoot, manifest.version)),
        packageRoot,
      )
    ) {
      const error = new Error(
        "The installed Python engine does not match the bundled build, or its build receipt is missing. Stop the dashboard, then run setup --evidence with this same --data-dir to refresh it. Existing preferences and sign-in stay intact.",
      );
      error.code = "RUNTIME_BUILD_MISMATCH";
      error.data_directory = root;
      throw error;
    }
    return invoke(executable, [
      "-m",
      "broader_vids",
      "--data-dir",
      root,
      ...args,
    ]);
  });
}

module.exports = {
  defaultRoot,
  dataRoot,
  commandIndex,
  cacheWheel,
  selectedRoot,
  selectRoot,
  bundledWheel,
  wheelDigest,
  runtimeBuildMatches,
  recordRuntimeBuild,
  requirementDigests,
};
if (require.main === module) {
  try {
    main();
  } catch (error) {
    if (error.code === "RUNTIME_BUSY")
      console.log(
        JSON.stringify(
          {
            status: "blocked",
            checks: { runtime: { status: "busy", remedy: error.message } },
          },
          null,
          2,
        ),
      );
    else if (error.code === "RUNTIME_BUILD_MISMATCH")
      console.log(
        JSON.stringify(
          {
            status: "setup_required",
            data_directory: error.data_directory,
            checks: {
              runtime: { status: "unverified", remedy: error.message },
            },
          },
          null,
          2,
        ),
      );
    else console.error(`broader-vids: ${error.message}`);
    process.exitCode = error.exitCode || 2;
  }
}
