'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { dataRoot, commandIndex, cacheWheel } = require('../bin/deepvids.cjs');

test('explicit state location survives spaces and shell metacharacters', () => {
  const directory = 'example space & literal';
  assert.equal(dataRoot(['--data-dir', directory, 'doctor'], {}), path.resolve(directory));
  assert.equal(dataRoot([`--data-dir=${directory}`, 'doctor'], {}), path.resolve(directory));
});

test('environment state selection is independent of working directory', () => {
  const directory = path.resolve('example state');
  assert.equal(dataRoot([], { DEEPVIDS_DATA_DIR: directory }), directory);
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
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'deepvids-wheel-'));
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
    assert.ok(path.basename(directory).startsWith('deepvids-wheel-'));
    fs.rmSync(directory, { recursive: true });
  }
});
