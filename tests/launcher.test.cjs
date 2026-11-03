'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { dataRoot, commandIndex, cacheWheel } = require('../bin/broader-vids.cjs');

test('explicit state location survives spaces and shell metacharacters', () => {
  const directory = 'example space & literal';
  assert.equal(dataRoot(['--data-dir', directory, 'doctor'], {}), path.resolve(directory));
  assert.equal(dataRoot([`--data-dir=${directory}`, 'doctor'], {}), path.resolve(directory));
});

test('environment state selection is independent of working directory', () => {
  const directory = path.resolve('example state');
  assert.equal(dataRoot([], { BROADER_VIDS_DATA_DIR: directory }), directory);
});

test('setup detection does not mistake a directory name for the command', () => {
  assert.equal(commandIndex(['--data-dir', 'setup', 'doctor']), 2);
  assert.equal(commandIndex(['--data-dir=x', 'setup']), 1);
  assert.equal(commandIndex(['setup', '--data-dir', 'x']), 0);
  assert.throws(() => dataRoot(['--data-dir'], {}));
});

test('repeated state options agree with argparse last-option precedence', () => {
  assert.equal(dataRoot(['--data-dir', 'first', '--data-dir=last', 'doctor'], {}), path.resolve('last'));
  assert.equal(dataRoot(['--data-dir=first', '--data-dir', 'last', 'doctor'], {}), path.resolve('last'));
  assert.throws(() => dataRoot(['--data-dir=first', '--data-dir='], {}));
});

