"""UI coordination regressions without a display or a physical mouse."""

from collections import deque
from copy import deepcopy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mx4_linux.desktop import DesktopController
from mx4_linux.actions import default_config
from mx4_linux.ring_model import RingState


@pytest.fixture
def controller():
    value = DesktopController.__new__(DesktopController)
    value.cancelled = threading.Event()
    value.hardware_paused = False
    value.requested_enabled = False
    value.target_identity = None
    value._intent_generation = 0
    value.reconnect_source = value.reconnect_attempt = 0
    value._reconnect_busy = False
    value._stop_waiters = []
    value.restoration_confirmed = None
    value.reacquire_blocked = False
    value.starting = False
    value.closed = False
    value._event_lock = threading.Lock()
    value._event_queue = deque()
    value._events_pending = False
    value._open_token = value._dispatch_token = 0
    value.opening = False
    value._pending_motion = [0, 0]
    value._opening_motion = deque()
    value._pending_released = False
    value.session = SimpleNamespace(session_id=2, device_id="mouse", ready=True, stopping=False, running=False)
    value.config = {"ring_button": 416, "button_bindings": {"83": "copy"}}
    value.effective_config = None
    value._ring_config = None
    value.context = value.observer = None
    value.context_available = None
    value._context_token = value._context_revision = value._active_revision = 0
    value._pending_configs = {}
    value.window = SimpleNamespace(demo=False, current={"id": "mouse", "online": True, "stable_id": "unit:a1b2c3d4"})
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
    controller.execute.assert_called_once_with("copy", target={"window_id": 10}, preview=False, config=controller.config)


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


@pytest.fixture
def runtime(controller):
    from mx4_linux.actions import default_config, resolve_config
    value = controller
    value.config = default_config()
    value.config['assignment_profiles'] = {
        'general': {'label': 'General', 'app_id': '*', 'ring': value.config['ring'][:], 'button_bindings': {'82': 'copy'}},
        'editor': {'label': 'Editor', 'app_id': 'editor', 'ring': value.config['ring'][:], 'button_bindings': {'83': 'paste'}}}
    value.config['active_profiles'] = {'*': 'general', 'editor': 'editor'}
    value.effective_config = resolve_config(value.config)
    value.context = None
    value._context_revision = value._active_revision = value._context_token = 0
    value._pending_configs = {}
    value._ring_config = None
    value.observer = None
    value.action_busy = False
    value.actions = Mock()
    value.cancelled = threading.Event()
    value.window = SimpleNamespace(demo=False, current={'id': 'mouse', 'online': True, 'stable_id': 'unit:a1b2c3d4'}, update_desktop_status=Mock())
    value.session.configure = Mock(return_value=1)
    value.session.feedback = Mock()
    value.session.stop = Mock()
    value.session.running = True
    value.ring.state.visible = False
    return value


def test_context_profile_becomes_live_only_after_worker_ack(runtime):
    runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    runtime.execute.assert_not_called()
    runtime.event({'type': 'configured', 'config_id': 1})
    runtime.event({'type': 'button', 'control': 83, 'pressed': True})
    assert runtime.execute.call_args.args == ('paste',)
    assert runtime.effective_config['button_bindings'] == {'83': 'paste'}


def test_first_ring_press_after_ready_does_not_wait_for_redundant_configuration(runtime):
    runtime.open_ring = Mock()
    runtime.event({'type': 'ready'})
    runtime.session.configure.assert_not_called()
    runtime.event({'type': 'button', 'control': 416, 'pressed': True})
    runtime.open_ring.assert_called_once_with(held=True)


@pytest.mark.parametrize('page', ['ring', 'actions'])
def test_opening_editor_preserves_enabled_integration_and_cancels_popup(runtime, monkeypatch, page):
    editor = Mock()
    monkeypatch.setattr('mx4_linux.desktop_editor.present_desktop_editor', editor)
    runtime.store = Mock()
    runtime.requested_enabled = True
    runtime.opening = True
    runtime._held_ring_button = 416
    runtime._pending_motion = [10, 20]
    runtime._opening_motion.append((10, 20))
    before_open, before_dispatch = runtime._open_token, runtime._dispatch_token
    runtime.configure(page=page)
    runtime.session.stop.assert_not_called()
    assert runtime.requested_enabled
    assert not runtime.opening and runtime._held_ring_button is None
    assert runtime._open_token > before_open and runtime._dispatch_token > before_dispatch
    assert runtime._pending_motion == [0, 0] and not runtime._opening_motion
    runtime.ring.dismiss.assert_called_once()
    editor.assert_called_once_with(runtime.window, runtime.store, runtime.saved, cid=None, page=page)


def test_editor_save_updates_live_assignments_at_worker_boundary_without_disabling(runtime):
    runtime.requested_enabled = True
    document = deepcopy(runtime.config)
    document['assignment_profiles']['general']['button_bindings'] = {'82': 'paste'}
    runtime.store = SimpleNamespace(read=lambda: deepcopy(document))
    runtime.window.desktop_config_changed = Mock()
    runtime.saved()
    runtime.session.stop.assert_not_called()
    assert runtime.requested_enabled
    assert runtime.effective_config['button_bindings'] == {'82': 'copy'}
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    runtime.execute.assert_not_called()
    runtime.event({'type': 'configured', 'config_id': 1})
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    assert runtime.execute.call_args.args == ('paste',)
    runtime.window.desktop_config_changed.assert_called_once()


def test_closing_editor_releases_only_its_own_controller_reference(runtime):
    runtime.editor = SimpleNamespace()
    editor = runtime.editor
    runtime._editor_destroyed(SimpleNamespace())
    assert runtime.editor is editor
    runtime._editor_destroyed(editor)
    assert runtime.editor is None


def test_hidden_editor_releases_controller_reference_before_object_finalization(runtime):
    runtime.editor = SimpleNamespace(get_visible=Mock(return_value=True))
    editor = runtime.editor
    runtime._editor_visibility_changed(editor, None)
    assert runtime.editor is editor
    editor.get_visible.return_value = False
    runtime._editor_visibility_changed(editor, None)
    assert runtime.editor is None


def test_focus_change_with_identical_assignments_keeps_ring_and_shortcuts_ready(runtime):
    runtime.open_ring = Mock()
    runtime.event({'type': 'context', 'context': {'app_id': 'browser', 'window_id': 8}, 'context_token': 0})
    runtime.session.configure.assert_not_called()
    assert runtime._active_revision == runtime._context_revision
    runtime.event({'type': 'button', 'control': 416, 'pressed': True})
    runtime.open_ring.assert_called_once_with(held=True)
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    runtime.execute.assert_called_once_with('copy', target={'app_id': 'browser', 'window_id': 8},
                                           config=runtime.effective_config)


def test_returning_to_acknowledged_assignments_still_waits_for_pending_profile(runtime):
    runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
    runtime.session.configure.return_value = 2
    runtime.event({'type': 'context', 'context': {'app_id': 'browser', 'window_id': 8}, 'context_token': 0})
    assert runtime.session.configure.call_count == 2
    runtime.event({'type': 'configured', 'config_id': 1})
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    runtime.execute.assert_not_called()
    runtime.event({'type': 'configured', 'config_id': 2})
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    assert runtime.execute.call_args.args == ('copy',)


def test_observer_failure_returns_general_and_ignores_stale_context(runtime):
    from mx4_linux.actions import resolve_config
    runtime.context = {'app_id': 'editor', 'window_id': 7}
    runtime.effective_config = resolve_config(runtime.config, 'editor')
    runtime.event({'type': 'context-unavailable', 'context_token': 0, 'message': 'General fallback'})
    assert runtime.context is None
    assert runtime.session.configure.call_args.args[0]['button_bindings'] == {'82': 'copy'}
    runtime.stop()
    runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 8}, 'context_token': 0})
    assert runtime.context is None


def test_own_overlay_focus_preserves_external_application(runtime):
    runtime.context = {'app_id': 'editor', 'window_id': 7}
    runtime.ring.state.visible = True
    runtime.ring.overlay_supported = False
    runtime.ring.context_app_id = 'io.github.mx4linux.Ring.unique'
    runtime.event({'type': 'context', 'context': {'app_id': runtime.ring.context_app_id, 'window_id': 9}, 'context_token': 0})
    assert runtime.context == {'app_id': 'editor', 'window_id': 7}
    runtime.session.configure.assert_not_called()


def test_ring_selection_dispatch_uses_detached_open_document(runtime, monkeypatch):
    from copy import deepcopy
    scheduled = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda _ms, fn: scheduled.append(fn))
    runtime._ring_config = deepcopy(runtime.config)
    runtime._ring_config['actions']['copy']['label'] = 'Original'
    runtime.selected('copy', False)
    runtime._ring_config['actions']['copy']['label'] = 'Later'
    runtime.config['actions']['copy']['label'] = 'Edited'
    scheduled[0]()
    assert runtime.execute.call_args.kwargs['config']['actions']['copy']['label'] == 'Original'


@pytest.mark.parametrize('source,events,expected', [
    ('hover', {}, True), ('open', {}, False), ('select', {}, False), ('complete', {}, False),
    ('open', {'open': True}, True), ('hover', {'hover': False}, False)])
def test_feedback_source_defaults_and_waveforms(runtime, source, events, expected):
    runtime.config['haptic_events'] = events
    runtime.config['haptic_waveforms'] = {source: 3}
    runtime.feedback(source, config=runtime.config)
    assert runtime.session.feedback.called == expected
    if expected:
        runtime.session.feedback.assert_called_once_with(3)


