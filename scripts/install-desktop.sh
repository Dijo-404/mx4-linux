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
import shutil
import sys
root, dest = map(Path, sys.argv[1:])
icon_name = 'io.github.mx4linux.Control'
icon_dir = dest.parent / 'icons/hicolor/scalable/apps'
icon_dir.mkdir(parents=True, exist_ok=True)
shutil.copyfile(root / 'src/mx4_linux/icons' / f'{icon_name}.svg', icon_dir / f'{icon_name}.svg')
executable = str(root / '.venv/bin/mx4-linux')
for old, new in [('\\', '\\\\'), ('"', '\\"'), ('`', '\\`'), ('$', '\\$'), ('%', '%%')]:
    executable = executable.replace(old, new)
text = f'''[Desktop Entry]
Type=Application
Name=MX4 Linux
Comment=Control MX Master 4 scrolling, buttons and haptics
Exec="{executable}"
Icon={icon_name}
Terminal=false
Categories=Settings;HardwareSettings;
StartupNotify=true
'''
(dest / 'io.github.mx4linux.Control.desktop').write_text(text)
print('Installed desktop launcher. Keep the project directory at:', root)
PY
icon_theme_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
touch -- "$icon_theme_dir"
if [[ -f "$icon_theme_dir/icon-theme.cache" ]] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$icon_theme_dir"
fi
