"""Pure Linux action templates using the executor's existing step primitives."""

from copy import deepcopy
import ipaddress
import re
import shutil
from urllib.parse import urlsplit

from .actions import validate_config
from .action_utilities import DATE_FORMATS, CLIPBOARD_OPERATIONS


def _shortcut(*keys):
    return [{"type": "shortcut", "keys": list(keys)}]


# Metadata is kept beside each template so search and action creation agree.
_PRESETS = {
    "copy": ("Editing", "Copy", "Copy the selection.", "edit-copy-symbolic", _shortcut("CTRL", "C")),
    "paste": ("Editing", "Paste", "Paste the clipboard.", "edit-paste-symbolic", _shortcut("CTRL", "V")),
    "cut": ("Editing", "Cut", "Cut the selection.", "edit-cut-symbolic", _shortcut("CTRL", "X")),
    "undo": ("Editing", "Undo", "Undo the last change.", "edit-undo-symbolic", _shortcut("CTRL", "Z")),
    "redo": ("Editing", "Redo", "Redo the last change.", "edit-redo-symbolic", _shortcut("CTRL", "SHIFT", "Z")),
    "select-all": ("Editing", "Select all", "Select all content.", "edit-select-all-symbolic", _shortcut("CTRL", "A")),
    "find": ("Editing", "Find", "Find in the focused application.", "edit-find-symbolic", _shortcut("CTRL", "F")),
    "save": ("Editing", "Save", "Save the current document.", "document-save-symbolic", _shortcut("CTRL", "S")),
    "back": ("Navigation", "Back", "Go back in the focused application.", "go-previous-symbolic", _shortcut("ALT", "LEFT")),
    "forward": ("Navigation", "Forward", "Go forward in the focused application.", "go-next-symbolic", _shortcut("ALT", "RIGHT")),
    "previous-app": ("Navigation", "Previous application", "Switch to the previous application.", "focus-windows-symbolic", _shortcut("ALT", "TAB")),
    "close-window": ("Navigation", "Close window", "Close the focused window.", "window-close-symbolic", _shortcut("ALT", "F4")),
    "play-pause": ("Media", "Play / pause", "Toggle media playback.", "media-playback-start-symbolic", _shortcut("PLAYPAUSE")),
    "media-next": ("Media", "Next track", "Skip to the next media track.", "media-skip-forward-symbolic", _shortcut("NEXTSONG")),
    "media-previous": ("Media", "Previous track", "Return to the previous media track.", "media-skip-backward-symbolic", _shortcut("PREVIOUSSONG")),
    "mute": ("Media", "Mute", "Toggle audio mute.", "audio-volume-muted-symbolic", _shortcut("MUTE")),
    "volume-up": ("Media", "Volume up", "Send the desktop volume increase key.", "audio-volume-high-symbolic", _shortcut("VOLUMEUP")),
    "volume-down": ("Media", "Volume down", "Send the desktop volume decrease key.", "audio-volume-low-symbolic", _shortcut("VOLUMEDOWN")),
    "adjustable-volume": ("Media", "Adjust volume", "Adjust audio volume; click to toggle mute.", "audio-volume-high-symbolic", _shortcut("MUTE")),
    "adjustable-zoom": ("Editing", "Adjust zoom", "Adjust zoom; click to reset zoom in compatible applications.", "zoom-in-symbolic", _shortcut("CTRL", "0")),
    "adjustable-brightness": ("Desktop", "Adjust brightness", "Adjust brightness; click to increase one step when desktop bindings support brightness keys.", "display-brightness-symbolic", _shortcut("BRIGHTNESSUP")),
    "overview": ("Desktop", "Overview", "Toggle the Niri overview.", "view-grid-symbolic", [{"type": "niri", "action": "toggle-overview"}]),
    "workspace-next": ("Desktop", "Next workspace", "Focus the next Niri workspace.", "go-down-symbolic", [{"type": "niri", "action": "focus-workspace-down"}]),
    "workspace-previous": ("Desktop", "Previous workspace", "Focus the previous Niri workspace.", "go-up-symbolic", [{"type": "niri", "action": "focus-workspace-up"}]),
    "screenshot": ("Desktop", "Screenshot", "Open the Niri screenshot selector.", "camera-photo-symbolic", [{"type": "niri", "action": "screenshot"}]),
    "lock": ("Desktop", "Lock session", "Ask systemd-logind to lock the current session; requires a running screen locker.", "system-lock-screen-symbolic", [{"type": "command", "argv": ["loginctl", "lock-session"]}]),
}
_PRESETS.update({
    "new-tab": ("Navigation", "New tab", "Open a new tab in a compatible application.", "tab-new-symbolic", _shortcut("CTRL", "T")),
    "close-tab": ("Navigation", "Close tab", "Close the current tab in a compatible application.", "window-close-symbolic", _shortcut("CTRL", "W")),
    "next-tab": ("Navigation", "Next tab", "Select the next tab.", "go-next-symbolic", _shortcut("CTRL", "TAB")),
    "previous-tab": ("Navigation", "Previous tab", "Select the previous tab.", "go-previous-symbolic", _shortcut("CTRL", "SHIFT", "TAB")),
    "home": ("Navigation", "Home", "Move to the start of the line.", "go-first-symbolic", _shortcut("HOME")),
    "end": ("Navigation", "End", "Move to the end of the line.", "go-last-symbolic", _shortcut("END")),
    "page-up": ("Navigation", "Page up", "Move up one page.", "go-up-symbolic", _shortcut("PAGEUP")),
    "page-down": ("Navigation", "Page down", "Move down one page.", "go-down-symbolic", _shortcut("PAGEDOWN")),
    "media-stop": ("Media", "Stop media", "Request media playback stop.", "media-playback-stop-symbolic", _shortcut("STOPCD")),
    "keyboard-layout-next": ("Desktop", "Next keyboard layout", "Select the next configured Niri keyboard layout.", "input-keyboard-symbolic", [{"type": "niri", "action": "switch-layout", "args": ["next"]}]),
    "do-nothing": ("Custom", "Do nothing", "Complete without input or a desktop request.", "process-stop-symbolic", [{"type": "delay", "milliseconds": 0}]),
})
_DATE_LABELS = {"iso-date": "ISO date", "iso-time": "ISO time", "iso-datetime": "ISO date and time",
                "day-of-year": "Day of year", "iso-week": "ISO week", "moon-phase": "Moon Phase (approximate)"}
