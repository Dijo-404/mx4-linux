"""Keep the native editor's form and JSON drafts lossless and side-effect free."""

from copy import deepcopy
import json
import threading
from time import monotonic, sleep

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as exc:
    pytest.skip(str(exc), allow_module_level=True)

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from mx4_linux.actions import default_config, validate_config
from mx4_linux.desktop_editor import ApplicationPicker, DesktopEditor, _DraftError, _step_values, _values_step


@pytest.mark.parametrize("step", [
    {"type": "shortcut", "keys": ["CTRL", "SHIFT", "S"]},
    {"type": "command", "argv": ["kitty", "--title", "Hello $(touch nope)", ""]},
    {"type": "application", "desktop_id": "org.example.Editor.desktop"},
    {"type": "text", "text": "Hello\n世界"},
    {"type": "delay", "milliseconds": 150},
    {"type": "niri", "action": "focus-workspace", "args": ["work space"]},
])
def test_step_fields_preserve_values_and_literal_arguments(step):
    assert _values_step(_step_values(step)) == step


@pytest.fixture
def editor(request):
    if not Gtk.init_check():
        pytest.skip("A graphical session is needed for native editor interaction tests")
    Adw.init()

    class Store:
        saved = None

        def read(self):
            return deepcopy(getattr(request, "param", default_config()))

        def save(self, value):
            self.saved = deepcopy(validate_config(value))

    store = Store()
    window = DesktopEditor(None, store, lambda: None)
    yield window
    iterate_until(lambda: not window.saving)
    window.allow_close = True
    window.destroy()


def iterate_until(predicate, timeout=2):
    context = GLib.MainContext.default()
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        sleep(0.001)
    assert predicate(), "GTK callback did not complete in time"


def select(editor, action_id):
    editor.action_choice.set_selected(editor.choice_ids.index(action_id))


def entry_in(widget, title):
    child = widget.get_first_child()
    while child:
        if isinstance(child, Adw.EntryRow) and child.get_title() == title:
            return child
        nested = entry_in(child, title)
        if nested:
            return nested
        child = child.get_next_sibling()
    return None


def test_incomplete_form_survives_routine_switch_without_saving(editor):
    select(editor, "terminal")
    entry_in(editor.steps_group, "Application and arguments").set_text('kitty --title "incomplete')
    assert editor.step_error.get_visible()
    select(editor, "copy")
    select(editor, "terminal")
    assert entry_in(editor.steps_group, "Application and arguments").get_text() == 'kitty --title "incomplete'
    with pytest.raises(_DraftError, match="quotation marks"):
        editor._candidate()
    assert editor.store.saved is None


def test_json_draft_survives_switch_and_returns_to_builder(editor):
    select(editor, "copy")
    editor.json_toggle.set_active(True)
    editor.steps_buffer.set_text("[{")
    editor.json_toggle.set_active(False)
    assert editor.json_toggle.get_active()
    select(editor, "paste")
    select(editor, "copy")
    assert editor.json_toggle.get_active()
    assert editor.drafts["copy"]["steps_text"] == "[{"
    steps = [{"type": "text", "text": "First\nSecond"}]
    editor.steps_buffer.set_text(json.dumps(steps))
    editor.json_toggle.set_active(False)
    assert not editor.json_toggle.get_active()
    assert editor._candidate()["actions"]["copy"]["steps"] == steps


def test_builder_add_reorder_remove_and_rename_preserves_assignments(editor):
    select(editor, "copy")
    editor.step_choice.set_selected(3)  # Wait
    editor._add_step()
    editor._move_step(None, 1, -1)
    assert editor._candidate()["actions"]["copy"]["steps"][0] == {"type": "delay", "milliseconds": 150}
    editor._move_step(None, 0, 0)
    editor.config["button_bindings"]["83"] = "copy"
    editor.id_entry.set_text("copy-selection")
    candidate = validate_config(editor._candidate())
    assert candidate["ring"][5] == "copy-selection"
    assert candidate["button_bindings"]["83"] == "copy-selection"
    assert candidate["actions"]["copy-selection"]["steps"] == [{"type": "shortcut", "keys": ["CTRL", "C"]}]
    assert editor.save_button.get_sensitive()
    editor._save()
    iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate


