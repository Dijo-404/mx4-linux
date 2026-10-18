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
    window.desktop.store.save = Mock(side_effect=RuntimeError('Disk full'))
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