for _format in sorted(DATE_FORMATS):
    _description = ("Insert an approximate eight-phase Moon name from a mean lunation; not precise phase timing."
                    if _format == "moon-phase" else "Insert the current local " + _DATE_LABELS[_format] + ".")
    _PRESETS[_format] = ("Date and time", _DATE_LABELS[_format], _description, "x-office-calendar-symbolic",
                         [{"type": "date", "format": _format}])
for _operation in sorted(CLIPBOARD_OPERATIONS):
    _PRESETS["clipboard-" + _operation] = ("Clipboard", "Clipboard: " + _operation.replace("-", " "),
        "Update only regular plain-text clipboard contents; does not paste. Sentence case uses deterministic punctuation boundaries.",
        "edit-paste-symbolic", [{"type": "clipboard", "operation": _operation}])
for _id, _label, _description in (
        ("stopwatch", "Stopwatch", "Open a local stopwatch; pauses when hidden."),
        ("tap-tempo", "Tap Tempo", "Open the local tap-tempo BPM counterpart; last 8 taps, resets after a pause."),
        ("bell", "System bell", "Request the system bell; the desktop may suppress audible sound.")):
    _PRESETS[_id] = ("Utilities", _label, _description, "preferences-system-time-symbolic", [{"type": "local", "operation": _id}])
for _enabled in (True, False):
    _PRESETS["adapt-on" if _enabled else "adapt-off"] = ("Profiles", "Adapt to App: " + ("on" if _enabled else "off"),
        "Set local application-profile adaptation for future invocations.", "preferences-system-symbolic",
        [{"type": "local", "operation": "adapt-to-app", "enabled": _enabled}])