@pytest.mark.parametrize('percent,suppressed', [(0, True), (10, True), (11, False),
    (None, False), ('5%', False), (True, False), (-1, False)])
def test_feedback_power_save_requires_fresh_numeric_low_battery(runtime, percent, suppressed):
    import time
    runtime.config['haptic_power_save'] = True
    runtime.window.current.update(battery_percent=percent, battery_observed_at=time.time())
    runtime.feedback('hover', config=runtime.config)
    assert runtime.session.feedback.called != suppressed


@pytest.mark.parametrize('state', ['master', 'preview', 'stopping', 'offline', 'stale-battery'])
def test_feedback_master_preview_session_and_unknown_battery(runtime, state):
    import time
    runtime.config['haptic_power_save'] = True
    runtime.window.current.update(battery_percent=1, battery_observed_at=time.time() - 300)
    if state == 'master':
        runtime.config['haptic_feedback'] = False
    elif state == 'stopping':
        runtime.session.stopping = True
    elif state == 'offline':
        runtime.window.current['online'] = False
    runtime.feedback('hover', config=runtime.config, preview=state == 'preview')
    assert runtime.session.feedback.called == (state == 'stale-battery')


def test_async_ring_open_pins_application_and_document_before_later_edits(runtime, monkeypatch):
    from concurrent.futures import Future
    from copy import deepcopy
    future = Future()
    scheduled = []
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    runtime.reload_config = runtime.reconcile_config = Mock()
    runtime.ring.show_ring = Mock()
    runtime.ring.preview = False
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    original = deepcopy(runtime.config)
    from mx4_linux.actions import resolve_config
    runtime.context = {'app_id': 'editor', 'window_id': 7}
    runtime.effective_config = resolve_config(runtime.config, 'editor')
    runtime.open_ring(held=True)
    runtime.config['actions']['copy']['label'] = 'Changed while querying'
    runtime.config['active_profiles'] = {}
    future.set_result({'app_id': 'editor', 'window_id': 7})
    scheduled.pop()()
    shown = runtime.ring.show_ring.call_args.args[0]
    assert shown['actions']['copy']['label'] == original['actions']['copy']['label']
    assert shown['button_bindings'] == {'83': 'paste'}
    runtime.context = {'app_id': 'browser', 'window_id': 8}
    assert runtime._ring_config['button_bindings'] == {'83': 'paste'}


def test_new_focus_cannot_revive_old_pending_profile_ack(runtime):
    runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
    runtime.session.configure.return_value = 2
    runtime.event({'type': 'context', 'context': {'app_id': 'browser', 'window_id': 8}, 'context_token': 0})
    runtime.event({'type': 'configured', 'config_id': 1})
    runtime.event({'type': 'button', 'control': 83, 'pressed': True})
    runtime.execute.assert_not_called()
    runtime.event({'type': 'configured', 'config_id': 2})
    runtime.event({'type': 'button', 'control': 82, 'pressed': True})
    assert runtime.execute.call_args.args == ('copy',)


@pytest.mark.parametrize('timestamp', [None, True, float('nan'), float('inf'), 1001, 939])
def test_unknown_or_stale_battery_timestamp_does_not_suppress(runtime, monkeypatch, timestamp):
    monkeypatch.setattr('mx4_linux.desktop.time.time', lambda: 1000)
    runtime.config['haptic_power_save'] = True
    runtime.window.current.update(battery_percent=5, battery_observed_at=timestamp)
    runtime.feedback('hover', config=runtime.config)
    runtime.session.feedback.assert_called_once()


def test_ring_open_pending_motion_preserves_radius_clamping(runtime, monkeypatch):
    from concurrent.futures import Future
    future = Future()
    scheduled = []
    state = RingState()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    runtime.reload_config = runtime.reconcile_config = Mock()
    runtime.ring.preview = False
    runtime.ring.show_ring = lambda *_a, **_kw: state.open(held=True)
    runtime.ring.motion = state.motion
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    from mx4_linux.actions import resolve_config
    runtime.context = {'app_id': 'editor', 'window_id': 7}
    runtime.effective_config = resolve_config(runtime.config, 'editor')
    runtime.open_ring(held=True)
    runtime.event({'type': 'motion', 'dx': 1000, 'dy': 0})
    runtime.event({'type': 'motion', 'dx': -1000, 'dy': 0})
    future.set_result({'app_id': 'editor', 'window_id': 7})
    scheduled.pop()()
    assert state.x == -155


def test_real_action_snapshot_completion_feedback_and_runner_cleanup(runtime, monkeypatch):
    from concurrent.futures import Future
    from copy import deepcopy
    queued, callbacks = [], []
    runner = SimpleNamespace(execute=Mock(return_value={'ok': True, 'label': 'Copy', 'completed': 1}), close=Mock())
    constructed = []
    def create(config, **_kw):
        constructed.append(deepcopy(config))
        return runner
    def submit(fn):
        future = Future()
        queued.append((fn, future))
        return future
    runtime.execute = DesktopController.execute.__get__(runtime)
    runtime.executor = SimpleNamespace(submit=submit)
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', create)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', lambda _wid: {'app_id': 'editor', 'window_id': 7})
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: callbacks.append(fn))
    runtime.config['haptic_events'] = {'complete': True}
    runtime.execute('copy', target={'app_id': 'editor', 'window_id': 7}, config=runtime.config)
    original_label = runtime.config['actions']['copy']['label']
    runtime.config['actions']['copy']['label'] = 'Changed'
    fn, future = queued.pop()
    future.set_result(fn())
    callbacks.pop()()
    assert constructed[0]['actions']['copy']['label'] == original_label
    runner.close.assert_called_once()
    runtime.session.feedback.assert_called_once_with(0)


@pytest.mark.parametrize('outcome', ['cancelled', 'preview', 'skipped', 'failed'])
def test_incomplete_or_preview_action_does_not_emit_completion_feedback(runtime, monkeypatch, outcome):
    from concurrent.futures import Future
    callbacks = []
    result = {'ok': True, 'label': 'Copy', 'completed': 1, 'steps': []}
    if outcome == 'skipped':
        result.update(skipped=True, reason='app guard')
    runner = SimpleNamespace(execute=Mock(return_value=result), close=Mock())
    if outcome == 'failed':
        runner.execute.side_effect = RuntimeError('failed')
    def submit(fn):
        future = Future()
        try:
            result = fn()
            if outcome == 'cancelled':
                runtime.cancelled.set()
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)
        return future
    runtime.execute = DesktopController.execute.__get__(runtime)
    runtime.executor = SimpleNamespace(submit=submit)
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', lambda *_a, **_kw: runner)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', lambda _wid: {'app_id': 'editor', 'window_id': 7})
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: callbacks.append(fn))
    runtime.config['haptic_events'] = {'complete': True}
    runtime.execute('copy', target={'app_id': 'editor', 'window_id': 7}, preview=outcome == 'preview', config=runtime.config)
    callbacks.pop()()
    runtime.session.feedback.assert_not_called()
    runner.close.assert_called_once()


@pytest.mark.parametrize('context,binding', [({'app_id': 'editor', 'window_id': 7}, '83'), (None, '82')])
def test_start_asynchronously_resolves_active_profile_before_first_diversion(runtime, monkeypatch, context, binding):
    from concurrent.futures import Future
    future, scheduled = Future(), []
    runtime.starting = False
    runtime.session.running = runtime.session.ready = False
    runtime.window.busy = False
    runtime.session.start = Mock()
    runtime.reload_config = runtime.reconcile_config = Mock()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    observer = SimpleNamespace(start=Mock(), stop=Mock())
    monkeypatch.setattr('mx4_linux.desktop.NiriFocusObserver', lambda _cb: observer)
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    runtime.start()
    runtime.session.start.assert_not_called()
    future.set_result(context)
    scheduled.pop()()
    assert list(runtime.session.start.call_args.args[1]['button_bindings']) == [binding]
    observer.start.assert_called_once()


def test_stop_invalidates_initial_context_query_without_starting_worker(runtime, monkeypatch):
    from concurrent.futures import Future
    future, scheduled = Future(), []
    runtime.starting = False
    runtime.session.running = runtime.session.ready = False
    runtime.window.busy = False
    runtime.session.start = Mock()
    runtime.reload_config = runtime.reconcile_config = Mock()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    runtime.start()
    runtime.stop()
    future.set_result({'app_id': 'editor', 'window_id': 7})
    scheduled.pop()()
    runtime.session.start.assert_not_called()


def test_close_stops_observer_and_rejects_queued_context_callbacks(runtime):
    runtime.executor = SimpleNamespace(submit=Mock(), shutdown=Mock())
    runtime.observer = SimpleNamespace(stop=Mock())
    observer = runtime.observer
    runtime.ring.hide_ring = Mock()
    runtime.ring.destroy = Mock()
    runtime.close()
    observer.stop.assert_called_once()
    runtime.session.stop.assert_called_once()
    runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
    assert runtime.context is None
    runtime.session.configure.assert_not_called()
    runtime.executor.shutdown.assert_called_once_with(wait=False, cancel_futures=False)


def test_stop_before_executor_dispatch_does_not_restore_focus_or_run_action(runtime, monkeypatch):
    from concurrent.futures import Future
    queued = []
    runtime.execute = DesktopController.execute.__get__(runtime)
    runtime.executor = SimpleNamespace(submit=lambda fn: queued.append(fn) or Future())
    runner, restore = Mock(), Mock()
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', runner)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', restore)
    runtime.execute('copy', target={'app_id': 'editor', 'window_id': 7}, config=runtime.config)
    runtime.stop()
    with pytest.raises(RuntimeError, match='cancelled'):
        queued.pop()()
    runner.assert_not_called()
    restore.assert_not_called()


