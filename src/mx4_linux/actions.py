"""Local Smart Actions, stored as explicit steps and executed without a shell.

Desktop integration uses niri's public IPC command line. On compatible Wayland
desktops, wtype sends keyboard shortcuts and Unicode text without a privileged
input device. Other keyboards use a lazily created uinput device. Importing or
previewing an action never creates an input device. Command steps launch
applications and return after starting them, so an editor can stay open.
"""

from copy import deepcopy
from collections.abc import MutableMapping

import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .action_utilities import DATE_FORMATS, CLIPBOARD_OPERATIONS, clipboard_operation, date_text


MAX_CONFIG_BYTES = 1024 * 1024
MAX_ACTIONS = 128
MAX_STEPS = 64
MAX_GROUP_ACTIONS = 6
MAX_ASSIGNMENT_PROFILES = 64
MAX_DELAY_MS = 30_000
MAX_TOTAL_DELAY_MS = 60_000
MAX_RUN_SECONDS = 120
BUTTON_IDS = frozenset({82, 83, 86, 195, 196, 416})
ACTION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
THEME_ICON = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
SHOW_RING = object()  # UI-only choice; never persisted as an action ID.
HAPTIC_SOURCES = frozenset({"open", "hover", "select", "complete"})
NIRI_ACTION = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")


def _terminal_argv():
    try:
        preferred = shlex.split(os.environ.get("TERMINAL", ""))
    except ValueError:
        preferred = []
    if _argv(preferred) and shutil.which(preferred[0]):
        return preferred
    for executable in ("kitty", "foot", "alacritty", "gnome-terminal", "konsole", "x-terminal-emulator", "xterm"):
        if shutil.which(executable):
            return [executable]
    return ["x-terminal-emulator"]


def default_config():
    """Return a fresh configuration, including eight useful ring assignments."""
    actions = {
        "overview": {"label": "Overview", "steps": [{"type": "niri", "action": "toggle-overview", "args": []}]},
        "screenshot": {"label": "Screenshot", "steps": [{"type": "niri", "action": "screenshot", "args": []}]},
        "terminal": {"label": "Terminal", "steps": [{"type": "command", "argv": _terminal_argv()}]},
        "workspace-next": {"label": "Next workspace", "steps": [{"type": "niri", "action": "focus-workspace-down", "args": []}]},
        "paste": {"label": "Paste", "steps": [{"type": "shortcut", "keys": ["CTRL", "V"]}]},
        "copy": {"label": "Copy", "steps": [{"type": "shortcut", "keys": ["CTRL", "C"]}]},
        "undo": {"label": "Undo", "steps": [{"type": "shortcut", "keys": ["CTRL", "Z"]}]},
        "workspace-previous": {"label": "Previous workspace", "steps": [{"type": "niri", "action": "focus-workspace-up", "args": []}]},
    }
    return {"version": 1, "ring": list(actions), "actions": actions,
            "button_bindings": {}, "ring_button": 416, "haptic_feedback": True}


def _fail(message):
    raise RuntimeError(f"Invalid desktop configuration: {message}")


def _fields(value, required, optional=(), location="entry"):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        _fail(f"{location} requires {', '.join(required)}.")
    extra = value.keys() - set(required) - set(optional)
    if extra:
        _fail(f"unknown field in {location}: {', '.join(sorted(map(str, extra)))}.")


def _plain_string(value, minimum=1, maximum=256):
    return isinstance(value, str) and minimum <= len(value) <= maximum and value.isprintable()


def _argv(value, allow_empty=False):
    return (isinstance(value, list) and (allow_empty or bool(value)) and len(value) <= 64
            and all(isinstance(part, str) and len(part) <= 4096 and "\0" not in part for part in value)
            and (allow_empty or bool(value[0].strip())))


