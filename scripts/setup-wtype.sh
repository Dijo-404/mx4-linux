#!/usr/bin/env bash
# Build the optional native Wayland keyboard helper into this project's venv.
# Source: https://github.com/atx/wtype (MIT), release v0.4.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
wtype_version="0.4"
wtype_commit="d71be3a7b3f93b534a2823fd68cabd7ac2a02359"
wtype_sha256="0e16ae99778082fa4c85a52b3a9222291b776c79dffb31085c275c2b5da11360"
tools_dir="$project_dir/.tools"
archive="$tools_dir/wtype-$wtype_commit.tar.gz"
source_dir="$tools_dir/wtype-$wtype_commit"
build_dir="$tools_dir/wtype-build-$wtype_commit"
prefix_dir="$project_dir/.venv"
checksum_file="$tools_dir/wtype-$wtype_commit.installed.sha256"

if [[ -x "$prefix_dir/bin/wtype" && -f "$checksum_file" ]] && sha256sum --check --status "$checksum_file"; then
    printf 'Native Wayland keyboard helper wtype %s is ready: %s/bin/wtype\n' "$wtype_version" "$prefix_dir"
    exit 0
fi

for tool in curl sha256sum tar meson ninja pkg-config cc wayland-scanner install; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Cannot build wtype: install %s, or install your distribution\x27s wtype package.\n' "$tool" >&2
        exit 1
    fi
done
if ! pkg-config --exists wayland-client wayland-cursor xkbcommon; then
    printf 'Cannot build wtype: install Wayland and xkbcommon development packages, or your distribution\x27s wtype package.\n' >&2
    exit 1
fi

mkdir -p "$tools_dir"
download_tmp="$archive.part"
trap 'rm -f -- "$download_tmp"' EXIT
if [[ ! -f "$archive" ]]; then
    curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 60 \
        "https://codeload.github.com/atx/wtype/tar.gz/$wtype_commit" -o "$download_tmp"
    mv -- "$download_tmp" "$archive"
fi
if ! printf '%s  %s\n' "$wtype_sha256" "$archive" | sha256sum --check --status; then
    printf 'wtype source checksum failed. Remove %s and rerun this script.\n' "$archive" >&2
    exit 1
fi

# Re-extract verified source; these directories contain only generated builds.
rm -rf -- "$source_dir" "$build_dir"
tar --extract --gzip --file "$archive" --directory "$tools_dir" --no-same-owner
meson setup "$build_dir" "$source_dir" --buildtype=release --prefix "$prefix_dir"
meson compile -C "$build_dir"

# v0.4 has no --help or --version option. With no arguments it prints Usage
# and exits before connecting to Wayland. Never send a test keystroke here.
if usage_output="$(WAYLAND_DISPLAY=mx4-wtype-build-check-no-display "$build_dir/wtype" 2>&1)"; then
    printf 'Unexpected wtype usage-check result.\n' >&2
    exit 1
fi
if [[ "$usage_output" != Usage:* ]]; then
    printf 'wtype binary verification failed: %s\n' "$usage_output" >&2
    exit 1
fi

install -Dm755 "$build_dir/wtype" "$prefix_dir/bin/wtype"
install -Dm644 "$source_dir/LICENSE" "$prefix_dir/share/licenses/wtype/LICENSE"
install -Dm644 "$source_dir/man/wtype.1" "$prefix_dir/share/man/man1/wtype.1"
sha256sum "$prefix_dir/bin/wtype" > "$checksum_file"
printf 'Installed native Wayland keyboard helper wtype %s: %s/bin/wtype\n' "$wtype_version" "$prefix_dir"
