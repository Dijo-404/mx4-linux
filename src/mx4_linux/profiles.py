"""User-invoked local profiles. Playback commands are never stored or replayed."""

import fcntl
import json
import os
from pathlib import Path
import tempfile

from .backend import BackendError

COMMAND_SETTINGS = {"haptic-play"}


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


def apply_profile(backend, device, profile, demo=False, cancelled=None):
    if profile.get("model") != "MX Master 4" or profile.get("demo", False) != demo:
        raise BackendError("This profile is for a different model or preview mode.")
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
    completed = 0
    for name, value, key in plan:
        if cancelled and cancelled():
            raise BackendError(f"Profile cancelled after {completed}/{len(plan)} changes.")
        try:
            backend.write(device["id"], name, value, key)
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