def _key_codes(keys):
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8 or any(not isinstance(k, str) for k in keys):
        raise RuntimeError("A shortcut needs 1–8 key names, such as [\"CTRL\", \"C\"].")
    try:
        from evdev import ecodes
    except ImportError as exc:
        raise RuntimeError("Keyboard shortcuts require python-evdev.") from exc
    aliases = {"CTRL": "LEFTCTRL", "CONTROL": "LEFTCTRL", "SHIFT": "LEFTSHIFT", "ALT": "LEFTALT",
               "SUPER": "LEFTMETA", "META": "LEFTMETA", "WIN": "LEFTMETA", "ESC": "ESC",
               "RETURN": "ENTER", "PAGEUP": "PAGEUP", "PAGEDOWN": "PAGEDOWN", "PGUP": "PAGEUP",
               "PGDN": "PAGEDOWN", "DEL": "DELETE"}
    result = []
    for key in keys:
        name = key.upper().removeprefix("KEY_")
        name = "KEY_" + aliases.get(name, name)
        code = getattr(ecodes, name, None)
        if not isinstance(code, int) or name in {"KEY_RESERVED", "KEY_MAX", "KEY_CNT", "KEY_UNKNOWN"}:
            raise RuntimeError(f"Unknown shortcut key {key!r}; use Linux KEY_* names or CTRL, ALT, SHIFT, SUPER.")
        if code in result:
            raise RuntimeError("A shortcut cannot contain the same key twice.")
        result.append(code)
    return result


