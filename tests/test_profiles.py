import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from mx4_linux.backend import BackendError, DemoBackend
from mx4_linux.profiles import ProfileStore, apply_profile


def test_round_trip_snapshot_excludes_haptic_playback(tmp_path):
    device = DemoBackend().device
    store = ProfileStore(tmp_path / "profiles.json")
    store.save("Work", device, demo=True)
    saved = store.read()["Work"]
    assert saved["demo"] is True
    assert "haptic-play" not in saved["settings"]
    assert saved["settings"]["dpi"] == 1000
    assert saved["settings"]["reprogrammable-keys"]["83"] == 83
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


def test_remember_opt_in_preserves_desired_on_scan_and_rejects_commands(tmp_path):
    from mx4_linux.profiles import RememberedSettingsStore
    store = RememberedSettingsStore(tmp_path / 'remembered.json')
    device = DemoBackend().device
    store.set_enabled(device, True, demo=True)
    old = store.get(device, demo=True)
    device['settings'][0]['value'] = 1600
    assert store.get(device, demo=True) == old
    store.update_confirmed(device['stable_id'], {'dpi': 1600}, demo=True)
    assert store.get(device, demo=True)['settings']['dpi'] == 1600
    with pytest.raises(BackendError, match='command'):
        store.update_confirmed(device['stable_id'], {'haptic-play': 0}, demo=True)
    store.set_enabled(device, False, demo=True)
    assert store.get(device, demo=True) is None


def test_corrupt_remembered_store_remains_intact(tmp_path):
    from mx4_linux.profiles import RememberedSettingsStore
    path = tmp_path / 'remembered.json'
    path.write_text('broken')
    with pytest.raises(BackendError):
        RememberedSettingsStore(path).set_enabled(DemoBackend().device, True, demo=True)
    assert path.read_text() == 'broken'


def test_real_profile_refreshes_identity_validates_whole_plan_and_excludes_listener(tmp_path, monkeypatch):
    from copy import deepcopy
    from mx4_linux.reporting_state import Ownership, configuration_lock
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    device = deepcopy(DemoBackend().device)
    device.update(id='old-path', stable_id='unit:a1b2c3d4')
    fresh = deepcopy(device)
    fresh['id'] = 'new-path'
    writes = []
    class Backend:
        def scan(self):
            return {'devices': [fresh]}
        def write(self, *args, **kwargs):
            with pytest.raises(RuntimeError, match='already'):
                with configuration_lock('unit:a1b2c3d4'):
                    pass
            writes.append((args, kwargs))
    backend = Backend()
    invalid = {'model': 'MX Master 4', 'settings': {'dpi': 1600, 'haptic-level': 101}}
    with pytest.raises(BackendError, match='Invalid value'):
        apply_profile(backend, device, invalid)
    assert not writes
    profile = {'model': 'MX Master 4', 'settings': {'dpi': 1600}}
    assert apply_profile(backend, device, profile) == 1
    assert writes[0][0][0] == 'new-path'
    assert writes[0][1]['expected_stable_id'] == device['stable_id']
    writes.clear()
    with Ownership(device['stable_id']) as owner:
        owner.touch(416, 0x104, 416, 17)
    with pytest.raises(BackendError, match='Recover'):
        apply_profile(backend, device, profile)
    assert not writes
    fresh['stable_id'] = 'unit:d1e2f3a4'
    with pytest.raises(BackendError, match='same mouse'):
        apply_profile(backend, device, profile)
    assert not writes


def test_equal_profile_skips_whole_plan_but_coupled_plan_keeps_later_writes():
    backend = DemoBackend()
    profile = {'model': 'MX Master 4', 'demo': True, 'settings': {'smart-shift': 20, 'scroll-ratchet': 2}}
    assert apply_profile(backend, backend.device, profile, demo=True) == 0
    profile['settings']['smart-shift'] = 30
    writes = []
    original = backend.write
    def write(*args):
        writes.append(args[1])
        return original(*args)
    backend.write = write
    assert apply_profile(backend, backend.device, profile, demo=True) == 2
    assert writes == ['smart-shift', 'scroll-ratchet']
