"""Pure Linux action templates using the executor's existing step primitives."""

from copy import deepcopy
import ipaddress
import re
from urllib.parse import urlsplit

from .actions import validate_config


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
_ADJUSTMENTS = {
    "adjustable-volume": {"increase": _shortcut("VOLUMEUP"), "decrease": _shortcut("VOLUMEDOWN")},
    "adjustable-zoom": {"increase": _shortcut("CTRL", "EQUAL"), "decrease": _shortcut("CTRL", "MINUS")},
    "adjustable-brightness": {"increase": _shortcut("BRIGHTNESSUP"), "decrease": _shortcut("BRIGHTNESSDOWN")},
}
_TEMPLATES = {
    "application": ("Open", "Open application", "Launch an application with explicit arguments.", "application-x-executable-symbolic", "argv", "Application arguments"),
    "command": ("Open", "Run command", "Launch a command as arguments without a shell.", "utilities-terminal-symbolic", "argv", "Command arguments"),
    "file": ("Open", "Open file", "Open a file with its default application.", "text-x-generic-symbolic", "path", "File path"),
    "folder": ("Open", "Open folder", "Open a folder with its default application.", "folder-symbolic", "path", "Folder path"),
    "url": ("Open", "Open URL", "Open an HTTP or HTTPS URL in the default browser.", "web-browser-symbolic", "url", "URL"),
    "shortcut": ("Custom", "Keyboard shortcut", "Send a keyboard shortcut to the focused application.", "input-keyboard-symbolic", "shortcut", "Key names"),
    "text": ("Custom", "Insert text", "Type text into the focused application.", "insert-text-symbolic", "text", "Text"),
}


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


def catalog_action(preset_id, value=None):
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
    elif preset_id in ("file", "folder"):
        if not isinstance(value, str) or not 1 <= len(value) <= 4096 or not value.isprintable() or not value.strip():
            raise RuntimeError("An explicit nonempty file or folder path is required.")
        # xdg-open treats leading hyphens as options unless the path is prefixed.
        path = "./" + value if value.startswith("-") else value
        action["steps"] = [{"type": "command", "argv": ["xdg-open", path]}]
    elif preset_id == "url":
        action["steps"] = [{"type": "command", "argv": ["xdg-open", _url(value)]}]
    elif preset_id == "shortcut":
        action["steps"] = [{"type": "shortcut", "keys": deepcopy(value)}]
    elif preset_id == "text":
        action["steps"] = [{"type": "text", "text": value}]
    validate_config({"version": 1, "actions": {"catalog": action}, "ring": ["catalog"] * 8,
                     "button_bindings": {}, "ring_button": 416, "haptic_feedback": True})
    return action
