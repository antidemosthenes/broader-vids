#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
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

function parseHost(args) {
  let host = "codex";
  let explicit = false;
  const values = [];
  for (let index = 0; index < args.length; index += 1) {
    let selected;
    if (args[index] === "--host") {
      selected = args[++index];
      if (!selected || selected.startsWith("--"))
        throw new Error("--host requires codex or claude.");
    } else if (args[index].startsWith("--host=")) {
      selected = args[index].slice("--host=".length);
    } else {
      values.push(args[index]);
      continue;
    }
    if (!["codex", "claude"].includes(selected))
      throw new Error("--host must be codex or claude.");
    if (explicit && host !== selected)
      throw new Error("Use one host value per command.");
    host = selected;
    explicit = true;
  }
  return { args: values, host, explicit };
}

function commandIndex(args) {
  let index = 0;
  while (index < args.length) {
    if (args[index] === "--data-dir") index += 2;
    else if (args[index].startsWith("--data-dir=")) index += 1;
    else if (args[index] === "--host") index += 2;
    else if (args[index].startsWith("--host=")) index += 1;
    else return index;
  }
  return -1;
}

function validateSetupArguments(args, index) {
  for (let item = index + 1; item < args.length; item += 1) {
    const value = args[item];
    if (["--asr", "--evidence", "--check"].includes(value)) continue;
    if (value === "--data-dir") {
      item += 1;
      continue;
    }
    if (value.startsWith("--data-dir=")) continue;
    throw new Error(
      "Use setup with optional --evidence, --asr, --check, and --data-dir PATH.",
    );
  }
}

function runtimeEnvironment() {
  return Object.fromEntries(
    Object.entries(process.env).filter(
      ([name]) =>
        ![
          "PYTHONHOME",
          "PYTHONPATH",
          "__PYVENV_LAUNCHER__",
          "UV_INTERNAL__PYTHONHOME",
        ].includes(name.toUpperCase()),
    ),
  );
}

function environmentArguments(
  runtime,
  platform = process.platform,
  linked = false,
) {
  if (platform === "win32" && runtime.includes(";"))
    throw new Error(
      "Python venv cannot use a Windows data path with a semicolon. Select a path without semicolons before setup.",
    );
  return platform === "win32"
    ? [
        "run",
        "--no-project",
        "--python",
        ">=3.12",
        "python",
        "-m",
        "venv",
        "--without-pip",
        linked ? "--symlinks" : "--copies",
        runtime,
      ]
    : ["venv", "--allow-existing", "--python", ">=3.12", runtime];
}

