# Architecture

MX4 Linux has three boundaries: the native user interface, isolated HID++ workers, and local desktop action execution. No Logitech proprietary executable is bundled or executed. Solaar 1.1.20 is pinned because the workers use its internal APIs.

## Components

| Module | Responsibility |
| :--- | :--- |
| `cli.py` | Argument parsing, diagnostics, hardware/profile commands and action import/export/run. |
| `gui.py` | GTK 4 / Libadwaita settings UI, asynchronous device operations and access-change refresh. |
| `backend.py` | JSON request/response bridge to the one-shot device worker; explicit simulated backend. |
| `device_worker.py` | Discovery, supported setting schemas, validated writes and readback through Solaar. |
| `profiles.py` | Manual snapshots, locked atomic saves, profile validation and sequential application. |
| `desktop.py` | GUI desktop integration, focus capture/restore, routine lifecycle and listener events. |
| `desktop_editor.py` | Ring assignment and Smart Actions editor. |
| `ring.py`, `ring_model.py` | GTK icon overlay, circular pointer geometry, held eight-sector interaction and optional side menus. |
| `ring_icons.py`, `ring_preview.py` | Step-based native icon mapping and the editor’s keyboard-accessible, animated assignment diagram. |
| `event_session.py` | Nonblocking subprocess lifecycle and session-tagged event delivery. |
| `event_worker.py` | Retained HID++ event handle, temporary control reporting and restoration. |
| `actions.py` | Desktop schema, defaults, persistence, execution and input backends. |
| `applications.py` | Read-only installed-application discovery and launch through GIO desktop entries. |
| `gtk_support.py` | GTK/Adwaita imports and optional layer-shell loading. |
| `gltf_model.py`, `model_renderer.py` | Offline loading of the supplied GLB, native GTK `GLArea` rendering and projection of model-space control landmarks. |
| `model_motion.py` | Unrestricted quaternion rotation, bounded zoom and finite, interruptible page-pose transitions without GTK or GPU dependencies. |
| `motion.py`, `mouse_motion.py`, `mouse_view.py` | Native model gestures, frame-time transitions, registered controls, baked-image fallback and finite wheel/haptic previews without hardware polling. |
| `bin/mx4-linux.js` | Linux npm entry point, explicit per-user runtime setup, argument/signal forwarding. |

## Hardware requests

The UI uses a one-shot subprocess for scans and writes. This keeps Solaar's GTK 3 imports outside the GTK 4 process and contains HID handles. The backend sends a JSON request and waits up to 90 seconds for a JSON response. Failures become visible application errors.

Discovery limits itself to supported MX Master 4 settings. Controls and numeric ranges come from the device through Solaar; unreadable settings are omitted with diagnostics. The worker avoids applying Solaar's saved settings during discovery. Writes address the selected receiver slot and construct only the requested setting and any coupled wheel settings, then validate, apply and read back the result. `haptic-play` is command-only and has no readback value. The worker bounds Solaar's optional paired-node search instead of repeating its default one-second wait; HID++ reply deadlines remain intact.

The GUI applies confirmed write values to existing controls instead of running another discovery scan and rebuilding the page. Coupled wheel readbacks update related controls without issuing extra writes. A failed write invalidates uncertain values and refreshes from hardware. Saving a profile uses the existing confirmed snapshot on the background executor and does not rescan the mouse.

Periodic battery reads use the serialized worker without another discovery scan. They update the battery labels and preserve the footer's last operation notice. A failed read clears the previous battery value and observation time; callbacks still require the same device identity and operation epoch.

Profile application validates a full plan before writing. It then makes sequential hardware changes, so it cannot provide a transaction across a disconnect or device failure. Errors state how many changes completed. Profiles never snapshot or replay waveform commands.

## Event listener lifecycle

Mouse integration starts only when the user enables it. `EventSession` starts the event worker, sends the selected ring/button controls and relays JSON-line events to the GUI. The worker captures the existing control reporting state, temporarily enables diversion and raw XY for selected supported buttons, and retains its HID handle for input and haptic feedback.

Opening the action editor dismisses any current ring selection while retaining enabled integration. Saving reconciles the configuration with the active worker through its existing control-release boundary; editing does not require disabling and re-enabling integration. The editor exposes the same integration status and Enable/Disable control as the main window.

Closing the main window hides it while integration is enabled or starting, unless `background` is explicitly false. The registered GTK window keeps the application and controller alive; launcher activation reuses that window without restarting integration. Explicit Quit uses the editor's existing unsaved-changes guard, cancels pending work, waits asynchronously for the listener's stopped/restoration callback, and then releases UI resources and exits. This does not add login autostart or automatically enable integration.

