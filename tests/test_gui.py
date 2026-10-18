"""Readback updates preserve native controls and avoid redundant HID discovery."""

from time import monotonic, sleep
from unittest.mock import Mock
from uuid import uuid4

import pytest

pytest.importorskip("gi")
from mx4_linux.gtk_support import Adw, Gio, GLib, Gtk
from mx4_linux.backend import BackendError
from mx4_linux.gui import ControlWindow


def settle(window):
    context = GLib.MainContext.default()
    deadline = monotonic() + 3
    while window.busy and monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        sleep(0.001)
    assert not window.busy


@pytest.fixture
def window(tmp_path, monkeypatch):
    if not Gtk.init_check():
        pytest.skip("A graphical session is needed for native control tests")
    Adw.init()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    app = Adw.Application(application_id=f"io.github.mx4linux.GuiTests.t{uuid4().hex}", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = ControlWindow(app, demo=True)
    settle(window)
    window.backend.scan = Mock(side_effect=AssertionError("Editing must not trigger another scan"))
    yield window
    window.close()


def setting(window, name):
    return next(item for item in window.current["settings"] if item["name"] == name)


def test_confirmed_edit_updates_existing_widget_without_scan(window):
    dropdown = window.setting_widgets[("dpi", None)][0]
    dropdown.set_selected(3)
    settle(window)
    assert setting(window, "dpi")["value"] == 1600
    assert window.setting_widgets[("dpi", None)][0] is dropdown
    window.backend.scan.assert_not_called()


def test_failed_toggle_refreshes_uncertain_hardware_state_without_retry(window):
    window.backend.write = Mock(side_effect=BackendError("Mouse disconnected"))
    window.backend.scan = Mock(return_value={"ok": True, "devices": [window.backend.device], "diagnostics": []})
    switch = window.setting_widgets[("hires-smooth-invert", None)][0]
    switch.set_active(True)
    settle(window)
    assert not window.setting_widgets[("hires-smooth-invert", None)][0].get_active()
    assert setting(window, "hires-smooth-invert")["value"] is False
    assert "Mouse disconnected" in window.status.get_text()
    window.backend.write.assert_called_once()
    window.backend.scan.assert_called_once()


def test_related_wheel_readback_updates_controls_without_another_write(window):
    window.backend.write = Mock(return_value={"ok": True, "value": 1, "values": {"smart-shift": 0}})
    window.setting_widgets[("scroll-ratchet", None)][0].set_selected(0)
    settle(window)
    assert setting(window, "smart-shift")["value"] == 0
    assert window.setting_widgets[("smart-shift", None)][0].get_value_as_int() == 0
    assert window.wheel.free_spin
    window.backend.write.assert_called_once()
    window.backend.scan.assert_not_called()


def test_save_profile_uses_confirmed_settings_without_scan(window):
    entry = None
    def find(widget):
        nonlocal entry
        if isinstance(widget, Gtk.Entry):
            entry = widget
        child = widget.get_first_child()
        while child:
            find(child)
            child = child.get_next_sibling()
    find(window.profile_controls)
    entry.set_text("Work")
    entry.emit("activate")
    settle(window)
    profile = window.store.read()["Work"]
    assert profile["settings"]["dpi"] == 1000
    assert "haptic-play" not in profile["settings"]
    window.backend.scan.assert_not_called()


def test_hardware_job_keeps_action_cancellation_available(window):
    window.desktop.action_busy = True
    window.change(setting(window, "haptic-level"), 60)
    assert window.cancel_action.is_sensitive()
    assert not window.controls.is_sensitive()
    settle(window)


def test_unchanged_value_does_not_write(window):
    window.backend.write = Mock()
    window.change(setting(window, "dpi"), 1000)
    assert not window.busy
    window.backend.write.assert_not_called()


def test_failed_refresh_after_uncertain_write_clears_stale_profile_state(window):
    window.backend.scan = Mock(side_effect=BackendError("Mouse still unavailable"))
    window.refresh_after_write("Profile partly applied")
    settle(window)
    assert window.current is None
    assert window.controls.get_first_child() is None
    assert not window.wheel.get_visible()
    assert not window.desktop_toggle.get_sensitive()


def test_uncertain_related_readback_refreshes_before_saving_profiles(window):
    window.backend.write = Mock(return_value={"ok": True, "value": 1, "refresh_required": True})
    window.backend.scan = Mock(side_effect=BackendError("Related settings unavailable"))
    window.change(setting(window, "scroll-ratchet"), 1)
    settle(window)
    window.backend.scan.assert_called_once()
    assert window.current is None


def test_battery_tick_is_lightweight_and_failure_clears_old_percent(window):
    window.backend.battery = Mock(side_effect=BackendError('asleep'))
    window.refresh_battery()
    settle(window)
    window.backend.battery.assert_called_once_with('demo', 'demo:mx-master-4')
    assert window.current['battery_percent'] is None
    assert window.current['battery_observed_at'] is None
    window.backend.scan.assert_not_called()


def test_remember_opt_in_and_confirmed_changes_preserve_target(window):
    window.remember_current(True)
    settle(window)
    window.setting_widgets[('dpi', None)][0].set_selected(3)
    settle(window)
    assert window.remembered.get(window.current, demo=True)['settings']['dpi'] == 1600
    window.remember_current(False)
    settle(window)
    assert window.remembered.get(window.current, demo=True) is None
    window.backend.scan.assert_not_called()


def test_switch_waits_for_restoration_and_cancel_invalidates_pending_request(window):
    pending = []
    window.desktop.wait_stopped = lambda callback, **_kw: pending.append(callback)
    window.backend.switch_host = Mock(return_value={'message': 'Switch requested; mouse may disconnect.'})
    window.request_host_switch(1)
    assert pending and not window.backend.switch_host.called
    window.cancel_hardware()
    pending[0](True)
    assert not window.backend.switch_host.called


@pytest.mark.parametrize('restored', [False, None])
def test_unconfirmed_restoration_prevents_computer_switch(window, restored):
    window.desktop.wait_stopped = lambda callback, **_kw: callback(restored)
    window.backend.switch_host = Mock()
    window.request_host_switch(1)
    assert not window.backend.switch_host.called
    assert 'not confirmed' in window.status.get_text()


def test_valid_switch_stays_unconfirmed_and_does_not_change_channel(window):
    window.backend.switch_host = Mock(return_value={'message': 'Switch requested; mouse may disconnect.'})
    window.request_host_switch(1)
    settle(window)
    window.backend.switch_host.assert_called_once_with('demo', 1, 'demo:mx-master-4')
    assert window.current['easy_switch']['current'] == 0
    assert window.status.get_text() == 'Switch requested; mouse may disconnect.'


def test_native_remap_waits_for_restoration_before_any_write(window):
    pending = []
    window.desktop.wait_stopped = lambda callback, **_kw: pending.append(callback)
    window.backend.write = Mock(return_value={'ok': True, 'value': 86})
    mapping = setting(window, 'reprogrammable-keys')
    window.change(mapping, 86, 83)
    window.backend.write.assert_not_called()
    pending[0](True)
    settle(window)
    window.backend.write.assert_called_once()
    assert mapping['value']['83'] == 86
