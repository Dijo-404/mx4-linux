# Interactive ring and background integration

Validated on 19 October 2026 with GTK 4 on Niri/Wayland.

The ring now traces its orbit into view over 220 ms while all eight root
buttons remain visible at their final click positions. A short blue selection
marker takes the shortest route between actions over 180 ms and continues
from its painted position when interrupted. Pointer, keyboard and held-panel
selection share that feedback. Held motion adds a direction tether; a captured
press changes the face tint and compresses its icon without moving the target.
Groups retain their guarded 160 ms reveal. Selecting an action still hides the
ring and dispatches immediately, without waiting for an exit animation.

Reduced motion shows the final selection directly and disables icon scaling.
Animation frame callbacks stop at rest and on dismissal; paint updates are
capped at 60 fps. No new dependency or continuous idle animation was added.

Closing the settings window now keeps enabled or starting integration running
by default. The hidden registered window retains the controller and preferences;
reopening the launcher presents the same instance without restarting the
listener. The Application Settings switch can disable background operation.
The application menu exposes **Quit MX4 Linux** at both compact and wide sizes,
and Ctrl+Q invokes the same action. Quit respects an unsaved editor's existing
Cancel/Discard decision, stops integration, waits asynchronously for restoration,
and then releases the application resources. Closing with integration disabled
exits normally. This change does not add login autostart or enable integration.

## Verification

| Check | Result |
| --- | --- |
| Full Python suite | 1,169 tests and 32 subtests passed; 1,616 deprecation warnings |
| Motion/geometry tests | 152 targeted tests passed |
| GUI and settings tests | 92 targeted tests passed |
| Native ring interactions | 75%, 100%, 150% and reduced motion; pointer press/release, keyboard commit, held release, group navigation and Escape passed |
| Native rendering | 32 ring captures; compact/wide settings in both themes and application menu captures |
| Idle behavior | No remaining ring animation callback after settling or dismissal in all four cases |
| Actual GApplication lifetime | Hidden process survived; ring opened with settings hidden; a second-process launch reused the same window; Quit waited for asynchronous restoration then exited |
| Source review | Independent lifecycle review found no blocking issues |
| Ruff, whitespace, mechanical design scan | Passed; no detector findings |

The GApplication proof used the real GUI entry point, a simulated device and a
fake event session. It did not change physical reporting, synthesize input or
execute actions. This pass did not exercise a physical panel press. Native
map/unmap checks emitted the existing GTK frame-timing warning on this system.

Capture harnesses were isolated from real cursor motion and explicitly focused
their own window before opening native popovers. All saved imagery contains
only application widget pixels.

Review the [short motion recording](assets/ring-motion/interactive-ring.mp4),
[selected action](assets/ring-motion/selected.png),
[held direction](assets/ring-motion/held.png),
[action group](assets/ring-motion/group.png), and
[background setting](assets/ring-motion/background-settings.png).
