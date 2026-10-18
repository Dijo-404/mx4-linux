"""Native keyboard selection and injection, using process/device doubles."""

from pathlib import Path
from types import SimpleNamespace

import evdev
import pytest

import mx4_linux.actions as actions
from mx4_linux.actions import DesktopActions, default_config


def routine(steps):
    config = default_config()
    config["actions"]["test"] = {"label": "Test", "steps": steps}
    return config


def native_only(monkeypatch):
    monkeypatch.setattr(actions, "_native_wtype", lambda: "/helper/wtype")
    monkeypatch.setattr(evdev, "UInput", lambda *_a, **_k: pytest.fail("Native injection must not open uinput"))


def test_helper_not_selected_outside_wayland(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(actions.shutil, "which", lambda *_a: pytest.fail("No helper probe outside Wayland"))
    assert actions._native_wtype() is None


def test_system_helper_preferred_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setattr(actions.shutil, "which", lambda executable: "/usr/bin/wtype" if executable == "wtype" else None)
    assert actions._native_wtype() == "/usr/bin/wtype"


def test_local_helper_is_found_without_venv_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setattr(actions.shutil, "which", lambda *_a: None)
    monkeypatch.setattr(actions.sys, "executable", str(tmp_path / "python"))
    helper = tmp_path / "wtype"
    assert actions._native_wtype() is None
    helper.write_text("#!/bin/sh\nexit 0\n")
    assert actions._native_wtype() is None  # An existing nonexecutable is not usable.
    helper.chmod(0o700)
    assert actions._native_wtype() == str(helper)


def test_native_chord_uses_literal_keys_and_releases_in_reverse(monkeypatch):
    native_only(monkeypatch)
    calls = []
    monkeypatch.setattr(actions.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    executor = DesktopActions(routine([{"type": "shortcut", "keys": ["CTRL", "SHIFT", "C"]}]))
    assert executor.execute("test")["completed"] == 1
    assert calls[0][0] == ["/helper/wtype", "-M", "ctrl", "-M", "shift", "-P", "c", "-s", "25",
                           "-p", "c", "-m", "shift", "-m", "ctrl"]
    assert calls[0][1]["shell"] is False
    assert 0 < calls[0][1]["timeout"] <= 5
    assert executor._keyboard is None


@pytest.mark.parametrize("linux_keys,symbols", [
    (["ALT", "TAB"], ["alt", "Tab"]),
    (["SUPER", "ENTER"], ["logo", "Return"]),
    (["KEY_RIGHTALT", "1"], ["altgr", "1"]),
    (["F12"], ["F12"]),
    (["PGDN"], ["Next"]),
    (["KEY_KPENTER"], ["KP_Enter"]),
    (["VOLUMEUP"], ["XF86AudioRaiseVolume"]),
    (["ESC"], ["Escape"]),
])
def test_common_linux_keys_map_to_xkb_symbols(monkeypatch, linux_keys, symbols):
    native_only(monkeypatch)
    calls = []
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: calls.append(argv))
    DesktopActions(routine([{"type": "shortcut", "keys": linux_keys}])).execute("test")
    assert calls[0][2:2 + len(symbols) * 2:2] == symbols


def test_unmapped_valid_linux_key_uses_uinput_without_partial_native_input(monkeypatch):
    monkeypatch.setattr(actions, "_native_wtype", lambda: "/helper/wtype")
    monkeypatch.setattr(actions, "_run", lambda *_a, **_k: pytest.fail("Do not emit a partially mapped chord"))
    events = []
    keyboard = SimpleNamespace(write=lambda *args: events.append(args), syn=lambda: None, close=lambda: None)
    monkeypatch.setattr(evdev, "UInput", lambda *_a, **_k: keyboard)
    executor = DesktopActions(routine([{"type": "shortcut", "keys": ["CTRL", "PROG1"]}]))
    monkeypatch.setattr(executor, "_delay", lambda *_a: None)
    assert executor.execute("test")["ok"]
    assert [(code, value) for _kind, code, value in events] == [
        (evdev.ecodes.KEY_LEFTCTRL, 1), (evdev.ecodes.KEY_PROG1, 1),
        (evdev.ecodes.KEY_PROG1, 0), (evdev.ecodes.KEY_LEFTCTRL, 0),
    ]


def test_native_failure_does_not_retry_shortcut_via_another_backend(monkeypatch):
    native_only(monkeypatch)
    def disconnected(*_a, **_k):
        raise actions.subprocess.CalledProcessError(1, "wtype", stderr="Compositor does not support the virtual keyboard protocol")
    monkeypatch.setattr(actions.subprocess, "run", disconnected)
    with pytest.raises(RuntimeError, match="virtual keyboard protocol"):
        DesktopActions().execute("copy")


def test_native_timeout_reports_error_and_never_retries(monkeypatch):
    native_only(monkeypatch)
    def timeout(*_a, **_k):
        raise actions.subprocess.TimeoutExpired("wtype", 5)
    monkeypatch.setattr(actions.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="did not respond"):
        DesktopActions().execute("copy")


def test_native_text_preserves_unicode_and_leading_option_text_without_clipboard(monkeypatch):
    native_only(monkeypatch)
    calls = []
    text = "-M ctrl; $(no shell) नमस्ते ✓\nnext line\t!"
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    executor = DesktopActions(routine([{"type": "text", "text": text}]))
    assert executor.execute("test")["ok"]
    assert calls == [(["/helper/wtype", "--", text], {"timeout": 5})]
    assert executor._keyboard is None


def test_long_native_text_preserves_order_and_can_cancel_between_chunks(monkeypatch):
    native_only(monkeypatch)
    calls = []
    text = "✓" * 700
    monkeypatch.setattr(actions, "_run", lambda argv, **kwargs: calls.append(argv))
    executor = DesktopActions(routine([{"type": "text", "text": text}]))
    assert executor.execute("test")["ok"]
    assert "".join(call[-1] for call in calls) == text
    assert max(len(call[-1].encode("utf-8")) for call in calls) <= 1024
    calls.clear()
    with pytest.raises(RuntimeError, match="cancelled"):
        executor.execute("test", cancelled=lambda: bool(calls))
    assert len(calls) == 1


def test_native_cancellation_before_execution_never_starts_helper(monkeypatch):
    native_only(monkeypatch)
    monkeypatch.setattr(actions, "_run", lambda *_a, **_k: pytest.fail("Cancelled action must not inject"))
    with pytest.raises(RuntimeError, match="cancelled"):
        DesktopActions().execute("copy", cancelled=lambda: True)


def test_native_preview_does_not_discover_or_start_helper(monkeypatch):
    monkeypatch.setattr(actions, "_native_wtype", lambda: pytest.fail("Preview must not probe input helpers"))
    assert DesktopActions(dry_run=True).execute("copy")["dry_run"]
