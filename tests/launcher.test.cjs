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