def _native_wtype():
    """Find the optional Wayland helper, including the local setup's prefix."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        return None
    executable = shutil.which("wtype")
    if executable:
        return executable
    local = Path(sys.executable).parent / "wtype"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def _wtype_shortcut_args(codes):
    """Map a validated Linux key chord, or return None for the uinput path.

    Map every key before emitting any events. A helper failure must never retry
    through uinput because an application may already have received the chord.
    """
    from evdev import ecodes

    modifiers = {
        ecodes.KEY_LEFTCTRL: "ctrl", ecodes.KEY_RIGHTCTRL: "ctrl",
        ecodes.KEY_LEFTSHIFT: "shift", ecodes.KEY_RIGHTSHIFT: "shift",
        ecodes.KEY_LEFTALT: "alt", ecodes.KEY_RIGHTALT: "altgr",
        ecodes.KEY_LEFTMETA: "logo", ecodes.KEY_RIGHTMETA: "logo",
    }
    names = {
        "ENTER": "Return", "ESC": "Escape", "BACKSPACE": "BackSpace",
        "TAB": "Tab", "SPACE": "space", "LEFT": "Left", "RIGHT": "Right",
        "UP": "Up", "DOWN": "Down", "HOME": "Home", "END": "End",
        "PAGEUP": "Prior", "PAGEDOWN": "Next", "INSERT": "Insert", "DELETE": "Delete",
        "CAPSLOCK": "Caps_Lock", "NUMLOCK": "Num_Lock", "SCROLLLOCK": "Scroll_Lock",
        "PAUSE": "Pause", "SYSRQ": "Print", "COMPOSE": "Multi_key", "MENU": "Menu",
        "MINUS": "minus", "EQUAL": "equal", "LEFTBRACE": "bracketleft",
        "RIGHTBRACE": "bracketright", "BACKSLASH": "backslash", "SEMICOLON": "semicolon",
        "APOSTROPHE": "apostrophe", "GRAVE": "grave", "COMMA": "comma", "DOT": "period",
        "SLASH": "slash", "KPASTERISK": "KP_Multiply", "KPMINUS": "KP_Subtract",
        "KPPLUS": "KP_Add", "KPDOT": "KP_Decimal", "KPSLASH": "KP_Divide",
        "KPENTER": "KP_Enter", "KPEQUAL": "KP_Equal", "KPCOMMA": "KP_Separator",
        "MUTE": "XF86AudioMute", "VOLUMEDOWN": "XF86AudioLowerVolume",
        "VOLUMEUP": "XF86AudioRaiseVolume", "PLAYPAUSE": "XF86AudioPlay",
        "STOPCD": "XF86AudioStop", "PREVIOUSSONG": "XF86AudioPrev", "NEXTSONG": "XF86AudioNext",
        "BRIGHTNESSDOWN": "XF86MonBrightnessDown", "BRIGHTNESSUP": "XF86MonBrightnessUp",
    }
    names.update({chr(letter): chr(letter).lower() for letter in range(ord("A"), ord("Z") + 1)})
    names.update({str(number): str(number) for number in range(10)})
    names.update({f"F{number}": f"F{number}" for number in range(1, 25)})
    names.update({f"KP{number}": f"KP_{number}" for number in range(10)})
    keysyms = {getattr(ecodes, f"KEY_{name}"): symbol for name, symbol in names.items()
               if hasattr(ecodes, f"KEY_{name}")}
    chord = []
    for code in codes:
        if code in modifiers:
            chord.append(("-M", "-m", modifiers[code]))
        elif code in keysyms:
            chord.append(("-P", "-p", keysyms[code]))
        else:
            return None
    argv = []
    for press, _release, name in chord:
        argv.extend((press, name))
    argv.extend(("-s", "25"))
    for _press, release, name in reversed(chord):
        argv.extend((release, name))
    return argv


def _validate_steps(steps, action_id):
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        _fail(f"action {action_id} needs 1–{MAX_STEPS} steps.")
    delay = 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("type"), str):
            _fail(f"step {index} of {action_id} needs a type.")
        kind = step["type"]
        location = f"{kind} step {index} of {action_id}"
        if kind == "niri":
            _fields(step, ("type", "action"), ("args",), location)
            if (not isinstance(step["action"], str) or not NIRI_ACTION.fullmatch(step["action"])
                    or step["action"] in {"spawn", "spawn-sh"}):
                _fail(f"{location} needs a niri action name; use a command step to launch applications.")
            if not _argv(step.get("args", []), allow_empty=True):
                _fail(f"{location} args must be an array of strings.")
        elif kind == "command":
            _fields(step, ("type", "argv"), location=location)
            if not _argv(step["argv"]):
                _fail(f"{location} argv must be a nonempty array of at most 64 arguments.")
        elif kind == "shortcut":
            _fields(step, ("type", "keys"), location=location)
            try:
                _key_codes(step["keys"])
            except RuntimeError as exc:
                _fail(f"{location}: {exc}")
        elif kind == "text":
            _fields(step, ("type", "text"), location=location)
            if not isinstance(step["text"], str) or not 1 <= len(step["text"]) <= 65_536 or "\0" in step["text"]:
                _fail(f"{location} needs 1–65536 characters without NUL bytes.")
        elif kind == "date":
            _fields(step, ("type", "format"), location=location)
            if not isinstance(step["format"], str) or step["format"] not in DATE_FORMATS:
                _fail(f"{location} needs a supported date format.")
        elif kind == "clipboard":
            _fields(step, ("type", "operation"), location=location)
            if not isinstance(step["operation"], str) or step["operation"] not in CLIPBOARD_OPERATIONS:
                _fail(f"{location} needs a supported clipboard operation.")
        elif kind == "local":
            operation = step.get("operation")
            if operation == "switch-profile":
                _fields(step, ("type", "operation", "profile_id"), location=location)
                if not isinstance(step["profile_id"], str) or not ACTION_ID.fullmatch(step["profile_id"]):
                    _fail(f"{location} needs a profile ID.")
            elif operation == "adapt-to-app":
                _fields(step, ("type", "operation", "enabled"), location=location)
                if type(step["enabled"]) is not bool:
                    _fail(f"{location} enabled must be true or false.")
            elif isinstance(operation, str) and operation in ("stopwatch", "tap-tempo", "bell"):
                _fields(step, ("type", "operation"), location=location)
            else:
                _fail(f"{location} needs a supported local operation.")
        elif kind == "delay":
            _fields(step, ("type", "milliseconds"), location=location)
            value = step["milliseconds"]
            if type(value) is not int or not 0 <= value <= MAX_DELAY_MS:
                _fail(f"{location} must be 0–{MAX_DELAY_MS} milliseconds.")
            delay += value
        else:
            _fail(f"unsupported step type {kind!r}.")
    if delay > MAX_TOTAL_DELAY_MS:
        _fail(f"action {action_id} has more than {MAX_TOTAL_DELAY_MS} milliseconds of delays.")


def action_step_lists(action):
    """Iterate every executable branch, including session-only toggle choices."""
    yield action["steps"]
    yield from action.get("adjustment", {}).values()
    yield from action.get("toggle_steps", [])


def profile_references(action):
    return {step["profile_id"] for steps in action_step_lists(action) for step in steps
            if step["type"] == "local" and step["operation"] == "switch-profile"}


def steps_send_input(steps):
    return any(step["type"] in ("shortcut", "text", "date") for step in steps)


def execution_steps(action, adjustment=None, toggle_state=None, action_id=None):
    if adjustment is not None:
        if not isinstance(adjustment, str) or adjustment not in ("increase", "decrease"):
            raise RuntimeError("An adjustment must be increase or decrease.")
        if "adjustment" not in action:
            raise RuntimeError(f"Smart Action {action_id!r} does not support adjustment.")
        return action["adjustment"][adjustment]
    if "toggle_steps" in action and toggle_state is not None:
        definition, index = toggle_state.get(action_id, (None, 0))
        index = index if definition == action else 0
        return [action["steps"], *action["toggle_steps"]][index]
    return action["steps"]


class ToggleState(MutableMapping):
    """Controller-session entries; compound selection/reset/commit uses this same lock."""
    def __init__(self, entries=None):
        self.lock = threading.RLock()
        self._entries = dict(entries or {})

    def __getitem__(self, key):
        with self.lock:
            return self._entries[key]

    def __setitem__(self, key, value):
        with self.lock:
            self._entries[key] = value

    def __delitem__(self, key):
        with self.lock:
            del self._entries[key]

    def __iter__(self):
        with self.lock:
            return iter(tuple(self._entries))

    def __len__(self):
        with self.lock:
            return len(self._entries)


class ExecutionSelection:
    """One detached branch and its session-cycle commit token for an invocation."""
    def __init__(self, action_id, action, adjustment=None, toggle_state=None):
        self.action_id = action_id
        self.action = deepcopy(action)
        self.adjustment = adjustment
        state = toggle_state if toggle_state is not None else {}
        self.toggle_entry = state.get(action_id)
        captured = {action_id: self.toggle_entry} if self.toggle_entry is not None else {}
        self.steps = deepcopy(execution_steps(self.action, adjustment, captured, action_id))
        definition, index = self.toggle_entry if self.toggle_entry is not None else (None, 0)
        self.toggle_index = index if definition == self.action else 0
        self.current = True

    def may_advance(self, config, toggle_state):
        return (self.current and config["actions"].get(self.action_id) == self.action
                and toggle_state.get(self.action_id) is self.toggle_entry)


def _validate_trigger(value):
    if value is not None and (type(value) is not int or value not in BUTTON_IDS):
        _fail("ring_button must be null or one of 82, 83, 86, 195, 196 or 416.")


def _validate_assignments(config, actions, ring_button):
    ring = config["ring"]
    if not isinstance(ring, list) or len(ring) != 8 or any(not isinstance(value, str) or value not in actions for value in ring):
        _fail("ring must contain exactly eight existing action IDs.")
    groups = config.get("ring_groups", {})
    if not isinstance(groups, dict):
        _fail("ring_groups must map ring positions 0–7 to lists of action IDs.")
    for position, children in groups.items():
        if not isinstance(position, str) or position not in {str(index) for index in range(8)}:
            _fail("ring_groups positions must be decimal strings from 0 to 7.")
        if (not isinstance(children, list) or not 1 <= len(children) <= MAX_GROUP_ACTIONS
                or any(not isinstance(child, str) or child not in actions for child in children)):
            _fail(f"ring group {position} needs 1–{MAX_GROUP_ACTIONS} existing action IDs.")
        if len(set(children)) != len(children):
            _fail(f"ring group {position} cannot repeat an action ID.")
    bindings = config["button_bindings"]
    if not isinstance(bindings, dict):
        _fail("button_bindings must map decimal button IDs to action IDs.")
    allowed = {str(cid) for cid in BUTTON_IDS - {ring_button}}
    for button, action_id in bindings.items():
        if button not in allowed or not isinstance(action_id, str) or action_id not in actions:
            _fail("button_bindings must use an existing action and a supported button distinct from ring_button.")


def validate_config(config):
    """Validate the entire document before it is saved or any action is run."""
    _fields(config, ("version", "ring", "actions", "button_bindings", "ring_button", "haptic_feedback"),
            ("ring_groups", "assignment_profiles", "active_profiles", "ring_scale", "ring_motion_gain",
             "ring_deadzone", "haptic_events", "haptic_waveforms", "haptic_power_save", "appearance",
             "autostart", "background", "resume_integration", "adapt_to_app"), location="document")
    if type(config["version"]) is not int or config["version"] != 1:
        _fail("only version 1 is supported.")
    actions = config["actions"]
    if not isinstance(actions, dict) or not 1 <= len(actions) <= MAX_ACTIONS:
        _fail(f"actions must contain 1–{MAX_ACTIONS} entries.")
    for action_id, action in actions.items():
        if not isinstance(action_id, str) or not ACTION_ID.fullmatch(action_id):
            _fail("action IDs must be 1–64 lowercase letters, numbers, hyphens or underscores.")
        _fields(action, ("label", "steps"), ("app_id", "icon", "adjustment", "toggle_steps"), f"action {action_id}")
        if not _plain_string(action["label"], maximum=80):
            _fail(f"action {action_id} needs a label of 1–80 printable characters.")
        if "app_id" in action and not _plain_string(action["app_id"]):
            _fail(f"action {action_id} needs a nonempty exact app_id condition.")
        if "icon" in action and (not isinstance(action["icon"], str) or not THEME_ICON.fullmatch(action["icon"])):
            _fail(f"action {action_id} needs a theme icon name of 1–128 letters, digits, dots, hyphens or underscores.")
        if "toggle_steps" in action and (not isinstance(action["toggle_steps"], list)
                or not 1 <= len(action["toggle_steps"]) <= 7):
            _fail(f"action {action_id} needs 1–7 extra toggle step lists.")
        if "adjustment" in action:
            _fields(action["adjustment"], ("increase", "decrease"), location=f"adjustment of {action_id}")
        for steps in action_step_lists(action):
            _validate_steps(steps, action_id)
        for reference in profile_references(action):
            if (not isinstance(config.get("assignment_profiles", {}), dict)
                    or reference not in config.get("assignment_profiles", {})):
                _fail(f"action {action_id} references a missing assignment profile.")
    ring_button = config["ring_button"]
    _validate_trigger(ring_button)
    _validate_assignments(config, actions, ring_button)
    for flag in ("haptic_feedback", "haptic_power_save", "autostart", "background", "resume_integration", "adapt_to_app"):
        if flag in config and type(config[flag]) is not bool:
            _fail(f"{flag} must be true or false.")
    if "ring_scale" in config and (type(config["ring_scale"]) is not int or not 75 <= config["ring_scale"] <= 150):
        _fail("ring_scale must be an integer from 75 to 150.")
    for field, minimum, maximum in (("ring_motion_gain", 0.1, 2.0), ("ring_deadzone", 8, 80)):
        if field in config:
            value = config[field]
            if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
                _fail(f"{field} must be a finite number from {minimum} to {maximum}.")
    if "appearance" in config and config["appearance"] not in ("system", "light", "dark"):
        _fail("appearance must be system, light or dark.")
    for field in ("haptic_events", "haptic_waveforms"):
        if field not in config:
            continue
        values = config[field]
        if not isinstance(values, dict) or values.keys() - HAPTIC_SOURCES:
            _fail(f"{field} must map open, hover, select or complete to values.")
        for value in values.values():
            if field == "haptic_events" and type(value) is not bool:
                _fail("haptic_events values must be true or false.")
            if field == "haptic_waveforms" and (type(value) is not int or not 0 <= value <= 255):
                _fail("haptic_waveforms values must be integers from 0 to 255.")
    if "assignment_profiles" in config:
        profiles = config["assignment_profiles"]
        if not isinstance(profiles, dict) or len(profiles) > MAX_ASSIGNMENT_PROFILES:
            _fail(f"assignment_profiles must contain at most {MAX_ASSIGNMENT_PROFILES} profiles.")
        for profile_id, profile in profiles.items():
            if not isinstance(profile_id, str) or not ACTION_ID.fullmatch(profile_id):
                _fail("assignment profile IDs must use the action ID syntax.")
            _fields(profile, ("label", "app_id", "ring", "button_bindings"), ("ring_groups", "ring_button"), f"profile {profile_id}")
            if not _plain_string(profile["label"], maximum=80) or not _plain_string(profile["app_id"]):
                _fail(f"profile {profile_id} needs a printable label of 1–80 and app_id of 1–256 characters.")
            _validate_trigger(profile.get("ring_button", ring_button))
            _validate_assignments(profile, actions, profile.get("ring_button", ring_button))
    if "active_profiles" in config:
        if "assignment_profiles" not in config:
            _fail("active_profiles requires assignment_profiles.")
        active = config["active_profiles"]
        if not isinstance(active, dict) or len(active) > MAX_ASSIGNMENT_PROFILES:
            _fail(f"active_profiles must map at most {MAX_ASSIGNMENT_PROFILES} exact app IDs to profile IDs.")
        for app_id, profile_id in active.items():
            if (not _plain_string(app_id) or not isinstance(profile_id, str) or profile_id not in profiles
                    or profiles[profile_id]["app_id"] != app_id):
                _fail("active_profiles must reference an existing profile with the same exact app_id.")
    try:
        size = len(json.dumps(config, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail(f"document cannot be encoded: {exc}")
    if size > MAX_CONFIG_BYTES:
        _fail("document exceeds 1 MiB.")
    return config


def assignment_scope(config, app_id=None):
    """Select an explicit editing scope, independently of runtime adaptation."""
    active = config.get("active_profiles", {})
    profile_id = active.get(app_id) if isinstance(app_id, str) else None
    profile_id = profile_id or active.get("*")
    return config["assignment_profiles"][profile_id] if profile_id else config


def apply_local_assignment(document, step, app_id):
    """Mutate only an effective compatible assignment scope (caller holds the lock)."""
    if step["operation"] == "adapt-to-app":
        document["adapt_to_app"] = step["enabled"]
        return
    profile_id = step["profile_id"]
    profile = document.get("assignment_profiles", {}).get(profile_id)
    if profile is None:
        raise RuntimeError("The assignment profile no longer exists.")
    adapting = document.get("adapt_to_app", True)
    scope = profile["app_id"]
    exact_active = app_id in document.get("active_profiles", {})
    if not ((adapting and app_id and scope == app_id)
            or (scope == "*" and (not adapting or not exact_active))):
        raise RuntimeError("This assignment profile is incompatible with the originating application context.")
    document.setdefault("active_profiles", {})[scope] = profile_id


def set_button_assignment(document, cid, action_id, app_id=None, *, root=False):
    """Change one scope, validating the complete copy before replacing anything."""
    if type(cid) is not int or cid not in BUTTON_IDS:
        _fail("unsupported assignment button.")
    candidate = deepcopy(document)
    target = candidate if root else assignment_scope(candidate, app_id)
    trigger = target.get("ring_button", candidate["ring_button"])
    if action_id is SHOW_RING:
        target["ring_button"] = cid
        target["button_bindings"].pop(str(cid), None)
    else:
        if cid == trigger:
            target["ring_button"] = None
        if action_id is None:
            target["button_bindings"].pop(str(cid), None)
        else:
            target["button_bindings"][str(cid)] = action_id
    validate_config(candidate)
    document.clear()
    document.update(candidate)


def resolve_config(config, app_id=None, *, for_editing=False):
    """Return a detached document with the active exact or General assignments."""
    validate_config(config)
    resolved = deepcopy(config)
    profile = assignment_scope(resolved, app_id if for_editing or config.get("adapt_to_app", True) else None)
    if profile is not resolved:
        # Other profiles must still inherit the persisted root in this detached
        # complete document when its effective root takes a profile override.
        root_trigger = config["ring_button"]
        for value in resolved.get("assignment_profiles", {}).values():
            value.setdefault("ring_button", root_trigger)
        for field in ("ring", "button_bindings", "ring_button"):
            resolved[field] = deepcopy(profile[field])
        resolved.pop("ring_groups", None)
        if "ring_groups" in profile:
            resolved["ring_groups"] = deepcopy(profile["ring_groups"])
    return resolved


class DesktopConfigStore:
    def __init__(self, path=None):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(path) if path else root / "mx4-linux" / "desktop.json"

    def read(self):
        try:
            with self.path.open(encoding="utf-8") as stream:
                payload = stream.read(MAX_CONFIG_BYTES + 1)
            if len(payload.encode("utf-8")) > MAX_CONFIG_BYTES:
                raise RuntimeError("Desktop configuration exceeds 1 MiB.")
            return validate_config(json.loads(payload))
        except FileNotFoundError:
            return default_config()
        except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
            raise RuntimeError(f"Cannot read {self.path}: {exc}") from exc

    def save(self, config):
        config = deepcopy(config)
        validate_config(config)
        self._commit(config=config)

    def update(self, mutate):
        """Mutate the latest document under its file lock and return the saved copy.

        ``mutate(document)`` edits in place; its return value is ignored. An
        invalid mutation or unreadable document leaves the existing file intact.
        The mutator must not call save/update on the same configuration file.
        """
        if not callable(mutate):
            raise TypeError("Configuration mutator must be callable.")
        return self._commit(mutate=mutate)

    def _commit(self, *, config=None, mutate=None):
        filename = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if mutate is not None:
                    config = self.read()
                    mutate(config)
                    config = deepcopy(config)
                    validate_config(config)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                    filename = stream.name
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(config, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(filename, self.path)
            return config
        except OSError as exc:
            raise RuntimeError(f"Cannot save {self.path}: {exc}") from exc
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)


def _run(argv, *, timeout=5, input=None):
    try:
        return subprocess.run(argv, input=input, text=True, capture_output=True, timeout=timeout, check=True, shell=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{argv[0]} is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{argv[0]} did not respond within {timeout:g} seconds.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or f"exit code {exc.returncode}").strip()[:600]
        raise RuntimeError(f"{argv[0]} failed: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot run {argv[0]}: {exc}") from exc


def focused_context():
    """Return the focused niri app/window, or None when niri is unavailable."""
    try:
        value = json.loads(_run(["niri", "msg", "--json", "focused-window"], timeout=2).stdout)
        if not isinstance(value, dict) or type(value.get("id")) is not int:
            return None
        return {"app_id": value.get("app_id") or "", "window_id": value["id"]}
    except (RuntimeError, ValueError):
        return None


def restore_focus(window_id):
    """Restore a ring's original target, failing before typing if it is gone."""
    if type(window_id) is not int or window_id < 0:
        raise RuntimeError("A valid niri window ID is required to restore focus.")
    _run(["niri", "msg", "action", "focus-window", "--id", str(window_id)], timeout=2)
    context = focused_context()
    if not context or context["window_id"] != window_id:
        raise RuntimeError("The original window could not be focused; the action was stopped.")
    return context


