"""Finite local catalog utilities; no clipboard, input, focus or presented UI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import mx4_linux.actions as actions


def document(steps, **extra):
    value = actions.default_config()
    value['actions']['test'] = {'label': 'Utility', 'steps': steps, **extra}
    return value


def profile(config, app='*'):
    return {'label': 'Work', 'app_id': app, 'ring': config['ring'][:], 'button_bindings': {}}


@pytest.mark.parametrize('step', [
    {'type': 'date', 'format': 'iso-date'},
    {'type': 'date', 'format': 'moon-phase'},
    {'type': 'clipboard', 'operation': 'sentence-case'},
    {'type': 'local', 'operation': 'adapt-to-app', 'enabled': False},
    {'type': 'local', 'operation': 'stopwatch'},
    {'type': 'local', 'operation': 'tap-tempo'},
    {'type': 'local', 'operation': 'bell'},
])
def test_utility_steps_validate_and_preview_without_effects(step, monkeypatch):
    monkeypatch.setattr(actions.subprocess, 'Popen', Mock(side_effect=AssertionError('effect')))
    assert actions.DesktopActions(document([step]), dry_run=True).execute('test')['steps'] == [step]


def test_profile_references_validate_in_every_branch():
    config = document([{'type': 'delay', 'milliseconds': 0}],
        toggle_steps=[[{'type': 'local', 'operation': 'switch-profile', 'profile_id': 'work'}]],
        adjustment={'increase': [{'type': 'local', 'operation': 'switch-profile', 'profile_id': 'work'}],
                    'decrease': [{'type': 'delay', 'milliseconds': 0}]})
    config['assignment_profiles'] = {'work': profile(config)}
    actions.validate_config(config)
    assert actions.profile_references(config['actions']['test']) == {'work'}
    del config['assignment_profiles']['work']
    with pytest.raises(RuntimeError, match='profile'):
        actions.validate_config(config)


def test_toggle_success_failure_preview_adjustment_and_definition_reset(monkeypatch):
    cfg = document([{'type': 'text', 'text': 'first'}],
                   toggle_steps=[[{'type': 'text', 'text': 'second'}]],
                   adjustment={'increase': [{'type': 'delay', 'milliseconds': 0}],
                               'decrease': [{'type': 'delay', 'milliseconds': 0}]})
    state, seen = {}, []
    def run(config=cfg, **kwargs):
        runner = actions.DesktopActions(config, toggle_state=state, **kwargs)
        monkeypatch.setattr(runner, '_step', lambda step, *_: seen.append(step.copy()) or {'type': step['type']})
        return runner
    run().execute('test')
    run(dry_run=True).execute('test')
    run().execute('test', adjustment='increase')
    with pytest.raises(RuntimeError, match='cancelled'):
        run().execute('test', cancelled=lambda: True)
    run().execute('test')
    assert [s.get('text') for s in seen] == ['first', None, 'second']
    changed = deepcopy(cfg)
    changed['actions']['test']['label'] = 'New definition'
    run(changed).execute('test')
    assert seen[-1]['text'] == 'first'
    assert len(state) == 1


def test_date_uses_guarded_text_at_execution_only(monkeypatch):
    from mx4_linux import action_utilities as utilities
    clock = Mock(return_value=datetime(2024, 12, 31, 23, 4, 5, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    monkeypatch.setattr(utilities, '_now', clock)
    runner = actions.DesktopActions(document([{'type': 'date', 'format': 'iso-datetime'}]))
    clock.assert_not_called()
    typed = Mock()
    monkeypatch.setattr(runner, '_text', typed)
    result = runner.execute('test')
    assert typed.call_args.args[0] == '2024-12-31T23:04:05+05:30'
    assert '2024' not in str(result)
    assert clock.call_count == 1


@pytest.mark.parametrize('format,expected', [('iso-date', '2024-12-31'), ('iso-time', '23:04:05'),
    ('day-of-year', '366'), ('iso-week', '01')])
def test_date_finite_formats(format, expected):
    from mx4_linux.action_utilities import date_text
    now = datetime(2024, 12, 31, 23, 4, 5, tzinfo=timezone.utc)
    assert date_text(format, now) == expected


def test_approximate_moon_phase_epoch_and_eight_bins():
    from mx4_linux.action_utilities import date_text
    epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    names = [date_text('moon-phase', epoch + timedelta(days=29.530589 * i / 8)) for i in range(8)]
    assert names == ['New Moon', 'Waxing Crescent', 'First Quarter', 'Waxing Gibbous',
                     'Full Moon', 'Waning Gibbous', 'Last Quarter', 'Waning Crescent']


@pytest.mark.parametrize('operation,value,expected', [('plain', 'a\n\n', 'a\n\n'), ('upper', 'Straße\n', 'STRASSE\n'),
    ('sentence-case', 'hELLO. wORLD!\nNEXT?', 'Hello. World!\nNext?'),
    ('url-encode', 'a b/✓', 'a%20b%2F%E2%9C%93'), ('url-decode', '%E2%9C%93%0A', '✓\n'),
    ('html-encode', '<a>&"', '&lt;a&gt;&amp;&quot;'), ('html-decode', '&lt;a&gt;', '<a>')])
def test_clipboard_transform_is_deterministic_and_preserves_newlines(operation, value, expected):
    from mx4_linux.action_utilities import transform_clipboard
    assert transform_clipboard(operation, value.encode()) == expected.encode()


@pytest.mark.parametrize('operation,payload', [('plain', b'\xff'), ('plain', b'a\0b'), ('url-decode', b'%ZZ'),
    ('url-decode', b'%F0%80%80%80'), ('url-decode', b'%00'), ('html-decode', b'&#0;'),
    ('html-decode', b'&unknown;'), ('html-decode', b'&#xD800;'), ('html-encode', b'&' * 65536),
    ('plain', b'a' * 65537)])
def test_clipboard_rejects_invalid_or_expanded_content_privately(operation, payload):
    from mx4_linux.action_utilities import transform_clipboard
    with pytest.raises(RuntimeError) as exc:
        transform_clipboard(operation, payload)
    assert str(payload[:20]) not in str(exc.value)


def test_local_without_native_handler_is_unavailable_and_dry_run_is_pure():
    cfg = document([{'type': 'local', 'operation': 'bell'}])
    assert actions.DesktopActions(cfg, dry_run=True).execute('test')['dry_run']
    with pytest.raises(RuntimeError, match='native controller'):
        actions.DesktopActions(cfg).execute('test')

@pytest.mark.parametrize('step', [
    {'type': 'date', 'format': 'strftime'}, {'type': 'date', 'format': []},
    {'type': 'clipboard', 'operation': 'paste'}, {'type': 'clipboard', 'operation': 'plain', 'primary': True},
    {'type': 'local', 'operation': 'adapt-to-app', 'enabled': 1},
    {'type': 'local', 'operation': 'switch-profile', 'profile_id': 'absent'},
    {'type': 'local', 'operation': 'bell', 'volume': 1}, {'type': 'local', 'operation': 'shell'},
])
def test_utility_schema_is_finite(step):
    with pytest.raises(RuntimeError):
        actions.validate_config(document([step]))


@pytest.mark.parametrize('toggles', [[], [[], []], [[{'type': 'delay', 'milliseconds': 0}]] * 8,
                                      [True], {'steps': []}])
def test_toggle_schema_bounds(toggles):
    with pytest.raises(RuntimeError):
        actions.validate_config(document([{'type': 'delay', 'milliseconds': 0}], toggle_steps=toggles))


def test_clipboard_literal_mime_stdin_and_no_content_results(monkeypatch):
    from mx4_linux import action_utilities as utilities
    calls = []
    def helper(argv, check, **kwargs):
        check()
        calls.append((argv, kwargs))
        return b'text/html\ntext/plain\n' if '--list-types' in argv else b'private sentinel\n\n'
    monkeypatch.setattr(utilities, 'clipboard_process', helper)
    result = actions.DesktopActions(document([{'type': 'clipboard', 'operation': 'upper'}])).execute('test')
    assert calls[1][0] == ['wl-paste', '--no-newline', '--type', 'text/plain']
    assert calls[2] == (['wl-copy', '--type', 'text/plain;charset=utf-8'], {'payload': b'PRIVATE SENTINEL\n\n'})
    assert 'sentinel' not in str(result).lower()
    assert all('private' not in str(argv).lower() and '--primary' not in argv for argv, _ in calls)


def test_clipboard_clear_does_not_read_and_cancel_before_commit(monkeypatch):
    from mx4_linux import action_utilities as utilities
    calls, cancelled = [], False
    def check():
        if cancelled:
            raise RuntimeError('cancelled')
    def helper(argv, check, **kwargs):
        nonlocal cancelled
        calls.append(argv)
        if '--no-newline' in argv:
            cancelled = True
            return b'private'
        return b'text/plain\n'
    monkeypatch.setattr(utilities, 'clipboard_process', helper)
    utilities.clipboard_operation('clear', check)
    assert calls == [['wl-copy', '--clear']]
    calls.clear()
    with pytest.raises(RuntimeError, match='cancelled'):
        utilities.clipboard_operation('upper', check)
    assert len(calls) == 2


def test_clipboard_rejects_binary_offer_before_read(monkeypatch):
    from mx4_linux import action_utilities as utilities
    helper = Mock(return_value=b'image/png\ntext/html\n')
    monkeypatch.setattr(utilities, 'clipboard_process', helper)
    with pytest.raises(RuntimeError, match='plain text'):
        utilities.clipboard_operation('plain', lambda: None)
    assert helper.call_count == 1


def test_bounded_helper_reaps_oversized_failure_and_cancel_children(monkeypatch):
    import sys
    from mx4_linux import action_utilities as utilities
    original = utilities.subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(utilities.subprocess, 'Popen', spawn)
    with pytest.raises(RuntimeError, match='limit'):
        utilities.clipboard_process([sys.executable, '-c', "import os;os.write(1,b'x'*9000)"], lambda: None, limit=32)
    with pytest.raises(RuntimeError, match='failed') as exc:
        utilities.clipboard_process([sys.executable, '-c', "import sys;sys.stderr.write('private sentinel');sys.exit(3)"], lambda: None)
    assert 'sentinel' not in str(exc.value)
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        if checks > 2:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        utilities.clipboard_process([sys.executable, '-c', 'import time;time.sleep(10)'], cancel)
    assert all(child.returncode is not None for child in children)


def test_bounded_helper_preserves_stdin_and_exact_stdout():
    import sys
    from mx4_linux.action_utilities import clipboard_process
    payload = 'private ✓\n\n'.encode()
    assert clipboard_process([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'],
                             lambda: None, payload=payload, limit=128) == payload


def test_local_assignment_compatibility_and_exact_activation():
    cfg = document([{'type': 'delay', 'milliseconds': 0}])
    cfg['assignment_profiles'] = {'general': profile(cfg), 'editor': profile(cfg, 'editor'),
                                 'other': profile(cfg, 'other')}
    cfg['active_profiles'] = {'*': 'general'}
    actions.apply_local_assignment(cfg, {'operation': 'switch-profile', 'profile_id': 'editor'}, 'editor')
    assert cfg['active_profiles']['editor'] == 'editor'
    before = deepcopy(cfg)
    for target in ('general', 'other', 'deleted'):
        with pytest.raises(RuntimeError, match='profile|Profile'):
            actions.apply_local_assignment(cfg, {'operation': 'switch-profile', 'profile_id': target}, 'editor')
        assert cfg == before
    actions.apply_local_assignment(cfg, {'operation': 'adapt-to-app', 'enabled': False}, 'editor')
    actions.apply_local_assignment(cfg, {'operation': 'switch-profile', 'profile_id': 'general'}, 'editor')
    assert cfg['active_profiles']['editor'] == 'editor'
    with pytest.raises(RuntimeError, match='profile|Profile'):
        actions.apply_local_assignment(cfg, {'operation': 'switch-profile', 'profile_id': 'editor'}, 'editor')


def test_local_preflight_happens_before_any_step_and_dry_run_is_pure(monkeypatch):
    step = {'type': 'local', 'operation': 'adapt-to-app', 'enabled': False}
    cfg = document([{'type': 'delay', 'milliseconds': 0}, step])
    preflight, handler = Mock(side_effect=RuntimeError('deleted profile')), Mock()
    runner = actions.DesktopActions(cfg, local_handler=handler, local_preflight=preflight)
    effect = Mock()
    monkeypatch.setattr(runner, '_step', effect)
    with pytest.raises(RuntimeError, match='deleted profile'):
        runner.execute('test')
    effect.assert_not_called()
    runner.dry_run = True
    runner.execute('test')
    assert preflight.call_count == 1
    handler.assert_not_called()


def test_toggle_failed_or_final_cancelled_run_does_not_advance(monkeypatch):
    cfg = document([{'type': 'delay', 'milliseconds': 0}], toggle_steps=[[{'type': 'delay', 'milliseconds': 1}]])
    state = {}
    runner = actions.DesktopActions(cfg, toggle_state=state)
    effect = Mock(side_effect=RuntimeError('failed'))
    monkeypatch.setattr(runner, '_step', effect)
    with pytest.raises(RuntimeError, match='failed'):
        runner.execute('test')
    assert not state
    cancel = False
    def performed(*_):
        nonlocal cancel
        cancel = True
        return {'type': 'delay'}
    monkeypatch.setattr(runner, '_step', performed)
    with pytest.raises(RuntimeError, match='cancelled'):
        runner.execute('test', cancelled=lambda: cancel)
    assert not state


def test_native_utility_models_use_monotonic_time_and_bounded_taps():
    from mx4_linux.local_utilities import Stopwatch, TapTempo
    clock = Mock(return_value=10.0)
    watch = Stopwatch(clock)
    watch.toggle()
    clock.return_value = 12.5
    assert watch.elapsed() == 2.5
    watch.toggle()
    clock.return_value = 20
    assert watch.elapsed() == 2.5
    watch.reset()
    assert watch.elapsed() == 0
    tempo = TapTempo(clock)
    for n in range(20):
        clock.return_value = 30 + n * .5
        tempo.tap()
    assert len(tempo.taps) == 8 and tempo.bpm() == 120
    clock.return_value = 50
    tempo.tap()
    assert tempo.bpm() is None and len(tempo.taps) == 1


def test_utility_manager_hiding_and_close_remove_timer_without_presenting(monkeypatch):
    from mx4_linux.local_utilities import UtilityWindows
    from types import SimpleNamespace
    removed = []
    monkeypatch.setattr('mx4_linux.local_utilities.GLib.source_remove', removed.append)
    manager = UtilityWindows.__new__(UtilityWindows)
    manager.windows = {'stopwatch': SimpleNamespace(destroy=Mock()), 'tap-tempo': SimpleNamespace(destroy=Mock())}
    manager.timer = 7
    manager.stopwatch = Mock()
    window = SimpleNamespace(get_visible=lambda: False)
    manager._visibility_changed(window, None)
    assert removed == [7] and manager.timer == 0
    manager.timer = 9
    windows = list(manager.windows.values())
    manager.close()
    assert removed == [7, 9]
    assert not manager.windows
    assert all(w.destroy.call_count == 1 for w in windows)


def test_clipboard_helper_timeout_and_missing_binary_are_static_and_reaped(monkeypatch):
    import sys
    from mx4_linux import action_utilities as utilities
    original = utilities.subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(utilities.subprocess, 'Popen', spawn)
    with pytest.raises(RuntimeError, match='timed out'):
        utilities.clipboard_process([sys.executable, '-c', 'import time;time.sleep(10)'], lambda: None)
    assert children[0].returncode is not None
    with pytest.raises(RuntimeError, match='unavailable'):
        utilities.clipboard_process(['/not/a/clipboard/helper'], lambda: None)


def test_oversized_html_numeric_escape_has_static_runtime_failure():
    from mx4_linux.action_utilities import transform_clipboard
    with pytest.raises(RuntimeError, match='HTML'):
        transform_clipboard('html-decode', ('&#' + '9' * 5000 + ';').encode())


def test_generated_date_does_not_appear_in_helper_failure(monkeypatch):
    monkeypatch.setattr(actions, 'date_text', lambda _: '2099-12-31')
    monkeypatch.setattr(actions, '_native_wtype', lambda: None)
    monkeypatch.setattr(actions, '_run', Mock(side_effect=RuntimeError('private helper output 2099-12-31')))
    with pytest.raises(RuntimeError) as exc:
        actions.DesktopActions(document([{'type': 'date', 'format': 'iso-date'}])).execute('test')
    assert '2099' not in str(exc.value)


def test_toggle_skipped_condition_and_removed_toggle_do_not_resume_old_cycle(monkeypatch):
    cfg = document([{'type': 'delay', 'milliseconds': 0}],
        toggle_steps=[[{'type': 'delay', 'milliseconds': 1}]], app_id='right')
    state = {}
    monkeypatch.setattr(actions, 'focused_context', lambda: {'app_id': 'wrong', 'window_id': 1})
    assert actions.DesktopActions(cfg, toggle_state=state).execute('test')['skipped']
    assert state == {}
    monkeypatch.setattr(actions, 'focused_context', lambda: {'app_id': 'right', 'window_id': 1})
    actions.DesktopActions(cfg, toggle_state=state).execute('test')
    assert state['test'][1] == 1
    without = deepcopy(cfg)
    del without['actions']['test']['toggle_steps']
    actions.DesktopActions(without, toggle_state=state).execute('test')
    assert 'test' not in state
    runner = actions.DesktopActions(cfg, toggle_state=state, dry_run=True)
    assert runner.execute('test')['steps'] == cfg['actions']['test']['steps']


def test_native_utility_widgets_build_hidden_and_cleanup_all_timer_paths(monkeypatch):
    from mx4_linux.local_utilities import UtilityWindows
    from mx4_linux.gtk_support import Adw, GLib, Gtk
    if not Gtk.init_check():
        pytest.skip('A graphical session is required for hidden native widget construction')
    Adw.init()
    parent = Adw.Window()
    manager = UtilityWindows(parent)
    manager.windows = {operation: manager._window(operation) for operation in ('stopwatch', 'tap-tempo')}
    assert all(not w.get_visible() for w in manager.windows.values())
    sources, removed = [], []
    monkeypatch.setattr(GLib, 'timeout_add', lambda ms, fn: sources.append((ms, fn)) or 42)
    monkeypatch.setattr(GLib, 'source_remove', removed.append)
    manager._toggle_watch()
    assert manager.timer == 42 and sources[0][0] == 100
    manager._visibility_changed(manager.windows['stopwatch'], None)
    assert manager.timer == 0 and manager.stopwatch.started is None
    manager._toggle_watch()
    manager._hide(manager.windows['stopwatch'])
    assert manager.timer == 0 and manager.stopwatch.started is None
    manager._toggle_watch()
    manager.close()
    parent.destroy()
    assert removed == [42, 42, 42]


def test_bell_requests_display_without_claiming_sound_or_showing_windows(monkeypatch):
    from types import SimpleNamespace
    from mx4_linux.local_utilities import UtilityWindows
    from mx4_linux import local_utilities
    display = SimpleNamespace(beep=Mock())
    monkeypatch.setattr(local_utilities.Gdk.Display, 'get_default', lambda: display)
    manager = UtilityWindows.__new__(UtilityWindows)
    manager.windows = {}
    manager.show('bell')
    display.beep.assert_called_once()
    assert manager.windows == {}
    handler = Mock()
    result = actions.DesktopActions(document([{'type': 'local', 'operation': 'bell'}]), local_handler=handler).execute('test')
    assert result['steps'] == [{'type': 'local', 'operation': 'bell', 'requested': True}]
    assert 'audible' not in str(result)


def test_date_dry_run_does_not_read_clock_or_input_helper(monkeypatch):
    from mx4_linux import action_utilities
    forbidden = Mock(side_effect=AssertionError('Dry-run effect'))
    monkeypatch.setattr(action_utilities, '_now', forbidden)
    monkeypatch.setattr(actions, '_native_wtype', forbidden)
    monkeypatch.setattr(actions, 'clipboard_operation', forbidden)
    monkeypatch.setattr(actions, 'focused_context', forbidden)
    cfg = document([{'type': 'date', 'format': 'moon-phase'}, {'type': 'clipboard', 'operation': 'clear'},
                    {'type': 'local', 'operation': 'bell'}])
    actions.DesktopActions(cfg, dry_run=True, local_handler=forbidden, local_preflight=forbidden).execute('test')
    forbidden.assert_not_called()


def test_valid_html_numeric_escape_with_leading_zeroes_stays_bounded():
    from mx4_linux.action_utilities import transform_clipboard
    assert transform_clipboard('html-decode', ('&#' + '0' * 5000 + '65;').encode()) == b'A'


def test_no_native_handler_keeps_preview_and_skipped_condition_effect_free(monkeypatch):
    cfg = document([{'type': 'delay', 'milliseconds': 0}, {'type': 'local', 'operation': 'bell'}], app_id='expected')
    runner = actions.DesktopActions(cfg, dry_run=True)
    earlier = Mock(side_effect=AssertionError('No branch effect allowed'))
    monkeypatch.setattr(runner, '_step', earlier)
    assert runner.execute('test')['dry_run']
    runner.dry_run = False
    monkeypatch.setattr(actions, 'focused_context', lambda: {'app_id': 'other', 'window_id': 1})
    assert runner.execute('test')['skipped']
    earlier.assert_not_called()


def test_failed_helper_cleanup_also_kills_descendant_after_leader_exit(monkeypatch):
    import os
    import signal
    import sys
    from mx4_linux import action_utilities
    spawned = []
    popen = action_utilities.subprocess.Popen
    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        spawned.append(child)
        return child
    monkeypatch.setattr(action_utilities.subprocess, 'Popen', spawn)
    # The owned leader exits only after its child installs its TERM handler.
    # Child holds stdout open, simulating a wedged helper descendant.
    code = '''import os,signal,time
r,w=os.pipe()
pid=os.fork()
if pid:
 os.close(w);os.read(r,1);os._exit(0)
os.close(r);signal.signal(signal.SIGTERM,signal.SIG_IGN)
os.write(w,b'1');os.close(w);time.sleep(30)
'''
    try:
        with pytest.raises(RuntimeError, match='timed out'):
            action_utilities.clipboard_process([sys.executable, '-c', code], lambda: None, limit=128)
        assert spawned[0].poll() == 0
        try:
            os.killpg(spawned[0].pid, 0)
        except ProcessLookupError:
            return
        pytest.fail('Failed helper descendant remains alive after leader exited and TERM was ignored')
    finally:
        if spawned:
            try:
                os.killpg(spawned[0].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            spawned[0].wait()



@pytest.mark.parametrize('leader_exits', [False, True])
def test_failed_helper_group_cleanup_escalates_after_term_ignoring_descendant(monkeypatch, leader_exits):
    import os
    import signal
    import sys
    from mx4_linux import action_utilities
    children = []
    popen = action_utilities.subprocess.Popen
    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(action_utilities.subprocess, 'Popen', spawn)
    code = '''import os,signal,time,sys
r,w=os.pipe()
pid=os.fork()
if pid:
 os.close(w);os.read(r,1)
 if sys.argv[1]=='exit': os._exit(0)
 time.sleep(30)
else:
 os.close(r);signal.signal(signal.SIGTERM,signal.SIG_IGN)
 os.write(w,b'1');os.close(w);time.sleep(30)
'''
    try:
        with pytest.raises(RuntimeError, match='timed out'):
            action_utilities.clipboard_process([sys.executable, '-c', code, 'exit' if leader_exits else 'wait'],
                                                lambda: None, limit=128)
        assert children[0].poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(children[0].pid, 0)
    finally:
        if children:
            try:
                os.killpg(children[0].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            children[0].wait()


def test_successful_clipboard_helper_preserves_selection_owner_descendant(monkeypatch):
    import os
    import signal
    import sys
    from mx4_linux import action_utilities
    children = []
    popen = action_utilities.subprocess.Popen
    def spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(action_utilities.subprocess, 'Popen', spawn)
    code = '''import os,time
r,w=os.pipe()
pid=os.fork()
if pid:
 os.close(w);os.read(r,1);os._exit(0)
os.close(r);os.write(w,b'1');os.close(w);time.sleep(30)
'''
    try:
        action_utilities.clipboard_process([sys.executable, '-c', code], lambda: None)
        assert children[0].poll() == 0
        os.killpg(children[0].pid, 0)  # Successful clipboard owners must survive their parent.
    finally:
        if children:
            try:
                os.killpg(children[0].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            children[0].wait()
