"""User-invoked local profiles. Playback commands are never stored or replayed."""

import fcntl
import json
import os
from pathlib import Path
import tempfile

from .backend import BackendError
from .reporting_state import MISSING, Ownership, atomic_json, configuration_lock, file_lock, private_dir, read_json, valid_identity

COMMAND_SETTINGS = {"haptic-play", "change-host", "switch_host", "recover", "pair", "unpair"}


def settings_snapshot(device):
    return {
        s["name"]: s["value"] for s in device["settings"]
        if s["name"] not in COMMAND_SETTINGS and s.get("value") is not None
    }


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mx4-linux" / "profiles.json"

    def read(self):
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError("Profile file is too large.")
            data = json.loads(self.path.read_text())
            if data.get("version") != 1 or not isinstance(data.get("profiles"), dict):
                raise ValueError("Unsupported profile format.")
            for name, profile in data["profiles"].items():
                if not isinstance(name, str) or not isinstance(profile, dict) or not isinstance(profile.get("settings"), dict):
                    raise ValueError("Invalid profile entry.")
            return data["profiles"]
        except (OSError, ValueError, AttributeError) as exc:
            raise BackendError(f"Cannot read {self.path}: {exc}") from exc

    def save(self, name, device, demo=False):
        name = name.strip()
        if not name or len(name) > 64 or any(ord(c) < 32 for c in name):
            raise BackendError("Choose a profile name of 1–64 characters.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            profiles = self.read()
            profiles[name] = {"model": "MX Master 4", "demo": demo, "settings": settings_snapshot(device)}
            self._write(profiles)

    def _write(self, profiles):
        filename = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
                filename = stream.name
                json.dump({"version": 1, "profiles": profiles}, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)


def apply_profile(backend, device, profile, demo=False, cancelled=None, remember=True):
    if profile.get("model") != "MX Master 4" or profile.get("demo", False) != demo:
        raise BackendError("This profile is for a different model or preview mode.")
    identity = device.get("stable_id")
    if not demo:
        if not valid_identity(identity) or identity.startswith("demo:"):
            raise BackendError("A verified mouse identity is required before applying settings")
        if cancelled and cancelled():
            raise BackendError("Profile cancelled before discovery")
        fresh = backend.scan()
        device = next((item for item in fresh["devices"] if item.get("online") and item.get("stable_id") == identity), None)
        if device is None:
            raise BackendError("The same mouse is unavailable; wake and refresh it before applying settings")
    if demo:
        return _apply_profile_plan(backend, device, profile, demo, cancelled, remember, identity)
    with configuration_lock(identity):
        with Ownership(identity) as ownership:
            if ownership.read() is not None:
                raise BackendError("Recover previous button reporting before applying a hardware profile")
        return _apply_profile_plan(backend, device, profile, demo, cancelled, remember, identity)


def _apply_profile_plan(backend, device, profile, demo, cancelled, remember, identity):
    settings = {s["name"]: s for s in device["settings"]}
    plan = []
    # Validate the entire profile before making any physical change.
    for name, value in profile["settings"].items():
        if name in COMMAND_SETTINGS or name not in settings:
            raise BackendError(f"Profile setting {name!r} is unavailable on this mouse.")
        setting = settings[name]
        if setting["kind"] == "map_choice":
            if not isinstance(value, dict):
                raise BackendError(f"Invalid button assignments in {name}.")
            keys = {str(k["value"]): k for k in setting["keys"]}
            for key, mapped in value.items():
                if str(key) not in keys:
                    raise BackendError(f"Unknown button {key} in {name}.")
                validate_value({"kind": "choice", "choices": keys[str(key)]["choices"]}, mapped)
                plan.append((name, mapped, int(key)))
        else:
            validate_value(setting, value)
            plan.append((name, value, None))
    def current_value(name, key):
        value = settings[name].get("value")
        return value.get(str(key), value.get(key)) if key is not None and isinstance(value, dict) else value
    if plan and all(type(current_value(name, key)) is type(value) and current_value(name, key) == value
                    for name, value, key in plan):
        return 0
    completed = 0
    for name, value, key in plan:
        if cancelled and cancelled():
            raise BackendError(f"Profile cancelled after {completed}/{len(plan)} changes.")
        try:
            if demo:
                backend.write(device["id"], name, value, key)
            else:
                backend.write(device["id"], name, value, key, expected_stable_id=identity, remember=remember)
        except BackendError as exc:
            raise BackendError(f"Applied {completed}/{len(plan)} changes; {name} failed: {exc}. Refresh to see current values.") from exc
        completed += 1
    return completed


def validate_value(setting, value):
    kind = setting["kind"]
    if kind == "toggle":
        valid = type(value) is bool
    elif kind == "range":
        valid = type(value) is int and setting["minimum"] <= value <= setting["maximum"]
    elif kind == "choice":
        valid = type(value) is int and value in {c["value"] for c in setting["choices"]}
    else:
        valid = False
    if not valid:
        raise BackendError(f"Invalid value {value!r} for {setting.get('label', setting.get('name', kind))}.")


class RememberedSettingsStore:
    """Desired confirmed hardware state, separate from ordinary device scans."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "mx4-linux/remembered.json"

    def read(self):
        try:
            data = read_json(self.path, 262144)
            return self._validate(data) if data is not MISSING else {}
        except (OSError, ValueError, TypeError) as exc:
            raise BackendError(f"Cannot read remembered settings: {exc}") from exc

    @staticmethod
    def _validate(data):
        if (not isinstance(data, dict) or set(data) != {"version", "units"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["units"], dict) or len(data["units"]) > 64):
            raise ValueError("Invalid remembered settings format")
        from .device_worker import ALLOWED_SETTINGS
        for identity, entry in data["units"].items():
            if (not valid_identity(identity) or not isinstance(entry, dict)
                    or set(entry) != {"model", "demo", "settings"}
                    or entry["model"] != "MX Master 4" or type(entry["demo"]) is not bool
                    or identity.startswith("demo:") != entry["demo"]
                    or not isinstance(entry["settings"], dict) or len(entry["settings"]) > 10):
                raise ValueError("Invalid remembered mouse entry")
            for name, value in entry["settings"].items():
                if name in COMMAND_SETTINGS or name not in ALLOWED_SETTINGS:
                    raise ValueError("Playback/host commands cannot be remembered")
                if name == 'reprogrammable-keys':
                    if (not isinstance(value, dict) or len(value) > 64 or any(
                            not isinstance(k, str) or not k.isdecimal() or not 0 < int(k) <= 65535
                            or type(v) is not int or not 0 < v <= 65535 for k, v in value.items())):
                        raise ValueError("Invalid remembered button mapping")
                elif type(value) not in (bool, int) or type(value) is int and not 0 <= value <= 65535:
                    raise ValueError("Invalid remembered setting")
        return data["units"]

    def get(self, device, demo=False):
        identity = device.get("stable_id")
        if not valid_identity(identity) or identity.startswith('demo:') != demo:
            return None
        return self.read().get(identity)

    def _mutate(self, update):
        private_dir(self.path.parent)
        try:
            with file_lock(self.path.with_suffix('.lock')):
                entries = self.read()
                if update(entries):
                    document = {"version": 1, "units": entries}
                    self._validate(document)
                    if len(json.dumps(document)) > 262144:
                        raise ValueError("Remembered settings exceed the private store limit")
                    atomic_json(self.path, document)
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise BackendError(f"Cannot save remembered settings: {exc}") from exc

    def set_enabled(self, device, enabled, demo=False):
        identity = device.get('stable_id')
        if type(enabled) is not bool or not valid_identity(identity) or identity.startswith('demo:') != demo:
            raise BackendError("A verified mouse identity is required before remembering settings")
        def update(entries):
            if enabled:
                entries[identity] = {"model": "MX Master 4", "demo": demo, "settings": settings_snapshot(device)}
            else:
                entries.pop(identity, None)
            return True
        self._mutate(update)

    def update_confirmed(self, identity, values, key=None, demo=False):
        if not valid_identity(identity) or identity.startswith('demo:') != demo:
            raise BackendError("Mouse identity changed before settings could be remembered")
        from .device_worker import ALLOWED_SETTINGS
        if any(name in COMMAND_SETTINGS or name not in ALLOWED_SETTINGS for name in values):
            raise BackendError("Playback and host commands cannot be remembered")
        # Opt-out means no file creation or writes.
        if not self.path.exists():
            return
        def update(entries):
            entry = entries.get(identity)
            if entry is None:
                return False
            for name, value in values.items():
                if name == 'reprogrammable-keys' and key is not None:
                    entry['settings'].setdefault(name, {})[str(key)] = value
                else:
                    entry['settings'][name] = value
            return True
        self._mutate(update)


def replay_remembered(backend, device, store=None, demo=False, cancelled=None):
    store = store or RememberedSettingsStore()
    desired = store.get(device, demo=demo)
    if desired is None:
        return 0
    return apply_profile(backend, device, desired, demo=demo, cancelled=cancelled, remember=False)