def test_feedback_rejects_device_selection_changed_while_session_ready(runtime):
    runtime.window.current['id'] = 'replacement-device'
    runtime.feedback('hover')
    runtime.session.feedback.assert_not_called()


@pytest.mark.parametrize('failure', ['unsupported-waveform', 'hardware-disabled', 'playback-rejected'])
def test_optional_feedback_warning_keeps_real_worker_context_transitions_alive(runtime, failure, tmp_path, monkeypatch):
    """Exercise the worker protocol, including native restore after the warning."""
    import struct

    from logitech_receiver.exceptions import FeatureCallError
    from test_event_worker import HAPTIC, INDEX, add_control, run, setup_session

    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    before = dict(dev.reporting)
    runtime.observer = SimpleNamespace(stop=Mock())
    runtime.open_ring = Mock()
    observer = runtime.observer
    setting = SimpleNamespace(choices=[0] if failure == 'unsupported-waveform' else [0, 1], write=Mock())
    api.settings_templates.SETTINGS = [SimpleNamespace(name='haptic-play')]
    api.settings_templates.check_feature.return_value = setting
    if failure == 'playback-rejected':
        setting.write.side_effect = FeatureCallError(number=2, request=2893, error=5, params=b'\x00')
    elif failure == 'hardware-disabled':
        original = dev._feature_request

        def request(feature, function, *data):
            if feature == HAPTIC:
                assert function == 0x10 and not data
                return b'\x00\x32\x00'
            return original(feature, function, *data)

        dev.feature_request.side_effect = request
    commands.put({'op': 'haptic', 'waveform': 1})
    if failure != 'unsupported-waveform':
        commands.put({'op': 'haptic', 'waveform': 1})
    config_id, reads = 0, 0

    def configure(config):
        nonlocal config_id
        config_id += 1
        from mx4_linux.event_session import _reporting_request
        commands.put({'op': 'configure', 'config_id': config_id, **_reporting_request(config)})
        return config_id

    def emit(event):
        events.append(event)
        runtime.event(event)

    class Events:
        append = staticmethod(emit)

    def read(*_args):
        nonlocal reads
        reads += 1
        assert runtime.error is None
        assert runtime.session.ready
        runtime.session.stop.assert_not_called()
        observer.stop.assert_not_called()
        if reads == 1:
            assert runtime._context_token == 0
            runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
        elif reads == 2:
            assert dev.reporting[82] == before[82]
            assert dev.reporting[83][0] & 1
            return 17, 255, bytes([INDEX, 0]) + struct.pack('!4H', 83, 0, 0, 0) + b'\0' * 8
        elif reads == 3:
            assert runtime.execute.call_args.args == ('paste',)
            return 17, 255, bytes([INDEX, 0]) + struct.pack('!4H', 416, 0, 0, 0) + b'\0' * 8
        else:
            runtime.open_ring.assert_called_once_with(held=True)
            commands.put({'op': 'stop'})

    runtime.session.configure.side_effect = configure
    api.base.read.side_effect = read
    assert run(dev, api, commands, Events(), controls=[82, 416])
    warnings = [e for e in events if e['type'] == 'warning' and e['source'] == 'haptic']
    assert len(warnings) == 1
    assert events[0]['type'] == 'ready'
    assert not any(e['type'] == 'error' for e in events)
    assert events[-1] == {'type': 'stopped', 'restored': True}
    if failure == 'playback-rejected':
        assert 'HID++ error 0x05, request 0x0B4D' in warnings[0]['message']
        setting.write.assert_called_once_with(1, save=False)
    else:
        setting.write.assert_not_called()
    assert dev.reporting == before


def test_fatal_integration_error_requests_restoration_and_stops_observation(runtime):
    runtime.observer = SimpleNamespace(stop=Mock())
    observer = runtime.observer
    runtime.event({'type': 'error', 'message': 'Mouse reporting configuration could not be confirmed'})
    runtime.session.stop.assert_called_once()
    observer.stop.assert_called_once()
    assert runtime.error == 'Mouse reporting configuration could not be confirmed'
    assert runtime._context_token == 1

@pytest.fixture
def wheel_runtime(runtime, monkeypatch):
    from concurrent.futures import Future
    from copy import deepcopy
    runtime.execute = DesktopController.execute.__get__(runtime)
    runtime._adjustment_pending = runtime._adjustment_yield = runtime._selection_pending = None
    runtime._adjustment_active = runtime._adjustment_running = False
    runtime._adjustment_token = 0
    runtime._ring_focus_lost = threading.Event()
    runtime._ring_config = deepcopy(runtime.config)
    runtime._ring_config['actions']['copy']['adjustment'] = {
        'increase': [{'type': 'text', 'text': '+'}], 'decrease': [{'type': 'text', 'text': '-'}]}
    runtime.target = {'app_id': 'editor', 'window_id': 7}
    runtime.ring.state.visible = True
    runtime.ring.yield_keyboard, runtime.ring.resume_keyboard = Mock(), Mock()
    runtime.ring.dismiss = lambda: (setattr(runtime.ring.state, 'visible', False), runtime.dismissed())
    queued, idle, timers, calls = [], [], [], []
    def submit(fn):
        future = Future()
        queued.append((fn, future))
        return future
    def runner(config, **kw):
        return SimpleNamespace(execute=lambda action, **args: calls.append((deepcopy(config), action, args))
            or {'ok': True, 'label': 'Copy', 'completed': 1}, close=Mock())
    runtime.executor = SimpleNamespace(submit=submit)
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', runner)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', lambda _wid: {'app_id': 'editor', 'window_id': 7})
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: {'app_id': 'editor', 'window_id': 7})
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: idle.append(fn))
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda _ms, fn: timers.append(fn))
    def finish():
        fn, future = queued.pop(0)
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)
        idle.pop(0)()
    return SimpleNamespace(controller=runtime, queued=queued, idle=idle, timers=timers, calls=calls, finish=finish)