_ADJUSTMENTS = {
    "adjustable-volume": {"increase": _shortcut("VOLUMEUP"), "decrease": _shortcut("VOLUMEDOWN")},
    "adjustable-zoom": {"increase": _shortcut("CTRL", "EQUAL"), "decrease": _shortcut("CTRL", "MINUS")},
    "adjustable-brightness": {"increase": _shortcut("BRIGHTNESSUP"), "decrease": _shortcut("BRIGHTNESSDOWN")},
}
for _button in ("left", "right", "middle"):
    _PRESETS["pointer-" + _button + "-click"] = ("Pointer", "Current cursor: " + _button + " click",
        "Click at the current cursor; keyboard focus does not establish the cursor's target.", "input-mouse-symbolic",
        [{"type": "pointer", "operation": "click", "button": _button, "count": 1}])
_PRESETS["pointer-double-click"] = ("Pointer", "Current cursor: double click", "Double click at the current cursor.",
    "input-mouse-symbolic", [{"type": "pointer", "operation": "click", "button": "left", "count": 2}])
for _axis in ("horizontal", "vertical"):
    _step = {"type": "pointer", "operation": "scroll", "horizontal": 0, "vertical": 0}
    _step[_axis] = 1
    _PRESETS["adjustable-" + _axis + "-scroll"] = ("Pointer", "Current cursor: " + _axis + " scroll",
        "Scroll at the current cursor. Software wheel steps depend on the application; no cursor warp.",
        "input-mouse-symbolic", [deepcopy(_step)])
    _decrease = deepcopy(_step)
    _decrease[_axis] = -1
    _ADJUSTMENTS["adjustable-" + _axis + "-scroll"] = {"increase": [deepcopy(_step)], "decrease": [_decrease]}
for _id, _dx, _dy in (("left", -40, 0), ("right", 40, 0), ("up", 0, -40), ("down", 0, 40)):
    _PRESETS["pointer-move-" + _id] = ("Pointer", "Current cursor: move " + _id,
        "Move the cursor relatively by 40 compositor units.", "input-mouse-symbolic",
        [{"type": "pointer", "operation": "move", "dx": _dx, "dy": _dy}])
_TEMPLATES = {
    "application": ("Open", "Open application", "Launch an application with explicit arguments.", "application-x-executable-symbolic", "argv", "Application arguments"),
    "installed-application": ("Open", "Open installed application", "Open an installed desktop launcher.", "application-x-executable-symbolic", "desktop-id", "Desktop application ID"),
    "command": ("Open", "Run command", "Launch a command as arguments without a shell.", "utilities-terminal-symbolic", "argv", "Command arguments"),
    "file": ("Open", "Open file", "Open a file with its default application.", "text-x-generic-symbolic", "path", "File path"),
    "folder": ("Open", "Open folder", "Open a folder with its default application.", "folder-symbolic", "path", "Folder path"),
    "url": ("Open", "Open URL", "Open an HTTP or HTTPS URL in the default browser.", "web-browser-symbolic", "url", "URL"),
    "shortcut": ("Custom", "Keyboard shortcut", "Send a keyboard shortcut to the focused application.", "input-keyboard-symbolic", "shortcut", "Key names"),
    "text": ("Custom", "Insert text", "Type text into the focused application.", "insert-text-symbolic", "text", "Text"),
}

_TEMPLATES.update({
    "switch-profile": ("Profiles", "Switch assignment profile", "Select a compatible named local profile for future invocations.", "preferences-system-symbolic", "profile", "Assignment profile"),
    "multi-toggle": ("Custom", "Multi-toggle", "Cycle 2–8 branches for this controller session; advance only after successful execution.", "view-refresh-symbolic", "toggle", "Branch step lists"),
    "sound": ("Utilities", "Play local sound", "Launch pw-play with a literal local audio file; reports launch, not confirmed playback.", "audio-x-generic-symbolic", "sound", "Absolute local audio path"),
})


def catalog_availability(preset_id):
    """Optional read-only hints, separate from pure templates and executable schema."""
    if preset_id not in _PRESETS.keys() | _TEMPLATES.keys():
        raise RuntimeError("Unknown action catalog preset.")
    if preset_id.startswith("clipboard-"):
        available = bool(shutil.which("wl-copy") and (preset_id == "clipboard-clear" or shutil.which("wl-paste")))
        return {"available": available, "reason": "Requires wl-clipboard and a compatible Wayland clipboard service."}
    if preset_id.startswith("pointer-") or preset_id in ("adjustable-horizontal-scroll", "adjustable-vertical-scroll"):
        from .pointer import pointer_availability
        return pointer_availability()
    if preset_id == "sound":
        return {"available": bool(shutil.which("pw-play")), "reason": "Requires pw-play and a local audio file; playback depends on the audio service."}
    return {"available": True, "reason": "Desktop input, focus, application support or the native controller may be required at execution."}


