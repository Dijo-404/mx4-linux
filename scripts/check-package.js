#!/usr/bin/env node
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const metadata = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const pythonVersion = readFileSync(join(root, 'src', 'mx4_linux', '__init__.py'), 'utf8').match(/__version__ = "([^"]+)"/)[1];
const projectVersion = readFileSync(join(root, 'pyproject.toml'), 'utf8').match(/^version = "([^"]+)"/m)[1];
assert.equal(metadata.version, pythonVersion, 'npm and Python versions differ');
assert.equal(metadata.version, projectVersion, 'npm and pyproject versions differ');
assert.ok(!metadata.scripts.install && !metadata.scripts.postinstall && !metadata.scripts.preinstall, 'npm installation must not modify Python or the host');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, encoding: 'utf8', ...options });
  if (result.error) throw result.error;
  assert.equal(result.status, 0, `${command} ${args.join(' ')}\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}

const directory = mkdtempSync(join(tmpdir(), 'mx4-linux-package-'));
try {
  const packed = JSON.parse(run('npm', ['pack', '--json', '--pack-destination', directory]));
  const [archive] = Array.isArray(packed) ? packed : Object.values(packed);
  const files = new Set(archive.files.map(file => file.path));
  for (const path of ['bin/mx4-linux.js', 'pyproject.toml', 'requirements.txt', 'LICENSE',
    'src/mx4_linux/__main__.py', 'src/mx4_linux/gui.py', 'src/mx4_linux/device_worker.py',
    'src/mx4_linux/icons/io.github.mx4linux.Control.svg', 'scripts/install-udev-rule.sh',
    'scripts/lib-device-access.sh', 'data/70-mx4-linux.rules', 'docs/install.md']) {
    assert.ok(files.has(path), `Missing package file: ${path}`);
  }
  for (const path of files) {
    assert.ok(!/(^|\/)(__pycache__|\.venv|\.git|node_modules|evidence)(\/|$)|\.(pyc|zip|tgz)$/.test(path), `Unexpected package file: ${path}`);
  }
  if (process.argv.includes('--smoke')) {
    const prefix = join(directory, 'installed');
    run('npm', ['install', '--global', '--prefix', prefix, '--ignore-scripts', '--no-audit', '--no-fund', join(directory, archive.filename)]);
    const executable = join(prefix, 'bin', 'mx4-linux');
    assert.equal(run(executable, ['--version']).trim(), `mx4-linux ${metadata.version}`);
    const env = { ...process.env, XDG_DATA_HOME: join(directory, 'data'), XDG_CONFIG_HOME: join(directory, 'config') };
    if (process.argv.includes('--native')) {
      delete env.MX4_LINUX_PYTHON;
      run(executable, ['setup'], { env });
    } else {
      env.MX4_LINUX_PYTHON = process.env.PYTHON || 'python3';
    }
    assert.match(run(executable, ['--help'], { env }), /MX Master 4 controls for Linux/);
    const doctor = JSON.parse(run(executable, ['doctor', '--json'], { env }));
    assert.ok(Array.isArray(doctor.logitech_nodes));
    if (process.argv.includes('--native')) {
      const runtimePython = join(env.XDG_DATA_HOME, 'mx4-linux', 'runtime', metadata.version, 'bin', 'python');
      console.log(run(runtimePython, [join(root, 'scripts', 'gtk-smoke.py')], { env }).trim());
    }
  }
  console.log(`Verified npm package ${archive.filename}: ${files.size} files${process.argv.includes('--smoke') ? ', installed CLI smoke passed' : ''}.`);
} finally {
  rmSync(directory, { recursive: true, force: true });
}
