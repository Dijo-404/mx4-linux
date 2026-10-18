#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dry_run=0
case "${1:-}" in
    --dry-run) dry_run=1; shift ;;
    '') ;;
    *) printf 'Usage: %s [--dry-run]\n' "$0" >&2; exit 2 ;;
esac
if (( $# )); then
    printf 'Usage: %s [--dry-run]\n' "$0" >&2
    exit 2
fi
if (( ! dry_run && EUID != 0 )); then
    exec sudo -- "$project_dir/scripts/install-input-access.sh"
fi
run_command() {
    if (( dry_run )); then
        printf '%q ' "$@"
        printf '\n'
    else
        "$@"
    fi
}
run_command modprobe uinput
run_command install -m 0644 "$project_dir/data/71-mx4-input.rules" /etc/udev/rules.d/71-mx4-input.rules
run_command udevadm control --reload-rules
run_command udevadm trigger --action=change --settle /sys/class/misc/uinput
if (( dry_run )); then
    printf 'Dry run: nothing changed.\n'
else
    printf 'Smart Actions virtual-keyboard access applied. Shortcuts are ready for the active local desktop user.\n'
fi
