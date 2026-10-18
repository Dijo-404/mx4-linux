# Installation

MX4 Linux is installed from a source checkout. Version 0.2.0 is unreleased; no distribution package or published binary is documented here. The validated environment is Arch Linux with Python 3.14, GTK 4.24, Libadwaita 1.10 and Niri / Wayland. Other distributions need equivalent packages and remain unverified.

## System dependencies

The GUI requires Python 3.11+, Python GObject, GTK 4.12+ and Libadwaita 1.4+. Install these through your distribution so the GObject typelib and shared libraries match. Typical base packages are:

```bash
# Arch Linux
sudo pacman -S python python-gobject gtk4 libadwaita python-pip

# Debian / Ubuntu; check that your release meets the GTK and Libadwaita minimums
sudo apt install python3-venv python3-pip python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

For the Actions Ring overlay, install the system `gtk4-layer-shell` library and its `Gtk4LayerShell-1.0` typelib. Package names vary by distribution. If the library or compositor support is unavailable, the ring can fall back to a normal window.

Desktop integration targets Niri on Wayland. Native shortcuts and Unicode text use `wtype`. A distribution-provided wtype works; otherwise setup attempts to build the pinned helper locally when `WAYLAND_DISPLAY` is set and no system wtype is found.

The local helper build needs `curl`, `sha256sum`, `tar`, Meson, Ninja, `pkg-config`, a C compiler, `wayland-scanner`, and the Wayland client/cursor and xkbcommon development files. Missing helper build tools do not prevent the Python application from installing; setup prints a diagnostic. No sudo is used for this build.

Without wtype, shortcuts can use python-evdev and `/dev/uinput`. Text actions instead call `wl-copy` from `wl-clipboard` and paste with Ctrl+V, replacing the clipboard contents. Niri actions still require Niri.

## Source setup

```bash
git clone https://github.com/Dijo-404/mx4-linux.git
cd mx4-linux
./scripts/setup.sh
.venv/bin/mx4-linux --version
.venv/bin/mx4-linux doctor --json
```

`setup.sh` creates `.venv` with `--system-site-packages` so the application can use the distribution's GObject bindings. It installs the editable application, test dependencies and Solaar 1.1.20. If `uv` is installed, setup uses it; otherwise it uses the environment's pip. To select a different compatible interpreter, run `PYTHON=python3.11 ./scripts/setup.sh` with that interpreter's matching system bindings installed.

The optional `scripts/setup-wtype.sh` downloads commit `d71be3a7b3f93b534a2823fd68cabd7ac2a02359` from wtype 0.4, verifies its source checksum and installs the built executable, MIT license and man page inside `.venv`. Download/source/build files stay under `.tools`. An existing local helper is reused only when its stored executable checksum matches.

To build the helper separately:

```bash
./scripts/setup-wtype.sh
```

## Device access

The application needs read/write access to the relevant `/dev/hidraw*` interfaces. If your installed Solaar rules already provide it, no additional rule is needed. Otherwise:

```bash
./scripts/install-udev-rule.sh --dry-run
./scripts/install-udev-rule.sh
```

The dry run does not invoke sudo or change files. The real installation uses sudo to copy `data/70-mx4-linux.rules` to `/etc/udev/rules.d/70-mx4-linux.rules`, reload udev rules and trigger settled change events only for matching connected hidraw interfaces.

The rule grants the active local desktop user access through `uaccess`. It matches Logitech Bolt USB `046d:c548` and MX Master 4 Bluetooth regular/business models `b042` / `b048`. It does not grant world access or `/dev/uinput` access. Bluetooth matching is implemented, but a live Bluetooth connection has not been validated.

Wake the mouse and click **Check again** or **Refresh**. The GUI also monitors attachment and permission changes. Run the application as your regular user.

## Optional input fallback

Native wtype input on Niri normally needs no extra privileged setup. For shortcuts that use the uinput fallback:

```bash
./scripts/install-input-access.sh --dry-run
./scripts/install-input-access.sh
```

The installer loads `uinput`, installs `/etc/udev/rules.d/71-mx4-input.rules`, reloads the rules and triggers `/sys/class/misc/uinput`. The rule grants the active local desktop user access to this virtual-input interface. It is separate from mouse access and affects the shared `/dev/uinput` interface, not only MX4 devices.

## Desktop launcher

```bash
./scripts/install-desktop.sh
```

This writes `$XDG_DATA_HOME/applications/io.github.mx4linux.Control.desktop`, defaulting to `~/.local/share/applications/`, and installs the application SVG under `$XDG_DATA_HOME/icons/hicolor/scalable/apps/`. It refreshes an existing icon cache when `gtk-update-icon-cache` is available. The launcher points directly to this checkout's `.venv/bin/mx4-linux`. Keep the checkout in place, or rerun the script after moving it. The script overwrites its named launcher and icon without backups. It does not add login autostart.

## Updating

Close MX4 Linux before updating:

```bash
git pull --ff-only
./scripts/setup.sh
```

On a rolling distribution, recreate `.venv` after a Python version upgrade so it uses the current interpreter and GObject bindings:

```bash
rm -rf -- .venv
./scripts/setup.sh
```

Profiles and desktop configuration are stored separately and survive environment recreation. If you change rule files, rerun the corresponding access installer to replace the installed copy.

## Uninstalling

Close the application first so an active listener can restore the mouse's previous reporting state. From the project directory, remove generated environments and the optional launcher:

```bash
rm -rf -- .venv .tools
rm -f -- "${XDG_DATA_HOME:-$HOME/.local/share}/applications/io.github.mx4linux.Control.desktop"
rm -f -- "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/io.github.mx4linux.Control.svg"
```

To also discard saved routines and hardware profiles:

```bash
rm -rf -- "${XDG_CONFIG_HOME:-$HOME/.config}/mx4-linux"
```

If you installed the project-specific system rules, remove those copies and reload udev:

```bash
sudo rm -f -- /etc/udev/rules.d/70-mx4-linux.rules /etc/udev/rules.d/71-mx4-input.rules
sudo udevadm control --reload-rules
```

Reloading rules does not revoke ACLs already applied to connected devices. Reconnect the receiver/mouse or end the local login session to let access be reassessed. The optional installer does not configure persistent module loading; leaving the already loaded `uinput` module alone avoids disrupting other applications.

These removal steps do not undo hardware settings already written to the mouse or stop applications launched by routines. There is no complete original-settings backup or factory-reset command. Remove the source checkout separately when you no longer need it.

## Troubleshooting

- **Missing GTK / Adwaita imports:** install the system packages and recreate `.venv` with that system Python. A pip-only PyGObject installation is not the documented setup path.
- **Mouse absent:** wake it, inspect `doctor --json`, verify access, and refresh. A receiver's presence alone does not establish an online MX Master 4.
- **Intermittent discovery or writes:** close other mouse configuration tools, including Solaar's UI, to avoid competing settings. Device operations can wait up to 90 seconds before reporting a timeout.
- **Helper build failure:** install system wtype, or install the missing build tools printed by `setup-wtype.sh` and rerun it.
- **Settings reset after reconnect:** apply a saved profile manually. There is no automatic hardware-profile reapplication.

See [recorded validation](validation.md) for tested behavior and remaining physical checks.
