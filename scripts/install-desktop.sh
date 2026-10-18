#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$project_dir/.venv/bin/mx4-linux" ]]; then
    printf 'Run ./scripts/setup.sh first.\n' >&2
    exit 1
fi
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$data_dir"
# Desktop Exec values use their own quoting rules, rather than shell quoting.
"$project_dir/.venv/bin/python" - "$project_dir" "$data_dir" <<'PY'
from pathlib import Path
import sys
root, dest = map(Path, sys.argv[1:])
executable = str(root / '.venv/bin/mx4-linux')
for old, new in [('\\', '\\\\'), ('"', '\\"'), ('`', '\\`'), ('$', '\\$'), ('%', '%%')]:
    executable = executable.replace(old, new)
text = f'''[Desktop Entry]
Type=Application
Name=MX4 Linux
Comment=Control MX Master 4 scrolling, buttons and haptics
Exec="{executable}"
Icon=input-mouse
Terminal=false
Categories=Settings;HardwareSettings;
StartupNotify=true
'''
(dest / 'io.github.mx4linux.Control.desktop').write_text(text)
print('Installed desktop launcher. Keep the project directory at:', root)
PY