def test_wheel_burst_is_bounded_coalesces_and_pins_document_target(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    assert runtime.adjusted('copy', 'increase')
    runtime.ring.yield_keyboard.assert_called_once()
    proof.timers.pop()()
    for _ in range(1000):
        assert runtime.adjusted('copy', 'increase')
    assert len(proof.queued) == 1
    assert runtime._adjustment_pending['count'] == 4
    for _ in range(4):
        runtime.adjusted('copy', 'decrease')
    assert runtime._adjustment_pending is None
    runtime.target = {'app_id': 'other', 'window_id': 99}
    runtime._ring_config['actions']['copy']['label'] = 'Edited later'
    proof.finish()
    assert proof.calls[0][0]['actions']['copy']['label'] == 'Copy'
    assert proof.calls[0][2]['adjustment'] == 'increase'
    runtime.ring.resume_keyboard.assert_called_once()


@pytest.mark.parametrize('context', [None, {'app_id': 'io.github.mx4linux.Control', 'window_id': 7}])
def test_wheel_selected_branch_checks_keyboard_guard_even_when_main_is_command(wheel_runtime, monkeypatch, context):
    proof = wheel_runtime
    runtime = proof.controller
    runtime._ring_config['actions']['copy']['steps'] = [{'type': 'command', 'argv': ['true']}]
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', lambda _wid: context)
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()
    assert not proof.calls
    assert 'another application' in runtime.notify.call_args.args[0]


def test_main_selection_waits_for_cancelled_adjustment_then_runs_exactly_once(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    runtime.ring.state.visible = False
    runtime.selected('copy', False)
    proof.timers.pop()()  # Ordinary 80ms elapsed while old future is still active.
    assert len(proof.queued) == 1
    proof.finish()
    assert len(proof.queued) == 1
    proof.finish()
    assert len(proof.calls) == 1
    assert 'adjustment' not in proof.calls[0][2]
    runtime.ring.resume_keyboard.assert_not_called()


def test_dismissal_cancels_pending_and_active_and_stale_completion_cannot_reopen(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    runtime.adjusted('copy', 'increase')
    runtime.ring.dismiss()
    assert runtime._adjustment_pending is None
    proof.finish()
    assert not proof.calls
    runtime.ring.resume_keyboard.assert_not_called()
    assert not runtime.ring.state.visible


def test_external_focus_cancels_wheel_but_pinned_source_and_overlay_do_not(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.adjusted('copy', 'increase')
    runtime.event({'type': 'context', 'context': runtime.target, 'context_token': 0})
    assert runtime._adjustment_active
    runtime.event({'type': 'context', 'context': None, 'context_token': 0})
    assert runtime._adjustment_active
    runtime.event({'type': 'context', 'context': {'app_id': 'other', 'window_id': 8}, 'context_token': 0})
    assert not runtime._adjustment_active
    proof.timers.pop()()
    assert not proof.queued
    runtime.ring.resume_keyboard.assert_not_called()

def test_main_selection_still_waits_for_unmap_delay_when_adjustment_finishes_first(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    runtime.ring.state.visible = False
    runtime.selected('copy', False)
    proof.finish()
    assert not proof.queued  # The layer-unmap timeout has not fired yet.
    proof.timers.pop()()
    assert len(proof.queued) == 1
    proof.finish()
    assert len(proof.calls) == 1

def test_ordinary_binding_during_wheel_yield_gap_cannot_steal_single_future(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.adjusted('copy', 'increase')
    runtime.execute('paste', target=runtime.target, config=runtime.config)
    assert not proof.queued
    proof.timers.pop()()
    proof.finish()
    assert [call[1] for call in proof.calls] == ['copy']


def test_unknown_pinned_target_cannot_type_into_later_focused_application(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.target = None
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: {'app_id': 'editor', 'window_id': 7})
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()
    assert not proof.calls

def test_standalone_ring_owns_observer_and_stale_focus_callbacks_cannot_cancel_next_ring(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.session.running = False
    callbacks, observers = [], []
    def observer(callback):
        callbacks.append(callback)
        value = SimpleNamespace(start=Mock(), stop=Mock())
        observers.append(value)
        return value
    monkeypatch.setattr('mx4_linux.desktop.NiriFocusObserver', observer)
    runtime._watch_ring_context()
    observers[0].start.assert_called_once()
    runtime.dismissed()
    observers[0].stop.assert_called_once()
    runtime._watch_ring_context()
    callbacks[0]({'type': 'context', 'context': {'app_id': 'other', 'window_id': 99}})
    runtime.flush_events()
    assert not runtime._ring_focus_lost.is_set()
    callbacks[1]({'type': 'context', 'context': {'app_id': 'other', 'window_id': 99}})
    runtime.flush_events()
    assert runtime._ring_focus_lost.is_set()
    assert not runtime.ring.state.visible


def test_delayed_adjustment_cancellation_predicate_rechecks_exact_pinned_window(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    current = {'app_id': 'editor', 'window_id': 7}
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: current.copy())
    def create_runner(_config, **_kwargs):
        def execute(_action, cancelled, **_kw):
            assert not cancelled()
            current['window_id'] = 99
            if cancelled():
                raise RuntimeError('cancelled before text')
            pytest.fail('The delayed branch would type into a different focused window')
        return SimpleNamespace(execute=execute, close=Mock())
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', create_runner)
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()
    runtime.ring.resume_keyboard.assert_not_called()
    assert not runtime.ring.state.visible

@pytest.mark.parametrize('phase', ['before-dispatch', 'during-command'])
def test_actual_settings_focus_cancels_adjustment_even_with_own_app_prefix(wheel_runtime, monkeypatch, phase):
    proof = wheel_runtime
    runtime = proof.controller
    current = {'app_id': 'editor', 'window_id': 7}
    settings = {'app_id': 'io.github.mx4linux.Control', 'window_id': 99}
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: current.copy())
    runtime._ring_config['actions']['copy']['adjustment']['increase'] = [
        {'type': 'delay', 'milliseconds': 300}, {'type': 'command', 'argv': ['true']}]
    restore = Mock(return_value=runtime.target)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', restore)
    def create_runner(_config, **_kwargs):
        def execute(_action, cancelled, **_kw):
            assert not cancelled()
            current.update(settings)
            if cancelled():
                raise RuntimeError('cancelled before command')
            pytest.fail('Command would execute after settings gained focus')
        return SimpleNamespace(execute=execute, close=Mock())
    monkeypatch.setattr('mx4_linux.desktop.DesktopActions', create_runner)
    runtime.adjusted('copy', 'increase')
    if phase == 'before-dispatch':
        current.update(settings)
    proof.timers.pop()()
    proof.finish()
    if phase == 'before-dispatch':
        restore.assert_not_called()
    runtime.ring.resume_keyboard.assert_not_called()
    assert not runtime.ring.state.visible


def test_settings_focus_is_not_preserved_as_owned_overlay_by_integration_observer(runtime):
    runtime.context = runtime.target = {'app_id': 'editor', 'window_id': 7}
    runtime.ring.state.visible = True
    runtime.ring.overlay_supported = True
    runtime.event({'type': 'context', 'context': {'app_id': 'io.github.mx4linux.Control', 'window_id': 99},
                   'context_token': 0})
    runtime.ring.dismiss.assert_called_once()
    assert runtime.context['window_id'] == 99


def test_popup_context_binds_exact_window_and_stale_identity_cannot_replace_new_open(runtime):
    runtime.ring.overlay_supported = False
    runtime.ring.context_app_id = 'io.github.mx4linux.Ring.unique'
    own = {'app_id': runtime.ring.context_app_id, 'window_id': 91}
    assert runtime._is_ring_context(own)
    assert not runtime._is_ring_context(own | {'window_id': 92})
    old_identity = runtime._ring_identity
    runtime._ring_identity = new_identity = {'app_id': runtime.ring.context_app_id, 'window_id': None}
    assert runtime._is_ring_context(own, old_identity)
    assert new_identity['window_id'] is None
    assert runtime._is_ring_context(own | {'window_id': 92})


def test_layer_surface_never_exempts_another_window_even_with_ring_app_id(runtime):
    runtime.ring.overlay_supported = True
    runtime.ring.context_app_id = 'io.github.mx4linux.Ring.unique'
    assert not runtime._is_ring_context({'app_id': runtime.ring.context_app_id, 'window_id': 91})


def test_non_layer_reacquisition_uses_exact_popup_and_never_restores_after_close(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.ring.overlay_supported = False
    runtime._ring_identity = {'app_id': 'io.github.mx4linux.Ring.unique', 'window_id': 91}
    restore = Mock(side_effect=lambda wid: runtime.target if wid == 7 else runtime._ring_identity)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', restore)
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()  # An async exact-popup focus check remains, with no action queued.
    runtime.ring.resume_keyboard.assert_not_called()
    assert len(proof.queued) == 1
    runtime.ring.dismiss()
    proof.finish()
    assert [call.args[0] for call in restore.call_args_list] == [7]
    runtime.ring.resume_keyboard.assert_not_called()


def test_non_layer_reacquisition_verifies_source_before_focusing_popup(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.ring.overlay_supported = False
    runtime._ring_identity = {'app_id': 'io.github.mx4linux.Ring.unique', 'window_id': 91}
    restore = Mock(side_effect=lambda wid: runtime.target if wid == 7 else runtime._ring_identity)
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', restore)
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()
    monkeypatch.setattr('mx4_linux.desktop.focused_context',
                        lambda: {'app_id': 'io.github.mx4linux.Control', 'window_id': 99})
    proof.finish()
    assert [call.args[0] for call in restore.call_args_list] == [7]
    assert not runtime.ring.state.visible
    runtime.ring.resume_keyboard.assert_not_called()


def test_unknown_popup_identity_never_requests_focus(wheel_runtime):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.ring.overlay_supported = False
    runtime._ring_identity = {'app_id': None, 'window_id': None}
    runtime._resume_adjustment_keyboard(runtime._adjustment_token)
    assert not proof.queued
    runtime.ring.resume_keyboard.assert_not_called()
    assert not runtime.ring.state.visible


def test_no_target_general_command_adjustment_remains_available(wheel_runtime, monkeypatch):
    proof = wheel_runtime
    runtime = proof.controller
    runtime.target = None
    runtime._ring_config['actions']['copy']['adjustment']['increase'] = [{'type': 'command', 'argv': ['true']}]
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: None)
    runtime.adjusted('copy', 'increase')
    proof.timers.pop()()
    proof.finish()
    assert [call[1] for call in proof.calls] == ['copy']


def test_unknown_restoration_never_reports_restored(controller):
    controller.event({'type': 'stopped', 'restored': None})
    assert 'not confirmed' in controller.notify.call_args.args[0]


def test_manual_stop_cancels_requested_resume_and_pending_callbacks(controller):
    controller.requested_enabled = True
    controller._intent_generation = 3
    controller.reconnect_source = 0
    controller._stop_waiters = []
    controller.stop()
    assert controller.requested_enabled is False
    assert controller._intent_generation == 4


def test_wait_stopped_uses_final_restoration_outcome_after_exit(runtime, monkeypatch):
    callbacks, seen = [], []
    runtime.session.running = True
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda _ms, fn: callbacks.append(fn) or len(callbacks))
    runtime.wait_stopped(seen.append)
    assert seen == []
    runtime.session.running = runtime.session.stopping = False
    runtime.event({'type': 'stopped', 'restored': False})
    assert seen == [False]
    callbacks[0]()
    assert seen == [False]


def test_reconnect_same_unit_new_connection_recovers_then_starts_once(runtime, monkeypatch):
    runtime.restoration_confirmed = True
    runtime.window.busy = False
    runtime.requested_enabled = True
    runtime.hardware_paused = False
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.config['resume_integration'] = True
    runtime.session.running = runtime.session.stopping = False
    device = {'id': 'new-connection', 'online': True, 'stable_id': 'unit:a1b2c3d4', 'settings': []}
    result = {'devices': [dict(device, id='other', stable_id='unit:d1e2f3a4'), device], 'diagnostics': []}
    runtime.window.backend = SimpleNamespace(scan=Mock(return_value=result), recover=Mock())
    runtime.window.render = Mock()
    runtime.window.job = lambda work, done, _msg, failed: done(work())
    runtime.start = Mock()
    replay = Mock(return_value=0)
    monkeypatch.setattr('mx4_linux.profiles.replay_remembered', replay)
    runtime._reconnect(runtime._intent_generation)
    runtime.window.backend.recover.assert_called_once_with('new-connection', 'unit:a1b2c3d4')
    assert replay.call_count == 1
    assert runtime.window.selected_id == 'new-connection'
    runtime.start.assert_called_once_with(automatic=True)


def test_manual_stop_while_reconnect_probe_pending_prevents_recovery_replay_start(runtime, monkeypatch):
    runtime.restoration_confirmed = True
    runtime.window.busy = False
    runtime.requested_enabled = True
    runtime.hardware_paused = False
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.session.running = runtime.session.stopping = False
    device = {'id': 'mouse', 'online': True, 'stable_id': 'unit:a1b2c3d4', 'settings': []}
    runtime.window.backend = SimpleNamespace(scan=Mock(return_value={'devices': [device]}), recover=Mock())
    queued = []
    runtime.window.job = lambda work, done, _msg, failed: queued.append((work, done, failed))
    runtime.start = Mock()
    replay = Mock()
    monkeypatch.setattr('mx4_linux.profiles.replay_remembered', replay)
    runtime._reconnect(runtime._intent_generation)
    runtime.stop()
    with pytest.raises(RuntimeError, match='not awake'):
        queued[0][0]()
    queued[0][2]('cancelled')
    runtime.window.backend.recover.assert_not_called()
    replay.assert_not_called()
    runtime.start.assert_not_called()
    assert not runtime._reconnect_busy


def test_enabled_integration_reconnects_without_preference_and_caps_backoff(runtime, monkeypatch):
    runtime.restoration_confirmed = True
    scheduled = []
    runtime.requested_enabled = True
    runtime.hardware_paused = False
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda delay, fn: scheduled.append((delay, fn)) or len(scheduled))
    for index, seconds in enumerate((1, 2, 5, 10, 20, 30, 30, 30)):
        runtime.reconnect_source = 0
        runtime.reconnect_attempt = index
        runtime._schedule_reconnect()
        assert scheduled[-1][0] == seconds * 1000
    runtime.reconnect_source = 0
    runtime.reconnect_attempt = 500
    runtime._schedule_reconnect()
    assert len(scheduled) == 9
    assert scheduled[-1][0] == 30000


def test_explicit_recovery_can_clear_prior_unknown_stop_for_next_operation(runtime):
    runtime.session.running = runtime.session.stopping = False
    runtime.session.process = object()
    runtime.session.restored = None
    runtime.restoration_confirmed = True  # Confirmed by a separate same-unit recovery operation.
    seen = []
    runtime.wait_stopped(seen.append)
    assert seen == [True]


@pytest.mark.parametrize('restored', [False, None])
def test_unconfirmed_disconnect_recovers_same_unit_before_resuming(runtime, monkeypatch, restored):
    scheduled = []
    runtime.requested_enabled = True
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.session.running = runtime.session.stopping = False
    runtime.window.busy = False
    device = {'id': 'reconnected', 'online': True, 'stable_id': runtime.target_identity, 'settings': []}
    order = []
    runtime.window.backend = SimpleNamespace(
        scan=Mock(return_value={'devices': [device]}),
        recover=Mock(side_effect=lambda *_args: order.append('recover')))
    runtime.window.render = Mock()
    runtime.window.job = lambda work, done, _msg, failed: done(work())
    runtime.start = Mock(side_effect=lambda **_kwargs: order.append('start'))
    replay = Mock(side_effect=lambda *_args, **_kwargs: order.append('replay'))
    monkeypatch.setattr('mx4_linux.profiles.replay_remembered', replay)
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda delay, fn: scheduled.append((delay, fn)) or len(scheduled))
    runtime.event({'type': 'stopped', 'restored': restored})
    assert runtime.restoration_confirmed is restored
    assert [delay for delay, _fn in scheduled] == [1000]
    scheduled.pop()[1]()
    runtime.window.backend.recover.assert_called_once_with('reconnected', runtime.target_identity)
    assert order == ['recover', 'replay', 'start']
    assert runtime.restoration_confirmed is True
    runtime.start.assert_called_once_with(automatic=True)


def test_reconnect_recovery_failure_retries_without_replaying_or_starting(runtime, monkeypatch):
    runtime.requested_enabled = True
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.session.running = runtime.session.stopping = False
    runtime.window.busy = False
    device = {'id': 'mouse', 'online': True, 'stable_id': runtime.target_identity, 'settings': []}
    runtime.window.backend = SimpleNamespace(scan=Mock(return_value={'devices': [device]}),
                                           recover=Mock(side_effect=RuntimeError('Recovery not confirmed')))
    def job(work, done, _message, failed):
        try:
            result = work()
        except RuntimeError as error:
            failed(str(error))
        else:
            done(result)
    runtime.window.job = job
    runtime.start = Mock()
    replay = Mock()
    monkeypatch.setattr('mx4_linux.profiles.replay_remembered', replay)
    timers = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda ms, fn: timers.append((ms, fn)) or len(timers))
    runtime._reconnect(runtime._intent_generation)
    runtime.window.backend.recover.assert_called_once_with('mouse', runtime.target_identity)
    replay.assert_not_called()
    runtime.start.assert_not_called()
    assert runtime.restoration_confirmed is None
    assert not runtime._reconnect_busy
    assert [ms for ms, _fn in timers] == [2000]


def test_reconnect_keeps_waiting_for_original_mouse_after_long_disconnect(runtime, monkeypatch):
    runtime.requested_enabled = True
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.session.running = runtime.session.stopping = False
    runtime.reconnect_attempt = 500
    runtime.window.busy = False
    other = {'id': 'other-mouse', 'online': True, 'stable_id': 'unit:d1e2f3a4', 'settings': []}
    runtime.window.backend = SimpleNamespace(scan=Mock(return_value={'devices': [other]}), recover=Mock())
    def job(work, done, _message, failed):
        try:
            result = work()
        except RuntimeError as error:
            failed(str(error))
        else:
            done(result)
    runtime.window.job = job
    runtime.start = Mock()
    timers = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda ms, fn: timers.append((ms, fn)) or len(timers))
    runtime._reconnect(runtime._intent_generation)
    runtime.window.backend.recover.assert_not_called()
    runtime.start.assert_not_called()
    assert runtime.target_identity == 'unit:a1b2c3d4'
    assert [ms for ms, _fn in timers] == [30000]


@pytest.mark.parametrize('disconnected_during_focus_query', [False, True])
def test_automatic_start_failure_keeps_reconnecting(runtime, monkeypatch, disconnected_during_focus_query):
    from concurrent.futures import Future
    runtime.requested_enabled = True
    runtime.target_identity = 'unit:a1b2c3d4'
    runtime.session.running = runtime.session.ready = False
    runtime.session.start = Mock(side_effect=OSError('Device disappeared'))
    runtime.window.busy = False
    runtime.reload_config = Mock()
    future = Future()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    callbacks, timers = [], []
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: callbacks.append(fn))
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda ms, fn: timers.append((ms, fn)) or len(timers))
    runtime.start(automatic=True)
    if disconnected_during_focus_query:
        runtime.window.current = None
    future.set_result(None)
    callbacks.pop()()
    assert runtime.requested_enabled
    assert not runtime.starting
    assert [ms for ms, _fn in timers] == [1000]
    if disconnected_during_focus_query:
        runtime.session.start.assert_not_called()
    else:
        runtime.session.start.assert_called_once()