def test_legacy_editor_keeps_groups_optional_and_collapsed(editor):
    assert not editor.groups_expander.get_expanded()
    assert "ring_groups" not in editor._candidate()
    assert all(choice.get_selected() == 0 for _row, choices in editor.group_choices for choice in choices)


def test_ring_preview_tracks_draft_labels_and_new_assignments(editor):
    select(editor, "copy")
    editor.name_entry.set_text("Copy selection")
    assert editor.ring_preview.labels[5].get_text() == "Copy selection"
    editor._add_routine()
    action_id = editor.selected_id
    editor.name_entry.set_text("My shortcut")
    editor.ring_choices[0].set_selected(editor.choice_ids.index(action_id))
    assert editor.ring_preview.labels[0].get_text() == "My shortcut"
    assert editor.ring_preview.selected == 0
    assert editor.store.saved is None


def test_groups_follow_order_and_renames_without_running_or_saving(editor):
    _row, choices = editor.group_choices[2]
    choices[0].set_selected(editor.choice_ids.index("copy") + 1)
    choices[2].set_selected(editor.choice_ids.index("paste") + 1)
    assert editor.config["ring_groups"] == {"2": ["copy", "paste"]}
    select(editor, "copy")
    editor.name_entry.set_text("Copy selection")
    editor.id_entry.set_text("copy-selection")
    candidate = validate_config(editor._candidate())
    assert candidate["ring_groups"] == {"2": ["copy-selection", "paste"]}
    assert editor.config["ring_groups"] == {"2": ["copy", "paste"]}
    assert editor.store.saved is None
    editor._save()
    iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate


def test_duplicate_group_choice_restores_previous_selection(editor):
    _row, choices = editor.group_choices[0]
    selected = editor.choice_ids.index("copy") + 1
    choices[0].set_selected(selected)
    choices[1].set_selected(selected)
    assert editor.config["ring_groups"] == {"0": ["copy"]}
    assert choices[0].get_selected() == selected and choices[1].get_selected() == 0
    assert editor.error.get_visible() and "only once" in editor.error.get_text()
    assert validate_config(editor._candidate())


def test_removing_last_group_choice_restores_direct_action(editor):
    _row, choices = editor.group_choices[0]
    choices[0].set_selected(editor.choice_ids.index("copy") + 1)
    choices[0].set_selected(0)
    assert editor._candidate()["ring_groups"] == {}
    assert editor.config["ring"][0] == "overview"


def test_deleting_routine_removes_group_references_and_empty_groups(editor):
    editor.config["ring_groups"] = {"0": ["copy", "paste"], "1": ["copy"]}
    editor.config["button_bindings"] = {"83": "copy"}
    select(editor, "copy")
    editor._delete_selected()
    dialog = next(window for window in Gtk.Window.list_toplevels()
                  if isinstance(window, Adw.MessageDialog) and window.get_transient_for() is editor)
    dialog.response("delete")
    candidate = validate_config(editor._candidate())
    assert candidate["ring_groups"] == {"0": ["paste"]}
    assert "copy" not in candidate["actions"]
    assert "copy" not in candidate["ring"]
    assert candidate["button_bindings"] == {}
    assert editor.store.saved is None


@pytest.mark.parametrize("editor", [{**default_config(), "ring_groups": {"7": ["paste", "copy"]}}], indirect=True)
def test_saved_groups_load_without_changing_order_or_dirtying_editor(editor):
    assert not editor.dirty
    _row, choices = editor.group_choices[7]
    assert choices[0].get_selected() == editor.choice_ids.index("paste") + 1
    assert choices[1].get_selected() == editor.choice_ids.index("copy") + 1
    assert editor._candidate()["ring_groups"] == {"7": ["paste", "copy"]}


def test_last_step_can_be_replaced_but_empty_routine_cannot_save(editor):
    select(editor, "copy")
    editor._move_step(None, 0, 0)
    assert editor.step_error.get_visible()
    editor._save()
    assert editor.store.saved is None
    assert editor.error.get_visible()
    editor._add_step()
    assert not editor.step_error.get_visible()
    assert validate_config(editor._candidate())


