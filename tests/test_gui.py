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
