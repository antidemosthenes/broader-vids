'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { dataRoot, commandIndex } = require('../bin/deepvids.cjs');

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