def test_worker_conflict_followed_by_confirmed_cleanup_inhibits_auto_acquisition(runtime, monkeypatch, tmp_path):
    from test_event_worker import setup_session, TARGET
    from mx4_linux import event_worker
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    dev, api, commands, events = setup_session()
    def emit(event):
        events.append(event)
        if event['type'] == 'ready':
            flags, mapping = dev.reporting[416]
            dev.reporting[416] = (flags & ~16, mapping)
            commands.put({'op': 'configure', 'config_id': 1, 'controls': [416], 'ring_button': 416, 'raw_xy': True})
    assert not event_worker.run({'device_id': TARGET}, api, commands, emit)
    assert events[-1]['restored'] is True
    runtime.requested_enabled = True
    runtime.config['resume_integration'] = True
    runtime.session.running = False
    runtime.window.busy = False
    runtime.window.job = Mock()
    runtime.start = Mock()
    timers = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda ms, fn: timers.append(fn) or len(timers))
    for event in events:
        if event['type'] in ('error', 'stopped'):
            runtime.event(event)
    assert not timers
    runtime._reconnect(runtime._intent_generation)  # A stale already-queued callback is inert too.
    runtime.window.job.assert_not_called()
    runtime.start.assert_not_called()
    assert runtime.reacquire_blocked is True


def test_explicit_valid_enable_clears_ownership_conflict_latch(runtime, monkeypatch):
    from concurrent.futures import Future
    runtime.reacquire_blocked = True
    runtime.session.running = runtime.session.ready = False
    runtime.window.busy = False
    runtime.reload_config = runtime.reconcile_config = Mock()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=Future()))
    runtime.start()
    assert runtime.reacquire_blocked is False


def test_ordinary_transport_error_with_confirmed_cleanup_retains_opt_in_reconnect(runtime, monkeypatch):
    runtime.requested_enabled = True
    runtime.config['resume_integration'] = True
    runtime.session.running = False
    timers = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda ms, fn: timers.append((ms, fn)) or len(timers))
    runtime.event({'type': 'error', 'message': 'Mouse disconnected'})
    runtime.event({'type': 'stopped', 'restored': True})
    assert runtime.reacquire_blocked is False
    assert [ms for ms, _fn in timers] == [1000]


def test_stale_trigger_press_is_suppressed_until_current_ack(controller):
    controller.ring.state.visible = False
    controller.open_ring = Mock()
    controller._context_revision = 2
    controller._active_revision = 1
    controller.event({'type': 'button', 'control': 416, 'pressed': True})
    controller.open_ring.assert_not_called()
    controller._active_revision = 2
    controller.event({'type': 'button', 'control': 416, 'pressed': True})
    controller.open_ring.assert_called_once_with(held=True)


