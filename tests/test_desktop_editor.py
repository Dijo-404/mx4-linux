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

from gi.repository import Adw, GLib, Gtk

from mx4_linux.actions import default_config, validate_config
from mx4_linux.desktop_editor import DesktopEditor, _DraftError, _step_values, _values_step


@pytest.mark.parametrize("step", [
    {"type": "shortcut", "keys": ["CTRL", "SHIFT", "S"]},
    {"type": "command", "argv": ["kitty", "--title", "Hello $(touch nope)", ""]},
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


def test_clearing_group_entry_compacts_the_visible_order_to_match_shortcuts(editor):
    _row, choices = editor.group_choices[2]
    choices[0].set_selected(editor.choice_ids.index("copy") + 1)
    choices[1].set_selected(editor.choice_ids.index("paste") + 1)
    choices[0].set_selected(0)
    assert editor.config["ring_groups"]["2"] == ["paste"]
    assert choices[0].get_selected() == editor.choice_ids.index("paste") + 1
    assert all(choice.get_selected() == 0 for choice in choices[1:])
