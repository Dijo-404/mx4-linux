#!/usr/bin/env bash
# Verify or install bundled OFL-licensed JetBrains Mono. No downloads or sudo.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${PYTHON:-python3}" - "$project_dir" "$@" <<'PY'
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(sys.argv[1])
FORMAT = '%{family}\t%{weight}\t%{slant}\t%{variable}\n'


def fail(message):
    raise SystemExit(f'JetBrains Mono: {message}')


def run(command, *args):
    try:
        return subprocess.run([command, *args], check=True, capture_output=True,
                              text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f'{command} failed: {exc}')


def records(output):
    result = []
    for line in output.splitlines():
        fields = line.split('\t')
        if len(fields) == 4:
            result.append(fields)
    return result


def is_jetbrains(record):
    # Match an actual family alias, never a filename or fontconfig fallback.
    return 'jetbrains mono' in [family.strip().casefold() for family in record[0].split(',')]


def supports(rows):
    regular = bold = False
    for family, weight, slant, variable in filter(is_jetbrains, rows):
        slants = re.findall(r'-?\d+(?:\.\d+)?', slant)
        if not slants or any(float(value) != 0 for value in slants):
            continue
        weights = [float(value) for value in re.findall(r'-?\d+(?:\.\d+)?', weight)]
        if not weights:
            continue
        if variable.casefold() == 'true' and len(weights) > 1:
            regular |= min(weights) <= 80 <= max(weights)
            bold |= min(weights) <= 200 <= max(weights)
        else:
            regular |= 80 in weights
            bold |= 200 in weights
    return regular and bold


def installed():
    return records(run('fc-list', '--format', FORMAT))


args = sys.argv[2:]
if args == ['--help']:
    print('Usage: setup-font.sh [--check]\n'
          'Checks for JetBrains Mono upright regular and bold. Without --check, missing fonts\n'
          'are installed from the bundled SIL Open Font License files in the user font directory.\n'
          'No download, account, purchase or sudo is required.')
    raise SystemExit(0)
if args not in ([], ['--check']):
    fail('use --check or no arguments; see --help.')
if not shutil.which('fc-list'):
    fail('install the fontconfig package first (fc-list, fc-scan and fc-cache).')
existing = installed()
if supports(existing):
    print('JetBrains Mono prerequisite satisfied: upright regular and bold are available.')
    raise SystemExit(0)
if args == ['--check']:
    fail('upright regular and bold are required. Run scripts/setup-font.sh to install the bundled '
         'OFL-licensed fonts. No fonts were installed by this check.')
for command in ('fc-scan', 'fc-cache'):
    if not shutil.which(command):
        fail(f'install the fontconfig package first ({command} is missing).')
source = PROJECT / 'src' / 'mx4_linux' / 'assets' / 'fonts'
candidates = [source / 'JetBrainsMono-Regular.ttf', source / 'JetBrainsMono-Bold.ttf']
license_file = source / 'OFL.txt'
if not all(path.is_file() for path in [*candidates, license_file]):
    fail('bundled font files or OFL.txt are missing; reinstall the package. Nothing changed.')

validated = []
combined = list(existing)
for path in candidates:
    rows = records(run('fc-scan', '--format', FORMAT, str(path)))
    if not rows or not all(is_jetbrains(row) for row in rows):
        fail(f'{path.name} does not declare the JetBrains Mono family. Nothing changed.')
    combined.extend(rows)
    validated.append((path, path.read_bytes()))
if not supports(combined):
    fail('the bundled fonts do not provide upright regular and bold. '
         'Reinstall the package; italic-only or incomplete fonts cannot satisfy the prerequisite. Nothing changed.')

data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
if not data_home.is_absolute():
    fail('XDG_DATA_HOME must be an absolute user data directory. Nothing changed.')
destination = data_home / 'fonts' / 'mx4-linux-jetbrains-mono'
if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
    fail(f'{destination} must be a normal directory, not a link or file. Nothing changed.')
destination.mkdir(parents=True, exist_ok=True)
validated.append((license_file, license_file.read_bytes()))
for original, data in validated:
    # Content-derived names avoid overwriting other font files or trusting a
    # supplied basename as a destination. Existing unrelated fonts are untouched.
    name = 'OFL.txt' if original == license_file else f'jetbrains-mono-{sha256(data).hexdigest()}{original.suffix.lower()}'
    descriptor, temporary = tempfile.mkstemp(prefix='.install-', dir=destination)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
# Refresh the parent too: the prerequisite probe may have cached it before this
# subdirectory existed. Fontconfig can otherwise retain an empty directory list
# when the probe and installation share the same filesystem timestamp interval.
run('fc-cache', '--force', str(destination.parent))
if not supports(installed()):
    fail(f'files were copied to {destination}, but Fontconfig does not yet expose regular and bold. '
         'Check your fontconfig search paths and rerun --check.')
print(f'Installed JetBrains Mono regular, bold and OFL.txt in {destination}.')
print('JetBrains Mono prerequisite satisfied. Restart MX4 Linux to use the font.')
PY