def test_adaptation_reconcile_keeps_open_invocation_and_intent(controller):
    from copy import deepcopy
    from mx4_linux.actions import default_config
    document = default_config()
    document['assignment_profiles'] = {'app': {'label': 'App', 'app_id': 'app.a',
        'ring': list(reversed(document['ring'])), 'button_bindings': {'83': 'copy'}, 'ring_button': 82}}
    document['active_profiles'] = {'app.a': 'app'}
    controller.config = deepcopy(document)
    controller.context = {'app_id': 'app.a', 'window_id': 10}
    controller._ring_config = deepcopy(document)
    pinned = controller._ring_config
    controller.target = deepcopy(controller.context)
    controller.requested_enabled = True
    controller.session.running = True
    controller.session.configure = Mock(return_value=5)
    controller.actions = SimpleNamespace(config=deepcopy(document))
    document['adapt_to_app'] = False
    controller.store = SimpleNamespace(read=Mock(return_value=document))
    controller.reconcile_config()
    assert controller.config['adapt_to_app'] is False
    assert controller._ring_config is pinned and controller.target == controller.context
    assert controller.requested_enabled and controller._dispatch_token == 0
    controller.ring.dismiss.assert_not_called()
    future, revision = controller._pending_configs[5]
    assert future['ring_button'] == 416 and revision == controller._context_revision
    assert controller.context == {'app_id': 'app.a', 'window_id': 10}
    controller.event({'type': 'configured', 'config_id': 5})
    assert controller.effective_config['ring_button'] == 416


def test_preference_during_start_does_not_discard_actual_focus_query(runtime, monkeypatch):
    from concurrent.futures import Future
    future, scheduled = Future(), []
    runtime.starting = False
    runtime.session.running = runtime.session.ready = False
    runtime.window.busy = False
    runtime.session.start = Mock()
    runtime.reload_config = Mock()
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    observer = SimpleNamespace(start=Mock(), stop=Mock())
    monkeypatch.setattr('mx4_linux.desktop.NiriFocusObserver', lambda _cb: observer)
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    runtime.start()
    runtime.config['adapt_to_app'] = False
    runtime._context_revision += 1
    future.set_result({'app_id': 'editor', 'window_id': 7})
    scheduled.pop()()
    assert runtime.context == {'app_id': 'editor', 'window_id': 7}
    assert runtime.session.start.call_args.args[1]['button_bindings'] == {'82': 'copy'}


def test_held_old_trigger_release_cleans_up_after_effective_trigger_changes(controller):
    controller._held_ring_button = 416
    controller.config['ring_button'] = None
    controller.config['button_bindings']['416'] = 'copy'
    controller.event({'type': 'button', 'control': 416, 'pressed': False})
    controller.ring.release_button.assert_called_once()
    controller.execute.assert_not_called()
    assert controller._held_ring_button is None


def test_freshly_changed_file_cannot_open_held_ring_before_new_ack(runtime):
    runtime.ring.state.visible = False
    runtime.executor = SimpleNamespace(submit=Mock())
    def changed():
        runtime._context_revision += 1
    runtime.reconcile_config = changed
    runtime.open_ring(held=True)
    runtime.executor.submit.assert_not_called()
    assert not runtime.opening


def pending_held_context_query(runtime, monkeypatch, trigger=416):
    """An acknowledged A session, with all action branches harmless/mocked."""
    from concurrent.futures import Future
    from copy import deepcopy
    from mx4_linux.actions import default_config, resolve_config
    document = default_config()
    for action in document['actions'].values():
        action['steps'] = [{'type': 'command', 'argv': ['/usr/bin/true']}]
    document['assignment_profiles'] = {
        'a': {'label': 'A', 'app_id': 'app.a', 'ring': document['ring'][:],
              'button_bindings': {}, 'ring_button': 416},
        'b': {'label': 'B', 'app_id': 'app.b', 'ring': list(reversed(document['ring'])),
              'button_bindings': {'416': 'copy'} if trigger != 416 else {}, 'ring_button': trigger}}
    document['active_profiles'] = {'app.a': 'a', 'app.b': 'b'}
    runtime.config = deepcopy(document)
    runtime.context = {'app_id': 'app.a', 'window_id': 1}
    runtime.effective_config = resolve_config(document, 'app.a')
    runtime.store = SimpleNamespace(read=lambda: deepcopy(document))
    future, scheduled = Future(), []
    runtime.executor = SimpleNamespace(submit=Mock(return_value=future))
    runtime.ring.show_ring = Mock()
    runtime.ring.preview = False
    runtime._watch_ring_context = Mock()
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: scheduled.append(fn))
    return future, scheduled


@pytest.mark.parametrize('trigger', [None, 83, 416])
def test_async_fresh_focus_must_reconcile_before_old_trigger_can_open(runtime, monkeypatch, trigger):
    future, scheduled = pending_held_context_query(runtime, monkeypatch, trigger)
    runtime.requested_enabled = True
    runtime.event({'type': 'button', 'control': 416, 'pressed': True})
    future.set_result({'app_id': 'app.b', 'window_id': 2})
    scheduled.pop()()
    runtime.ring.show_ring.assert_not_called()
    assert not runtime.opening and runtime.requested_enabled
    assert runtime.context == {'app_id': 'app.b', 'window_id': 2}
    assert runtime._context_revision > runtime._active_revision
    assert runtime.session.configure.call_args.args[0]['ring_button'] == trigger
    runtime.execute.assert_not_called()
    runtime.event({'type': 'button', 'control': 416, 'pressed': False})
    assert runtime._held_ring_button is None
    runtime.execute.assert_not_called()
    runtime.ring.release_button.assert_not_called()


@pytest.mark.parametrize('queried_app', ['app.a', 'app.b'])
@pytest.mark.parametrize('ack_first', [False, True])
def test_observer_during_held_query_cannot_show_or_be_overwritten(runtime, monkeypatch, queried_app, ack_first):
    future, scheduled = pending_held_context_query(runtime, monkeypatch, None)
    runtime.event({'type': 'button', 'control': 416, 'pressed': True})
    runtime.event({'type': 'context', 'context': {'app_id': 'app.b', 'window_id': 2}, 'context_token': 0})
    if ack_first:
        runtime.event({'type': 'configured', 'config_id': 1})
    revision = runtime._context_revision
    future.set_result({'app_id': queried_app, 'window_id': 1 if queried_app == 'app.a' else 2})
    scheduled.pop()()
    runtime.ring.show_ring.assert_not_called()
    assert runtime.context == {'app_id': 'app.b', 'window_id': 2}
    assert runtime._context_revision == revision
    runtime.execute.assert_not_called()


def test_unchanged_held_query_keeps_release_and_detached_document(runtime, monkeypatch):
    future, scheduled = pending_held_context_query(runtime, monkeypatch)
    runtime.event({'type': 'button', 'control': 416, 'pressed': True})
    runtime.event({'type': 'button', 'control': 416, 'pressed': False})
    future.set_result({'app_id': 'app.a', 'window_id': 1})
    scheduled.pop()()
    assert runtime.ring.show_ring.call_args.kwargs['held'] is True
    assert runtime.ring.show_ring.call_args.args[0]['ring_button'] == 416
    runtime.ring.release_button.assert_called_once()
    runtime.execute.assert_not_called()


def test_manual_query_can_open_new_manual_only_profile_without_integration(runtime, monkeypatch):
    future, scheduled = pending_held_context_query(runtime, monkeypatch, None)
    runtime.session.ready = runtime.session.running = False
    runtime.open_ring()
    future.set_result({'app_id': 'app.b', 'window_id': 2})
    scheduled.pop()()
    assert runtime.ring.show_ring.call_args.kwargs['held'] is False
    assert runtime.ring.show_ring.call_args.args[0]['ring_button'] is None
    assert runtime.target == {'app_id': 'app.b', 'window_id': 2}
    assert runtime.context == runtime.target
    runtime.session.configure.assert_not_called()
    runtime.execute.assert_not_called()


def test_newer_observer_also_prevents_stale_manual_query_target(runtime, monkeypatch):
    future, scheduled = pending_held_context_query(runtime, monkeypatch, None)
    runtime.open_ring()
    runtime.event({'type': 'context', 'context': {'app_id': 'app.b', 'window_id': 2}, 'context_token': 0})
    future.set_result({'app_id': 'app.a', 'window_id': 1})
    scheduled.pop()()
    runtime.ring.show_ring.assert_not_called()
    assert runtime.context == {'app_id': 'app.b', 'window_id': 2}
    runtime.execute.assert_not_called()


def utility_execution(runtime, monkeypatch, config, context):
    """Run only mocked effects synchronously; GTK callbacks are explicitly drained."""
    from concurrent.futures import Future
    callbacks, futures = [], []
    def submit(fn):
        future = Future()
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)
        futures.append(future)
        return future
    runtime.execute = DesktopController.execute.__get__(runtime)
    runtime.executor = SimpleNamespace(submit=submit)
    runtime.config = config
    runtime.effective_config = None
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: callbacks.append(fn))
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', lambda _: context)
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: context)
    return callbacks, futures


def test_date_in_toggle_branch_obeys_controller_input_guard(runtime, monkeypatch):
    from mx4_linux.actions import default_config
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Date cycle', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'date', 'format': 'iso-date'}]]}
    own = {'app_id': 'io.github.mx4linux.Control', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, own)
    typed = Mock()
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._text', typed)
    runtime.execute('test', target=own)
    callbacks.pop()()
    assert futures[-1].result()['completed'] == 1
    runtime.execute('test', target=own)
    callbacks.pop()()
    assert isinstance(futures[-1].exception(), RuntimeError)
    typed.assert_not_called()


