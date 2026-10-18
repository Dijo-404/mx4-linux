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


def test_observer_failure_returns_general_and_ignores_stale_context(runtime):
    runtime.context = {'app_id': 'editor', 'window_id': 7}
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


def test_unsupported_feedback_warning_keeps_real_worker_context_transitions_alive(runtime):
    """Exercise the worker protocol, including native restore after the warning."""
    import struct

    from test_event_worker import INDEX, add_control, run, setup_session

    dev, api, commands, events = setup_session()
    add_control(dev, 83)
    before = dict(dev.reporting)
    runtime.observer = SimpleNamespace(stop=Mock())
    observer = runtime.observer
    setting = SimpleNamespace(choices=[0], write=Mock())
    api.settings_templates.SETTINGS = [SimpleNamespace(name='haptic-play')]
    api.settings_templates.check_feature.return_value = setting
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
        if reads == 1:
            assert runtime.error is None
            observer.stop.assert_not_called()
            assert runtime._context_token == 0
            runtime.event({'type': 'context', 'context': {'app_id': 'editor', 'window_id': 7}, 'context_token': 0})
        elif reads == 2:
            assert dev.reporting[82] == before[82]
            assert dev.reporting[83][0] & 1
            observer.stop.assert_not_called()
            return 17, 255, bytes([INDEX, 0]) + struct.pack('!4H', 83, 0, 0, 0) + b'\0' * 8
        else:
            assert runtime.execute.call_args.args == ('paste',)
            commands.put({'op': 'stop'})

    runtime.session.configure.side_effect = configure
    api.base.read.side_effect = read
    assert run(dev, api, commands, Events(), controls=[82, 416])
    assert any(e['type'] == 'warning' and e['source'] == 'haptic' for e in events)
    assert dev.reporting == before
    setting.write.assert_not_called()


def test_fatal_integration_error_requests_restoration_and_stops_observation(runtime):
    runtime.observer = SimpleNamespace(stop=Mock())
    observer = runtime.observer
    runtime.event({'type': 'error', 'message': 'Device did not acknowledge haptic playback'})
    runtime.session.stop.assert_called_once()
    observer.stop.assert_called_once()
    assert runtime.error == 'Device did not acknowledge haptic playback'
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


def test_reconnect_budget_and_preference_are_bounded(runtime, monkeypatch):
    runtime.restoration_confirmed = True
    scheduled = []
    runtime.requested_enabled = True
    runtime.hardware_paused = False
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda delay, fn: scheduled.append((delay, fn)) or len(scheduled))
    runtime._schedule_reconnect()
    assert scheduled == []  # Opt in is required.
    runtime.config['resume_integration'] = True
    for index, seconds in enumerate((1, 2, 5, 10, 20, 30)):
        runtime.reconnect_source = 0
        runtime.reconnect_attempt = index
        runtime._schedule_reconnect()
        assert scheduled[-1][0] == seconds * 1000
    runtime.reconnect_source = 0
    runtime.reconnect_attempt = 6
    runtime._schedule_reconnect()
    assert len(scheduled) == 6
    assert 'enable integration to retry' in runtime.notify.call_args.args[0]


def test_explicit_recovery_can_clear_prior_unknown_stop_for_next_operation(runtime):
    runtime.session.running = runtime.session.stopping = False
    runtime.session.process = object()
    runtime.session.restored = None
    runtime.restoration_confirmed = True  # Confirmed by a separate same-unit recovery operation.
    seen = []
    runtime.wait_stopped(seen.append)
    assert seen == [True]


@pytest.mark.parametrize('restored', [False, None])
def test_unconfirmed_stop_blocks_new_and_already_queued_automatic_reconnect(runtime, monkeypatch, restored):
    scheduled = []
    runtime.requested_enabled = True
    runtime.config['resume_integration'] = True
    runtime.window.backend = SimpleNamespace(scan=Mock())
    runtime.window.job = Mock()
    monkeypatch.setattr('mx4_linux.desktop.GLib.timeout_add', lambda delay, fn: scheduled.append((delay, fn)) or len(scheduled))
    runtime.event({'type': 'stopped', 'restored': restored})
    assert scheduled == []
    runtime._reconnect(runtime._intent_generation)
    runtime.window.job.assert_not_called()
    runtime.window.backend.scan.assert_not_called()


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
