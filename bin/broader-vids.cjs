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

function main(args = process.argv.slice(2)) {
  const packageRoot = path.resolve(__dirname, "..");
  const manifest = JSON.parse(
    fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"),
  );
  const index = commandIndex(args);
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
  if (args[commandIndex(args)] === "setup") {
    const pythonVersion = manifest.version.replace("-alpha.", "a");
    const wheelRoot = path.join(packageRoot, "python");
    const wheels = fs.existsSync(wheelRoot)
      ? fs
          .readdirSync(wheelRoot)
          .filter(
            (name) =>
              name.startsWith(`broader_vids-${pythonVersion}-`) &&
              name.endsWith(".whl"),
          )
      : [];
    if (wheels.length !== 1)
      throw new Error(
        "The package needs exactly one matching bundled wheel; in a checkout run: uv build --out-dir python",
      );
    return withSetup(root, () => {
      require("./profile-data.cjs").ensureMarker(root);
      if (!fs.existsSync(executable))
        invoke("uv", [
          "venv",
          "--allow-existing",
          "--python",
          ">=3.12",
          runtime,
        ]);
      const wheel = cacheWheel(path.join(wheelRoot, wheels[0]), root);
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
      for (const extra of ["core", ...extras]) {
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
  return withRuntimeUse(root, () =>
    invoke(executable, ["-m", "broader_vids", "--data-dir", root, ...args]),
  );
}

module.exports = {
  defaultRoot,
  dataRoot,
  commandIndex,
  cacheWheel,
  selectedRoot,
  selectRoot,
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
    else console.error(`broader-vids: ${error.message}`);
    process.exitCode = error.exitCode || 2;
  }
}
