import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { setTimeout } from 'node:timers/promises';

const root = dirname(dirname(dirname(fileURLToPath(import.meta.url))));

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), 'mx4 launcher '));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const packageRoot = join(directory, 'package');
  mkdirSync(join(packageRoot, 'bin'), { recursive: true });
  for (const path of ['bin/mx4-linux.js', 'package.json', 'pyproject.toml', 'README.md', 'LICENSE']) copyFileSync(join(root, path), join(packageRoot, path));
  mkdirSync(join(packageRoot, 'src', 'mx4_linux'), { recursive: true });
  copyFileSync(join(root, 'src', 'mx4_linux', '__init__.py'), join(packageRoot, 'src', 'mx4_linux', '__init__.py'));
  const log = join(directory, 'python.jsonl');
  const python = join(directory, 'system python');
  writeFileSync(python, `#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.TEST_LOG, JSON.stringify({args, source: process.env.PYTHONPATH}) + '\\n');
if (args[0] === '-m' && args[1] === 'venv') {
  const bin = path.join(args.at(-1), 'bin');
  fs.mkdirSync(bin, {recursive: true});
  fs.copyFileSync(__filename, path.join(bin, 'python'));
  fs.chmodSync(path.join(bin, 'python'), 0o755);
}
if (process.env.FAIL_PIP && args[1] === 'pip') process.exit(9);
if (process.env.TEST_WAIT && args[1] === 'mx4_linux') {
  fs.writeFileSync(process.env.TEST_WAIT, 'ready');
  process.on('SIGTERM', () => { fs.writeFileSync(process.env.TEST_WAIT, 'stopped'); process.exit(0); });
  setInterval(() => {}, 1000);
} else process.exit(Number(process.env.TEST_EXIT || 0));
`);
  chmodSync(python, 0o755);
  const env = { ...process.env, XDG_DATA_HOME: join(directory, 'data'), TEST_LOG: log };
  delete env.MX4_LINUX_PYTHON;
  delete env.PYTHON;
  const launcher = join(packageRoot, 'bin', 'mx4-linux.js');
  const version = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')).version;
  return {
    directory, packageRoot, python, launcher, env, version,
    ready: join(env.XDG_DATA_HOME, 'mx4-linux', 'runtime', version, '.ready'),
    logs: () => readFileSync(log, 'utf8').trim().split('\n').map(line => JSON.parse(line)),
    run: (args = [], extra = {}) => spawnSync(process.execPath, [launcher, ...args], { env: { ...env, ...extra }, encoding: 'utf8' }),
  };
}

test('version and setup help work before installing Python dependencies', t => {
  const f = fixture(t);
  assert.equal(f.run(['--version']).stdout, `mx4-linux ${f.version}\n`);
  assert.match(f.run(['--help']).stdout, /mx4-linux setup/);
  const result = f.run();
  assert.equal(result.status, 1);
  assert.match(result.stderr, /setup first/);
  assert.ok(!existsSync(f.ready));
});

test('forwards argument boundaries, bundled source, and exit codes without a shell', t => {
  const f = fixture(t);
  const args = ['actions', 'run', 'literal $(touch sentinel); id with spaces'];
  const result = f.run(args, { MX4_LINUX_PYTHON: f.python, TEST_EXIT: '7', PYTHONPATH: '/existing/path' });
  assert.equal(result.status, 7);
  assert.deepEqual(f.logs()[0].args, ['-m', 'mx4_linux', ...args]);
  assert.equal(f.logs()[0].source, `${join(f.packageRoot, 'src')}:/existing/path`);
});

test('setup creates a per-user environment and only marks successful installation ready', t => {
  const f = fixture(t);
  const setup = f.run(['setup'], { PYTHON: f.python });
  assert.equal(setup.status, 0, setup.stderr);
  assert.ok(existsSync(f.ready));
  assert.deepEqual(f.logs()[1].args.slice(0, 3), ['-m', 'venv', '--system-site-packages']);
  assert.deepEqual(f.logs()[2].args.slice(0, 5), ['-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade']);
  assert.notEqual(f.logs()[2].args.at(-1), f.packageRoot);
  assert.ok(!existsSync(f.logs()[2].args.at(-1)), 'temporary build source should be removed');
  assert.equal(f.run(['doctor']).status, 0);
  assert.deepEqual(f.logs().at(-1).args, ['-m', 'mx4_linux', 'doctor']);
  assert.equal(f.run(['setup'], { PYTHON: f.python, FAIL_PIP: '1' }).status, 1);
  assert.ok(!existsSync(f.ready));
  assert.equal(f.run(['doctor']).status, 1);
});

test('setup rejects unsupported arguments and reports a missing Python executable', t => {
  const f = fixture(t);
  assert.match(f.run(['setup', '--unknown']).stderr, /Usage:/);
  const result = f.run(['doctor'], { MX4_LINUX_PYTHON: join(f.directory, 'absent') });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Cannot start Python/);
});

test('termination reaches Python so the listener can clean up', { timeout: 5000 }, async t => {
  const f = fixture(t);
  const marker = join(f.directory, 'state');
  const child = spawn(process.execPath, [f.launcher], { env: { ...f.env, MX4_LINUX_PYTHON: f.python, TEST_WAIT: marker }, stdio: 'pipe' });
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL'); });
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  for (let attempt = 0; !existsSync(marker) && attempt < 100; attempt++) await setTimeout(20);
  assert.ok(existsSync(marker), 'Python child did not start');
  child.kill('SIGTERM');
  assert.deepEqual(await exited, { code: 0, signal: null });
  assert.equal(readFileSync(marker, 'utf8'), 'stopped');
});