def test_blocked_save_keeps_gtk_responsive_and_prevents_edits_or_close(editor, monkeypatch):
    select(editor, "copy")
    editor.name_entry.set_text("Copy selection")
    candidate, draft = editor._candidate(), deepcopy(editor.drafts)
    entered, release, pulse = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    save_threads, notifications = [], []
    persist = editor.store.save

    def blocked_save(value):
        save_threads.append(threading.get_ident())
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Save was not released by the test")
        persist(value)

    monkeypatch.setattr(editor.store, "save", blocked_save)
    editor.on_saved = lambda: notifications.append(threading.get_ident())
    try:
        editor._save()
        assert entered.wait(1)
        assert editor.saving and editor.dirty
        assert not editor.stack.get_sensitive()
        assert not editor.save_button.get_sensitive()
        assert editor._close_requested()
        assert editor.close_dialog is None
        editor._save()
        GLib.idle_add(lambda: (pulse.set(), GLib.SOURCE_REMOVE)[1])
        iterate_until(pulse.is_set)
        assert editor.store.saved is None
        assert editor.drafts == draft
        assert len(save_threads) == 1 and save_threads[0] != main_thread
    finally:
        release.set()
        iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate
    assert notifications == [main_thread]
    assert editor.allow_close and not editor.dirty


def test_failed_background_save_keeps_draft_and_allows_retry(editor, monkeypatch):
    select(editor, "copy")
    editor.name_entry.set_text("Copy selection")
    candidate, draft = editor._candidate(), deepcopy(editor.drafts)
    persist = editor.store.save
    notifications = []
    editor.on_saved = lambda: notifications.append(True)

    def failed_save(_value):
        raise OSError("Disk is full")

    monkeypatch.setattr(editor.store, "save", failed_save)
    editor._save()
    iterate_until(lambda: not editor.saving)
    assert editor.dirty and not editor.allow_close
    assert editor.stack.get_sensitive() and editor.save_button.get_sensitive()
    assert editor.error.get_visible() and "Disk is full" in editor.error.get_text()
    assert editor.drafts == draft
    assert not notifications
    monkeypatch.setattr(editor.store, "save", persist)
    editor._save()
    iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate
    assert notifications == [True]


def test_keyboard_save_validates_the_current_draft_before_writing(editor):
    select(editor, "copy")
    editor.name_entry.set_text("")
    assert editor._key_pressed(None, Gdk.KEY_s, 0, Gdk.ModifierType.CONTROL_MASK)
    assert editor.store.saved is None and editor.dirty
    assert editor.error.get_visible()
    assert editor.name_entry.get_text() == ""
    assert editor.details_error.get_visible()
    assert editor.error.get_text() == "Give this routine a name."
    assert Gtk.test_accessible_has_state(editor.name_entry, Gtk.AccessibleState.INVALID)

    editor.name_entry.set_text("Copy selection")
    assert not editor.details_error.get_visible()
    assert not Gtk.test_accessible_has_state(editor.name_entry, Gtk.AccessibleState.INVALID)
    candidate = editor._candidate()
    assert editor._key_pressed(None, Gdk.KEY_s, 0, Gdk.ModifierType.CONTROL_MASK)
    iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate


def test_keyboard_dismissal_uses_window_close_and_waits_for_save(editor, monkeypatch):
    closed = []
    monkeypatch.setattr(editor, "close", lambda: closed.append(True))
    assert not editor._key_pressed(None, Gdk.KEY_s, 0, 0)
    editor.saving = True
    try:
        assert editor._key_pressed(None, Gdk.KEY_Escape, 0, 0)
        assert closed == []
    finally:
        editor.saving = False
    assert editor._key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert closed == [True]


