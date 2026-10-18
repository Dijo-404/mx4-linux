# Validation · 2026-10-18

Environment: Arch Linux, Python 3.14.8, GTK 4.24, Libadwaita 1.10, Niri / Wayland. Solaar 1.1.20 is installed in the project's virtual environment.

- `./scripts/setup.sh`: passed, including editable package installation.
- `.venv/bin/python -m pytest -q`: **45 passed**, including 30 successful unittest subtests. Tests use simulated hardware, with actual Solaar force-sensing adapters where applicable.
- Python compilation and shell syntax checks: passed.
- `udevadm verify data/70-mx4-linux.rules`: passed.
- Desktop launcher installation from a different working directory: passed.
- Native GTK preview: launched and visually inspected. Programmatic GTK checks confirmed that range Apply changes the simulated mouse, a failed toggle write restores the confirmed value and displays the error, and profile Save excludes haptic playback.
- Native application: launched and visually inspected the permission guidance. The real application remains available in the desktop launcher.
- `mx4-linux doctor --json`: detected Logitech `046d:c548` receiver interfaces with no read/write access.
- `mx4-linux devices --json`: returned no accessible mouse and an explicit permission diagnostic for `/dev/hidraw4`.
- `sudo -n true`: password required. No udev rule was installed or device permissions changed.

No physical mouse settings were written. Physical DPI changes, haptic strength/playback, panel force, wheel behavior, button mappings, Bolt wake/reconnect, and Bluetooth require hardware verification after permission setup. The current receiver alone does not establish the paired mouse's model.

REA static investigation and its separate limitations are recorded in [installer-analysis.md](installer-analysis.md). The vendor installer was never executed.

## Permission setup follow-up

The user installed the rule, but the first application view still showed permission denial. Inspection found the existing receiver had only the `seat` tag: the original installer reloaded the rules without triggering an event for devices already connected. After the receiver reconnected, `/dev/hidraw4` had `CURRENT_TAGS=:uaccess:seat:` and an ACL granting `user:dj:rw-`.

Read-only discovery then confirmed **MX Master 4**, online through Bolt slot 2, with no diagnostics. It returned eleven settings: DPI, SmartShift, wheel ratchet mode and torque, vertical direction and resolution, thumb-wheel direction, six button assignments, haptic strength, sixteen waveforms, and panel force. Observed DPI was 1000 and haptic strength 60. No write command was used for these checks.

The installer now triggers `change` events with `--settle` only for matching connected interfaces. Dry-run selected exactly `/sys/class/hidraw/hidraw2` through `hidraw5`. This avoids needing another reconnect. Reload alone does not apply rules to existing devices; see [udevadm documentation](https://www.freedesktop.org/software/systemd/man/latest/udevadm.html).

The application now monitors device attachment/removal and permission changes, coalesces events, ignores ordinary HID I/O events to avoid refresh loops, and provides a visible Check again button. GTK smoke checks verified event coalescing and permission-screen retry with simulated hardware. The connected physical mouse's controls were visually inspected in the real application.

Automated suite after the fix: **54 passed**, plus 30 successful subtests. New checks cover exact USB/Bluetooth rule matching and a dry-run that cannot execute privileged commands. The updated privileged trigger path was previewed, not run by the agent; existing receiver permissions were already correct after reconnect.

## Desktop features, version 0.2

Scope: Actions Ring and Smart Actions. Flow was explicitly deferred by the user for a later Linux, Windows and macOS implementation.

- Automated suite: **258 passed**, plus 30 successful subtests. New coverage includes action/config validation, native Wayland input and fallback, held-key cleanup, macro cancellation, ring interaction, listener packet decoding, reporting restoration, child-process lifecycle/generation races, stale queued events, and cancellation during selection.
- Live physical MX4 listener: reached ready with haptic panel diversion and raw XY enabled. After stop, flags/mapping were exactly restored from `[0,416]` to `[0,416]`. Exit code 0, no error events. See [hardware record](../evidence/desktop-hardware-smoke.json). No haptic waveform or macro was executed by this check.
- Native GTK layer-shell preview: opened, selected Copy without side effects, closed/reopened, and canceled correctly on a synthetic cleanup release. Both editor tabs and all eight slot assignments were exercised.
- Native input: locally built checksum-pinned wtype 0.4. A controlled Niri/Wayland test focused a dedicated GTK entry, executed Ctrl+A and inserted `MX4 ring ✓`, and verified the exact entry contents. The original focus was restored. No user application received test text.
- Remote CLI: `mx4-linux ring --preview` successfully showed the running application's `mx4-actions-ring` layer on output DP-3, with overlay layer and exclusive keyboard handling. Preview was dismissed.
- Setup: rebuilt and installed version 0.2.0, reused the verified local keyboard helper. No new privileged permission changes were needed. Both udev rules pass `udevadm verify`; shell syntax and Python compilation checks pass.

Interactive checks still needed: a real haptic panel press/move/release selecting an action, haptic ticks during selection, and unplug/reconnect while holding. Bluetooth and other compositors remain outside verified coverage. The existing hardware-setting read-only evidence is separate from listener setup/cleanup and native-input testing.
