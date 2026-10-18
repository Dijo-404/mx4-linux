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
| `ring.py`, `ring_model.py` | GTK overlay and testable eight-sector interaction state. |
| `event_session.py` | Nonblocking subprocess lifecycle and session-tagged event delivery. |
| `event_worker.py` | Retained HID++ event handle, temporary control reporting and restoration. |
| `actions.py` | Desktop schema, defaults, persistence, execution and input backends. |
| `gtk_support.py` | GTK/Adwaita imports and optional layer-shell loading. |

## Hardware requests

The UI uses a one-shot subprocess for scans and writes. This keeps Solaar's GTK 3 imports outside the GTK 4 process and contains HID handles. The backend sends a JSON request and waits up to 90 seconds for a JSON response. Failures become visible application errors.

Discovery limits itself to supported MX Master 4 settings. Controls and numeric ranges come from the device through Solaar; unreadable settings are omitted with diagnostics. The worker avoids applying Solaar's saved settings during discovery. A write validates its value against the discovered schema, applies it and checks readback. `haptic-play` is command-only and has no readback value.

Profile application validates a full plan before writing. It then makes sequential hardware changes, so it cannot provide a transaction across a disconnect or device failure. Errors state how many changes completed. Profiles never snapshot or replay waveform commands.

## Event listener lifecycle

Mouse integration starts only when the user enables it. `EventSession` starts the event worker, sends the selected ring/button controls and relays JSON-line events to the GUI. The worker captures the existing control reporting state, temporarily enables diversion and raw XY for selected supported buttons, and retains its HID handle for input and haptic feedback.

Only nonprimary controls are allowed. The prior reporting flags and mapping are restored on the worker's cleanup path. Stop requests, EOF and termination use this path; a disconnected mouse or forced process loss can prevent immediate hardware restoration and may require reconnecting.

The settings worker uses HID++ software ID `0xC`; the event worker uses `0xD`, distinct from Solaar's `0xB`. These IDs separate concurrent replies, but do not prevent other tools from writing conflicting settings.

Each event session has an identity. Old readers cannot mutate a replacement session's lifecycle or emit events into it; the GUI also checks session identity for events already queued on the GTK loop. Cleanup releases are explicitly cancelled, so disconnecting or disabling while holding the panel cannot commit a ring selection. Readiness is cleared before restoration finishes, and shutdown waits off the GTK thread.

## Ring and focus

The ring uses an eight-sector model with a center dead zone. It supports raw relative mouse movement while held, pointer selection after a short click and keyboard selection. It opens at screen center, without warping the pointer. Layer-shell gives it an overlay and exclusive keyboard handling on supported Wayland desktops; otherwise it can be a regular window.

Before opening the ring, desktop integration records Niri's focused application/window. Before executing the chosen action, it restores that focus and verifies the original window still exists. A vanished target stops execution. Ring/button dispatch refuses shortcuts and text when an MX4 configuration window is the target; direct CLI `actions run` does not use this GUI focus guard. A preview follows the selection UI but prints steps without action execution or haptic feedback.

## Smart Actions

The whole configuration is validated before saving or executing. Routines contain explicit steps: Niri actions, command argument arrays, keyboard chords, text or delays. Command steps start a process without a shell and do not wait for the application to close. Their processes are reaped in the background.

The executor checks cancellation and its two-minute deadline between steps. Delays and native text chunks bound cancellation latency. The GUI permits one active routine and rejects repeated triggers instead of accumulating a queue. Cancellation prevents later steps; completed input and launched applications remain.

On Wayland, wtype handles supported chords and Unicode text. Unsupported shortcut mappings can use a lazily created evdev virtual keyboard, whose held keys are released on cancellation or errors. An already attempted native chord is not replayed through another backend. Without wtype, text replaces the clipboard using `wl-copy` then triggers paste. Imports, validation and dry runs create no virtual input device.

## Persistence and installation

Hardware profiles and desktop configuration are separate versioned JSON documents under the XDG configuration root. Writes take a file lock and replace a temporary file atomically. Desktop documents are limited to 1 MiB and saved with private file permissions. No backup history or daemon is installed.

Setup installs into a local virtual environment with system site packages for GObject. Mouse hidraw access and optional `/dev/uinput` access have separate udev installers. Each uses active-seat `uaccess`, with a dry-run mode. The desktop launcher points directly to the checkout; no compositor configuration or login autostart is changed.

## Verification boundaries

Tests simulate device schemas, writes, protocol replies and lifecycle races. Recorded live checks separately cover Bolt discovery, exact listener reporting restoration, GTK preview and controlled native input. They do not establish actual panel press/move/release, haptic feel, physical settings behavior or Bluetooth reliability. See [validation.md](validation.md) and retain this distinction when reporting new checks.