@pytest.mark.parametrize("kind", ["date", "clipboard"])
def test_date_and_clipboard_abort_when_origin_changes_before_effect(runtime, monkeypatch, kind):
    from mx4_linux.actions import default_config
    cfg = default_config()
    origin = {'app_id': 'editor', 'window_id': 7}
    step = {'type': 'date', 'format': 'iso-date'} if kind == 'date' else {'type': 'clipboard', 'operation': 'plain'}
    cfg['actions']['test'] = {'label': 'Utility', 'steps': [step]}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    monkeypatch.setattr('mx4_linux.desktop.focused_context', lambda: {'app_id': 'other', 'window_id': 8})
    typed = Mock()
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._text', typed)
    monkeypatch.setattr('mx4_linux.actions.clipboard_operation', typed)
    runtime.execute('test', target=origin)
    callbacks.pop()()
    assert isinstance(futures[-1].exception(), RuntimeError)
    typed.assert_not_called()


def test_local_switch_fresh_locked_update_preserves_editor_and_reconciles_without_cancel(runtime, monkeypatch, tmp_path):
    from copy import deepcopy
    from mx4_linux.actions import DesktopConfigStore, resolve_config
    origin = {'app_id': 'editor', 'window_id': 7}
    cfg = deepcopy(runtime.config)
    cfg['assignment_profiles']['editor-new'] = deepcopy(cfg['assignment_profiles']['editor'])
    cfg['assignment_profiles']['editor-new']['button_bindings'] = {'86': 'copy'}
    cfg['actions']['switch'] = {'label': 'Switch', 'steps': [
        {'type': 'local', 'operation': 'switch-profile', 'profile_id': 'editor-new'},
        {'type': 'delay', 'milliseconds': 0}]}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime.store = DesktopConfigStore(tmp_path / 'desktop.json')
    latest = deepcopy(cfg)
    latest['actions']['copy']['label'] = 'Concurrent editor label'
    runtime.store.save(latest)
    runtime.context = origin
    runtime.effective_config = resolve_config(cfg, 'editor')
    old = deepcopy(runtime.effective_config)
    runtime.requested_enabled = True
    runtime.window.desktop_config_changed = Mock()
    token = runtime._dispatch_token
    runtime.execute('switch', target=origin, config=old)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 2
    saved = runtime.store.read()
    assert saved['active_profiles']['editor'] == 'editor-new'
    runtime.window.desktop_config_changed.assert_called_once()
    assert saved['actions']['copy']['label'] == 'Concurrent editor label'
    assert runtime.session.configure.call_args.args[0]['button_bindings'] == {'86': 'copy'}
    assert runtime._dispatch_token == token and runtime.requested_enabled
    assert not runtime.cancelled.is_set()
    assert runtime.effective_config == old  # New assignments wait for matching ACK.
    runtime.event({'type': 'configured', 'config_id': 1})
    assert runtime.effective_config['button_bindings'] == {'86': 'copy'}


def test_deleted_local_profile_preflight_prevents_earlier_adapt_mutation(runtime, monkeypatch, tmp_path):
    from copy import deepcopy
    from mx4_linux.actions import DesktopConfigStore
    origin = {'app_id': 'editor', 'window_id': 7}
    cfg = deepcopy(runtime.config)
    cfg['actions']['switch'] = {'label': 'Switch', 'steps': [
        {'type': 'local', 'operation': 'adapt-to-app', 'enabled': True},
        {'type': 'local', 'operation': 'switch-profile', 'profile_id': 'editor'}]}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime.store = DesktopConfigStore(tmp_path / 'desktop.json')
    latest = deepcopy(cfg)
    del latest['actions']['switch']
    del latest['assignment_profiles']['editor']
    del latest['active_profiles']['editor']
    runtime.store.save(latest)
    before = runtime.store.path.read_bytes()
    runtime.execute('switch', target=origin, config=cfg)
    while callbacks:
        callbacks.pop(0)()
    assert isinstance(futures[-1].exception(), RuntimeError)
    assert runtime.store.path.read_bytes() == before


@pytest.mark.parametrize('kind', ['clipboard', 'date'])
def test_incompatible_local_preflight_prevents_earlier_utility_effect(runtime, monkeypatch, tmp_path, kind):
    from copy import deepcopy
    from mx4_linux.actions import DesktopConfigStore
    origin = {'app_id': 'editor', 'window_id': 7}
    cfg = deepcopy(runtime.config)
    first = {'type': 'clipboard', 'operation': 'upper'} if kind == 'clipboard' else {'type': 'date', 'format': 'iso-date'}
    cfg['actions']['switch'] = {'label': 'Switch', 'steps': [first,
        {'type': 'local', 'operation': 'switch-profile', 'profile_id': 'general'}]}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime.store = DesktopConfigStore(tmp_path / 'desktop.json')
    runtime.store.save(cfg)
    before = runtime.store.path.read_bytes()
    effect = Mock(side_effect=AssertionError('An invalid local target must be rejected first'))
    monkeypatch.setattr('mx4_linux.actions.clipboard_operation', effect)
    monkeypatch.setattr('mx4_linux.actions.date_text', effect)
    runtime.execute('switch', target=origin)
    while callbacks:
        callbacks.pop(0)()
    assert 'incompatible' in str(futures[-1].exception())
    assert runtime.store.path.read_bytes() == before
    effect.assert_not_called()


def test_native_utility_handoff_cancellation_unblocks_and_suppresses_late_show(runtime, monkeypatch):
    callbacks, failures = [], []
    posted = threading.Event()
    def post(callback):
        callbacks.append(callback)
        posted.set()
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', post)
    runtime.utilities = SimpleNamespace(show=Mock())
    def check():
        if runtime.cancelled.is_set():
            raise RuntimeError('cancelled')
    def request():
        try:
            runtime._local_step({'type': 'local', 'operation': 'stopwatch'}, check, None)
        except RuntimeError as exc:
            failures.append(str(exc))
    worker = threading.Thread(target=request)
    worker.start()
    assert posted.wait(1)
    runtime.cancelled.set()
    worker.join(1)
    assert not worker.is_alive() and failures == ['cancelled']
    callbacks.pop()()
    runtime.utilities.show.assert_not_called()


def test_controller_shutdown_closes_native_utility_manager(runtime):
    runtime.utilities = SimpleNamespace(close=Mock())
    manager = runtime.utilities
    runtime.executor = SimpleNamespace(submit=Mock(), shutdown=Mock())
    runtime.ring.hide_ring = Mock()
    runtime.ring.destroy = Mock()
    runtime.close()
    manager.close.assert_called_once()
    assert runtime.utilities is None


def test_native_utility_success_handoff_runs_on_scheduled_callback(runtime, monkeypatch):
    runtime.utilities = SimpleNamespace(show=Mock())
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', lambda fn: fn())
    runtime._local_step({'type': 'local', 'operation': 'tap-tempo'}, lambda: None, None)
    runtime.utilities.show.assert_called_once_with('tap-tempo')


def test_reconcile_resets_changed_toggle_definition_without_cancelling(runtime):
    from copy import deepcopy
    old = deepcopy(runtime.config['actions']['copy'])
    runtime._toggle_state = {'copy': (old, 1), 'removed': (old, 1)}
    latest = deepcopy(runtime.config)
    latest['actions']['copy']['label'] = 'Changed'
    runtime.store = SimpleNamespace(read=lambda: latest)
    token = runtime._dispatch_token
    runtime.reconcile_config()
    assert runtime._toggle_state == {}
    assert runtime._dispatch_token == token and not runtime.cancelled.is_set()


def test_editor_reload_resets_changed_toggle_definition(runtime):
    from copy import deepcopy
    old = deepcopy(runtime.config['actions']['copy'])
    runtime._toggle_state = {'copy': (old, 1)}
    latest = deepcopy(runtime.config)
    latest['actions']['copy']['label'] = 'Edited in the existing editor'
    runtime.store = SimpleNamespace(read=lambda: latest)
    runtime.reload_config()
    assert runtime._toggle_state == {}


def test_branch_selection_must_stay_pinned_across_toggle_reset(runtime, monkeypatch):
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Date cycle', 'steps': [{'type': 'date', 'format': 'iso-date'}],
                             'toggle_steps': [[{'type': 'delay', 'milliseconds': 0}]]}
    own = {'app_id': 'io.github.mx4linux.Control', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, own)
    runtime._toggle_state = {'test': (deepcopy(cfg['actions']['test']), 1)}
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'Concurrent definition change'
    def restore(_):
        runtime._reset_changed_toggles(newer)
        return own
    monkeypatch.setattr('mx4_linux.desktop.restore_focus', restore)
    typed = Mock()
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._text', typed)
    runtime.execute('test', target=own)
    while callbacks:
        callbacks.pop(0)()
    # The classified/selected delay branch must stay pinned for this invocation.
    typed.assert_not_called()
    assert futures[-1].result()['steps'] == [{'type': 'delay'}]


