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

function resolveClaude(env = process.env, platform = process.platform) {
  if (platform !== "win32") return ["claude", []];
  const directories = (env.PATH || env.Path || "").split(path.delimiter);
  for (const directory of directories) {
    const folder = directory.replace(/^"(.*)"$/, "$1");
    if (!folder) continue;
    const executable = path.join(folder, "claude.exe");
    if (fs.existsSync(executable)) return [executable, []];
    const script = path.join(
      folder,
      "node_modules",
      "@anthropic-ai",
      "claude-code",
      "cli.js",
    );
    if (fs.existsSync(script)) return [process.execPath, [script]];
  }
  throw new Error(
    "Install Claude Code CLI and add claude.exe to PATH before installing the plugin.",
  );
}

function guardedDirectories(paths, message) {
  for (const folder of paths) {
    fs.mkdirSync(folder, { recursive: true });
    if (fs.lstatSync(folder).isSymbolicLink()) throw new Error(message);
  }
}

function withMarketplaceLock(root, runInstall) {
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
  try {
    return runInstall();
  } finally {
    fs.closeSync(handle);
    fs.unlinkSync(lock);
  }
}

function installCodexPlugin(version, run, env) {
  const [command, prefix] = resolveCodex(env);
  const home = path.resolve(
    env.CODEX_HOME || path.join(os.homedir(), ".codex"),
  );
  const root = path.join(home, "broader-vids-npm");
  const directory = path.join(root, ".agents", "plugins");
  guardedDirectories(
    [root, path.join(root, ".agents"), directory],
    "The managed marketplace must not contain symbolic links.",
  );
  return withMarketplaceLock(root, () => {
    const catalog = path.join(directory, "marketplace.json");
    if (fs.existsSync(catalog) && fs.lstatSync(catalog).isSymbolicLink())
      throw new Error("The managed catalog must not be a symbolic link.");
    const temporary = path.join(directory, `.${randomUUID()}.tmp`);
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
    try {
      fs.writeFileSync(temporary, JSON.stringify(marketplace, null, 2) + "\n", {
        flag: "wx",
        mode: 0o600,
      });
      fs.renameSync(temporary, catalog);
      run(command, [...prefix, "plugin", "marketplace", "add", root]);
      run(command, [
        ...prefix,
        "plugin",
        "add",
        "broader-vids@broader-vids-npm",
      ]);
      return { status: "installed", marketplace: marketplace.name, version };
    } finally {
      fs.rmSync(temporary, { force: true });
    }
  });
}

function installClaudePlugin(version, run, env) {
  const [command, prefix] = resolveClaude(env);
  const home = path.resolve(
    env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude"),
  );
  const root = path.join(home, "marketplaces", "broader-vids-npm");
  const directory = path.join(root, ".claude-plugin");
  guardedDirectories(
    [home, path.join(home, "marketplaces"), root, directory],
    "The managed Claude marketplace must not contain symbolic links.",
  );
  return withMarketplaceLock(root, () => {
    const catalog = path.join(directory, "marketplace.json");
    if (fs.existsSync(catalog) && fs.lstatSync(catalog).isSymbolicLink())
      throw new Error(
        "The managed Claude catalog must not be a symbolic link.",
      );
    const temporary = path.join(directory, `.${randomUUID()}.tmp`);
    const marketplace = {
      name: "broader-vids-npm",
      description: "Broader Vids npm plugin catalog for Claude Code",
      owner: { name: "Broader Vids contributors" },
      plugins: [
        {
          name: "broader-vids",
          source: {
            source: "npm",
            package: "broader-vids",
            version,
            registry: "https://registry.npmjs.org",
          },
          description:
            "Curated YouTube and Bilibili recommendations with local evidence and feedback.",
        },
      ],
    };
    try {
      fs.writeFileSync(temporary, JSON.stringify(marketplace, null, 2) + "\n", {
        flag: "wx",
        mode: 0o600,
      });
      fs.renameSync(temporary, catalog);
      run(command, [...prefix, "plugin", "marketplace", "add", root]);
      run(command, [
        ...prefix,
        "plugin",
        "install",
        "broader-vids@broader-vids-npm",
      ]);
      return { status: "installed", marketplace: marketplace.name, version };
    } finally {
      fs.rmSync(temporary, { force: true });
    }
  });
}

function installPlugin(version, run, env = process.env, host = "codex") {
  if (host === "codex") return installCodexPlugin(version, run, env);
  if (host === "claude") return installClaudePlugin(version, run, env);
  throw new Error("Host must be codex or claude.");
}

module.exports = { installPlugin, resolveCodex, resolveClaude };