def test_editor_visual_preferences_follow_system_and_disconnect_when_hidden(editor):
    scheme = editor.style.get_color_scheme()
    animations = editor.settings.get_property("gtk-enable-animations")
    editor._connect_visual_preferences()
    style_handler, motion_handler = editor.style_handler, editor.motion_handler
    try:
        editor._connect_visual_preferences()
        assert (editor.style_handler, editor.motion_handler) == (style_handler, motion_handler)
        editor.style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        assert editor.has_css_class("mx4-light")
        editor.style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        assert not editor.has_css_class("mx4-light")
        editor.settings.set_property("gtk-enable-animations", False)
        assert editor.stack.get_transition_duration() == 0
        assert editor.has_css_class("reduced-motion")
        editor.settings.set_property("gtk-enable-animations", True)
        assert editor.stack.get_transition_duration() > 0
        editor._disconnect_visual_preferences()
        assert not GObject.signal_handler_is_connected(editor.style, style_handler)
        assert not GObject.signal_handler_is_connected(editor.settings, motion_handler)
        assert editor.style_handler == editor.motion_handler == 0
        editor._disconnect_visual_preferences()
    finally:
        editor._disconnect_visual_preferences()
        editor.style.set_color_scheme(scheme)
        editor.settings.set_property("gtk-enable-animations", animations)


def test_clearing_group_entry_compacts_the_visible_order_to_match_shortcuts(editor):
    _row, choices = editor.group_choices[2]
    choices[0].set_selected(editor.choice_ids.index("copy") + 1)
    choices[1].set_selected(editor.choice_ids.index("paste") + 1)
    choices[0].set_selected(0)
    assert editor.config["ring_groups"]["2"] == ["paste"]
    assert choices[0].get_selected() == editor.choice_ids.index("paste") + 1
    assert all(choice.get_selected() == 0 for choice in choices[1:])


def test_manual_trigger_and_all_six_button_rows_preserve_overrides_and_adjustments(editor):
    from mx4_linux.actions import SHOW_RING
    assert {cid for cid, _choice in editor.binding_choices} == {82, 83, 86, 195, 196, 416}
    choice = dict(editor.binding_choices)[416]
    assert choice.get_model().get_string(1) == 'Show Actions Ring'
    choice.set_selected(editor.choice_ids.index('copy') + 2)
    assert editor.config['ring_button'] is None
    assert editor.config['button_bindings']['416'] == 'copy'
    assert 'Manual' in editor.trigger_row.get_subtitle() and 'None' not in editor.trigger_row.get_subtitle()
    dict(editor.binding_choices)[83].set_selected(1)
    assert editor.config['ring_button'] == 83
    assert '83' not in editor.config['button_bindings']
    assert editor.config['button_bindings']['416'] == 'copy'
    assert SHOW_RING not in editor.config.values()


def test_existing_action_icon_adjustment_and_profile_override_survive_trigger_edit(editor):
    editor.config['actions']['copy']['icon'] = 'edit-copy-symbolic'
    editor.config['actions']['copy']['adjustment'] = {
        'increase': [{'type': 'command', 'argv': ['/usr/bin/true']}],
        'decrease': [{'type': 'command', 'argv': ['/usr/bin/true']}],
    }
    editor.config['assignment_profiles'] = {'p': {'label': 'P', 'app_id': 'app.p',
        'ring': editor.config['ring'][:], 'button_bindings': {}, 'ring_button': None}}
    candidate = editor._candidate()
    assert candidate['actions']['copy']['icon'] == 'edit-copy-symbolic'
    assert candidate['actions']['copy']['adjustment'] == editor.config['actions']['copy']['adjustment']
    assert candidate['assignment_profiles']['p']['ring_button'] is None


