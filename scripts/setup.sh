#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
python_bin="${PYTHON:-python3}"
"$python_bin" - <<'PY'
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw
except (ImportError, ValueError) as exc:
    raise SystemExit(f'Install Python GObject, GTK 4 and Libadwaita first (see README): {exc}')
PY
if [[ ! -x .venv/bin/python ]]; then
    "$python_bin" -m venv --system-site-packages .venv
fi
if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -e '.[test]'
else
    .venv/bin/python -m pip install -e '.[test]'
fi
if [[ -n "${WAYLAND_DISPLAY:-}" ]] && ! command -v wtype >/dev/null 2>&1; then
    if ! "$project_dir/scripts/setup-wtype.sh"; then
        printf 'Optional Wayland keyboard helper was not built. Install wtype or use scripts/install-input-access.sh for keyboard shortcuts.\n' >&2
    fi
fi
printf '\nReady. Launch with: %s/.venv/bin/mx4-linux\n' "$project_dir"
printf 'Check hardware access with: %s/.venv/bin/mx4-linux doctor\n' "$project_dir"
