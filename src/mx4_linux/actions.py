"""Local Smart Actions, stored as explicit steps and executed without a shell.

Desktop integration uses niri's public IPC command line. On compatible Wayland
desktops, wtype sends keyboard shortcuts and Unicode text without a privileged
input device. Other keyboards use a lazily created uinput device. Importing or
previewing an action never creates an input device. Command steps launch
applications and return after starting them, so an editor can stay open.
"""

from copy import deepcopy
import fcntl
import json
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


MAX_CONFIG_BYTES = 1024 * 1024
MAX_ACTIONS = 128
MAX_STEPS = 64
MAX_GROUP_ACTIONS = 6
MAX_DELAY_MS = 30_000
MAX_TOTAL_DELAY_MS = 60_000
MAX_RUN_SECONDS = 120
BUTTON_IDS = frozenset({82, 83, 86, 195, 196, 416})
ACTION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
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


def validate_config(config):
    """Validate the entire document before it is saved or any action is run."""
    _fields(config, ("version", "ring", "actions", "button_bindings", "ring_button", "haptic_feedback"),
            ("ring_groups",), location="document")
    if type(config["version"]) is not int or config["version"] != 1:
        _fail("only version 1 is supported.")
    actions = config["actions"]
    if not isinstance(actions, dict) or not 1 <= len(actions) <= MAX_ACTIONS:
        _fail(f"actions must contain 1–{MAX_ACTIONS} entries.")
    for action_id, action in actions.items():
        if not isinstance(action_id, str) or not ACTION_ID.fullmatch(action_id):
            _fail("action IDs must be 1–64 lowercase letters, numbers, hyphens or underscores.")
        _fields(action, ("label", "steps"), ("app_id",), f"action {action_id}")
        if not _plain_string(action["label"], maximum=80):
            _fail(f"action {action_id} needs a label of 1–80 printable characters.")
        if "app_id" in action and not _plain_string(action["app_id"]):
            _fail(f"action {action_id} needs a nonempty exact app_id condition.")
        steps = action["steps"]
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
    ring_button = config["ring_button"]
    if type(ring_button) is not int or ring_button not in BUTTON_IDS:
        _fail("ring_button must be one of 82, 83, 86, 195, 196 or 416.")
    if type(config["haptic_feedback"]) is not bool:
        _fail("haptic_feedback must be true or false.")
    bindings = config["button_bindings"]
    if not isinstance(bindings, dict):
        _fail("button_bindings must map decimal button IDs to action IDs.")
    allowed = {str(cid) for cid in BUTTON_IDS - {ring_button}}
    for button, action_id in bindings.items():
        if button not in allowed or not isinstance(action_id, str) or action_id not in actions:
            _fail("button_bindings must use an existing action and a supported button distinct from ring_button.")
    try:
        size = len(json.dumps(config, ensure_ascii=False).encode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        _fail(f"document cannot be encoded: {exc}")
    if size > MAX_CONFIG_BYTES:
        _fail("document exceeds 1 MiB.")
    return config


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
        filename = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                    filename = stream.name
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(config, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(filename, self.path)
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
    def __init__(self, config=None, dry_run=False):
        self.config = deepcopy(config) if config is not None else DesktopConfigStore().read()
        validate_config(self.config)
        self.dry_run = bool(dry_run)
        self._keyboard = None
        self._lock = threading.RLock()

    def close(self):
        with self._lock:
            if self._keyboard is not None:
                self._keyboard.close()
                self._keyboard = None

    def execute(self, action_id, cancelled=None):
        with self._lock:
            validate_config(self.config)
            if not isinstance(action_id, str) or action_id not in self.config["actions"]:
                raise RuntimeError(f"Unknown Smart Action {action_id!r}.")
            action = deepcopy(self.config["actions"][action_id])
            result = {"ok": True, "action_id": action_id, "label": action["label"], "completed": 0,
                      "dry_run": self.dry_run, "steps": []}
            if self.dry_run:
                result["steps"] = deepcopy(action["steps"])
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
                for step in action["steps"]:
                    self._check(cancelled, deadline)
                    performed = self._step(step, cancelled, deadline)
                    result["steps"].append(performed)
                    result["completed"] += 1
                return result
            except (RuntimeError, OSError) as exc:
                raise RuntimeError(f"Smart Action {action['label']!r} stopped after {result['completed']}/{len(action['steps'])} steps: {exc}") from exc

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
            native = _native_wtype()
            if native:
                # Small chunks bound cancellation latency and avoid the kernel's
                # per-argument byte limit for long Unicode text. Each helper's
                # virtual keyboard is released when the process exits.
                for start in range(0, len(step["text"]), 256):
                    self._check(cancelled, deadline)
                    _run([native, "--", step["text"][start:start + 256]],
                         timeout=min(5, max(0.1, deadline - time.monotonic())))
                self._check(cancelled, deadline)
            else:
                _run(["wl-copy", "--type", "text/plain;charset=utf-8"], timeout=3, input=step["text"])
                self._check(cancelled, deadline)
                self._shortcut(["CTRL", "V"], cancelled, deadline)
        elif kind == "delay":
            self._delay(step["milliseconds"] / 1000, cancelled, deadline)
        return result
