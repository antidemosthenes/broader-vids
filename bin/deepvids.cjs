#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');

function dataRoot(args, env = process.env) {
  let override;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === '--data-dir') override = args[++index];
    else if (args[index].startsWith('--data-dir=')) override = args[index].slice('--data-dir='.length);
    else continue;
    if (!override || override.startsWith('--')) throw new Error('--data-dir requires a path');
  }
  return path.resolve(override || env.DEEPVIDS_DATA_DIR || path.join(env.LOCALAPPDATA || path.join(os.homedir(), '.local', 'share'), 'deepvids'));
}

function commandIndex(args) {
  let index = 0;
  while (index < args.length) {
    if (args[index] === '--data-dir') index += 2;
    else if (args[index].startsWith('--data-dir=')) index += 1;
    else return index;
  }
  return -1;
}

function invoke(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', shell: false });
  if (result.error) throw new Error(`Could not launch ${path.basename(command)} (${result.error.code || 'unknown error'})`);
  if (result.status !== 0) process.exit(result.status ?? (result.signal === 'SIGINT' ? 130 : 1));
}

function cacheWheel(source, root) {
  const content = fs.readFileSync(source);
  const digest = createHash('sha256').update(content).digest('hex');
  const directory = path.join(root, 'wheels', digest);
  const destination = path.join(directory, path.basename(source));
  fs.mkdirSync(directory, { recursive: true });
  if (!fs.existsSync(destination) || !fs.readFileSync(destination).equals(content)) fs.writeFileSync(destination, content);
  return destination;
}

function main(args = process.argv.slice(2)) {
  const packageRoot = path.resolve(__dirname, '..');
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
  const root = dataRoot(args);
  const runtime = path.join(root, 'runtimes', manifest.version);
  const executable = process.platform === 'win32' ? path.join(runtime, 'Scripts', 'python.exe') : path.join(runtime, 'bin', 'python');
  if (args[commandIndex(args)] === 'setup') {
    const pythonVersion = manifest.version.replace('-alpha.', 'a');
    const wheelRoot = path.join(packageRoot, 'python');
    const wheels = fs.existsSync(wheelRoot) ? fs.readdirSync(wheelRoot).filter(name => name.startsWith(`deepvids-${pythonVersion}-`) && name.endsWith('.whl')) : [];
    if (wheels.length !== 1) throw new Error('The package needs exactly one matching bundled wheel; in a checkout run: uv build --out-dir python');
    if (!fs.existsSync(executable)) invoke('uv', ['venv', '--allow-existing', '--python', '>=3.12', runtime]);
    const wheel = cacheWheel(path.join(wheelRoot, wheels[0]), root);
    invoke('uv', ['pip', 'install', '--python', executable, '--reinstall', '--no-index', '--no-deps', wheel]);
    const extras = args.includes('--asr') ? ['evidence', 'asr'] : args.includes('--evidence') ? ['evidence'] : [];
    for (const extra of extras) {
      invoke('uv', ['pip', 'install', '--python', executable, '--require-hashes', '-r', path.join(wheelRoot, `${extra}-requirements.txt`)]);
    }
    console.log(JSON.stringify({ status: 'ready', runtime, data_directory: root }, null, 2));
    return;
  }
  if (!fs.existsSync(executable)) throw new Error('Deepvids runtime is missing; run deepvids setup with the same --data-dir first.');
  invoke(executable, ['-m', 'deepvids', ...args]);
}

module.exports = { dataRoot, commandIndex, cacheWheel };
if (require.main === module) {
  try { main(); }
  catch (error) { console.error(`deepvids: ${error.message}`); process.exitCode = 2; }
}

