#!/usr/bin/env bash
# Optional local helper; build checks never connect or create input devices.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_dir/native/pointer"
build_dir="$project_dir/.tools/pointer-build"
for tool in cc pkg-config wayland-scanner sha256sum install; do
    command -v "$tool" >/dev/null || { printf 'Install %s to build mx4-pointer.\n' "$tool" >&2; exit 1; }
done
pkg-config --exists wayland-client || { printf 'Install Wayland development headers and libraries.\n' >&2; exit 1; }
printf '%s  %s\n' 3ff6d540be0bc5228195bf072bde42117ea17945a5c2061add5d3cf97d6bb524 "$source_dir/wlr-virtual-pointer-unstable-v1.xml" | sha256sum --check --status
mkdir -p "$build_dir"
wayland-scanner client-header "$source_dir/wlr-virtual-pointer-unstable-v1.xml" "$build_dir/virtual-pointer-client.h"
wayland-scanner private-code "$source_dir/wlr-virtual-pointer-unstable-v1.xml" "$build_dir/virtual-pointer-protocol.c"
read -r -a wayland_flags <<< "$(pkg-config --cflags --libs wayland-client)"
cc -std=c11 -O2 -Wall -Wextra -Werror -pedantic -I "$build_dir" -I "$source_dir" \
    "$source_dir/client.c" "$build_dir/virtual-pointer-protocol.c" "${wayland_flags[@]}" -o "$build_dir/mx4-pointer"
WAYLAND_DISPLAY=mx4-build-check-no-display "$build_dir/mx4-pointer" --validate click left 2
if WAYLAND_DISPLAY=mx4-build-check-no-display "$build_dir/mx4-pointer" --validate move nan 0; then
    printf 'Invalid parser result.\n' >&2; exit 1
fi
install -Dm755 "$build_dir/mx4-pointer" "$project_dir/.venv/bin/mx4-pointer"
install -Dm644 "$source_dir/wlr-virtual-pointer-unstable-v1.xml" "$project_dir/.venv/share/licenses/mx4-pointer/protocol.xml"
printf 'Installed optional current-cursor helper: %s/.venv/bin/mx4-pointer\n' "$project_dir"