class DesktopActions:
    def __init__(self, config=None, dry_run=False, *, local_handler=None, local_preflight=None, toggle_state=None):
        self.config = deepcopy(config) if config is not None else DesktopConfigStore().read()
        validate_config(self.config)
        self.dry_run = bool(dry_run)
        self.local_handler = local_handler
        self.local_preflight = local_preflight
        self.toggle_state = toggle_state if toggle_state is not None else ToggleState()
        self._toggle_lock = getattr(self.toggle_state, "lock", threading.RLock())
        self._keyboard = None
        self._lock = threading.RLock()

    def close(self):
        with self._lock:
            if self._keyboard is not None:
                self._keyboard.close()
                self._keyboard = None

    def execute(self, action_id, cancelled=None, adjustment=None, *, selection=None):
        with self._lock:
            validate_config(self.config)
            if not isinstance(action_id, str) or action_id not in self.config["actions"]:
                raise RuntimeError(f"Unknown Smart Action {action_id!r}.")
            action = deepcopy(self.config["actions"][action_id])
            if selection is None:
                with self._toggle_lock:
                    selection = ExecutionSelection(action_id, action, adjustment, self.toggle_state)
            elif (not isinstance(selection, ExecutionSelection) or selection.action_id != action_id
                  or selection.action != action or selection.adjustment != adjustment):
                raise RuntimeError("The execution selection does not match this Smart Action snapshot.")
            steps = selection.steps
            result = {"ok": True, "action_id": action_id, "label": action["label"], "completed": 0,
                      "dry_run": self.dry_run, "steps": []}
            if adjustment is not None:
                result["adjustment"] = adjustment
            if self.dry_run:
                result["steps"] = deepcopy(steps)
                if "app_id" in action:
                    result["condition"] = {"app_id": action["app_id"], "evaluated": False}
                return result
            deadline = time.monotonic() + MAX_RUN_SECONDS
            try:
                self._check(cancelled, deadline)
                if "app_id" in action:
                    context = focused_context()
                    if context is None or context["app_id"] != action["app_id"]:
                        result.update(ok=False, skipped=True, reason=f"Requires focused app_id {action['app_id']!r}.")
                        return result
                if any(step["type"] == "local" for step in steps):
                    if self.local_handler is None:
                        raise RuntimeError("Local utilities require the native controller.")
                    if self.local_preflight:
                        self.local_preflight(steps)
                        self._check(cancelled, deadline)
                for step in steps:
                    self._check(cancelled, deadline)
                    performed = self._step(step, cancelled, deadline)
                    result["steps"].append(performed)
                    result["completed"] += 1
                self._check(cancelled, deadline)
                if adjustment is None:
                    next_entry = ((deepcopy(action), (selection.toggle_index + 1) % (1 + len(action["toggle_steps"])))
                                  if "toggle_steps" in action else None)
                    with self._toggle_lock:
                        if selection.may_advance(self.config, self.toggle_state):
                            if next_entry is None:
                                self.toggle_state.pop(action_id, None)
                            else:
                                self.toggle_state[action_id] = next_entry
                                # The validated document bounds this controller-session map.
                                for removed in self.toggle_state.keys() - self.config["actions"].keys():
                                    del self.toggle_state[removed]
                return result
            except (RuntimeError, OSError) as exc:
                raise RuntimeError(f"Smart Action {action['label']!r} stopped after {result['completed']}/{len(steps)} steps: {exc}") from exc

    @staticmethod
    def _check(cancelled, deadline):
        if cancelled and cancelled():
            raise RuntimeError("cancelled.")
        if time.monotonic() >= deadline:
            raise RuntimeError("the action exceeded its two-minute time limit.")

    def _delay(self, seconds, cancelled, deadline):
        finish = time.monotonic() + seconds
        while time.monotonic() < finish:
            self._check(cancelled, deadline)
            time.sleep(min(0.05, max(0, finish - time.monotonic())))
        self._check(cancelled, deadline)

    def _shortcut(self, keys, cancelled, deadline):
        codes = _key_codes(keys)
        native = _native_wtype()
        native_args = _wtype_shortcut_args(codes) if native else None
        if native_args is not None:
            self._check(cancelled, deadline)
            _run([native, *native_args], timeout=min(5, max(0.1, deadline - time.monotonic())))
            self._check(cancelled, deadline)
            return
        from evdev import UInput, ecodes
        if self._keyboard is None:
            try:
                capabilities = sorted({code for name, code in vars(ecodes).items()
                                       if name.startswith("KEY_") and isinstance(code, int) and 0 < code < ecodes.KEY_MAX})
                self._keyboard = UInput({ecodes.EV_KEY: capabilities}, name="MX4 Linux Smart Actions Keyboard")
            except OSError as exc:
                raise RuntimeError("Keyboard shortcuts need wtype on a supported Wayland desktop or access to /dev/uinput. "
                                   "Run scripts/setup-wtype.sh (or install wtype), or install the bundled input-access rule and refresh the app.") from exc
            # A new keyboard needs to reach the compositor before its first key.
            self._delay(0.2, cancelled, deadline)
        pressed = []
        try:
            for code in codes:
                self._check(cancelled, deadline)
                pressed.append(code)
                self._keyboard.write(ecodes.EV_KEY, code, 1)
                self._keyboard.syn()
            self._delay(0.025, cancelled, deadline)
        finally:
            failures = []
            for code in reversed(pressed):
                try:
                    self._keyboard.write(ecodes.EV_KEY, code, 0)
                    self._keyboard.syn()
                except OSError as exc:
                    failures.append(exc)
            if failures:
                self.close()  # Removing the virtual device clears any stuck keys.
                raise RuntimeError("The virtual keyboard disconnected while releasing keys.") from failures[0]

    def _step(self, step, cancelled, deadline):
        kind = step["type"]
        result = {"type": kind}
        if kind == "niri":
            _run(["niri", "msg", "action", step["action"], *step.get("args", [])], timeout=min(5, max(0.1, deadline - time.monotonic())))
        elif kind == "command":
            try:
                process = subprocess.Popen(step["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True, shell=False)
            except OSError as exc:
                raise RuntimeError(f"Cannot launch {step['argv'][0]}: {exc}") from exc
            # Reap a launched app when it exits; a long-lived terminal must not
            # block the next macro step or leave zombies in the GUI process.
            threading.Thread(target=process.wait, name="mx4-action-child", daemon=True).start()
            result["pid"] = process.pid
        elif kind == "shortcut":
            self._shortcut(step["keys"], cancelled, deadline)
        elif kind == "text":
            self._text(step["text"], cancelled, deadline)
        elif kind == "date":
            try:
                self._text(date_text(step["format"]), cancelled, deadline)
            except (RuntimeError, OSError):
                raise RuntimeError("Date text could not be inserted; input may be partial.") from None
        elif kind == "clipboard":
            clipboard_operation(step["operation"], lambda: self._check(cancelled, deadline))
        elif kind == "local":
            if self.local_handler is None:
                raise RuntimeError("Local utilities require the native controller.")
            self._check(cancelled, deadline)
            self.local_handler(deepcopy(step), lambda: self._check(cancelled, deadline))
            result["operation"] = step["operation"]
            if step["operation"] == "bell":
                result["requested"] = True
        elif kind == "delay":
            self._delay(step["milliseconds"] / 1000, cancelled, deadline)
        return result

    def _text(self, value, cancelled, deadline):
        native = _native_wtype()
        if native:
            # Small chunks bound cancellation latency and avoid the kernel's
            # per-argument byte limit for long Unicode text. Each helper's
            # virtual keyboard is released when the process exits.
            for start in range(0, len(value), 256):
                self._check(cancelled, deadline)
                _run([native, "--", value[start:start + 256]],
                     timeout=min(5, max(0.1, deadline - time.monotonic())))
            self._check(cancelled, deadline)
        else:
            _run(["wl-copy", "--type", "text/plain;charset=utf-8"], timeout=3, input=value)
            self._check(cancelled, deadline)
            self._shortcut(["CTRL", "V"], cancelled, deadline)