function clearInterpreterEntries(runtime) {
  for (const directory of [path.dirname(runtime), runtime]) {
    try {
      if (fs.lstatSync(directory).isSymbolicLink())
        throw new Error(
          "The managed runtime directory is a link. Setup stopped before removing interpreter entries.",
        );
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  if (!fs.existsSync(runtime)) return;
  const config = fs
    .readdirSync(runtime, { withFileTypes: true })
    .find((entry) => entry.name.toLowerCase() === "pyvenv.cfg");
  if (config?.isSymbolicLink())
    throw new Error(
      "The runtime configuration is a link. Setup left it unchanged.",
    );
  const scripts = path.join(runtime, "Scripts");
  if (!fs.existsSync(scripts)) return;
  const target = fs.realpathSync(scripts);
  const relative = path.relative(fs.realpathSync(runtime), target);
  if (relative.toLowerCase() !== "scripts")
    throw new Error(
      "The runtime Scripts directory resolves outside its managed location. Setup stopped before removing interpreter entries.",
    );
  for (const entry of fs.readdirSync(scripts, { withFileTypes: true })) {
    if (
      !entry.isDirectory() &&
      /^(?:python[\dw]*\.(?:exe|dll)|vcruntime\d+(?:_\d+)?\.dll)$/i.test(
        entry.name,
      )
    )
      fs.unlinkSync(path.join(scripts, entry.name));
  }
}

function checkRuntime(runtime, executable) {
  const report = {
    version: 1,
    runtime,
    executable,
    creation_backend: process.platform === "win32" ? "stdlib-venv" : "uv-venv",
    executable_linked: false,
    status: "unverified",
    reason: "",
  };
  let expectedIdentity;
  try {
    report.executable_linked = fs.lstatSync(executable).isSymbolicLink();
    fs.statSync(path.join(runtime, "pyvenv.cfg"));
    const entry = fs.statSync(runtime, { bigint: true });
    expectedIdentity = [entry.dev.toString(), entry.ino.toString()];
  } catch (error) {
    if (error.code === "ENOENT")
      return {
        ...report,
        status: "missing",
        reason: "The interpreter or pyvenv.cfg is missing.",
      };
    return {
      ...report,
      status: "unusable",
      reason: `The host cannot inspect the selected runtime (${error.code}). Request access to this same directory.`,
    };
  }
  const result = spawnSync(
    executable,
    [
      "-I",
      "-B",
      "-c",
      [
        "import json,sys,pathlib,os",
        'result={"prefix":sys.prefix,"base_prefix":sys.base_prefix,"interpreter_executable":sys.executable,"python_version":list(sys.version_info[:2])}',
        "environment=pathlib.Path(sys.executable).parent.parent",
        "try:",
        ' environment.joinpath("pyvenv.cfg").read_bytes()',
        ' result["configuration_readable"]=True',
        " entry=os.stat(sys.prefix)",
        ' result["root_identity"]=[str(entry.st_dev & 0xffffffff if os.name=="nt" else entry.st_dev),str(entry.st_ino)]',
        ' result["environment_matches_executable"]=os.path.samestat(entry,os.stat(environment))',
        ' result["isolated"]=os.path.normcase(os.path.abspath(sys.prefix))!=os.path.normcase(os.path.abspath(sys.base_prefix))',
        "except OSError as error:",
        ' result.setdefault("configuration_readable",False)',
        ' key="identity_error" if result["configuration_readable"] else "configuration_error"',
        ' result[key]={"message":str(error),"errno":error.errno,"winerror":getattr(error,"winerror",None)}',
        "print(json.dumps(result))",
      ].join("\n"),
    ],
    {
      encoding: "utf8",
      timeout: 15000,
      windowsHide: true,
      env: runtimeEnvironment(),
    },
  );
  if (result.error || result.status !== 0)
    return {
      ...report,
      status: "unusable",
      reason:
        result.error?.code ||
        result.stderr?.trim().slice(0, 1500) ||
        "The interpreter did not complete its check.",
    };
  try {
    const value = JSON.parse(result.stdout);
    const version = value.python_version;
    Object.assign(report, {
      prefix: value.prefix,
      base_prefix: value.base_prefix,
      python_version: version,
      interpreter_executable: value.interpreter_executable,
      environment_matches_executable: value.environment_matches_executable,
      isolated: value.isolated,
      root_identity: value.root_identity,
      expected_identity: expectedIdentity,
      identity_error: value.identity_error,
      configuration_readable: value.configuration_readable,
      configuration_error: value.configuration_error,
    });
    if (value.configuration_readable === false)
      return {
        ...report,
        status: "unusable",
        reason:
          "The Python process cannot read its own pyvenv.cfg in this host context. Setup stopped before dependency installation.",
      };
    if (
      value.environment_matches_executable !== true ||
      value.isolated !== true ||
      !Array.isArray(value.root_identity) ||
      value.root_identity.length !== 2 ||
      value.root_identity.some(
        (part, index) => part !== expectedIdentity[index],
      )
    )
      return {
        ...report,
        status: "unusable",
        reason:
          "The interpreter prefix does not identify the selected virtual environment.",
      };
    if (
      !Array.isArray(version) ||
      version.length !== 2 ||
      !version.every(Number.isInteger) ||
      version[0] !== 3 ||
      version[1] < 12
    )
      return {
        ...report,
        status: "unusable",
        reason: "The runtime requires Python 3.12 or later.",
      };
    return {
      ...report,
      status: "ready",
      reason: "The selected Python environment is isolated.",
    };
  } catch {
    return {
      ...report,
      status: "unverified",
      reason: "The interpreter returned an invalid identity report.",
    };
  }
}

function invoke(command, args, env = process.env) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: false,
    env: { ...env, BROADER_VIDS_LAUNCHER: __filename },
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

function withWheelSource(wheel, action) {
  const unsafe = (value) => /[#%?]/.test(value);
  if (!unsafe(wheel)) return action(pathToFileURL(wheel).href);
  const temporaryRoot = os.tmpdir();
  if (unsafe(temporaryRoot))
    throw new Error(
      "UV cannot install this wheel from the selected paths. Set TEMP and TMP to a directory without #, %, or ?, then repeat setup.",
    );
  const directory = fs.mkdtempSync(
    path.join(temporaryRoot, "broader-vids-wheel-"),
  );
  const temporary = path.join(directory, path.basename(wheel));
  try {
    fs.copyFileSync(wheel, temporary);
    return action(pathToFileURL(temporary).href);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    fs.rmdirSync(directory);
  }
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
  const selectedHost = parseHost(args);
  args = selectedHost.args;
  const host = selectedHost.host;
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
      console.log(
        "Usage: broader-vids plugin install [--host codex|claude] or broader-vids plugin update --host claude",
      );
      return;
    }
    if (
      index !== 0 ||
      args.length !== 2 ||
      !["install", "update"].includes(args[1])
    )
      throw new Error(
        "Use broader-vids plugin install, or plugin update --host claude, without profile arguments.",
      );
    const result = require("./plugin-install.cjs").installPlugin(
      manifest.version,
      invoke,
      process.env,
      host,
      args[1],
    );
    console.log(JSON.stringify(result, null, 2));
    if (args[1] === "update")
      console.log(
        "Start a new Claude Code session and run setup to refresh the matching runtime.",
      );
    else
      console.log(
        host === "claude"
          ? "Start a new Claude Code session and invoke the Broader Vids setup skill."
          : "Start a new Codex conversation and invoke the Broader Vids setup skill.",
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
    if (args.includes("--check")) {
      const report = withRuntimeUse(root, () =>
        checkRuntime(runtime, executable),
      );
      console.log(JSON.stringify({ data_directory: root, ...report }, null, 2));
      if (report.status !== "ready") process.exitCode = 2;
      return;
    }
    const creationArguments = environmentArguments(runtime);
    const sourceWheel = bundledWheel(packageRoot, manifest.version);
    const wheelRoot = path.dirname(sourceWheel);
    return withSetup(root, () => {
      require("./profile-data.cjs").ensureMarker(root);
      const receipt = path.join(runtime, "broader-vids-build.json");
      if (fs.existsSync(receipt)) fs.unlinkSync(receipt);
      const environment = runtimeEnvironment();
      let runtimeCheck = checkRuntime(runtime, executable);
      if (runtimeCheck.status !== "ready") {
        if (process.platform === "win32") clearInterpreterEntries(runtime);
        invoke("uv", creationArguments, environment);
        runtimeCheck = checkRuntime(runtime, executable);
        if (runtimeCheck.status !== "ready" && process.platform === "win32") {
          clearInterpreterEntries(runtime);
          invoke(
            "uv",
            environmentArguments(runtime, "win32", true),
            environment,
          );
          runtimeCheck = checkRuntime(runtime, executable);
          runtimeCheck.link_fallback = runtimeCheck.executable_linked
            ? "created"
            : "unavailable";
        }
        if (runtimeCheck.status !== "ready") {
          const error = new Error(
            `The Python runtime is not an isolated environment. ${runtimeCheck.reason}`,
          );
          error.code = "RUNTIME_NOT_ISOLATED";
          error.report = { data_directory: root, ...runtimeCheck };
          throw error;
        }
      }
      const wheel = cacheWheel(sourceWheel, root);
      withWheelSource(wheel, (url) =>
        invoke(
          "uv",
          [
            "pip",
            "install",
            "--python",
            executable,
            "--reinstall",
            "--no-index",
            "--no-deps",
            url,
          ],
          environment,
        ),
      );
      const extras = args.includes("--asr")
        ? ["evidence", "asr"]
        : args.includes("--evidence")
          ? ["evidence"]
          : [];
      const groups = ["core", ...extras];
      for (const extra of groups) {
        invoke(
          "uv",
          [
            "pip",
            "install",
            "--python",
            executable,
            "--require-hashes",
            "-r",
            path.join(wheelRoot, `${extra}-requirements.txt`),
          ],
          environment,
        );
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
    const engineArgs = [
      ...args.slice(0, index),
      ...(selectedHost.explicit ? ["--host", host] : []),
      ...args.slice(index),
    ];
    return invoke(
      executable,
      ["-m", "broader_vids", "--data-dir", root, ...engineArgs],
      runtimeEnvironment(),
    );
  });
}

module.exports = {
  defaultRoot,
  dataRoot,
  environmentArguments,
  commandIndex,
  parseHost,
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
    else if (error.code === "RUNTIME_NOT_ISOLATED")
      console.log(JSON.stringify(error.report, null, 2));
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