Only nonprimary controls are allowed. The prior reporting flags and mapping are restored on the worker's cleanup path. Stop requests, EOF and termination use this path; a disconnected mouse or forced process loss can prevent immediate hardware restoration and may require reconnecting.

The settings worker uses HID++ software ID `0xC`; the event worker uses `0xD`, distinct from Solaar's `0xB`. These IDs separate concurrent replies, but do not prevent other tools from writing conflicting settings.

Each event session has an identity. Old readers cannot mutate a replacement session's lifecycle or emit events into it; the GUI also checks session identity for events already queued on the GTK loop. Cleanup releases are explicitly cancelled, so disconnecting or disabling while holding the panel cannot commit a ring selection. Readiness is cleared before restoration finishes, and shutdown waits off the GTK thread.

An event burst schedules one GTK idle dispatch. The queue preserves each XY packet and its order relative to button releases; summing deltas would change selection because the ring clamps movement at its radius. Listener setup addresses the selected receiver slot directly.

## Ring and focus

The physical trigger opens a latched ring on press; releasing it does not select an action. Pointer selection hits the actual round icon faces; the gaps and square widget corners cannot execute actions. The center close button and native keyboard focus remain available. Brief transparent layer surfaces locate the stationary pointer on its output without warping it or taking keyboard focus. The ring then anchors around that position, clamped inside the output, with a centered fallback after 100 ms. Layer-shell gives it an overlay and exclusive keyboard handling on supported Wayland desktops; otherwise it can be a regular window.

Before opening the ring, desktop integration records Niri's focused application/window. Before executing the chosen action, it restores that focus and verifies the original window still exists. A vanished target stops execution. Ring/button dispatch refuses shortcuts and text when an MX4 configuration window is the target; direct CLI `actions run` does not use this GUI focus guard. A preview follows the selection UI but prints steps without action execution or haptic feedback.

Animations use GTK frame callbacks and elapsed time. Wheel motion settles after an impulse; ring callbacks stop when transitions finish or the ring hides. GTK's `gtk-enable-animations` preference disables decorative motion. Labels and buttons are native widgets reused across frames. Ring motion is paint only: `RingStage` shifts and scales each face about its fixed center through GTK snapshot transforms, so allocations and picking never move. Opening bursts all faces from the center to their slots over 110 ms while root hit regions are already final. Group children sit on an outer arc centered on the parent's angle and grow from dots in place, the middle child first; they are not targets until mostly grown, and a closing group's children shrink away without hit testing. Labels wait 120 ms, then fade in on the outward side of their face. Choosing or cancelling ends the ring's logical state at once and hands the action to the controller unchanged; the faces then collapse into the center for 120 ms while the surface has already returned the keyboard (layer-shell keyboard mode none) and passes pointer input through an empty input region. An immediate `hide_ring()`, a new opening or reduced motion finishes the collapse at once. The wheel illustration previews scrolling visually and does not read or claim to represent physical wheel rotation.

Mouse artwork renders the supplied GLB in a native GTK `GLArea`. The offline loader reads the embedded mesh attributes, materials and textures; no browser, network fetch or third-party 3D engine runs in the application. `ModelPicture` owns the renderer, input controllers, motion state and baked-image fallback. Surface targets, leaders and host indicators use model-space landmarks projected through the current orientation and zoom, while their native callout labels stay parked. Surface overlays hide during page turns and on faces pointing away from the viewer. Easy-Switch initially shows the model; its Switch computer callout opens the host selector, so compact layouts can show the turn without a covering inspector.

The home model supports drag rotation in every direction, including complete turns and upside-down views. Wheel scrolling and the +/− keys adjust zoom within a bounded range; arrow keys rotate the focused model, and Home or 0 restores its initial pose and magnification. Device pages share a model viewport: Haptic Feedback turns it sideways toward the thumb panel, and Easy-Switch starts a backward flip before settling with the underside upright. `ModelMotion` uses unit quaternions and 650 ms eased interpolation; the underside follows a spherical cubic curve to combine that backward turn with its readable final pose. Navigation interrupts and retargets from the current displayed orientation. The model remains still after settling. Frame callbacks run only during active transitions, capped at 60 fps; unmapping pauses elapsed time, and the toolbar pause control or system reduced-motion setting stops decorative playback. Reduced motion snaps page poses while keeping direct home-model manipulation available.