def catalog_entries():
    """Return detached searchable metadata; parameter types describe required values."""
    entries = []
    for preset_id, data in {**_PRESETS, **_TEMPLATES}.items():
        category, label, description, icon = data[:4]
        entry = {"id": preset_id, "category": category, "label": label, "description": description, "icon": icon}
        if preset_id in _TEMPLATES:
            entry["parameter"] = {"type": data[4], "label": data[5]}
        entries.append(entry)
    return entries


def _url(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isprintable()
            or any(char.isspace() for char in value) or "\\" in value):
        raise RuntimeError("A URL must be printable HTTP or HTTPS text without whitespace or control characters.")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host:
            raise ValueError("missing HTTP or HTTPS hostname")
        parsed.port  # Validate an optional port as well as the hostname.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            encoded = host.encode("idna").decode("ascii").removesuffix(".")
            if (len(encoded) > 253 or not encoded
                    or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                           for label in encoded.split("."))
                    or all(char in "0123456789." for char in encoded)):
                raise ValueError("invalid hostname")
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"A URL needs a valid HTTP or HTTPS hostname: {exc}.") from exc
    return value


def catalog_action(preset_id, value=None, *, config=None):
    """Create a validated detached action, never discovering or launching applications."""
    if not isinstance(preset_id, str) or preset_id not in _PRESETS.keys() | _TEMPLATES.keys():
        raise RuntimeError(f"Unknown action catalog preset {preset_id!r}.")
    data = _PRESETS.get(preset_id) or _TEMPLATES[preset_id]
    action = {"label": data[1], "icon": data[3]}
    if preset_id in _PRESETS:
        if value is not None:
            raise RuntimeError(f"Catalog preset {preset_id!r} does not take a parameter.")
        action["steps"] = deepcopy(data[4])
        if preset_id in _ADJUSTMENTS:
            action["adjustment"] = deepcopy(_ADJUSTMENTS[preset_id])
    elif preset_id in ("application", "command"):
        action["steps"] = [{"type": "command", "argv": deepcopy(value)}]
    elif preset_id == "installed-application":
        action["steps"] = [{"type": "application", "desktop_id": value}]
    elif preset_id in ("file", "folder"):
        if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isprintable() or not value.strip():
            raise RuntimeError("An explicit nonempty file or folder path is required.")
        # Prefix relative paths so xdg-open cannot interpret options or URI schemes.
        path = value if value.startswith("/") else "./" + value
        action["steps"] = [{"type": "command", "argv": ["xdg-open", path]}]
    elif preset_id == "url":
        action["steps"] = [{"type": "command", "argv": ["xdg-open", _url(value)]}]
    elif preset_id == "shortcut":
        action["steps"] = [{"type": "shortcut", "keys": deepcopy(value)}]
    elif preset_id == "text":
        action["steps"] = [{"type": "text", "text": value}]
    elif preset_id == "switch-profile":
        if config is None:
            raise RuntimeError("Switch profile requires the current assignment configuration.")
        action["steps"] = [{"type": "local", "operation": "switch-profile", "profile_id": value}]
    elif preset_id == "multi-toggle":
        if not isinstance(value, list) or not 2 <= len(value) <= 8:
            raise RuntimeError("Multi-toggle needs 2–8 branch step lists.")
        action["steps"], action["toggle_steps"] = deepcopy(value[0]), deepcopy(value[1:])
    elif preset_id == "sound":
        if not isinstance(value, str) or not value.startswith("/") or not value.isprintable() or len(value) > 4096:
            raise RuntimeError("Sound requires an absolute local audio file path.")
        action["steps"] = [{"type": "command", "argv": ["pw-play", "--", value]}]
    document = deepcopy(config) if config is not None else {
        "version": 1, "actions": {}, "ring": ["catalog"] * 8,
        "button_bindings": {}, "ring_button": 416, "haptic_feedback": True}
    document["actions"]["catalog"] = action
    validate_config(document)
    return action