test('same-version rebuilds use different immutable wheel cache paths', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-wheel-'));
  try {
    const source = path.join(directory, 'same-version.whl');
    fs.writeFileSync(source, 'first build');
    const first = cacheWheel(source, directory);
    fs.writeFileSync(source, 'second build');
    const second = cacheWheel(source, directory);
    assert.notEqual(first, second);
    assert.equal(fs.readFileSync(first, 'utf8'), 'first build');
    assert.equal(fs.readFileSync(second, 'utf8'), 'second build');
    assert.equal(cacheWheel(source, directory), second);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-wheel-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('setup receipts gate stale engines and refresh atomically across same-version rebuilds', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-build-lifecycle-'));
  const packageRoot = path.join(directory, 'plugin');
  const bin = path.join(packageRoot, 'bin');
  const python = path.join(packageRoot, 'python');
  const manifest = require('../package.json');
  const entrypoint = path.join(bin, 'broader-vids.cjs');
  const runtime = path.join(directory, 'profile', 'runtimes', manifest.version);
  const executable = path.join(runtime, process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
  const receipt = path.join(runtime, 'broader-vids-build.json');
  const profile = path.join(directory, 'profile');
  const calls = [];
  let failNextPip = false;
  const uv = (args) => {
    calls.push({ command: 'uv', args });
    if (args[0] === 'venv') {
      fs.mkdirSync(path.dirname(executable), { recursive: true });
      fs.writeFileSync(executable, 'fake python executable');
      return { status: 0 };
    }
    if (args[0] === 'pip' && failNextPip) {
      failNextPip = false;
      return { status: 1 };
    }
    return { status: 0 };
  };
  const spawnSync = (command, args) => {
    if (command === 'uv') return uv(args);
    if (command === executable && args.includes('dashboard') && args.includes('stop')) {
      const leases = fs.readdirSync(path.join(profile, '.launcher')).filter((name) => name.endsWith('.use.json'));
      assert.equal(leases.length, 1);
    }
    calls.push({ command, args });
    return { status: 0 };
  };
  const output = [];
  const stderr = [];
  const fakeRequire = (file) => {
    if (file === 'node:child_process') return { spawnSync };
    return createRequire(entrypoint)(file);
  };
  const source = fs.readFileSync(path.resolve(__dirname, '../bin/broader-vids.cjs'), 'utf8');
  const loadMain = (args, { cli = false } = {}) => {
    const module = { exports: {} };
    const localRequire = (file) => {
      if (file === 'node:child_process') return { spawnSync };
      return fakeRequire(file);
    };
    if (cli) localRequire.main = module;
    const childProcess = { env: process.env, platform: process.platform, pid: process.pid, argv: ['node', entrypoint, ...args], exitCode: 0 };
    vm.runInNewContext(`${source}\nmodule.exports.main = main;`, {
      module,
      exports: module.exports,
      require: localRequire,
      __dirname: bin,
      __filename: entrypoint,
      process: childProcess,
      console: {
        log: (value) => output.push(String(value)),
        error: (value) => stderr.push(String(value)),
      },
      Buffer,
    }, { filename: entrypoint });
    return { main: module.exports.main, process: childProcess };
  };

  try {
    fs.mkdirSync(bin, { recursive: true });
    fs.mkdirSync(python, { recursive: true });
    for (const name of ['runtime-lock.cjs', 'profile-data.cjs']) {
      fs.copyFileSync(path.resolve(__dirname, '../bin', name), path.join(bin, name));
    }
    fs.writeFileSync(path.join(packageRoot, 'package.json'), JSON.stringify(manifest));
    const wheel = path.join(python, `broader_vids-${manifest.version.replace('-alpha.', 'a')}-py3-none-any.whl`);
    fs.writeFileSync(wheel, 'first same-version wheel');
    fs.writeFileSync(path.join(python, 'core-requirements.txt'), '');
    fs.writeFileSync(path.join(python, 'evidence-requirements.txt'), '');
    fs.writeFileSync(path.join(python, 'asr-requirements.txt'), '');
    const { main } = loadMain([]);

    main(['--data-dir', profile, 'setup', '--evidence']);
    const firstReceipt = JSON.parse(fs.readFileSync(receipt, 'utf8'));
    const firstDigest = require('../bin/broader-vids.cjs').wheelDigest(wheel);
    assert.equal(firstReceipt.version, 2);
    assert.equal(firstReceipt.wheel_sha256, firstDigest);
    assert.deepEqual(firstReceipt.groups, ['core', 'evidence']);
    assert.deepEqual(Object.keys(firstReceipt.requirements_sha256), ['core', 'evidence']);
    assert.equal(fs.existsSync(path.join(profile, '.launcher', 'setup.json')), false);

    const evidenceRequirements = path.join(python, 'evidence-requirements.txt');
    fs.writeFileSync(evidenceRequirements, 'changed dependency lock');
    assert.throws(() => main(['--data-dir', profile, 'onboarding', 'status']), (error) => error.code === 'RUNTIME_BUILD_MISMATCH');
    fs.writeFileSync(evidenceRequirements, '');

    calls.length = 0;
    main(['--data-dir', profile, 'onboarding', 'complete', '--format', 'chat']);
    const completion = calls.find((call) => call.command === executable);
    assert.deepEqual([...completion.args], ['-m', 'broader_vids', '--data-dir', profile, '--data-dir', profile, 'onboarding', 'complete', '--format', 'chat']);

    fs.unlinkSync(receipt);
    assert.throws(() => main(['--data-dir', profile, 'onboarding', 'status']), (error) => error.code === 'RUNTIME_BUILD_MISMATCH');
    fs.writeFileSync(receipt, 'malformed receipt');
    calls.length = 0;
    const malformed = loadMain(['--data-dir', profile, 'onboarding', 'status'], { cli: true });
    assert.equal(malformed.process.exitCode, 2);
    assert.equal(JSON.parse(output.pop()).status, 'setup_required');
    assert.deepEqual(calls, []);

    calls.length = 0;
    main(['--data-dir', profile, 'dashboard', 'stop']);
    assert.equal(calls.filter((call) => call.command === executable).length, 1);

    fs.writeFileSync(wheel, 'second same-version wheel');
    calls.length = 0;
    assert.throws(() => main(['--data-dir', profile, 'onboarding', 'status']), (error) => error.code === 'RUNTIME_BUILD_MISMATCH');
    assert.equal(calls.some((call) => call.command === executable), false);
    main(['--data-dir', profile, 'setup', '--evidence']);
    const secondReceipt = JSON.parse(fs.readFileSync(receipt, 'utf8'));
    const secondDigest = require('../bin/broader-vids.cjs').wheelDigest(wheel);
    assert.notEqual(secondDigest, firstDigest);
    assert.equal(secondReceipt.wheel_sha256, secondDigest);

    main(['--data-dir', profile, 'setup']);
    const coreOnlyReceipt = JSON.parse(fs.readFileSync(receipt, 'utf8'));
    assert.deepEqual(coreOnlyReceipt.groups, ['core']);
    assert.deepEqual(Object.keys(coreOnlyReceipt.requirements_sha256), ['core']);

    failNextPip = true;
    assert.throws(() => main(['--data-dir', profile, 'setup', '--evidence']), /did not complete successfully/);
    assert.equal(fs.existsSync(receipt), false);
    assert.equal(fs.existsSync(path.join(profile, '.launcher', 'setup.json')), false);
    assert.equal(fs.existsSync(path.join(profile, '.launcher', 'gate.lock')), false);
    const temporaryFiles = [];
    const findTemporaryFiles = (root) => {
      for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        const target = path.join(root, entry.name);
        if (entry.isDirectory()) findTemporaryFiles(target);
        else if (entry.name.endsWith('.tmp')) temporaryFiles.push(target);
      }
    };
    findTemporaryFiles(profile);
    assert.deepEqual(temporaryFiles, []);
    assert.deepEqual(stderr, []);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-build-lifecycle-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('saved data selection persists without overriding explicit paths or environment', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-choice-'));
  const env = { LOCALAPPDATA: directory };
  const { selectedRoot, selectRoot } = require('../bin/broader-vids.cjs');
  try {
    const profile = path.join(directory, 'profile with spaces & text');
    selectRoot(profile, env);
    assert.equal(selectedRoot(env), profile);
    assert.equal(dataRoot([], env, selectedRoot(env)), profile);
    assert.equal(dataRoot(['--data-dir', directory], env, profile), directory);
    assert.equal(dataRoot([], { ...env, BROADER_VIDS_DATA_DIR: directory }, profile), directory);
    assert.throws(() => selectRoot('', env));
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-choice-'));
    fs.rmSync(directory, { recursive: true });
  }
});


test('fresh recommendation and preflight requests report setup without installing a runtime', () => {
  const { spawnSync } = require('node:child_process');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-onboarding-'));
  try {
    for (const command of ['recommend', 'preflight', 'onboarding']) {
      const result = spawnSync(process.execPath, [path.resolve(__dirname, '../bin/broader-vids.cjs'), '--data-dir', directory, command, ...(command === 'onboarding' ? ['status'] : [])], { encoding: 'utf8' });
      assert.equal(result.status, 2);
      const report = JSON.parse(result.stdout);
      assert.equal(report.status, 'setup_required');
      assert.equal(report.checks.runtime.status, 'missing');
      assert.equal(report.data_directory, directory);
      assert.deepEqual(fs.readdirSync(directory), []);
    }
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-onboarding-'));
    fs.rmSync(directory, { recursive: true });
  }
});


test('runtime leases permit parallel readers and reject setup until they finish', () => {
  const { withRuntimeUse, withSetup } = require('../bin/runtime-lock.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-runtime-'));
  try {
    withRuntimeUse(directory, () => {
      withRuntimeUse(directory, () => assert.throws(() => withSetup(directory, () => {}), /holds this profile/));
      assert.throws(() => withSetup(directory, () => {}), /holds this profile/);
    });
    withSetup(directory, () => {
      assert.throws(() => withSetup(directory, () => {}), /installation/);
      assert.throws(() => withRuntimeUse(directory, () => {}), /installation/);
      const { spawnSync } = require('node:child_process');
      const response = spawnSync(process.execPath, [path.resolve(__dirname, '../bin/broader-vids.cjs'), '--data-dir', directory, 'recommend'], { encoding: 'utf8' });
      assert.equal(response.status, 2);
      assert.equal(JSON.parse(response.stdout).checks.runtime.status, 'busy');
    });
    assert.throws(() => withSetup(directory, () => { throw Error('Failed install'); }), /Failed install/);
    assert.deepEqual(fs.readdirSync(path.join(directory, '.launcher')), []);
    const dashboard = path.join(directory, '.dashboard');
    fs.mkdirSync(dashboard);
    fs.writeFileSync(path.join(dashboard, 'server.json'), JSON.stringify({ pid: process.pid }));
    assert.throws(() => withSetup(directory, () => {}), /dashboard server/);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-runtime-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('failed UV launch releases the installation lock', () => {
  const { spawnSync } = require('node:child_process');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-install-fail-'));
  try {
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.toUpperCase() !== 'PATH'));
    env.PATH = '';
    const fakePlugin = path.join(directory, 'plugin');
    fs.mkdirSync(path.join(fakePlugin, 'bin'), { recursive: true });
    fs.mkdirSync(path.join(fakePlugin, 'python'));
    for (const name of ['broader-vids.cjs', 'runtime-lock.cjs', 'profile-data.cjs']) fs.copyFileSync(path.resolve(__dirname, '../bin', name), path.join(fakePlugin, 'bin', name));
    const manifest = require('../package.json');
    fs.writeFileSync(path.join(fakePlugin, 'package.json'), JSON.stringify(manifest));
    fs.writeFileSync(path.join(fakePlugin, 'python', `broader_vids-${manifest.version.replace('-alpha.', 'a')}-py3-none-any.whl`), 'fixture');
    const runtime = path.join(directory, 'runtimes', manifest.version);
    fs.mkdirSync(runtime, { recursive: true });
    const receipt = path.join(runtime, 'broader-vids-build.json');
    fs.writeFileSync(receipt, JSON.stringify({ version: 1, wheel_sha256: 'old receipt' }));
    const result = spawnSync(process.execPath, [path.join(fakePlugin, 'bin', 'broader-vids.cjs'), '--data-dir', directory, 'setup'], { encoding: 'utf8', env });
    assert.match(result.stderr, /Could not launch uv/);
    assert.equal(fs.existsSync(receipt), false);
    assert.notEqual(result.status, 0);
    assert.equal(fs.existsSync(path.join(directory, '.launcher', 'setup.json')), false);
    assert.equal(fs.existsSync(path.join(directory, '.launcher', 'gate.lock')), false);
  } finally {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('broader-vids-install-fail-'));
    fs.rmSync(directory, { recursive: true });
  }
});


test('Windows defaults honor redirected LOCALAPPDATA and have a native fallback', () => {
  const { defaultRoot } = require('../bin/broader-vids.cjs');
  const directory = path.resolve('example-user');
  const sandbox = path.join(directory, 'sandbox', 'AC');
  assert.equal(defaultRoot({ LOCALAPPDATA: sandbox, USERPROFILE: directory }, 'win32'), path.join(sandbox, 'broader-vids'));
  assert.equal(defaultRoot({ USERPROFILE: directory }, 'win32'), path.join(directory, 'AppData', 'Local', 'broader-vids'));
});


test('npm plugin installation registers a pinned registry source without touching a profile', () => {
  const { installPlugin } = require('../bin/plugin-install.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-npm-'));
  try {
    const cli = path.join(directory, 'cli with spaces');
    fs.mkdirSync(cli);
    fs.writeFileSync(path.join(cli, 'codex.exe'), 'fixture');
    const env = { CODEX_HOME: path.join(directory, 'codex'), PATH: cli, LOCALAPPDATA: path.join(directory, 'profiles') };
    const calls = [];
    const version = require('../package.json').version;
    const report = installPlugin(version, (command, args) => calls.push({ command, args }), env);
    assert.equal(report.status, 'installed');
    const root = path.join(env.CODEX_HOME, 'broader-vids-npm');
    const catalog = path.join(root, '.agents', 'plugins', 'marketplace.json');
    const saved = JSON.parse(fs.readFileSync(catalog, 'utf8'));
    assert.deepEqual(saved.plugins[0].source, { source: 'npm', package: 'broader-vids', version, registry: 'https://registry.npmjs.org' });
    assert.deepEqual(calls.map(call => call.args), [
      ['plugin', 'marketplace', 'add', root],
      ['plugin', 'add', 'broader-vids@broader-vids-npm'],
    ]);
    assert.equal(fs.existsSync(env.LOCALAPPDATA), false);
    assert.equal(fs.existsSync(path.join(root, 'install.lock')), false);
    assert.deepEqual(fs.readdirSync(path.dirname(catalog)), ['marketplace.json']);
    installPlugin(version, () => {}, env);
    assert.deepEqual(JSON.parse(fs.readFileSync(catalog, 'utf8')), saved);
    assert.throws(() => installPlugin(version, () => { throw new Error('fixture registration failed'); }, env), /fixture registration failed/);
    assert.equal(fs.existsSync(path.join(root, 'install.lock')), false);
    assert.deepEqual(fs.readdirSync(path.dirname(catalog)), ['marketplace.json']);
    fs.writeFileSync(path.join(root, 'install.lock'), 'busy');
    assert.throws(() => installPlugin(version, () => assert.fail('must not invoke Codex'), env), /Another plugin installation/);
    assert.equal(fs.readFileSync(path.join(root, 'install.lock'), 'utf8'), 'busy');
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-npm-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('npm installer resolves Windows native and npm-provided Codex without shell execution', () => {
  const { resolveCodex } = require('../bin/plugin-install.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-codex-'));
  try {
    const env = { PATH: directory };
    assert.throws(() => resolveCodex(env, 'win32'), /Install Codex CLI/);
    const script = path.join(directory, 'node_modules', '@openai', 'codex', 'bin', 'codex.js');
    fs.mkdirSync(path.dirname(script), { recursive: true });
    fs.writeFileSync(script, 'fixture');
    assert.deepEqual(resolveCodex(env, 'win32'), [process.execPath, [script]]);
    const executable = path.join(directory, 'codex.exe');
    fs.writeFileSync(executable, 'fixture');
    assert.deepEqual(resolveCodex(env, 'win32'), [executable, []]);
    assert.deepEqual(resolveCodex(env, 'linux'), ['codex', []]);
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-codex-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('plugin help needs no runtime and profile flags cannot redirect the installer', () => {
  const { spawnSync } = require('node:child_process');
  const launcher = path.resolve(__dirname, '../bin/broader-vids.cjs');
  const env = { ...process.env, BROADER_VIDS_DATA_DIR: 'unused installer profile' };
  const help = spawnSync(process.execPath, [launcher, 'plugin', '--help'], { env, encoding: 'utf8' });
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /broader-vids plugin install/);
  const invalid = spawnSync(process.execPath, [launcher, '--data-dir', 'unused installer profile', 'plugin', 'install'], { env, encoding: 'utf8' });
  assert.equal(invalid.status, 2);
  assert.match(invalid.stderr, /without profile arguments/);
});