If graphics initialization fails, the model retains its baked product illustration and registered controls. Existing image assets also provide the finite wheel demonstration: it temporarily holds the 3D viewport, settles the baked camera, plays the wheel frames and restores the model. The decoded-image LRU cache remains bounded at 16 MiB, plus images retained by visible widgets. Neither the model pose nor the authored wheel/haptic previews represent physical motion telemetry.

The native shell uses a single monospaced family, flat warm-neutral surfaces and restrained transitions. Button contents and ring icons scale slightly on hover while their layout allocations and pointer hit regions stay fixed. Below 760 pixels, a device-page chooser replaces the wide sidebar; title truncation and bounded dropdowns preserve window sizing. The editor shares theme and motion preferences, supports Ctrl+S and guarded Escape, and exposes field errors beside the relevant details. Ring preview arrows move between assignments; Enter or Space opens the selected assignment. Global settings subscriptions follow mapping or explicit artwork disposal, and ring CSS providers are shared per display. See [design-system.md](design-system.md) for the visual and motion rules.

Choice rows place their label and help above a full-width control so narrow inspectors do not truncate the selected DPI or assignment. Main-window Back and Escape share route history: close the selected inspector first, then restore the previous page and its selected target. A bubbling key controller leaves native popovers and modal dialogs in control of their dismissal; Escape on Home does not close the application.

Optional `ring_groups` references existing routines by slot. Hovering or choosing a grouped position opens its side menu; an explicit child choice dispatches its routine. The physical trigger opens the ring latched on press; release cannot dispatch a selection. This does not change direct CLI execution of the parent action. Version 1 documents without groups remain valid. The editor prevalidates group references, preserves ID renames and removes deleted children before saving.

The existing 80 ms compositor focus handoff before input execution remains. It allows Niri focus restoration to reach the target application and is separate from animation timing.

## Smart Actions

The whole configuration is validated before saving or executing. Routines contain explicit steps: Niri actions, installed applications, command argument arrays, keyboard chords, text or delays. Command steps start a process without a shell and do not wait for the application to close. Their processes are reaped in the background.

The ring's **Add application** picker searches visible installed desktop entries, then creates a routine and assigns it to the chosen position in the editor draft. An application step stores `{"type":"application","desktop_id":"org.example.App.desktop"}`. Execution resolves that ID through `GioUnix.DesktopAppInfo` (or the legacy `Gio.DesktopAppInfo`) and calls its launch API, preserving desktop-entry semantics without parsing an `Exec` string. A removed launcher produces an actionable error. Discovery, selection, saving and dry runs do not launch applications.

The executor checks cancellation and its two-minute deadline between steps. Delays and native text chunks bound cancellation latency. The GUI permits one active routine and rejects repeated triggers instead of accumulating a queue. Cancellation prevents later steps; completed input and launched applications remain.

On Wayland, wtype handles supported chords and Unicode text. Unsupported shortcut mappings can use a lazily created evdev virtual keyboard, whose held keys are released on cancellation or errors. An already attempted native chord is not replayed through another backend. Without wtype, text replaces the clipboard using `wl-copy` then triggers paste. Imports, validation and dry runs create no virtual input device.

## Persistence and installation

Hardware profiles and desktop configuration are separate versioned JSON documents under the XDG configuration root. Writes take a file lock and replace a temporary file atomically. Desktop documents are limited to 1 MiB and saved with private file permissions. No backup history or daemon is installed.

Setup installs into a local virtual environment with system site packages for GObject. Mouse hidraw access and optional `/dev/uinput` access have separate udev installers. Each uses active-seat `uaccess`, with a dry-run mode. The desktop launcher points directly to the checkout; no compositor configuration or login autostart is changed.

The npm launcher is a thin distribution boundary around the same Python modules. Its tarball includes source and assets, with no npm dependencies or install hooks. Explicit setup builds a versioned environment under the user's XDG data directory with access to system GObject bindings. The launcher forwards CLI arguments and termination signals, loads its own bundled source, and leaves device access to the existing optional installers. A readiness marker prevents an interrupted setup from being selected as a completed runtime.

Python runtime dependencies and optional test/development tools live in `pyproject.toml`. CI installs the project and its extras directly, checks supported Python and Node versions, native GTK demo widgets, subprocess cleanup, and installation of the actual npm archive. Release workflows build Python/npm archives with checksums after CI passes and verify release tags against the package version. Publishing remains a separate maintainer action.

## Verification boundaries

Tests simulate device schemas, writes, protocol replies and lifecycle races. Recorded live checks separately cover Bolt discovery, exact listener reporting restoration, GTK preview and controlled native input. They do not establish actual panel press/move/release, haptic feel, physical settings behavior or Bluetooth reliability. See [validation.md](validation.md) and retain this distinction when reporting new checks.