def test_utility_steps_and_toggle_metadata_remain_lossless_without_execution(editor):
    editor.config['assignment_profiles'] = {'work': {'label': 'Work', 'app_id': '*',
        'ring': editor.config['ring'][:], 'button_bindings': {}}}
    action = editor.config['actions']['copy']
    action['icon'] = 'edit-copy-symbolic'
    action['steps'] = [{'type': 'local', 'operation': 'switch-profile', 'profile_id': 'work'}]
    action['toggle_steps'] = [[{'type': 'date', 'format': 'moon-phase'}]]
    action['adjustment'] = {'increase': [{'type': 'clipboard', 'operation': 'upper'}],
                            'decrease': [{'type': 'clipboard', 'operation': 'lower'}]}
    editor.drafts['copy']['steps_text'] = json.dumps(action['steps'])
    editor.drafts['copy'].pop('form_values', None)
    select(editor, 'copy')
    assert editor.json_toggle.get_active()
    assert editor._validate_selected()
    assert editor._candidate()['actions']['copy'] == action
    editor.name_entry.set_text('New label')
    candidate = editor._candidate()
    assert candidate['actions']['copy'] == {**action, 'label': 'New label'}
    validate_config(candidate)


def test_adding_application_assigns_selected_slot_and_preserves_other_drafts(editor, monkeypatch):
    import mx4_linux.actions as actions
    monkeypatch.setattr(actions, "launch_application", lambda *_: pytest.fail("Editing must not launch applications"))
    select(editor, "copy")
    editor.name_entry.set_text("Copy selection")
    editor.config["ring_groups"] = {"2": ["copy", "paste"], "5": ["paste"]}
    editor._add_application({"name": "Editor", "desktop_id": "org.example.Editor.desktop"}, ring_index=2)
    action_id = editor.selected_id
    candidate = validate_config(editor._candidate())
    assert candidate["ring"][2] == action_id
    assert candidate["actions"][action_id] == {
        "label": "Editor", "steps": [{"type": "application", "desktop_id": "org.example.Editor.desktop"}]}
    assert candidate["actions"]["copy"]["label"] == "Copy selection"
    assert candidate["ring_groups"] == {"5": ["paste"]}
    assert editor.application_notice.get_visible()
    assert editor.store.saved is None
    editor._save()
    iterate_until(lambda: not editor.saving)
    assert editor.store.saved == candidate