def test_cancelled_native_utility_callback_rechecks_request_guard(runtime, monkeypatch):
    runtime.utilities = type('Utilities', (), {'show': Mock()})()
    token = runtime._dispatch_token
    def check():
        if runtime._dispatch_token != token:
            raise RuntimeError('cancelled')
    def post(fn):
        # A stopped session invalidates the dispatch token while the idle is pending.
        runtime._dispatch_token += 1
        return fn()
    monkeypatch.setattr('mx4_linux.desktop.GLib.idle_add', post)
    try:
        runtime._local_step({'type': 'local', 'operation': 'stopwatch'}, check, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Expected cancellation')
    runtime.utilities.show.assert_not_called()


def test_old_pinned_completion_must_not_reinsert_reset_toggle_definition(runtime, monkeypatch):
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Old definition', 'steps': [{'type': 'delay', 'milliseconds': 0}],
                             'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}]]}
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = {'test': (deepcopy(cfg['actions']['test']), 0)}
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'New definition'
    runtime.store = type('Store', (), {'read': lambda _: deepcopy(newer)})()
    def effect(*_):
        runtime.reconcile_config()
        assert 'test' not in runtime._toggle_state
        return {'type': 'delay'}
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._step', effect)
    runtime.execute('test', target=origin)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    assert 'test' not in runtime._toggle_state


def test_missing_local_handler_fails_before_any_earlier_effect(monkeypatch):
    from mx4_linux.actions import DesktopActions
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Unavailable utility', 'steps': [
        {'type': 'delay', 'milliseconds': 1}, {'type': 'local', 'operation': 'bell'}]}
    runner = DesktopActions(cfg)
    earlier = Mock()
    monkeypatch.setattr(runner, '_delay', earlier)
    try:
        runner.execute('test')
    except RuntimeError as exc:
        assert 'native controller' in str(exc)
    else:
        raise AssertionError('Expected unavailable native controller')
    earlier.assert_not_called()


def test_changed_back_definition_during_first_run_still_resets_cycle(runtime, monkeypatch):
    from copy import deepcopy
    from mx4_linux.actions import default_config
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}]]}
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = {}
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'Edited then restored'
    def effect(*_):
        runtime._reset_changed_toggles(newer)
        runtime._reset_changed_toggles(cfg)
        return {'type': 'delay'}
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._step', effect)
    runtime.execute('test', target=origin)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    assert 'test' not in runtime._toggle_state


def test_pinned_completion_does_not_overwrite_newer_toggle_index(runtime, monkeypatch):
    from copy import deepcopy
    from mx4_linux.actions import default_config
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}]]}
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = {'test': (deepcopy(cfg['actions']['test']), 0)}
    newer_entry = (deepcopy(cfg['actions']['test']), 1)
    def effect(*_):
        runtime._toggle_state['test'] = newer_entry
        return {'type': 'delay'}
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._step', effect)
    runtime.execute('test', target=origin)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    assert runtime._toggle_state['test'] is newer_entry


@pytest.mark.parametrize('mutation', ['definition-reset', 'newer-entry'])
def test_cycle_commit_must_be_atomic_with_reset_or_newer_entry(runtime, monkeypatch, mutation):
    from threading import Event, Thread
    import mx4_linux.actions as actions
    cfg = default_config()
    action = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
              'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}], [{'type': 'delay', 'milliseconds': 2}]]}
    cfg['actions']['test'] = action
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = {'test': (deepcopy(action), 0)}
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'New definition'
    newer_entry = (deepcopy(action), 2)
    effect_finished, commit_copy, mutation_done = Event(), Event(), Event()
    original_copy = actions.deepcopy
    def instrumented_copy(value, *args, **kwargs):
        if effect_finished.is_set() and value == action:
            commit_copy.set()
            assert mutation_done.wait(1), 'Mutation did not run during commit copy'
        return original_copy(value, *args, **kwargs)
    monkeypatch.setattr(actions, 'deepcopy', instrumented_copy)
    def effect(*_):
        effect_finished.set()
        return {'type': 'delay'}
    monkeypatch.setattr(actions.DesktopActions, '_step', effect)
    def change_at_commit():
        assert commit_copy.wait(1), 'Cycle commit was not reached'
        if mutation == 'definition-reset':
            runtime._reset_changed_toggles(newer)
        else:
            runtime._toggle_state['test'] = newer_entry
        mutation_done.set()
    worker = Thread(target=change_at_commit)
    worker.start()
    runtime.execute('test', target=origin)
    worker.join(1)
    assert not worker.is_alive()
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    if mutation == 'definition-reset':
        assert 'test' not in runtime._toggle_state
    else:
        assert runtime._toggle_state['test'] is newer_entry


@pytest.mark.parametrize('mutation', ['definition-reset', 'newer-entry'])
def test_toggle_guard_and_commit_share_lock_with_other_writers(runtime, monkeypatch, mutation):
    from threading import Event, Thread
    import mx4_linux.actions as actions
    cfg = default_config()
    action = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}], [{'type': 'delay', 'milliseconds': 2}]]}
    cfg['actions']['test'] = action
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = {'test': (deepcopy(action), 0)}
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'New definition'
    newer_entry = (deepcopy(action), 2)
    checking, attempted, changed = Event(), Event(), Event()
    original = actions.ExecutionSelection.may_advance
    def checked(selection, config, state):
        checking.set()
        assert attempted.wait(1)
        assert not changed.is_set(), 'A writer entered inside the cycle-commit critical section'
        return original(selection, config, state)
    monkeypatch.setattr(actions.ExecutionSelection, 'may_advance', checked)
    def mutate():
        assert checking.wait(1)
        attempted.set()
        if mutation == 'definition-reset':
            runtime._reset_changed_toggles(newer)
        else:
            runtime._toggle_state['test'] = newer_entry
        changed.set()
    worker = Thread(target=mutate)
    worker.start()
    runtime.execute('test', target=origin)
    worker.join(1)
    assert not worker.is_alive() and changed.is_set()
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    if mutation == 'definition-reset':
        assert 'test' not in runtime._toggle_state
    else:
        assert runtime._toggle_state['test'] is newer_entry


def test_toggle_state_lock_is_not_held_across_action_effect(runtime, monkeypatch):
    from threading import Event, Thread
    cfg = default_config()
    cfg['actions']['test'] = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}]]}
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    changed = Event()
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'New definition'
    def effect(*_):
        worker = Thread(target=lambda: (runtime._reset_changed_toggles(newer), changed.set()))
        worker.start()
        assert changed.wait(1), 'An action effect held the session-state lock'
        worker.join(1)
        return {'type': 'delay'}
    monkeypatch.setattr('mx4_linux.actions.DesktopActions._step', effect)
    runtime.execute('test', target=origin)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    assert 'test' not in runtime._toggle_state


def test_stale_ring_snapshot_must_not_restart_cycle_after_prior_definition_reset(runtime, monkeypatch):
    from mx4_linux import actions
    cfg = actions.default_config()
    old = {'label': 'Old action', 'steps': [{'type': 'delay', 'milliseconds': 0}],
           'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}]]}
    cfg['actions']['test'] = old
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    runtime._toggle_state = actions.ToggleState({'test': (deepcopy(old), 1)})
    newer = deepcopy(cfg)
    newer['actions']['test']['label'] = 'Changed while a ring retained its detached document'
    runtime.config = newer
    runtime._reset_changed_toggles(newer)
    assert 'test' not in runtime._toggle_state
    effect = Mock(return_value={'type': 'delay'})
    monkeypatch.setattr(actions.DesktopActions, '_step', effect)
    # Production selected()/dispatch_selection() intentionally passes the ring's older document.
    runtime.execute('test', target=origin, config=cfg)
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    # Pinned effects can complete; they must not establish obsolete future-cycle state.
    assert 'test' not in runtime._toggle_state


@pytest.mark.parametrize('change', ['edit', 'remove', 'change-back', 'remove-recreate'])
def test_old_ring_cycle_token_survives_delayed_dispatch_without_advancing_future_cycle(runtime, monkeypatch, change):
    from mx4_linux import actions
    cfg = actions.default_config()
    action = {'label': 'Original', 'steps': [{'type': 'delay', 'milliseconds': 0}],
        'toggle_steps': [[{'type': 'delay', 'milliseconds': 1}], [{'type': 'delay', 'milliseconds': 2}]]}
    cfg['actions']['test'] = action
    origin = {'app_id': 'editor', 'window_id': 7}
    callbacks, futures = utility_execution(runtime, monkeypatch, cfg, origin)
    state = runtime._toggle_session()
    runtime._reset_changed_toggles(cfg)
    runtime.reconcile_config = Mock()
    runtime._watch_ring_context = Mock()
    runtime.ring.preview = False
    runtime.ring.show_ring = Mock()
    runtime.open_ring()
    callbacks.pop(0)()
    assert runtime._ring_cycle_tokens == state.cycle_tokens()
    timeouts = []
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda _ms, fn: timeouts.append(fn))
    runtime.selected('test', False)
    newer = deepcopy(cfg)
    if change in ('remove', 'remove-recreate'):
        del newer['actions']['test']
    else:
        newer['actions']['test']['label'] = 'Changed definition'
    runtime.config = newer
    runtime._reset_changed_toggles(newer)
    newer_entry = None
    if change in ('change-back', 'remove-recreate'):
        runtime.config = deepcopy(cfg)
        runtime._reset_changed_toggles(cfg)
        newer_entry = (deepcopy(action), 2)
        state['test'] = newer_entry
    effect = Mock(return_value={'type': 'delay'})
    monkeypatch.setattr(actions.DesktopActions, '_step', effect)
    timeouts.pop()()
    while callbacks:
        callbacks.pop(0)()
    assert futures[-1].result()['completed'] == 1
    if newer_entry is None:
        assert 'test' not in state
    else:
        assert state['test'] is newer_entry
