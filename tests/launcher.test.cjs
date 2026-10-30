'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
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
    const result = spawnSync(process.execPath, [path.join(fakePlugin, 'bin', 'broader-vids.cjs'), '--data-dir', directory, 'setup'], { encoding: 'utf8', env });
    assert.match(result.stderr, /Could not launch uv/);
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
