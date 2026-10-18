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
- Live physical MX4 listener: reached ready with haptic panel diversion and raw XY enabled. After stop, flags/mapping were exactly restored from `[0,416]` to `[0,416]`. Exit code 0, no error events. See [hardware record](https://github.com/Dijo-404/mx4-linux/blob/main/evidence/desktop-hardware-smoke.json). No haptic waveform or macro was executed by this check.
- Native GTK layer-shell preview: opened, selected Copy without side effects, closed/reopened, and canceled correctly on a synthetic cleanup release. Both editor tabs and all eight slot assignments were exercised.
- Native input: locally built checksum-pinned wtype 0.4. A controlled Niri/Wayland test focused a dedicated GTK entry, executed Ctrl+A and inserted `MX4 ring ✓`, and verified the exact entry contents. The original focus was restored. No user application received test text.
- Remote CLI: `mx4-linux ring --preview` successfully showed the running application's `mx4-actions-ring` layer on output DP-3, with overlay layer and exclusive keyboard handling. Preview was dismissed.
- Setup: rebuilt and installed version 0.2.0, reused the verified local keyboard helper. No new privileged permission changes were needed. Both udev rules pass `udevadm verify`; shell syntax and Python compilation checks pass.

Interactive checks still needed: a real haptic panel press/move/release selecting an action, haptic ticks during selection, and unplug/reconnect while holding. Bluetooth and other compositors remain outside verified coverage. The existing hardware-setting read-only evidence is separate from listener setup/cleanup and native-input testing.


## UI, latency and npm packaging follow-up

Changes on `feat/ui-performance-packaging` retain the native Python/GTK application. Mouse, Actions and Profiles have separate views; the wheel preview and ring use finite frame callbacks that stop when settled or hidden and honor GTK's animation preference. Ring labels are reused native controls with keyboard focus, while pointer hit testing follows the painted wedges. Smart Actions now has a native step builder with optional JSON, inline validation and reordering. The installer ZIP digest still matches the retained static analysis; it contains no application UI source or reusable device protocol implementation, and was not executed.

Read-only MX Master 4 / Bolt measurements on this machine:

| Operation | Observed work |
| --- | --- |
| Discover and read all eleven settings | 0.780 seconds; 33 HID feature requests |
| Discover and read one selected setting | 0.060 seconds; 2 HID feature requests |
| Cold import, target selection and selected read before paired-node wait change | 2.074 seconds |
| Same cold operation after bounded paired-node wait | 1.496 seconds |

These are individual samples, with variable radio timing. The first comparison measures capability discovery/read work, not complete setting-write latency or whole-application speed. Profiling attributed approximately one second and 600,000 Python calls to Solaar's repeated paired-node udev busy wait. Worker isolation bounds that lookup to 10 ms and retains its receiver fallback. Writes still discover and validate the live target, require acknowledgement and verify readback; no persistent HID cache was introduced.

Confirmed setting edits now update existing controls without a follow-up scan. Failed writes and partial profile applications clear uncertain controls and refresh before profiles can save stale values. Profile and Smart Actions persistence run off the GTK thread. Event dispatch batches a burst into one GTK idle callback while retaining each raw packet in order; summing motion would change the model's radius-clamped trajectory.

Validation includes full Python regressions, real GTK demo/widget checks under Wayland and X11, reduced motion and finite callback cleanup, dark/light editor inspection, Python/Node syntax and package checks, and Python wheel/source distribution builds. The npm tarball was installed into a temporary prefix; its real setup created an isolated per-user runtime with system Python, and that installed runtime passed CLI and GTK smoke checks without an interpreter override. GitHub workflow definitions are checked locally; hosted Actions have not run for these local changes.

No physical settings, haptic waveform, button diversion or user routine was executed for this follow-up. Physical interaction, haptic feel, reconnect and Bluetooth checks remain open. npm/PyPI publication and remote branch changes were not performed.

Final local checks: **301 Python tests and 32 subtests**, five Node launcher tests, Ruff, Python compilation, ShellCheck (Bash), both udev rules, 33 local documentation links, and actionlint passed. Native GTK smoke passed on Wayland and X11. The reviewed npm archive, Python wheel and source archive are generated under `dist/`; their Python sources and icon are checked against the working tree.


## Video-reference ring follow-up

Visual reference: supplied `looks.mp4`, 62.93 seconds, 720 × 1280, 30 fps; SHA-256 `e94b9ff40bff8cfa690f087fb180391a0b637d4c60e952bbb4b68c8cea33f4ae`. Review used overview frame samples and denser samples around the transitions, plus the two supplied screenshots. Audio was not analyzed. Around 00:46.10–00:46.27 the icon ring expands; around 00:47.10–00:47.30 a group reveals its side menu; around 00:48.10–00:48.20 a hovered child inverts its circle and displays an outward label. These timings are approximate observations, not a recovered vendor implementation.

The application now uses eight separate native icon buttons, a small center close button, outward hover/focus labels and a radial opening transition. The editor has a labeled, live assignment diagram. Optional `ring_groups` add one to six existing routines to a side menu; legacy version 1 documents remain valid. Held physical motion keeps its original calibration, while latched pointer targeting follows the actual painted circles during animation. Emerging children cannot accept pointer clicks while overlapping a trigger or sibling. Keyboard focus, explicit close, Escape/back, cancellation and reduced motion are covered by native tests. Group entries retain references across routine renames and remove deleted children; empty editor entries compact to match displayed shortcut order.

Final local checks: **401 Python tests and 32 subtests**, five Node launcher tests, Ruff, compilation, whitespace checks and 35 local documentation links passed. The mapped GTK smoke checks pass on Wayland and X11, including groups and finite/reduced-motion behavior. The actual npm archive was installed into a temporary prefix, its native runtime was set up, and that installed runtime passed the updated GTK group smoke check. Native snapshots were inspected on light and dark backgrounds, with six-child menus and long labels. Source screenshots are in `docs/assets/`; a four-second scripted GTK preview is generated under ignored `evidence/artifacts/actions-ring-demo.mp4`. Captures contain only the application's widgets, and selection callbacks did not execute routines.

This follow-up did not write physical mouse settings, enable diversion, play haptics or execute user routines. Interactive panel selection, haptic feel, Bluetooth and reconnect checks remain open. Changes are committed locally on the existing improvement branch; hosted CI and registry publication were not run.