def test_application_picker_search_and_selection_are_read_only(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    applications = [
        {"name": "Browser", "desktop_id": "browser.desktop", "description": "Visit the web", "icon": None},
        {"name": "Editor", "desktop_id": "org.editor.desktop", "description": "Edit code", "icon": None},
    ]
    monkeypatch.setattr(module, "installed_applications", lambda: applications)
    selected = []
    picker = ApplicationPicker(selected.append, lambda: None)
    picker.search.set_text("edit CODE")
    picker._search_changed()
    assert not picker.empty.get_visible()
    assert not picker.rows[0].get_child_visible() and picker.rows[1].get_child_visible()
    picker.list.emit("row-activated", picker.rows[1])
    assert selected == [applications[1]]
    assert not editor.dirty and editor.store.saved is None
    picker.search.set_text("unavailable")
    picker._search_changed()
    assert picker.empty.get_visible() and not picker.scroll.get_visible()


def test_launch_template_waits_for_selection_and_cancel_keeps_draft(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    monkeypatch.setattr(module, "installed_applications", lambda: [])
    original = deepcopy(editor.drafts)
    editor.template_choice.set_selected(1)
    editor._add_routine()
    assert editor.action_application_box.get_visible()
    picker = editor.action_application_box.get_first_child()
    picker.search.emit("stop-search")
    assert not editor.action_application_box.get_visible()
    assert editor.drafts == original and not editor.dirty


def test_escape_dismisses_application_picker_before_leaving_editor(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    monkeypatch.setattr(module, "installed_applications", lambda: [])
    closed = []
    monkeypatch.setattr(editor, "close", lambda: closed.append(True))
    editor._open_ring_application_picker()
    assert editor.ring_application_box.get_visible()
    assert editor._key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert not editor.ring_application_box.get_visible() and closed == []
    assert editor._key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert closed == [True]


def test_choosing_application_step_preserves_invalid_command_and_other_steps(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    application = {"name": "Editor", "desktop_id": "editor.desktop", "description": "", "icon": None}
    monkeypatch.setattr(module, "installed_applications", lambda: [application])
    select(editor, "terminal")
    entry_in(editor.steps_group, "Application and arguments").set_text('kitty "incomplete')
    editor._choose_step_application()
    picker = editor.step_application_box.get_first_child()
    picker.list.emit("row-activated", picker.rows[0])
    values = editor.drafts["terminal"]["form_values"]
    assert values[0]["value"] == 'kitty "incomplete'
    assert _values_step(values[1]) == {"type": "application", "desktop_id": "editor.desktop"}
    assert editor.step_error.get_visible()
    assert editor.store.saved is None


def test_stale_application_picker_does_not_edit_another_routine(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    application = {"name": "Editor", "desktop_id": "editor.desktop", "description": "", "icon": None}
    monkeypatch.setattr(module, "installed_applications", lambda: [application])
    select(editor, "terminal")
    editor._choose_step_application(0)
    picker = editor.step_application_box.get_first_child()
    select(editor, "copy")
    before = deepcopy(editor.drafts)
    picker.list.emit("row-activated", picker.rows[0])
    assert editor.drafts == before and not editor.dirty


def test_application_picker_is_cancelled_when_json_replaces_the_step_draft(editor, monkeypatch):
    import mx4_linux.desktop_editor as module
    application = {"name": "Editor", "desktop_id": "editor.desktop", "description": "", "icon": None}
    monkeypatch.setattr(module, "installed_applications", lambda: [application])
    select(editor, "copy")
    editor._choose_step_application()
    picker = editor.step_application_box.get_first_child()
    editor.json_toggle.set_active(True)
    assert not editor.step_application_box.get_visible()
    editor.json_toggle.set_active(False)
    before = deepcopy(editor.drafts)
    picker.list.emit("row-activated", picker.rows[0])
    assert editor.drafts == before and not editor.dirty


@pytest.mark.parametrize("running,busy,online,enabled", [(False, False, True, True),
                                                         (True, True, False, True),
                                                         (False, True, True, False),
                                                         (False, False, False, False)])
def test_integration_retry_remains_available_after_error_and_disable_while_busy(editor, running, busy, online, enabled):
    from types import SimpleNamespace
    editor.integration = SimpleNamespace(
        message="Disconnected; wake the mouse and retry", error="Read failed", starting=False,
        session=SimpleNamespace(running=running, stopping=False),
        window=SimpleNamespace(demo=False, busy=busy, pending_hardware=False, current={"online": online}))
    editor.integration_status = Adw.ActionRow()
    editor.integration_toggle = Gtk.Button()
    editor.update_integration_status()
    assert editor.integration_toggle.get_sensitive() is enabled
    assert editor.integration_toggle.get_label() == ("Disable" if running else "Enable")


def test_new_routine_can_be_assigned_to_a_button_before_first_save(editor):
    editor._add_routine()
    action_id = editor.selected_id
    dict(editor.binding_choices)[83].set_selected(editor.choice_ids.index(action_id) + 2)
    candidate = validate_config(editor._candidate())
    assert candidate["button_bindings"]["83"] == action_id
    assert candidate["actions"][action_id]["steps"] == [{"type": "shortcut", "keys": ["CTRL", "C"]}]


def test_deleting_routine_makes_room_for_application_at_action_limit(editor):
    from mx4_linux.actions import MAX_ACTIONS
    for number in range(MAX_ACTIONS - len(editor.drafts)):
        action_id = f"extra-{number}"
        action = {"label": action_id, "steps": [{"type": "delay", "milliseconds": 0}]}
        editor.config["actions"][action_id] = action
        editor.drafts[action_id] = {"id": action_id, "label": action_id, "app_id": "",
                                    "steps_text": json.dumps(action["steps"])}
    editor._refresh_choices()
    select(editor, "extra-0")
    editor._delete_selected()
    dialog = next(window for window in Gtk.Window.list_toplevels()
                  if isinstance(window, Adw.MessageDialog) and window.get_transient_for() is editor)
    dialog.response("delete")
    editor._add_application({"name": "Editor", "desktop_id": "editor.desktop"}, ring_index=0)
    candidate = validate_config(editor._candidate())
    assert len(candidate["actions"]) == MAX_ACTIONS
    assert candidate["actions"][candidate["ring"][0]]["steps"] == [{"type": "application", "desktop_id": "editor.desktop"}]
