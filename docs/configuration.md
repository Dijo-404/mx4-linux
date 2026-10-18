# Configuration reference

MX4 Linux separates hardware profiles from desktop actions. Both use `$XDG_CONFIG_HOME/mx4-linux/`, normally `~/.config/mx4-linux/`. The files are created on save, not merely by reading defaults. Saves use a lock file and atomic replacement; no history or backup is maintained.

## Desktop configuration

`desktop.json` holds the ring, routines, button bindings and feedback preference. Edit it through **Actions → Edit actions** in the GUI, or export the complete document before changing it:

```bash
.venv/bin/mx4-linux actions show > desktop.json
.venv/bin/mx4-linux actions validate desktop.json
.venv/bin/mx4-linux actions import desktop.json
```

`validate` checks without saving or executing. `import` validates then replaces the saved document. Disable and re-enable mouse integration after importing changed button bindings while the GUI is running. Saving explicit button/ring assignments in the GUI stops mouse integration; enable it again afterwards. Local preferences, including Adapt to App, reconcile future assignments without stopping an open invocation.

The required top-level fields are `version`, `ring`, `actions`, `button_bindings`, `ring_button` and `haptic_feedback`. Additional fields described below are optional. Unknown fields are rejected. The file and its serialized configuration are limited to 1 MiB.

| Field | Type and limits | Default when no file exists |
| :--- | :--- | :--- |
| `version` | Integer, exactly `1` | `1` |
| `ring` | Exactly eight existing action IDs, clockwise from the top; IDs may repeat | `overview`, `screenshot`, `terminal`, `workspace-next`, `paste`, `copy`, `undo`, `workspace-previous` |
| `ring_groups` | Optional object mapping decimal ring positions `"0"`–`"7"` to 1–6 distinct existing action IDs in display order | Absent; each position runs its assigned action directly |
| `actions` | Object containing 1–128 named routines | Eight built-in routines listed below |
| `button_bindings` | Object mapping supported decimal button IDs to existing action IDs | `{}` |
| `ring_button` | One of `82`, `83`, `86`, `195`, `196`, `416`, or `null` for manual opening only | `416` (haptic panel) |
| `haptic_feedback` | Boolean controlling selection feedback | `true` |
| `adapt_to_app` | Optional boolean: use exact application assignments when enabled | `true` when omitted |

`button_bindings` cannot assign the selected `ring_button`. Supported IDs are a configuration allowlist; the listener also verifies that the connected device supports each selected control. Primary left/right click and scrolling are excluded from diversion.

### Application assignments and ring trigger

Optional `assignment_profiles` maps profile IDs to objects with `label`, exact `app_id` (or `"*"` for General), eight-position `ring`, and `button_bindings`. `ring_groups` and `ring_button` are optional in each profile. `active_profiles` maps exact app IDs and/or `"*"` to an existing profile with the same app ID. Up to 64 profiles are allowed; all profiles, including inactive ones, are validated before saving.

A profile without `ring_button` inherits the persisted root trigger. Explicit `null` disables its physical ring trigger, while an allowed integer overrides it. Each profile's ordinary bindings must exclude its effective trigger. With no physical trigger, all six supported controls may have ordinary bindings; **Open Actions Ring** and the CLI/desktop shortcut still work. An integration session with no trigger or bindings keeps its ownership/recovery checks and makes no new reporting changes.

With `adapt_to_app` enabled (the default), the active exact-app profile wins, then active General, then root assignments. Disabled adaptation uses General/root while retaining the actual focused app and window for action conditions and focus safety. Changing this preference preserves an already-open ring and running routine; future assignments become live after the listener acknowledges its safe control transition.

The device page's context selector edits the selected exact-app/General scope even when adaptation is disabled. Every local button menu offers **Default**, **Show Actions Ring**, and existing actions. Choosing a different trigger releases the old trigger; replacing a trigger with an ordinary action or Default explicitly disables that scope's trigger. Conflicts in any inheriting profile reject the save. The earlier full-window editor labels its default/root scope explicitly; shared routine definitions remain shared.

Built-in mouse functions are global hardware settings. Replacing a local assignment with a built-in function first waits for confirmed reporting restoration, then saves the local clear before attempting the hardware write. If the hardware request fails, the local clear remains saved and the status reports both results.

### Adjusting an action

An action may have an `adjustment` object with nonempty `increase` and `decrease` step arrays, validated with the same limits as its normal `steps`. Scroll over its fully revealed circle to adjust it, or press the primary mouse button on the circle and drag horizontally. Drag capture pins that initial action, including child actions; crossing a gap or another circle does not retarget it. The first detent requires 10 logical pixels of predominantly horizontal motion, followed by 12-pixel detents at every ring size. Small motion preserves a normal click. A recognized drag suppresses the ordinary action on release, including after reversal or a rejected adjustment. One motion callback emits at most 4 steps, and the controller bounds/coalesces pending work. Preview never runs adjustment steps.

### Action groups

Expand **Action groups (optional)** in the Actions Ring editor to make a position open a submenu. Its assigned routine provides the group's label and icon; choosing a child runs that child's existing routine. A group belongs to a ring position, so assigning the same routine elsewhere does not create another group. Calling the parent routine directly, including through a mouse button shortcut, still runs its normal steps.

For example, this optional fragment puts three routines behind the right-hand position (position `"2"`):

```json
"ring_groups": {"2": ["copy", "paste", "undo"]}
```

Choose entries in the order you want them displayed. Empty dropdowns are omitted; leaving all six empty removes the group. Groups have one level and cannot repeat an action within the same group. Renaming a routine updates its group entries when you save. Deleting a routine removes its entries and removes groups left empty. Existing version 1 files without `ring_groups` continue to work unchanged.

### Routines

