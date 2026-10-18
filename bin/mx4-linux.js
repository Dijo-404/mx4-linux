#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, constants, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const { version } = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const dataRoot = process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share');
const runtime = join(dataRoot, 'mx4-linux', 'runtime', version);
const runtimePython = join(runtime, 'bin', 'python');
const ready = join(runtime, '.ready');
const args = process.argv.slice(2);

function run(command, arguments_) {
  const result = spawnSync(command, arguments_, { stdio: 'inherit' });
  if (result.error) throw new Error(`Cannot run ${command}: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${command} failed. Check the output above and rerun mx4-linux setup.`);
}

function setup() {
  if (args.length !== 1) throw new Error('Usage: mx4-linux setup (select the system interpreter with PYTHON=/path/to/python3).');
  const python = process.env.PYTHON || 'python3';
  run(python, ['-c', `
import sys
if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 or later is required.')
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    gi.require_foreign('cairo')
    from gi.repository import Gtk, Adw
    if Gtk.get_major_version() < 4 or Gtk.get_minor_version() < 12 or Adw.get_major_version() < 1 or (Adw.get_major_version() == 1 and Adw.get_minor_version() < 4):
        raise ValueError('GTK 4.12+ and Libadwaita 1.4+ are required')
except (ImportError, ValueError) as exc:
    raise SystemExit(f'Install system Python GObject with Cairo support, GTK 4.12+ and Libadwaita 1.4+ first. See https://github.com/Dijo-404/mx4-linux/blob/main/docs/install.md: {exc}')
`]);
  mkdirSync(dirname(runtime), { recursive: true });
  rmSync(ready, { force: true });
  run(python, ['-m', 'venv', '--system-site-packages', runtime]);
  // setuptools writes metadata while building; npm may install into a read-only location.
  const staging = mkdtempSync(join(tmpdir(), 'mx4-linux-setup-'));
  try {
    for (const file of ['pyproject.toml', 'README.md', 'LICENSE']) copyFileSync(join(root, file), join(staging, file));
    cpSync(join(root, 'src'), join(staging, 'src'), {
      recursive: true,
      filter: source => !source.endsWith('__pycache__') && !source.endsWith('.egg-info'),
    });
    run(runtimePython, ['-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', staging]);
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
  writeFileSync(ready, `${version}\n`, { mode: 0o600 });
  console.log(`Ready. Run mx4-linux, or mx4-linux gui --demo. Runtime: ${runtime}`);
}

function launch() {
  const python = process.env.MX4_LINUX_PYTHON
    || (existsSync(ready) && existsSync(runtimePython) ? runtimePython : null)
    || (existsSync(join(root, '.venv', 'bin', 'python')) ? join(root, '.venv', 'bin', 'python') : null);
  if (!python) {
    if (args[0] === '--help' || args[0] === '-h') {
      console.log('MX4 Linux — native Linux mouse controls\n\n  mx4-linux setup          Install the per-user Python runtime\n  mx4-linux [gui --demo]   Open the control panel\n  mx4-linux doctor        Check device access\n\nInstall system GTK 4.12+, Libadwaita 1.4+ and Python GObject first.\nAfter setup, mx4-linux --help lists every application command.');
      return;
    }
    throw new Error('Run mx4-linux setup first (or npm run setup in a checkout). See docs/install.md for system dependencies.');
  }
  const source = join(root, 'src');
  const env = { ...process.env, PYTHONPATH: [source, process.env.PYTHONPATH].filter(Boolean).join(':') };
  const child = spawn(python, ['-m', 'mx4_linux', ...args], { stdio: 'inherit', env });
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
  child.on('error', error => {
    console.error(`mx4-linux: Cannot start Python: ${error.message}. Rerun mx4-linux setup after Python upgrades.`);
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    process.exitCode = code ?? (128 + (constants.signals[signal] || 1));
  });
}

try {
  if (process.platform !== 'linux') throw new Error('MX4 Linux requires Linux.');
  if (args.length === 1 && args[0] === '--version') console.log(`mx4-linux ${version}`);
  else if (args[0] === 'setup') setup();
  else launch();
} catch (error) {
  console.error(`mx4-linux: ${error.message}`);
  process.exitCode = 1;
}
