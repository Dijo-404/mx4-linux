import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from mx4_linux.backend import BackendError, DemoBackend
from mx4_linux.profiles import ProfileStore, apply_profile, settings_snapshot


def test_round_trip_snapshot_excludes_haptic_playback(tmp_path):
    device = DemoBackend().device
    store = ProfileStore(tmp_path / "profiles.json")
    store.save("Work", device, demo=True)
    saved = store.read()["Work"]
    assert saved["demo"] is True
    assert "haptic-play" not in saved["settings"]
    assert saved["settings"]["dpi"] == 1000
    assert saved["settings"]["reprogrammable-keys"] == {"83": 83}
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_invalid_late_setting_rejected_before_any_write():
    backend = DemoBackend()
    device = backend.device
    profile = {"model": "MX Master 4", "demo": True, "settings": {"dpi": 1600, "haptic-level": 101}}
    with pytest.raises(BackendError, match="Invalid value"):
        apply_profile(backend, device, profile, demo=True)
    assert device["settings"][0]["value"] == 1000


def test_playback_cannot_be_injected_into_profile():
    backend = DemoBackend()
    profile = {"model": "MX Master 4", "demo": True, "settings": {"haptic-play": 1}}
    with pytest.raises(BackendError, match="unavailable"):
        apply_profile(backend, backend.device, profile, demo=True)


def test_preview_profile_cannot_change_real_device():
    backend = DemoBackend()
    profile = {"model": "MX Master 4", "demo": True, "settings": {"dpi": 1600}}
    with pytest.raises(BackendError, match="preview mode"):
        apply_profile(backend, backend.device, profile)


@pytest.mark.parametrize("value", [True, 1000.0, "1000", None])
def test_choice_values_reject_bool_float_and_text(value):
    backend = DemoBackend()
    profile = {"model": "MX Master 4", "demo": True, "settings": {"dpi": value}}
    with pytest.raises(BackendError, match="Invalid value"):
        apply_profile(backend, backend.device, profile, demo=True)


def test_profile_applies_each_button_mapping():
    backend = DemoBackend()
    profile = {"model": "MX Master 4", "demo": True, "settings": {"reprogrammable-keys": {"83": 86}}}
    assert apply_profile(backend, backend.device, profile, demo=True) == 1
    assert backend.device["settings"][-1]["value"]["83"] == 86


def test_failure_reports_partial_application():
    class FailingBackend(DemoBackend):
        def write(self, device_id, setting, value, key=None):
            if setting == "haptic-level":
                raise BackendError("asleep")
            return super().write(device_id, setting, value, key)
    backend = FailingBackend()
    profile = {"model": "MX Master 4", "demo": True, "settings": {"dpi": 1600, "haptic-level": 50}}
    with pytest.raises(BackendError, match="Applied 1/2 changes"):
        apply_profile(backend, backend.device, profile, demo=True)
    assert backend.device["settings"][0]["value"] == 1600


def test_corrupt_profiles_do_not_get_overwritten(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("broken")
    with pytest.raises(BackendError, match="Cannot read"):
        ProfileStore(path).save("Work", DemoBackend().device)
    assert path.read_text() == "broken"


def test_unknown_profile_version_rejected(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"version": 9, "profiles": {}}))
    with pytest.raises(BackendError, match="Unsupported"):
        ProfileStore(path).read()


def test_concurrent_profile_saves_preserve_both_entries(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles.json")
    original_read = store.read
    def slow_read():
        result = original_read()
        time.sleep(0.02)
        return result
    monkeypatch.setattr(store, "read", slow_read)
    start = threading.Barrier(2)
    def save(name):
        start.wait()
        store.save(name, DemoBackend().device)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save, name) for name in ("A", "B")]
        for future in futures:
            future.result()
    assert set(store.read()) == {"A", "B"}


def test_closing_stops_subsequent_profile_writes():
    backend = DemoBackend()
    calls = []
    original = backend.write
    def write(*args):
        calls.append(args)
        return original(*args)
    backend.write = write
    profile = {"model": "MX Master 4", "demo": True, "settings": {"dpi": 1600, "haptic-level": 50}}
    with pytest.raises(BackendError, match="cancelled after 1/2"):
        apply_profile(backend, backend.device, profile, demo=True, cancelled=lambda: len(calls) == 1)
    assert len(calls) == 1