An action ID is 1–64 lowercase ASCII letters, digits, hyphens or underscores, starting with a letter or digit. Each routine requires:

| Field | Type and limits |
| :--- | :--- |
| `label` | 1–80 printable characters |
| `steps` | An array containing 1–64 steps |
| `app_id` | Optional exact focused-application match; 1–256 printable characters |

An application condition is evaluated before a real execution. A dry run reports it without querying focus or evaluating it. Inspect Niri's current application ID with `niri msg --json focused-window`.

### Step types

Each step requires `type` and the fields below. Unknown step fields are rejected.

| Type | Fields | Behavior and limits |
| :--- | :--- | :--- |
| `shortcut` | `keys` | 1–8 distinct Linux key names or aliases such as `CTRL`, `ALT`, `SHIFT`, `SUPER`, `C`, `ENTER`; releases held keys after the chord. |
| `command` | `argv` | Nonempty array of up to 64 strings, each at most 4096 characters without NUL. Starts the process without a shell and returns after launch. |
| `text` | `text` | 1–65,536 characters without NUL. Types through wtype, or replaces the clipboard through `wl-copy` and pastes. |
| `delay` | `milliseconds` | Integer from 0 to 30,000; total configured delay per routine cannot exceed 60,000 ms. |
| `niri` | `action`; optional `args` | Runs `niri msg action ACTION ...`. `args` defaults to `[]`, with up to 64 string arguments. `spawn` and `spawn-sh` are rejected; use a command step. |

Niri action names use lowercase letters, digits and hyphens, start with a letter and have at most 80 characters. Validation checks this format; Niri determines whether an action and its arguments are supported by the installed compositor.

Example routine fragment inside `actions`:

```json
{
  "copy-terminal": {
    "label": "Copy and open terminal",
    "steps": [
      {"type": "shortcut", "keys": ["CTRL", "C"]},
      {"type": "delay", "milliseconds": 150},
      {"type": "command", "argv": ["kitty"]}
    ]
  }
}
```

This is a fragment, not a complete importable desktop document. A launch does not wait for a new window to focus; add an appropriate delay before typing into a launched application. Cancellation prevents later steps but does not undo completed input or stop a launched application. Runs have a two-minute execution limit. The GUI rejects repeated triggers while a routine is running.

### Built-in routines

| ID | Steps |
| :--- | :--- |
| `overview` | Niri `toggle-overview` |
| `screenshot` | Niri `screenshot` |
| `terminal` | Launch the preferred available terminal |
| `workspace-next` | Niri `focus-workspace-down` |
| `paste` | `CTRL` + `V` |
| `copy` | `CTRL` + `C` |
| `undo` | `CTRL` + `Z` |
| `workspace-previous` | Niri `focus-workspace-up` |

The default Terminal routine uses the executable command in `TERMINAL` if valid and available. Otherwise it checks `kitty`, `foot`, `alacritty`, `gnome-terminal`, `konsole`, `x-terminal-emulator`, then `xterm`; its final fallback is `x-terminal-emulator`. This choice is stored when the default configuration is saved. Changing `TERMINAL` does not rewrite an already saved routine.

### Input backends

With `WAYLAND_DISPLAY` set, the application searches for wtype on `PATH`, then next to the active Python executable (normally `.venv/bin/wtype`). Native input supports common modifiers, letters, digits, function/navigation/media keys and Unicode text. It preserves the clipboard.

A shortcut whose key codes are unsupported by the native mapping can fall back to python-evdev and `/dev/uinput`. A failed native injection is not replayed through a second backend, because the target may already have received input. Without wtype, text uses `wl-copy` and Ctrl+V; the clipboard is replaced and not restored.

## Hardware profiles

`profiles.json` has the following shape:

```json
{
  "version": 1,
  "profiles": {
    "Work": {
      "model": "MX Master 4",
      "demo": false,
      "settings": {"dpi": 1600}
    }
  }
}
```

Use the application to save profiles so values match the discovered device schema:

```bash
.venv/bin/mx4-linux profile save Work
.venv/bin/mx4-linux profile list
.venv/bin/mx4-linux profile apply Work
```

Add `--device "$DEVICE_ID"` when more than one mouse is online, using the quoted opaque identifier shown in the [README example](../README.md#examples). Names are trimmed and must contain 1–64 characters without control characters. Saving an existing name replaces that profile. Profiles contain readable current settings and exclude the command-only `haptic-play` setting. Preview profiles are marked `demo` and cannot be applied to physical devices.

Application validates the entire profile against the current device's available settings, ranges, choices and button IDs before any write. Hardware failures or cancellation can still leave part of the plan applied; the error reports the completed count. Refresh to inspect actual values. Profiles are manual and do not auto-apply on login, focus change or reconnect.

## Environment variables

| Variable | Scope | Behavior |
| :--- | :--- | :--- |
| `XDG_CONFIG_HOME` | Application | Configuration root, default `~/.config`. |
| `XDG_DATA_HOME` | Desktop installer | Data root, default `~/.local/share`; launcher goes under `applications/`. |
| `TERMINAL` | Default desktop configuration | Preferred terminal command, parsed into arguments without an implicit shell. |
| `PYTHON` | `scripts/setup.sh` | Interpreter used for setup, default `python3`. |
| `WAYLAND_DISPLAY` | Setup and input | Enables optional Wayland helper setup/detection in an active session. |
| `XDG_SESSION_TYPE` | Diagnostics | Reported by `doctor`, default `unknown`. |
| `XDG_CURRENT_DESKTOP` | Diagnostics | Reported by `doctor`, default `unknown`. |

Standard `PATH` executable lookup also applies to Niri, wtype, configured commands and helper build tools. There is no `NO_COLOR` option or application-specific environment-variable prefix.
