# Interface and Actions Ring fixes

Validated on 19 October 2026 against the native GTK application on Niri/Wayland.

The supplied click-hand mark is retained, with smooth contours and the baked
checkerboard removed. The launcher uses a warm white tile with transparent
corners, so the mark remains visible on light and dark desktops. Its existing
SVG path embeds the cleaned image; it is not a newly hand-authored vector.
The desktop launcher and icon cache were refreshed locally.

Setting choices now occupy their own full-width row. Buttons stay vertically
centered alongside descriptions, dropdown menus allow readable names, and both
action editor tabs use the same bounded content width and compact layout.
The ring preview retains circular controls and readable labels at narrow widths.

Escape dismisses the active picker or inspector before returning through page
history. Native popovers and dialogs retain their dismissal behavior. Periodic
battery reads update the battery displays without overwriting the footer with
an unfinished loading message.

Opening an editor no longer disables mouse integration. Its Enable/Disable
control stays visible on the Actions Ring tab and follows device-job state.
Saving reconciles the active listener through its existing release boundary;
unchanged focus assignments no longer cause redundant reconfiguration.

**Actions Ring → Add application → choose a position and application → Save
changes** creates an installed-application action. GIO launches the exact saved
`.desktop` ID, preserving desktop-entry handling for terminal, Flatpak and
D-Bus applications. Selection, editing, saving and dry runs do not launch it.
The runtime reports a removed application with a recovery instruction.

## Verification

| Check | Result |
| --- | --- |
| Full Python suite | 1,112 tests and 32 subtests passed; 1,423 GTK/PyGObject and related deprecation warnings |
| Node launcher tests | 6 passed |
| Ruff and whitespace checks | Passed |
| Impeccable mechanical scan | No findings on the shared native shell/CSS targets |
| Real GIO launch | Temporary installed desktop fixture launched and wrote its expected marker; dry run left no marker |
| Distribution archives | Wheel and source archive built; all 9 checked changed runtime modules/assets match the source bytes |
| Native visual inspection | 400, 620 and 1100 px, light and dark, settings, pointer, button assignment, ring and Smart Actions; picker search and empty states |

The physical haptic-panel press was not exercised in this pass. Tests cover its
controller/event lifecycle; the running application needs a restart, followed
by **Actions Ring → Enable** if mouse integration is off. No live mouse setting,
button assignment or user action configuration was changed by validation.

Representative captures: [compact ring](assets/ui-fixes/ring-compact.png),
[application search](assets/ui-fixes/application-picker.png),
[full-width setting](assets/ui-fixes/pointer-compact.png), and
[Smart Actions](assets/ui-fixes/smart-actions.png).

## Haptic feedback integration correction

A subsequent live report exposed HID++ request `0x0B4D`, error `0x05`.
Read-only feature discovery mapped index `0x0B` to HAPTIC (`0x19B0`), and
the mouse reported its haptic feedback as disabled. Optional waveform playback
was escaping the listener as a fatal error, stopping integration and the ring.

The listener now checks the hardware feedback state before its first playback.
It respects the disabled setting, reports a readable warning, and keeps mouse
integration active. Capability or playback failures suppress further feedback
attempts for that session. Identity and reporting ownership failures still stop
integration through the normal restoration path. Structured HID++ errors retain
their diagnostic codes without exposing a raw exception dictionary.

A bounded production `EventSession` check on the connected mouse confirmed
readiness with the haptic panel as ring trigger, one disabled-feedback warning,
a successful configuration acknowledgement afterward, and continued readiness.
Repeated feedback requests did not repeat the warning. Graceful stop restored
all six original control flags and mappings exactly, cleared the reporting
journal, and preserved both the hardware haptic setting and saved configuration.
This check did not execute actions, synthesize input, or exercise a physical
panel press.

The installed launcher uses the editable source checkout. Each Enable starts a
new worker process, so this correction loads without restarting the open editor.
Regression validation passed: 1,140 Python tests and 32 subtests, including
continued ring dispatch after feedback rejection; Ruff and whitespace checks
also passed. The suite emitted 1,781 deprecation warnings.

## Icon provenance

The built-in image generator refined the user-supplied `/home/dj/Downloads/1.svg`.
The project asset is `src/mx4_linux/icons/io.github.mx4linux.Control.svg`.
The generation brief preserved the click-hand and five rays, requested smooth,
consistent near-black contours with rounded joins, removed the checkerboard,
and required transparency, no text, gradients, shadows or new decorative elements.
The clean mark is embedded on the application tile during asset packaging.
