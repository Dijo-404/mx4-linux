"""UI coordination regressions without a display or a physical mouse."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mx4_linux.desktop import DesktopController


@pytest.fixture
def controller():
    value = DesktopController.__new__(DesktopController)
    value.closed = False
    value._open_token = value._dispatch_token = 0
    value.opening = False
    value._pending_motion = [0, 0]
    value._pending_released = False
    value.session = SimpleNamespace(session_id=2, ready=True, stopping=False, running=False)
    value.config = {"ring_button": 416, "button_bindings": {"83": "copy"}}
    value.ring = SimpleNamespace(state=SimpleNamespace(visible=True), dismiss=Mock(), release_button=Mock(), motion=Mock())
    value.notify = Mock()
    value.target = {"window_id": 10}
    value.error = None
    value.execute = Mock()
    return value


def test_synthetic_cleanup_release_cancels_instead_of_commits(controller):
    controller.opening = True
    controller._pending_motion = [50, -50]
    controller.event({"type": "button", "control": 416, "pressed": False, "cancelled": True})
    controller.ring.release_button.assert_not_called()
    controller.ring.dismiss.assert_called_once()
    assert not controller.opening
    assert controller._pending_motion == [0, 0]


def test_stale_session_events_are_ignored(controller):
    controller.event({"type": "button", "control": 416, "pressed": False, "session_id": 1})
    controller.ring.release_button.assert_not_called()


def test_real_release_reaches_ring(controller):
    controller.event({"type": "button", "control": 416, "pressed": False, "session_id": 2})
    controller.ring.release_button.assert_called_once()


def test_error_closes_ring_and_invalidates_pending_selection(controller):
    controller.opening = True
    controller.event({"type": "error", "message": "unplugged"})
    controller.ring.dismiss.assert_called_once()
    assert controller.error == "unplugged"
    assert not controller.opening
    assert controller._dispatch_token == 1


def test_queued_motion_during_stop_does_not_move_preview(controller):
    controller.session.stopping = True
    controller.event({"type": "motion", "dx": 100, "dy": 0})
    controller.ring.motion.assert_not_called()


def test_motion_and_release_while_focus_query_pending_are_preserved(controller):
    controller.opening = True
    controller.event({"type": "motion", "dx": 100, "dy": -50})
    controller.event({"type": "button", "control": 416, "pressed": False})
    assert controller._pending_motion == [100, -50]
    assert controller._pending_released
    controller.ring.release_button.assert_not_called()


def test_stop_cancels_already_scheduled_action(controller, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mx4_linux.desktop.GLib.timeout_add", lambda _ms, fn: scheduled.append(fn))
    controller.selected("copy", False)
    controller.stop()
    scheduled[0]()
    controller.execute.assert_not_called()


def test_selection_dispatch_preserves_original_focus_target(controller, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mx4_linux.desktop.GLib.timeout_add", lambda _ms, fn: scheduled.append(fn))
    controller.selected("copy", False)
    controller.target = {"window_id": 99}
    scheduled[0]()
    controller.execute.assert_called_once_with("copy", target={"window_id": 10}, preview=False)
