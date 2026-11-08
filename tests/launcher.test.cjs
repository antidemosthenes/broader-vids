'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { dataRoot, commandIndex, cacheWheel, parseHost } = require('../bin/broader-vids.cjs');

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
  assert.equal(commandIndex(['--host', 'claude', 'recommend']), 2);
  assert.deepEqual(parseHost(['--host=claude', 'recommend']), { args: ['recommend'], host: 'claude', explicit: true });
  assert.throws(() => dataRoot(['--data-dir'], {}));
});

test('repeated state options agree with argparse last-option precedence', () => {
  assert.equal(dataRoot(['--data-dir', 'first', '--data-dir=last', 'doctor'], {}), path.resolve('last'));
  assert.equal(dataRoot(['--data-dir=first', '--data-dir', 'last', 'doctor'], {}), path.resolve('last'));
  assert.throws(() => dataRoot(['--data-dir=first', '--data-dir='], {}));
});

test('Windows environment creation uses standard Python while Linux retains UV venv', () => {
  const { environmentArguments } = require('../bin/broader-vids.cjs');
  const runtime = path.resolve('profile #! with spaces', 'runtimes', 'fixture');
  assert.deepEqual(environmentArguments(runtime, 'win32'), ['run', '--no-project', '--python', '>=3.12', 'python', '-m', 'venv', '--without-pip', '--copies', runtime]);
  assert.equal(environmentArguments(runtime, 'win32', true).includes('--symlinks'), true);
  assert.throws(() => environmentArguments('profile;name', 'win32'), /semicolon/);
  assert.deepEqual(environmentArguments(runtime, 'linux'), ['venv', '--allow-existing', '--python', '>=3.12', runtime]);
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
  const packageRoot = path.join(directory, 'plugin #! 100%');
  const bin = path.join(packageRoot, 'bin');
  const python = path.join(packageRoot, 'python');
  const manifest = require('../package.json');
  const entrypoint = path.join(bin, 'broader-vids.cjs');
  const runtime = path.join(directory, 'profile with #! and spaces', 'runtimes', manifest.version);
  const executable = path.join(runtime, 'Scripts', 'python.exe');
  const receipt = path.join(runtime, 'broader-vids-build.json');
  const profile = path.join(directory, 'profile with #! and spaces');
  const calls = [];
  let failNextPip = false;
  let failRuntimeProbe = false;
  let unreadableConfiguration = false;
  let inaccessibleConfiguration = false;
  let resolvedSandboxPrefix;
  let foreignRuntimeIdentity = false;
  let requiresLinkedRuntime = false;
  let linkedRuntime = false;
  let refuseRuntimeLinks = false;
  let linkedRuntimeDirectory = false;
  let linkedConfiguration = false;
  const uv = (args) => {
    calls.push({ command: 'uv', args });
    if (args[0] === 'venv' || (args[0] === 'run' && args.includes('venv'))) {
      fs.mkdirSync(path.dirname(executable), { recursive: true });
      linkedRuntime = args.includes('--symlinks') && !refuseRuntimeLinks;
      fs.writeFileSync(executable, 'fake python executable');
      fs.writeFileSync(path.join(runtime, 'pyvenv.cfg'), 'include-system-site-packages = false');
      return { status: 0 };
    }
    if (args[0] === 'pip' && failNextPip) {
      failNextPip = false;
      return { status: 1 };
    }
    return { status: 0 };
  };
  const spawnSync = (command, args, options) => {
    if (command === 'uv' || command === executable) {
      for (const name of ['PYTHONHOME', 'PYTHONPATH', '__PYVENV_LAUNCHER__', 'UV_INTERNAL__PYTHONHOME']) {
        assert.equal(options.env[name], undefined);
      }
    }
    if (command === 'uv') return uv(args);
    if (command === executable && args[0] === '-I') {
      calls.push({ command, args });
      const launcher = path.join(profile, '.launcher');
      if (!fs.existsSync(path.join(launcher, 'setup.json'))) {
        assert.equal(fs.readdirSync(launcher).filter((name) => name.endsWith('.use.json')).length, 1);
        assert.throws(() => createRequire(entrypoint)('./runtime-lock.cjs').withSetup(profile, () => {}), /holds this profile/);
      }
      const identity = fs.statSync(runtime, { bigint: true });
      return { status: 0, stdout: JSON.stringify({ root_identity: [identity.dev.toString(), foreignRuntimeIdentity ? '0' : identity.ino.toString()], prefix: failRuntimeProbe ? packageRoot : (resolvedSandboxPrefix || runtime), base_prefix: packageRoot, interpreter_executable: executable, environment_matches_executable: !failRuntimeProbe, isolated: !failRuntimeProbe, python_version: [3, 13], configuration_readable: !(unreadableConfiguration || (requiresLinkedRuntime && !linkedRuntime)), ...((unreadableConfiguration || (requiresLinkedRuntime && !linkedRuntime)) ? { configuration_error: { message: 'Access denied', errno: 13, winerror: 5 } } : {}) }) };
    }
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
      if (file === 'node:fs') return { ...fs, lstatSync: (target, ...args) => {
        const value = fs.lstatSync(target, ...args);
        if (target === executable) value.isSymbolicLink = () => linkedRuntime;
        if (target === runtime) value.isSymbolicLink = () => linkedRuntimeDirectory;
        return value;
      }, readdirSync: (target, options) => {
        const entries = fs.readdirSync(target, options);
        if (linkedConfiguration && target === runtime && options?.withFileTypes) {
          const config = entries.find((entry) => entry.name === 'pyvenv.cfg');
          config.name = 'PyVenv.CFG';
          config.isSymbolicLink = () => true;
        }
        return entries;
      }, statSync: (target, ...args) => {
        if (inaccessibleConfiguration && target === path.join(runtime, 'pyvenv.cfg')) {
          throw Object.assign(new Error('Access denied'), { code: 'EACCES' });
        }
        return fs.statSync(target, ...args);
      }, realpathSync: (target) => {
        if (target === packageRoot) throw Object.assign(new Error('Base directory metadata is restricted'), { code: 'EACCES' });
        return fs.realpathSync(target);
      } };
      return fakeRequire(file);
    };
    if (cli) localRequire.main = module;
    const childProcess = { env: { ...process.env, PYTHONHOME: packageRoot, PYTHONPATH: packageRoot, __PYVENV_LAUNCHER__: packageRoot, UV_INTERNAL__PYTHONHOME: packageRoot }, platform: 'win32', pid: process.pid, argv: ['node', entrypoint, ...args], exitCode: 0 };
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

    const invalidProfile = path.join(directory, 'invalid setup profile');
    assert.throws(() => main(['setup', '--data-dir', invalidProfile, '--evidnce']), /optional --evidence, --asr/);
    assert.throws(() => main(['setup', '--host', 'claude', '--invalid']), /optional --evidence, --asr/);
    assert.deepEqual(calls, []);
    assert.equal(fs.existsSync(invalidProfile), false);
    const semicolonProfile = path.join(directory, 'profile;invalid');
    assert.throws(() => main(['setup', '--data-dir', semicolonProfile]), /semicolon/);
    assert.equal(fs.existsSync(semicolonProfile), false);

    main(['setup', '--evidence', '--data-dir', profile]);
    const wheelInstall = calls.find((call) => call.command === 'uv' && call.args.includes('--reinstall'));
    const installedWheelURL = wheelInstall.args.at(-1);
    assert.match(installedWheelURL, /^file:/);
    const installedWheelPath = require('node:url').fileURLToPath(installedWheelURL);
    assert.equal(path.basename(installedWheelPath), path.basename(wheel));
    assert.doesNotMatch(installedWheelPath, /[#%?]/);
    assert.equal(fs.existsSync(path.dirname(installedWheelPath)), false);
    assert.equal(fs.readFileSync(cacheWheel(wheel, profile), 'utf8'), fs.readFileSync(wheel, 'utf8'));
    const firstReceipt = JSON.parse(fs.readFileSync(receipt, 'utf8'));
    const firstDigest = require('../bin/broader-vids.cjs').wheelDigest(wheel);
    assert.equal(firstReceipt.version, 2);
    assert.equal(firstReceipt.wheel_sha256, firstDigest);
    assert.deepEqual(firstReceipt.groups, ['core', 'evidence']);
    assert.deepEqual(Object.keys(firstReceipt.requirements_sha256), ['core', 'evidence']);
    assert.equal(fs.existsSync(path.join(profile, '.launcher', 'setup.json')), false);

    calls.length = 0;
    main(['setup', '--evidence', '--data-dir', profile]);
    assert.equal(calls.some((call) => call.command === 'uv' && call.args.includes('venv')), false);
    fs.unlinkSync(path.join(runtime, 'pyvenv.cfg'));
    calls.length = 0;
    main(['setup', '--evidence', '--data-dir', profile]);
    assert.equal(calls.some((call) => call.command === 'uv' && call.args.includes('venv')), true);
    assert.equal(fs.existsSync(path.join(runtime, 'pyvenv.cfg')), true);

    resolvedSandboxPrefix = path.join(directory, 'physical #!sandbox.uuid', 'runtimes', manifest.version);
    calls.length = 0;
    main(['--data-dir', profile, 'setup', '--check']);
    const aliased = JSON.parse(output.pop());
    assert.equal(aliased.status, 'ready');
    assert.notEqual(aliased.prefix, runtime);
    assert.equal(aliased.environment_matches_executable, true);
    assert.equal(calls.some((call) => call.command === 'uv'), false);
    foreignRuntimeIdentity = true;
    main(['--data-dir', profile, 'setup', '--check']);
    assert.equal(JSON.parse(output.pop()).status, 'unusable');
    foreignRuntimeIdentity = false;
    resolvedSandboxPrefix = undefined;
    inaccessibleConfiguration = true;
    calls.length = 0;
    main(['--data-dir', profile, 'setup', '--check']);
    const inaccessible = JSON.parse(output.pop());
    assert.equal(inaccessible.status, 'unusable');
    assert.match(inaccessible.reason, /EACCES/);
    assert.deepEqual(calls, []);
    assert.deepEqual(fs.readdirSync(path.join(profile, '.launcher')), []);
    assert.equal(fs.existsSync(receipt), true);
    inaccessibleConfiguration = false;

    requiresLinkedRuntime = true;
    calls.length = 0;
    main(['--data-dir', profile, 'setup', '--evidence']);
    const creations = calls.filter((call) => call.command === 'uv' && call.args.includes('venv'));
    assert.equal(creations.length, 2);
    assert.equal(creations[0].args.includes('--copies'), true);
    assert.equal(creations[1].args.includes('--symlinks'), true);
    assert.equal(linkedRuntime, true);
    main(['--data-dir', profile, 'setup', '--check']);
    assert.equal(JSON.parse(output.pop()).executable_linked, true);
    refuseRuntimeLinks = true;
    linkedRuntime = false;
    calls.length = 0;
    const noLinks = loadMain(['--data-dir', profile, 'setup'], { cli: true });
    const noLinksReport = JSON.parse(output.pop());
    assert.equal(noLinks.process.exitCode, 2);
    assert.equal(noLinksReport.link_fallback, 'unavailable');
    assert.equal(calls.filter((call) => call.command === 'uv' && call.args.includes('venv')).length, 2);
    assert.equal(calls.some((call) => call.command === 'uv' && call.args[0] === 'pip'), false);
    refuseRuntimeLinks = false;
    requiresLinkedRuntime = false;
    main(['--data-dir', profile, 'setup']);

    linkedRuntimeDirectory = true;
    failRuntimeProbe = true;
    calls.length = 0;
    const preservedInterpreter = fs.readFileSync(executable, 'utf8');
    assert.throws(() => main(['--data-dir', profile, 'setup']), /managed runtime directory is a link/);
    assert.equal(fs.readFileSync(executable, 'utf8'), preservedInterpreter);
    assert.equal(calls.some((call) => call.command === 'uv'), false);
    linkedRuntimeDirectory = false;
    linkedConfiguration = true;
    assert.throws(() => main(['--data-dir', profile, 'setup']), /configuration is a link/);
    assert.equal(fs.readFileSync(executable, 'utf8'), preservedInterpreter);
    assert.equal(calls.some((call) => call.command === 'uv'), false);
    linkedConfiguration = false;
    failRuntimeProbe = false;
    main(['--data-dir', profile, 'setup']);

    unreadableConfiguration = true;
    calls.length = 0;
    const checked = loadMain(['--data-dir', profile, 'setup', '--check'], { cli: true });
    const diagnostic = JSON.parse(output.pop());
    assert.equal(checked.process.exitCode, 2);
    assert.equal(diagnostic.status, 'unusable');
    assert.equal(diagnostic.configuration_error.winerror, 5);
    assert.equal(diagnostic.prefix, runtime);
    assert.equal(calls.some((call) => call.command === 'uv'), false);
    assert.equal(fs.existsSync(receipt), true);
    calls.length = 0;
    const blocked = loadMain(['--data-dir', profile, 'setup'], { cli: true });
    assert.equal(blocked.process.exitCode, 2);
    assert.equal(JSON.parse(output.pop()).configuration_error.winerror, 5);
    assert.equal(calls.some((call) => call.command === 'uv' && call.args[0] === 'pip'), false);
    unreadableConfiguration = false;
    main(['--data-dir', profile, 'setup', '--evidence']);

    failRuntimeProbe = true;
    calls.length = 0;
    assert.throws(() => main(['setup', '--evidence', '--data-dir', profile]), /not an isolated/);
    assert.equal(calls.some((call) => call.command === 'uv' && call.args[0] === 'pip'), false);
    assert.equal(fs.existsSync(receipt), false);
    assert.equal(fs.existsSync(path.join(profile, '.launcher', 'setup.json')), false);
    failRuntimeProbe = false;
    main(['setup', '--evidence', '--data-dir', profile]);

    const evidenceRequirements = path.join(python, 'evidence-requirements.txt');
    fs.writeFileSync(evidenceRequirements, 'changed dependency lock');
    assert.throws(() => main(['--data-dir', profile, 'onboarding', 'status']), (error) => error.code === 'RUNTIME_BUILD_MISMATCH');
    fs.writeFileSync(evidenceRequirements, '');

    calls.length = 0;
    main(['--data-dir', profile, 'onboarding', 'complete', '--format', 'chat']);
    const completion = calls.find((call) => call.command === executable);
    assert.deepEqual([...completion.args], ['-m', 'broader_vids', '--data-dir', profile, '--data-dir', profile, 'onboarding', 'complete', '--format', 'chat']);
    calls.length = 0;
    main(['--data-dir', profile, '--host', 'claude', 'onboarding', 'complete', '--format', 'chat', '--model', 'claude-model', '--effort', 'high']);
    const claudeCompletion = calls.find((call) => call.command === executable);
    assert.deepEqual([...claudeCompletion.args], ['-m', 'broader_vids', '--data-dir', profile, '--data-dir', profile, '--host', 'claude', 'onboarding', 'complete', '--format', 'chat', '--model', 'claude-model', '--effort', 'high']);

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
    for (const call of calls.filter((call) => call.command === 'uv' && call.args.includes('--reinstall'))) {
      assert.equal(fs.existsSync(path.dirname(require('node:url').fileURLToPath(call.args.at(-1)))), false);
    }
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
      for (const command of [['recommend'], ['setup', '--check']]) {
        const response = spawnSync(process.execPath, [path.resolve(__dirname, '../bin/broader-vids.cjs'), '--data-dir', directory, ...command], { encoding: 'utf8' });
        assert.equal(response.status, 2);
        assert.equal(JSON.parse(response.stdout).checks.runtime.status, 'busy');
      }
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
    const env = { CODEX_HOME: path.join(directory, 'codex'), CLAUDE_CONFIG_DIR: path.join(directory, 'claude'), PATH: cli, LOCALAPPDATA: path.join(directory, 'profiles') };
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
    assert.equal(fs.existsSync(env.CLAUDE_CONFIG_DIR), false);
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

test('Claude npm installer uses an isolated marketplace and native CLI commands', () => {
  const { installPlugin } = require('../bin/plugin-install.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-claude-'));
  try {
    const cli = path.join(directory, 'claude cli');
    fs.mkdirSync(cli);
    fs.writeFileSync(path.join(cli, 'claude.exe'), 'fixture');
    const env = {
      CLAUDE_CONFIG_DIR: path.join(directory, 'claude-home'),
      CODEX_HOME: path.join(directory, 'codex-home'),
      LOCALAPPDATA: path.join(directory, 'profiles'),
      PATH: cli,
    };
    const calls = [];
    const version = require('../package.json').version;
    const report = installPlugin(version, (command, args) => calls.push({ command, args }), env, 'claude');
    assert.deepEqual(report, { status: 'installed', marketplace: 'broader-vids-npm', version });
    const root = path.join(env.CLAUDE_CONFIG_DIR, 'marketplaces', 'broader-vids-npm');
    const catalogPath = path.join(root, '.claude-plugin', 'marketplace.json');
    const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
    assert.deepEqual(catalog.plugins[0].source, {
      source: 'npm', package: 'broader-vids', version, registry: 'https://registry.npmjs.org',
    });
    assert.deepEqual(calls.map(call => call.args), [
      ['plugin', 'marketplace', 'add', root],
      ['plugin', 'install', 'broader-vids@broader-vids-npm'],
      ['plugin', 'marketplace', 'update', 'broader-vids-npm'],
      ['plugin', 'update', 'broader-vids@broader-vids-npm', '--scope', 'user'],
    ]);
    assert.equal(fs.existsSync(env.CODEX_HOME), false);
    assert.equal(fs.existsSync(env.LOCALAPPDATA), false);
    assert.equal(fs.existsSync(path.join(root, 'install.lock')), false);
    assert.deepEqual(fs.readdirSync(path.join(root, '.claude-plugin')), ['marketplace.json']);
    installPlugin(version, () => {}, env, 'claude');
    assert.deepEqual(JSON.parse(fs.readFileSync(catalogPath, 'utf8')), catalog);
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-claude-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('Claude npm update refreshes its pinned marketplace and preserves user data', () => {
  const { installPlugin } = require('../bin/plugin-install.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-claude-update-'));
  try {
    const cli = path.join(directory, 'claude cli');
    fs.mkdirSync(cli);
    fs.writeFileSync(path.join(cli, 'claude.exe'), 'fixture');
    const env = {
      CLAUDE_CONFIG_DIR: path.join(directory, 'claude-home'),
      LOCALAPPDATA: path.join(directory, 'profiles'),
      PATH: cli,
    };
    const calls = [];
    const version = require('../package.json').version;
    const nextVersion = `${version.split('.')[0]}.${version.split('.')[1]}.${Number(version.split('.')[2]) + 1}`;
    installPlugin(version, (command, args) => calls.push({ command, args }), env, 'claude');
    const root = path.join(env.CLAUDE_CONFIG_DIR, 'marketplaces', 'broader-vids-npm');
    const catalogPath = path.join(root, '.claude-plugin', 'marketplace.json');
    const settingsPath = path.join(env.CLAUDE_CONFIG_DIR, 'settings.json');
    const settings = '{"enabledPlugins":{"unrelated@marketplace":true}}\n';
    fs.writeFileSync(settingsPath, settings);
    const profileMarker = path.join(env.LOCALAPPDATA, 'profile-marker');
    fs.mkdirSync(path.dirname(profileMarker), { recursive: true });
    fs.writeFileSync(profileMarker, 'keep');
    calls.length = 0;

    const report = installPlugin(
      nextVersion,
      (command, args) => calls.push({ command, args }),
      env,
      'claude',
      'update',
    );

    assert.deepEqual(report, { status: 'updated', marketplace: 'broader-vids-npm', version: nextVersion });
    assert.deepEqual(JSON.parse(fs.readFileSync(catalogPath, 'utf8')).plugins[0].source, {
      source: 'npm', package: 'broader-vids', version: nextVersion, registry: 'https://registry.npmjs.org',
    });
    assert.deepEqual(calls.map(call => call.args), [
      ['plugin', 'marketplace', 'update', 'broader-vids-npm'],
      ['plugin', 'update', 'broader-vids@broader-vids-npm', '--scope', 'user'],
    ]);
    assert.equal(fs.readFileSync(settingsPath, 'utf8'), settings);
    assert.equal(fs.readFileSync(profileMarker, 'utf8'), 'keep');
    assert.equal(fs.existsSync(path.join(root, 'install.lock')), false);

    assert.throws(
      () => installPlugin(nextVersion, () => {}, { ...env, CLAUDE_CONFIG_DIR: path.join(directory, 'fresh') }, 'claude', 'update'),
      /managed Claude marketplace is not installed/,
    );
    assert.equal(fs.existsSync(path.join(directory, 'fresh')), false);

    assert.throws(
      () => installPlugin(nextVersion, (command, args) => {
        if (args[1] === 'update') throw new Error('fixture update failed');
      }, env, 'claude', 'update'),
      /fixture update failed/,
    );
    assert.equal(fs.existsSync(path.join(root, 'install.lock')), false);
    assert.throws(
      () => installPlugin(nextVersion, () => assert.fail('must not invoke Codex'), env, 'codex', 'update'),
      /supported only for Claude/,
    );
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-claude-update-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('Claude npm installer resolves Windows npm and native CLI entries without a shell', () => {
  const { resolveClaude } = require('../bin/plugin-install.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-claude-cli-'));
  try {
    assert.throws(() => resolveClaude({ PATH: directory }, 'win32'), /Install Claude Code CLI/);
    const script = path.join(directory, 'node_modules', '@anthropic-ai', 'claude-code', 'cli.js');
    fs.mkdirSync(path.dirname(script), { recursive: true });
    fs.writeFileSync(script, 'fixture');
    assert.deepEqual(resolveClaude({ PATH: directory }, 'win32'), [process.execPath, [script]]);
    fs.unlinkSync(script);
    const executable = path.join(directory, 'claude.exe');
    fs.writeFileSync(executable, 'fixture');
    assert.deepEqual(resolveClaude({ PATH: directory }, 'win32'), [executable, []]);
    assert.deepEqual(resolveClaude({}, 'linux'), ['claude', []]);
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-claude-cli-'));
    fs.rmSync(directory, { recursive: true });
  }
});

test('Node-handled data display consumes Claude host routing without a host CLI call', () => {
  const { spawnSync } = require('node:child_process');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'broader-vids-host-data-'));
  try {
    const launcher = path.resolve(__dirname, '../bin/broader-vids.cjs');
    const result = spawnSync(process.execPath, [launcher, 'data', 'show', '--host', 'claude'], {
      encoding: 'utf8',
      env: { ...process.env, LOCALAPPDATA: directory },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).data_directory, path.join(directory, 'broader-vids'));
    assert.deepEqual(fs.readdirSync(directory), []);
  } finally {
    assert.ok(path.basename(directory).startsWith('broader-vids-host-data-'));
    fs.rmSync(directory, { recursive: true });
  }
});
