"""UI coordination regressions without a display or a physical mouse."""

from collections import deque
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mx4_linux.desktop import DesktopController
from mx4_linux.ring_model import RingState


@pytest.fixture
def controller():
    value = DesktopController.__new__(DesktopController)
    value.closed = False
    value._event_lock = threading.Lock()
    value._event_queue = deque()
    value._events_pending = False
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


def test_motion_burst_uses_one_ui_callback_and_preserves_release_order(controller, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mx4_linux.desktop.GLib.idle_add", lambda fn: scheduled.append(fn))
    controller.event = Mock()
    controller.queue_event({"type": "button", "control": 416, "pressed": True, "session_id": 2})
    for _ in range(1000):
        controller.queue_event({"type": "motion", "dx": 2, "dy": -1, "session_id": 2})
    controller.queue_event({"type": "button", "control": 416, "pressed": False, "session_id": 2})
    assert len(scheduled) == 1
    scheduled.pop()()
    events = [call.args[0] for call in controller.event.call_args_list]
    assert [event["type"] for event in events] == ["button", *["motion"] * 1000, "button"]
    assert all(event == {"type": "motion", "dx": 2, "dy": -1, "session_id": 2} for event in events[1:-1])
    assert not events[-1]["pressed"]


def test_batched_motion_preserves_radius_clamping_when_direction_reverses(controller, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mx4_linux.desktop.GLib.idle_add", lambda fn: scheduled.append(fn))
    state = RingState()
    state.open(held=True)
    controller.ring.motion = state.motion
    controller.queue_event({"type": "motion", "dx": 1000, "dy": 0, "session_id": 2})
    controller.queue_event({"type": "motion", "dx": -1000, "dy": 0, "session_id": 2})
    scheduled.pop()()
    assert state.x == -155
    assert state.selected == 6


def test_motion_batch_keeps_session_boundaries_and_invalid_values(controller, monkeypatch):
    monkeypatch.setattr("mx4_linux.desktop.GLib.idle_add", lambda fn: None)
    controller.event = Mock()
    events = [{"type": "motion", "dx": 2, "dy": 1, "session_id": 1},
              {"type": "motion", "dx": 2, "dy": 1, "session_id": 2},
              {"type": "motion", "dx": "bad", "dy": 1, "session_id": 2}]
    for event in events:
        controller.queue_event(event)
    controller.flush_events()
    assert [call.args[0] for call in controller.event.call_args_list] == events


def test_events_arriving_during_flush_get_another_callback(controller, monkeypatch):
    scheduled, seen = [], []
    monkeypatch.setattr("mx4_linux.desktop.GLib.idle_add", lambda fn: scheduled.append(fn))

    def receive(event):
        seen.append(event["type"])
        if event["type"] == "ready":
            controller.queue_event({"type": "stopped"})

    controller.event = receive
    controller.queue_event({"type": "ready"})
    scheduled.pop(0)()
    assert seen == ["ready"]
    scheduled.pop(0)()
    assert seen == ["ready", "stopped"]
