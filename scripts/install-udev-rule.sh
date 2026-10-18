#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dry_run=0
case "${1:-}" in
    --dry-run) dry_run=1; shift ;;
    '') ;;
    *) printf 'Usage: %s [--dry-run]\n' "$0" >&2; exit 2 ;;
esac
if (( $# != 0 )); then
    printf 'Usage: %s [--dry-run]\n' "$0" >&2
    exit 2
fi
if (( ! dry_run && EUID != 0 )); then
    exec sudo -- "$project_dir/scripts/install-udev-rule.sh"
fi
source "$project_dir/scripts/lib-device-access.sh"
run_command() {
    if (( dry_run )); then
        printf '%q ' "$@"
        printf '\n'
    else
        "$@"
    fi
}
run_command install -m 0644 "$project_dir/data/70-mx4-linux.rules" /etc/udev/rules.d/70-mx4-linux.rules
run_command udevadm control --reload-rules
mapfile -d '' -t device_paths < <(mx4_hidraw_paths)
if (( ${#device_paths[@]} )); then
    run_command udevadm trigger --action=change --settle "${device_paths[@]}"
fi
if (( dry_run )); then
    printf 'Dry run: %d matching connected interfaces; nothing changed.\n' "${#device_paths[@]}"
else
    printf 'MX4 rule installed and applied to %d connected interfaces. Refresh the app.\n' "${#device_paths[@]}"
    if (( ${#device_paths[@]} == 0 )); then
        printf 'Connect the receiver or Bluetooth mouse to apply device access.\n'
    fi
fi
