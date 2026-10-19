"""Reference navigation is immediate; inspectors remain tied to confirmed settings."""
from copy import deepcopy
from time import monotonic, sleep
from unittest.mock import Mock
from uuid import uuid4

import pytest

pytest.importorskip('gi')
from mx4_linux.gtk_support import Adw, Gio, GLib, Gtk
from mx4_linux.gui import ControlWindow


def settle(window):
    deadline = monotonic() + 3
    context = GLib.MainContext.default()
    while window.busy and monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        sleep(.001)
    assert not window.busy


@pytest.fixture
def window(tmp_path, monkeypatch):
    if not Gtk.init_check():
        pytest.skip('Native display required')
    Adw.init()
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    app = Adw.Application(application_id=f'io.github.mx4linux.ShellTests.t{uuid4().hex}',
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    window = ControlWindow(app, demo=True)
    settle(window)
    window.backend.scan = Mock(side_effect=AssertionError('Navigation must not read hardware'))
    yield window
    window.close()


def test_home_device_inspector_back_preserves_real_target(window):
    assert window.route == 'home'
    window.navigate('buttons')
    assert window.route == 'buttons' and window.selected_target is None
    window.select_target('button:83')
    assert window.selected_target == 'button:83'
    assert window.shell.inspector.get_show_sidebar()
    assert not window.shell.sidebar.get_reveal_child()
    assert window.shell.inspector_stack.get_visible_child_name() == 'button:83'
    assert ('reprogrammable-keys', 83) in window.setting_widgets
    window.go_back()
    assert window.route == 'buttons' and window.selected_target is None
    window.go_back()
    assert window.route == 'home'
    window.backend.scan.assert_not_called()


def test_every_verified_callout_opens_its_setting_or_cid(window):
    targets = {'buttons': ['button:82', 'button:83', 'button:86', 'button:195', 'button:196', 'button:416', 'thumb'],
               'point': ['wheel', 'thumb', 'pressure', 'pointer'], 'haptics': ['haptics']}
    for route, names in targets.items():
        window.navigate(route)
        for name in names:
            window.select_target(name)
            assert window.shell.inspector_stack.get_visible_child_name() == name
            assert window.selected_target == name
    window.backend.scan.assert_not_called()


def test_selected_inspector_readback_preserves_widget_and_selection(window):
    window.navigate('point')
    window.select_target('pointer')
    dropdown = window.setting_widgets[('dpi', None)][0]
    dropdown.set_selected(3)
    settle(window)
    assert window.current['settings'][0]['value'] == 1600
    assert window.selected_target == 'pointer'
    assert window.setting_widgets[('dpi', None)][0] is dropdown
    assert '1600' in window.shell.art.callouts['pointer'].get_label()


def test_missing_capability_has_no_inert_hotspot_or_invented_control(window):
    device = deepcopy(window.current)
    device['settings'] = [s for s in device['settings'] if s['name'] not in ('force-sensing', 'thumb-scroll-invert')]
    window.devices = [device]
    window.render({'ok': True, 'devices': [device], 'diagnostics': []})
    window.navigate('point')
    assert 'pressure' not in window.shell.art.callouts
    assert 'thumb' not in window.shell.art.callouts
    window.select_target('pressure')
    assert window.selected_target is None
    assert ('force-sensing', None) not in window.setting_widgets


def test_navigation_during_hardware_job_keeps_controls_disabled(window):
    import threading
    started, release = threading.Event(), threading.Event()
    def work():
        started.set()
        assert release.wait(2)
        return 1
    window.job(work, lambda _: None, 'Working…')
    assert started.wait(1)
    window.navigate('point')
    window.select_target('pointer')
    assert not window.controls.is_sensitive()
    release.set()
    settle(window)
    assert window.controls.is_sensitive()


def test_empty_device_state_keeps_home_and_help_available(window):
    window.devices = []
    window.render({'ok': True, 'devices': [], 'diagnostics': []})
    assert window.current is None
    window.navigate('settings')
    assert window.selected_target is None
    assert not window.shell.device_card.get_sensitive()
    window.navigate('guide')
    assert window.route == 'guide'
    window.navigate('home')
    assert window.route == 'home'


def _texts(widget):
    found = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child:
        found += _texts(child)
        child = child.get_next_sibling()
    return found


@pytest.mark.parametrize('kind, heading, step', [('missing', 'Connect your\nMX Master 4', 'Pair it'),
                                                 ('denied', 'Allow access to\nyour mouse', './scripts/install-udev-rule.sh'),
                                                 ('offline', 'Wake your\nmouse', 'Charge it')])
def test_disconnected_states_explain_next_steps_and_hide_device_context(window, kind, heading, step):
    shell = window.shell
    diagnostics = ([{'path': '/dev/hidraw3', 'message': 'Permission denied', 'permission_denied': True}]
                   if kind == 'denied' else [])
    window.devices = [dict(window.current, online=False)] if kind == 'offline' else []
    window.navigate('buttons')
    window.reload = Mock()
    window.render({'ok': True, 'devices': window.devices, 'diagnostics': diagnostics})
    page = window.controls.get_first_child()
    texts = _texts(page)
    assert heading in texts and step in texts
    details = [label for label in texts if label == 'Details']
    assert bool(details) == (kind == 'denied')  # Diagnostics stay collapsed and appear only when reported.
    if details:
        assert 'Device access denied' in texts
    assert not shell.header_context_reveal.get_reveal_child() and not shell.context_reveal.get_reveal_child()
    first = shell.connect_picture
    assert first.interactive and first.motion_enabled == shell.motion_enabled
    shell.motion_button.set_active(True)
    assert not first.motion_enabled
    retry = next(b for b in _buttons(page) if b.get_label() == 'Check again')
    retry.emit('clicked')
    window.reload.assert_called_once_with()
    window.render({'ok': True, 'devices': window.devices, 'diagnostics': diagnostics})
    assert first.disposed and shell.connect_picture is not first


def _buttons(widget):
    found = [widget] if isinstance(widget, Gtk.Button) else []
    child = widget.get_first_child()
    while child:
        found += _buttons(child)
        child = child.get_next_sibling()
    return found


def test_native_choice_names_and_round_trip_use_real_cid_values(window):
    window.navigate('buttons')
    window.select_target('button:83')
    dropdown = window.setting_widgets[('reprogrammable-keys', 83)][0]
    assert dropdown.get_model().get_string(0) == 'Middle button'
    original = window.backend.write
    window.backend.write = Mock(side_effect=original)
    dropdown.set_selected(2)
    settle(window)
    assert window.current['settings'][-1]['value']['83'] == 86
    dropdown.set_selected(1)
    settle(window)
    assert window.current['settings'][-1]['value']['83'] == 83
    assert window.backend.write.call_count == 2
    assert window.selected_target == 'button:83'


def test_failed_write_reopens_same_target_with_confirmed_readback(window):
    from mx4_linux.backend import BackendError
    window.navigate('point')
    window.select_target('pointer')
    window.backend.write = Mock(side_effect=BackendError('Disconnected'))
    window.backend.scan = Mock(return_value={'ok': True, 'devices': [deepcopy(window.backend.device)], 'diagnostics': []})
    window.setting_widgets[('dpi', None)][0].set_selected(3)
    settle(window)
    assert window.selected_target == 'pointer'
    assert window.current['settings'][0]['value'] == 1000
    assert '1000' in window.shell.art.callouts['pointer'].get_label()
    assert 'Disconnected' in window.status.get_text()


def test_application_binding_updates_only_actual_active_profile(window):
    config = deepcopy(window.desktop.config)
    profile = {'label': 'General', 'app_id': '*', 'ring': config['ring'][:], 'button_bindings': {}}
    config['assignment_profiles'] = {'general': profile, 'editor': {**deepcopy(profile), 'label': 'Editor', 'app_id': 'org.example.Editor'}}
    config['active_profiles'] = {'*': 'general', 'org.example.Editor': 'editor'}
    window.desktop.store.save(config)
    window.desktop.reload_config()
    window.shell.update_context()
    window.shell.context.set_selected(1)
    assert window.shell.context_app == 'org.example.Editor'
    window.navigate('buttons')
    window.select_target('button:83')
    window.save_button_assignment(83, 'copy', window.shell.context_app)
    settle(window)
    saved = window.desktop.store.read()
    assert saved['assignment_profiles']['editor']['button_bindings'] == {'83': 'copy'}
    assert saved['assignment_profiles']['general']['button_bindings'] == {}
    assert saved['button_bindings'] == {}
    assert 'Copy' in window.shell.art.callouts['button:83'].get_label()


def test_failed_local_policy_save_reverts_switch_without_hardware_write(window):
    window.navigate('haptics')
    window.select_target('haptics')
    master = window.shell.preference_widgets[('haptic_feedback', None)][0]
    window.desktop.store.update = Mock(side_effect=RuntimeError('Disk full'))
    window.backend.write = Mock()
    master.set_active(False)
    settle(window)
    assert master.get_active() is True
    assert window.desktop.config['haptic_feedback'] is True
    assert 'Disk full' in window.status.get_text()
    window.backend.write.assert_not_called()


def test_appearance_is_local_and_feedback_policy_uses_current_config(window):
    window.navigate('application')
    appearance = window.shell.preference_widgets[('appearance', None)][0]
    appearance.set_selected(1)
    settle(window)
    assert window.desktop.store.read()['appearance'] == 'light'
    assert window.has_css_class('mx4-light')
    window.navigate('haptics')
    window.select_target('haptics')
    hover = window.shell.preference_widgets[('haptic_events', 'hover')][0]
    hover.set_active(False)
    settle(window)
    assert window.desktop.store.read()['haptic_events']['hover'] is False
    assert window.desktop.effective_config['haptic_events']['hover'] is False


def test_restoration_or_ownership_warning_remains_visible_on_other_pages(window):
    window.desktop.reacquire_blocked = True
    window.desktop.message = 'Another application changed reporting. Recover this mouse.'
    window.navigate('home')
    window.update_desktop_status()
    assert window.integration_warning.get_revealed()
    assert 'Recover' in window.integration_warning.get_title()
    window.navigate('point')
    window.select_target('pointer')
    assert window.integration_warning.get_revealed()


def test_range_edits_are_drafts_until_explicit_apply(window):
    window.navigate('haptics')
    window.select_target('haptics')
    window.backend.write = Mock(side_effect=window.backend.write)
    spin = window.setting_widgets[('haptic-level', None)][0]
    spin.set_value(70)
    assert next(s for s in window.current['settings'] if s['name'] == 'haptic-level')['value'] == 50
    window.backend.write.assert_not_called()
    buttons = []
    def visit(widget):
        if isinstance(widget, Gtk.Button) and widget.get_label() == 'Apply':
            buttons.append(widget)
        child = widget.get_first_child()
        while child:
            visit(child)
            child = child.get_next_sibling()
    visit(window.shell.targets['haptics'])
    buttons[0].emit('clicked')
    settle(window)
    window.backend.write.assert_called_once()
    assert next(s for s in window.current['settings'] if s['name'] == 'haptic-level')['value'] == 70
    assert window.setting_widgets[('haptic-level', None)][0] is spin


def test_confirmed_labels_survive_closing_and_reopening_inspector(window):
    window.navigate('point')
    window.select_target('pointer')
    window.setting_widgets[('dpi', None)][0].set_selected(3)
    settle(window)
    window.close_inspector()
    assert '1600' in window.shell.art.callouts['pointer'].get_label()
    window.select_target('pointer')
    assert window.shell.art.callouts['pointer'].get_label().startswith('✓ ')
    assert '1600' in window.shell.art.callouts['pointer'].get_label()


def test_callout_uses_confirmed_native_remap_when_no_local_action(window):
    window.navigate('buttons')
    window.select_target('button:83')
    window.setting_widgets[('reprogrammable-keys', 83)][0].set_selected(2)
    settle(window)
    assert 'Forward' in window.shell.art.callouts['button:83'].get_label()


def test_editor_save_refreshes_callouts_from_actual_config(window):
    window.navigate('buttons')
    config = deepcopy(window.desktop.config)
    config['button_bindings']['83'] = 'copy'
    config['actions']['copy']['label'] = 'Copy selection'
    window.desktop.store.save(config)
    window.desktop.saved()
    assert window.shell.art.callouts['button:83'].get_label() == 'Copy selection'


def test_invalid_local_config_does_not_hide_hardware_or_overwrite_file(window):
    path = window.desktop.store.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('not valid json')
    another = None
    try:
        another = ControlWindow(window.get_application(), demo=True)
        settle(another)
        assert another.current is not None
        assert another.desktop.config is None
        assert another.integration_warning.get_revealed()
        another.navigate('haptics')
        another.select_target('haptics')
        assert not another.shell.preference_widgets[('haptic_feedback', None)][0].get_sensitive()
        assert path.read_text() == 'not valid json'
    finally:
        if another:
            another.close()


def test_native_overlay_dismissal_returns_to_overview_state(window):
    window.navigate('buttons')
    window.select_target('button:83')
    window.shell.inspector.set_show_sidebar(False)
    assert window.selected_target is None
    assert window.route == 'buttons'
    assert window.shell.sidebar.get_reveal_child()


@pytest.mark.parametrize('narrow', [False, True])
def test_easy_switch_turn_stays_visible_until_computers_are_requested(window, narrow):
    shell = window.shell
    shell.set_narrow(narrow)
    window.navigate('easy-switch')
    assert shell.art.picture.view_name == 'underside'
    assert not shell.inspector.get_show_sidebar()
    assert 'hosts' in shell.art.callouts
    shell.art.callouts['hosts'].emit('clicked')
    assert window.selected_target == 'hosts'
    assert shell.inspector_stack.get_visible_child_name() == 'hosts'
    assert shell.inspector.get_show_sidebar()
    shell.inspector.set_show_sidebar(False)
    assert window.route == 'easy-switch' and window.selected_target is None
    assert shell.art.picture.view_name == 'underside'
    window.backend.scan.assert_not_called()


def test_home_utilities_and_crosslinks_preserve_editor_destination(window):
    window.desktop.configure = Mock()
    def find(widget, predicate):
        if predicate(widget):
            return widget
        child = widget.get_first_child()
        while child:
            result = find(child, predicate)
            if result:
                return result
            child = child.get_next_sibling()
    ring = find(window.shell.utilities, lambda w: isinstance(w, Gtk.Button) and w.get_tooltip_text() == 'Actions Ring')
    smart = find(window.shell.utilities, lambda w: isinstance(w, Gtk.Button) and w.get_tooltip_text() == 'Smart Actions')
    ring.emit('clicked')
    assert window.route == 'ring'
    window.desktop.configure.assert_called_with(page='ring')
    smart.emit('clicked')
    assert window.route == 'smart-actions'
    window.desktop.configure.assert_called_with(page='actions')
    window.navigate('haptics')
    link = find(window.shell.targets['haptics'], lambda w: isinstance(w, Gtk.Button) and w.get_label() == 'Open Actions Ring')
    link.emit('clicked')
    assert window.route == 'ring'
    window.desktop.configure.assert_called_with(page='ring')
    window.backend.scan.assert_not_called()


def test_multi_device_picker_prefers_awake_device_and_preserves_explicit_selection(window):
    first = deepcopy(window.current)
    first.update(id='offline-mouse', name='Sleeping MX Master 4', online=False, stable_id='demo:sleeping')
    second = deepcopy(window.current)
    second.update(id='awake-mouse', name='Awake MX Master 4', stable_id='demo:awake')
    window.devices = [first, second]
    result = {'ok': True, 'devices': window.devices, 'diagnostics': []}
    window.selected_id = None
    window.render(result)
    assert window.current['id'] == second['id']
    chooser = window.shell.device_selector
    assert chooser.get_model().get_string(0) == first['name']
    assert chooser.get_model().get_string(1) == second['name']
    epoch = window.operation_epoch
    window.desktop.stop = Mock()
    chooser.set_selected(0)
    assert window.current['id'] == first['id']
    assert window.operation_epoch == epoch + 1
    window.desktop.stop.assert_called_once()
    window.render(result)
    assert window.current['id'] == first['id']
    chooser.set_selected(1)
    assert window.current['id'] == second['id']
    assert window.setting_widgets
    window.backend.scan.assert_not_called()


@pytest.mark.parametrize('result', [{'battery': '79%', 'battery_percent': 79, 'battery_observed_at': 1}, {}, None])
def test_lightweight_battery_updates_all_visible_labels(window, result):
    window.navigate('point')
    window.backend.battery = Mock(return_value=result)
    window.refresh_battery()
    settle(window)
    expected = '79%' if result and result.get('battery') else 'Battery unavailable'
    assert window.shell.device_battery.get_text() == expected
    assert expected in window.shell.home_battery.get_text()
    window.shell.apply_appearance('light')
    window.navigate('haptics')
    assert window.shell.device_battery.get_text() == expected
    window.backend.scan.assert_not_called()


def test_lightweight_battery_failure_clears_both_displays(window):
    from mx4_linux.backend import BackendError
    window.navigate('buttons')
    window.backend.battery = Mock(side_effect=BackendError('asleep'))
    window.refresh_battery()
    settle(window)
    assert window.shell.device_battery.get_text() == 'Battery unavailable'
    assert 'Battery unavailable' in window.shell.home_battery.get_text()
    window.backend.scan.assert_not_called()


def test_valid_long_labels_and_context_ids_keep_bounded_closed_display(window):
    config = deepcopy(window.desktop.config)
    action_label = 'W' * 80
    app_id = 'org.example.' + 'W' * 100
    config['actions']['copy']['label'] = action_label
    config['assignment_profiles'] = {'long-app': {'label': 'Long application', 'app_id': app_id,
                                                 'ring': config['ring'][:], 'button_bindings': {'83': 'copy'}}}
    config['active_profiles'] = {app_id: 'long-app'}
    window.desktop.store.save(config)
    window.desktop.reload_config()
    window.shell.update_context()
    window.shell.header_context.set_selected(1)
    dropdown, _keys = window.shell.binding_widgets[83]
    for widget in (dropdown, window.shell.header_context, window.shell.context):
        minimum, _natural, _, _ = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
        assert minimum < 300
    assert action_label in dropdown.get_tooltip_text()
    assert app_id in window.shell.header_context.get_tooltip_text()
    assert app_id in window.shell.context.get_tooltip_text()
    assert window.shell.effective_config()['button_bindings']['83'] == 'copy'


def test_actual_model_and_identifier_have_operating_copy_controls(window):
    window.shell.copy_text = Mock()
    window.navigate('settings')
    for field in ('name', 'stable_id'):
        window.shell.metadata_copy_buttons[field].emit('clicked')
        window.shell.copy_text.assert_called_with(window.current[field])
    assert 'Preview' in window.shell.connection_summary()
    assert 'serial' not in window.shell.metadata_copy_buttons


def test_local_configuration_destination_is_reachable_and_truthful(window):
    window.navigate('configuration')
    assert window.route == 'configuration'
    assert window.shell.stack.get_visible_child_name() == 'configuration'
    assert str(window.desktop.store.path) in window.shell.configuration_paths.get_text()
    assert 'copy these locations' in window.shell.configuration_status.get_text()
    window.backend.scan.assert_not_called()


def test_background_preference_is_explicit_without_enabling_integration(window, monkeypatch):
    assert window.shell.preference_widgets[('background', None)][0].get_active()
    assert 'background' not in window.desktop.config
    monkeypatch.setattr(window.desktop, 'start', Mock())
    window.shell.preference_widgets[('background', None)][0].set_active(False)
    settle(window)
    assert window.desktop.store.read()['background'] is False
    assert not window.desktop.requested_enabled
    window.desktop.start.assert_not_called()
    window.backend.scan.assert_not_called()


def test_quit_is_available_in_application_menu_at_every_width(window):
    for narrow in (False, True):
        window.shell.set_narrow(narrow)
        assert window.shell.utility_menu.get_visible()
        assert window.shell.quit_button.get_label() == 'Quit MX4 Linux'


def test_compact_navigation_keeps_every_device_page_reachable(window):
    from mx4_linux.reference_shell import DEVICE_ROUTES
    window.navigate('buttons')
    shell = window.shell
    shell.set_narrow(True)
    assert shell.compact_nav.get_visible()
    assert not shell.sidebar.get_reveal_child()
    assert shell.utility_menu.get_visible()
    assert shell.home_note.get_visible()
    for index, route in enumerate(DEVICE_ROUTES):
        shell.compact_nav.set_selected(index)
        assert window.route == route
    window.navigate('buttons')
    shell.set_narrow(False)
    assert shell.sidebar.get_reveal_child()
    assert not shell.compact_nav.get_visible()
    window.backend.scan.assert_not_called()


def test_paused_mouse_previews_stay_paused_across_workspace_rebuild(window):
    shell = window.shell
    shell.motion_button.set_active(True)
    assert not shell.home_picture.motion_enabled
    assert not shell.art.picture.motion_enabled
    window.render_workspace()
    assert not shell.art.picture.motion_enabled
    shell.motion_button.set_active(False)
    assert shell.home_picture.motion_enabled
    assert shell.art.picture.motion_enabled
    settings = Gtk.Settings.get_default()
    original = settings.get_property('gtk-enable-animations')
    try:
        settings.set_property('gtk-enable-animations', False)
        assert window.has_css_class('reduced-motion')
        assert not shell.motion_button.get_sensitive()
        assert shell.home_picture.ambient_tick_id == shell.art.picture.ambient_tick_id == 0
    finally:
        settings.set_property('gtk-enable-animations', original)


def test_existing_editor_opens_requested_routine_tab(window, monkeypatch):
    from mx4_linux.desktop_editor import DesktopEditor, present_desktop_editor
    monkeypatch.setattr(DesktopEditor, 'present', lambda _self: None)
    editor = present_desktop_editor(window, window.desktop.store, lambda: None, page='actions')
    try:
        assert editor.stack.get_visible_child_name() == 'actions'
        assert not editor.dirty
    finally:
        editor.destroy()


def test_pending_hardware_operation_disables_device_picker(window):
    second = deepcopy(window.current)
    second.update(id='second', name='Second MX Master 4', stable_id='demo:second')
    window.devices = [window.current, second]
    result = {'ok': True, 'devices': window.devices, 'diagnostics': []}
    window.render(result)
    original = window.selected_id
    epoch = window.operation_epoch
    window.set_hardware_wait(object())
    assert not window.shell.device_selector.get_sensitive()
    window.shell.device_selector.set_selected(1)
    assert window.selected_id == original
    assert window.operation_epoch == epoch
    assert window.shell.device_selector.get_selected() == 0
    window.set_hardware_wait(None)


def test_rebuilt_context_can_open_inspector_before_first_allocation(window, capfd):
    config = deepcopy(window.desktop.config)
    for action in config['actions'].values():
        action['steps'] = [{'type': 'command', 'argv': ['/usr/bin/true']}]
    config['assignment_profiles'] = {'editor': {'label': 'Editor', 'app_id': 'org.example.Editor',
                                               'ring': config['ring'][:], 'button_bindings': {'83': 'copy'}}}
    config['active_profiles'] = {'org.example.Editor': 'editor'}
    window.desktop.store.save(config)
    window.desktop.reload_config()
    window.shell.update_context()
    context = GLib.MainContext.default()
    def pump(allocated=None):
        deadline = monotonic() + (3 if allocated else .35)
        while monotonic() < deadline and (allocated is None or not allocated()):
            while context.pending():
                context.iteration(False)
            sleep(.002)
    window.present()
    pump()
    capfd.readouterr()
    window.shell.header_context.set_selected(1)
    assert window.shell.inspector.get_width() == 0
    window.navigate('buttons')
    window.select_target('button:83')
    pump(lambda: window.shell.inspector.get_width() > 0)
    assert window.selected_target == 'button:83'
    assert window.shell.inspector.get_show_sidebar()
    assert window.shell.inspector.get_width() > 0
    assert window.shell.inspector_stack.get_visible_child_name() == 'button:83'
    assert 'attempt to allocate AdwBin' not in capfd.readouterr().err


def test_cancelled_device_completion_clears_home_and_selection(window):
    import threading
    release = threading.Event()
    try:
        window.navigate('buttons')
        window.select_target('button:83')
        window.job(lambda: release.wait(3), lambda _: None, 'Delayed device operation')
        window.cancel_hardware()
        release.set()
        settle(window)
        assert window.current is None
        assert window.selected_target is None
        assert not window.shell.device_card.get_sensitive()
        assert window.shell.home_battery.get_text() == 'No device available'
        assert not window.wheel.get_visible()
    finally:
        release.set()


@pytest.mark.parametrize('assignment', [False, True], ids=['preference', 'assignment'])
@pytest.mark.parametrize('completion_error', [False, True], ids=['success', 'post-commit-error'])
def test_cancelled_local_commit_reconciles_file_cache_and_theme(window, monkeypatch, assignment, completion_error):
    import threading
    import mx4_linux.actions as actions
    release, committed = threading.Event(), threading.Event()
    replace = actions.os.replace
    def delayed_replace(*args):
        replace(*args)
        committed.set()
        assert release.wait(3)
        if completion_error:
            raise OSError('Completion interrupted after replacement')
    monkeypatch.setattr(actions.os, 'replace', delayed_replace)
    try:
        if assignment:
            window.save_button_assignment(83, 'copy')
        else:
            window.navigate('application')
            window.shell.preference_widgets[('appearance', None)][0].set_selected(1)
        assert committed.wait(1)
        window.cancel_hardware()
        release.set()
        settle(window)
        saved = window.desktop.store.read()
        assert window.desktop.config == saved
        if assignment:
            assert saved['button_bindings']['83'] == 'copy'
        else:
            assert saved['appearance'] == 'light'
            assert window.has_css_class('mx4-light')
            assert window.shell.preference_widgets[('appearance', None)][0].get_selected() == 1
        if completion_error:
            assert 'Completion interrupted' in window.status.get_text()
    finally:
        release.set()


@pytest.mark.parametrize('assignment', [False, True], ids=['preference', 'assignment'])
def test_local_update_preserves_newer_unrelated_file_edit(window, assignment):
    document = deepcopy(window.desktop.config)
    document['background'] = False
    window.desktop.store.save(document)
    assert window.desktop.config != document
    if assignment:
        window.save_button_assignment(83, 'copy')
    else:
        window.save_local_preference('appearance', 'light')
    settle(window)
    assert window.desktop.store.read()['background'] is False
    assert window.desktop.config['background'] is False


def test_local_completion_reconciles_latest_file_instead_of_requested_theme(window, monkeypatch):
    update = window.desktop.store.update
    def newer_edit(mutate):
        committed = update(mutate)
        latest = deepcopy(committed)
        latest['appearance'] = 'dark'
        window.desktop.store.save(latest)
        return committed
    monkeypatch.setattr(window.desktop.store, 'update', newer_edit)
    window.save_local_preference('appearance', 'light')
    settle(window)
    assert window.desktop.config['appearance'] == 'dark'
    assert not window.has_css_class('mx4-light')


def test_local_completion_after_close_skips_ui_reconciliation(window, monkeypatch):
    import threading
    release, committed, finished = threading.Event(), threading.Event(), threading.Event()
    update = window.desktop.store.update
    def delayed_update(mutate):
        try:
            result = update(mutate)
            committed.set()
            assert release.wait(3)
            return result
        finally:
            finished.set()
    monkeypatch.setattr(window.desktop.store, 'update', delayed_update)
    window.local_config_finished = Mock()
    try:
        window.save_local_preference('appearance', 'light')
        assert committed.wait(1)
        # The fixture stays unmapped; invoke its close-request handler directly.
        window.on_close()
        assert window.closed
        release.set()
        assert finished.wait(1)
        window.executor.shutdown(wait=True)  # Ensure its idle completion was queued.
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        assert window.desktop.store.read()['appearance'] == 'light'
        window.local_config_finished.assert_not_called()
    finally:
        release.set()


def test_all_button_menus_can_replace_trigger_and_edit_explicit_app_with_adapt_off(window):
    from mx4_linux.actions import SHOW_RING
    config = deepcopy(window.desktop.config)
    config['assignment_profiles'] = {'app': {'label': 'Editor', 'app_id': 'org.example.Editor',
        'ring': config['ring'][:], 'button_bindings': {}}}
    config['active_profiles'] = {'org.example.Editor': 'app'}
    config['adapt_to_app'] = False
    window.desktop.store.save(config)
    window.desktop.reload_config()
    window.shell.context_app = 'org.example.Editor'
    window.shell.update_context()
    window.navigate('buttons')
    assert set(window.shell.binding_widgets) == {82, 83, 86, 195, 196, 416}
    dropdown, choices = window.shell.binding_widgets[416]
    assert choices[:2] == [None, SHOW_RING]
    assert dropdown.get_selected() == 1
    dropdown.set_selected(choices.index('copy'))
    settle(window)
    saved = window.desktop.store.read()
    assert saved['ring_button'] == 416
    assert saved['assignment_profiles']['app']['ring_button'] is None
    assert window.shell.effective_config()['button_bindings'] == {'416': 'copy'}
    dropdown, choices = window.shell.binding_widgets[83]
    dropdown.set_selected(choices.index(SHOW_RING))
    settle(window)
    assert window.desktop.store.read()['assignment_profiles']['app']['ring_button'] == 83
    assert 'Show Actions Ring' in window.shell.art.callouts['button:83'].get_label()
    assert window.shell.binding_widgets[416][0].get_selected() == window.shell.binding_widgets[416][1].index('copy')


def test_native_mapping_clears_selected_local_trigger_before_hid_write(window):
    window.navigate('buttons')
    window.select_target('button:416')
    original = window.backend.write
    observations = []
    def write(*args, **kwargs):
        observations.append(window.desktop.store.read()['ring_button'])
        return original(*args, **kwargs)
    window.backend.write = Mock(side_effect=write)
    setting = next(s for s in window.current['settings'] if s['name'] == 'reprogrammable-keys')
    window.change(setting, 83, 416)
    settle(window)
    assert observations == [None]
    assert window.desktop.config['ring_button'] is None
    assert 'Show Actions Ring' not in window.shell.art.callouts['button:416'].get_label()


def test_native_mapping_never_writes_after_local_clear_save_failure(window):
    window.navigate('buttons')
    original = window.desktop.store.read()
    window.desktop.store.update = Mock(side_effect=RuntimeError('disk unavailable'))
    window.backend.write = Mock()
    window.backend.scan = Mock(return_value={'ok': True, 'devices': deepcopy(window.devices), 'diagnostics': []})
    setting = next(s for s in window.current['settings'] if s['name'] == 'reprogrammable-keys')
    window.change(setting, 83, 416)
    settle(window)
    window.backend.write.assert_not_called()
    assert window.desktop.store.read() == original
    assert 'not changed' in window.status.get_text()


def test_root_trigger_conflict_rolls_back_selected_menu_and_document(window):
    from mx4_linux.actions import SHOW_RING
    document = deepcopy(window.desktop.config)
    document['assignment_profiles'] = {'inactive': {'label': 'Inactive', 'app_id': 'app.inactive',
        'ring': document['ring'][:], 'button_bindings': {'83': 'copy'}}}
    window.desktop.store.save(document)
    window.desktop.reload_config()
    window.navigate('buttons')
    window.select_target('button:83')
    dropdown, choices = window.shell.binding_widgets[83]
    dropdown.set_selected(choices.index(SHOW_RING))
    settle(window)
    assert window.desktop.store.read() == document
    assert window.shell.binding_widgets[83][0].get_selected() == 0
    assert window.selected_target == 'button:83'
    assert 'distinct' in window.status.get_text()


def test_native_mapping_failure_keeps_local_clear_and_reports_two_operations(window):
    window.navigate('buttons')
    window.backend.write = Mock(side_effect=RuntimeError('readback missing'))
    window.backend.scan = Mock(return_value={'ok': True, 'devices': deepcopy(window.devices), 'diagnostics': []})
    setting = next(s for s in window.current['settings'] if s['name'] == 'reprogrammable-keys')
    window.change(setting, 83, 416)
    settle(window)
    assert window.desktop.store.read()['ring_button'] is None
    assert window.desktop.config['ring_button'] is None
    assert 'Local assignment cleared' in window.status.get_text()
    assert 'not confirmed' in window.status.get_text()


def test_adapt_preference_does_not_stop_or_dismiss_pinned_ring(window):
    window.desktop.stop = Mock()
    window.desktop.ring = Mock()
    window.desktop.ring.state.visible = True
    snapshot = window.desktop._ring_config = deepcopy(window.desktop.config)
    target = window.desktop.target = {'window_id': 12, 'app_id': 'app.original'}
    window.save_local_preference('adapt_to_app', False)
    settle(window)
    assert window.desktop.config['adapt_to_app'] is False
    window.desktop.stop.assert_not_called()
    window.desktop.ring.dismiss.assert_not_called()
    assert window.desktop._ring_config is snapshot and window.desktop.target is target
    window.desktop.ring = None
