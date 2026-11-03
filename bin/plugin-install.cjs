"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function resolveCodex(env = process.env, platform = process.platform) {
  if (platform !== "win32") return ["codex", []];
  const directories = (env.PATH || env.Path || "").split(path.delimiter);
  for (const directory of directories) {
    const folder = directory.replace(/^"(.*)"$/, "$1");
    if (!folder) continue;
    const executable = path.join(folder, "codex.exe");
    if (fs.existsSync(executable)) return [executable, []];
    const script = path.join(
      folder,
      "node_modules",
      "@openai",
      "codex",
      "bin",
      "codex.js",
    );
    if (fs.existsSync(script)) return [process.execPath, [script]];
  }
  throw new Error(
    "Install Codex CLI and add it to PATH before installing the plugin.",
  );
}

function installPlugin(version, run, env = process.env) {
  const [command, prefix] = resolveCodex(env);
  const home = path.resolve(
    env.CODEX_HOME || path.join(os.homedir(), ".codex"),
  );
  const root = path.join(home, "broader-vids-npm");
  const directory = path.join(root, ".agents", "plugins");
  for (const folder of [root, path.join(root, ".agents"), directory]) {
    fs.mkdirSync(folder, { recursive: true });
    if (fs.lstatSync(folder).isSymbolicLink())
      throw new Error(
        "The managed marketplace must not contain symbolic links.",
      );
  }
  const lock = path.join(root, "install.lock");
  let handle;
  try {
    handle = fs.openSync(lock, "wx", 0o600);
  } catch (error) {
    if (error.code === "EEXIST")
      throw new Error(
        "Another plugin installation holds this marketplace. Wait for it to finish.",
      );
    throw error;
  }
  const catalog = path.join(directory, "marketplace.json");
  const temporary = path.join(directory, `.${randomUUID()}.tmp`);
  try {
    if (fs.existsSync(catalog) && fs.lstatSync(catalog).isSymbolicLink())
      throw new Error("The managed catalog must not be a symbolic link.");
    const marketplace = {
      name: "broader-vids-npm",
      interface: { displayName: "Broader Vids (npm)" },
      plugins: [
        {
          name: "broader-vids",
          source: {
            source: "npm",
            package: "broader-vids",
            version,
            registry: "https://registry.npmjs.org",
          },
          policy: { installation: "AVAILABLE", authentication: "ON_USE" },
          category: "Productivity",
        },
      ],
    };
    fs.writeFileSync(temporary, JSON.stringify(marketplace, null, 2) + "\n", {
      flag: "wx",
      mode: 0o600,
    });
    fs.renameSync(temporary, catalog);
    run(command, [...prefix, "plugin", "marketplace", "add", root]);
    run(command, [...prefix, "plugin", "add", "broader-vids@broader-vids-npm"]);
    return { status: "installed", marketplace: marketplace.name, version };
  } finally {
    try {
      fs.rmSync(temporary, { force: true });
    } finally {
      try {
        fs.closeSync(handle);
      } finally {
        fs.unlinkSync(lock);
      }
    }
  }
}

module.exports = { installPlugin, resolveCodex };
