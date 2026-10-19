"""Exercise the editor preview without presenting a desktop overlay."""

from copy import deepcopy

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as exc:
    pytest.skip(str(exc), allow_module_level=True)

from mx4_linux.actions import default_config
from mx4_linux.gtk_support import Adw, Gdk, Gtk
from mx4_linux.ring_icons import action_icon
from mx4_linux.ring_preview import BUTTON_RADIUS, button_center
from mx4_linux.ring_preview import RingPreview


@pytest.fixture
def preview():
    if not Gtk.init_check():
        pytest.skip("A graphical session is needed for native ring preview tests")
    Adw.init()
    selected = []
    widget = RingPreview(default_config(), selected.append)
    widget.allocate(640, 390, -1, None)
    return widget, selected


def test_round_preview_preserves_ring_order_and_shared_geometry(preview):
    widget, selected = preview
    assert len(widget.buttons) == len(widget.labels) == 8
    for index, action_id in enumerate(widget.config["ring"]):
        action = widget.config["actions"][action_id]
        assert widget.labels[index].get_text() == action["label"]
        assert widget.icons[index].get_icon_name() == action_icon(action)
        x, y = button_center(index)
        assert widget.get_child_position(widget.buttons[index]) == pytest.approx((320 + x - BUTTON_RADIUS,
                                                                                 190 + y - BUTTON_RADIUS))
        assert widget.buttons[index].get_tooltip_text().startswith(f"{index + 1}. ")
    assert not selected


def test_preview_selection_is_visual_and_click_only_focuses_assignment(preview):
    widget, selected = preview
    widget.select(2)
    assert not selected
    assert widget.buttons[2].has_css_class("selected")
    widget.buttons[5].emit("clicked")
    assert selected == [5]
    assert widget.selected == 5
    assert widget.buttons[5].has_css_class("selected")
    assert widget.labels[5].has_css_class("selected")
    assert not widget.buttons[2].has_css_class("selected")


def test_preview_refresh_keeps_draft_labels_and_group_icons(preview):
    widget, selected = preview
    config = deepcopy(widget.config)
    action_id = config["ring"][0]
    config["actions"][action_id]["label"] = "My actions"
    config["ring_groups"] = {"0": ["copy", "paste"]}
    widget.select(0)
    widget.update_config(config)
    assert widget.labels[0].get_text() == "My actions"
    assert widget.icons[0].get_icon_name() == action_icon(config["actions"][action_id], grouped=True)
    assert widget.buttons[0].get_tooltip_text() == "1. My actions. Action group"
    assert widget.buttons[0].has_css_class("selected")
    assert not selected

@pytest.mark.parametrize('scale', [75, 100, 150])
def test_preview_fits_620_pixels_and_keeps_saved_scale(preview, scale):
    widget, selected = preview
    config = deepcopy(widget.config) | {'ring_scale': scale}
    widget.update_config(config)
    widget.allocate(620, 390, -1, None)
    assert widget.config['ring_scale'] == scale
    assert widget.measure(Gtk.Orientation.HORIZONTAL, -1).minimum <= 620
    for child in widget.buttons + widget.labels + [widget.center]:
        x, _ = widget.get_child_position(child)
        assert x >= 0
        assert x + child.get_width() <= 620
    assert not selected

def test_narrow_preview_actual_label_allocations_fit_even_long_native_text(preview):
    widget, _ = preview
    widget.allocate(320, 390, -1, None)
    for label in widget.labels:
        x, _ = widget.get_child_position(label)
        assert x >= 0
        assert x + label.get_width() <= 320


def test_preview_keyboard_selects_assignments_and_preserves_modified_shortcuts(preview):
    widget, selected = preview
    assert widget.key_pressed(None, Gdk.KEY_Left, 0, 0)
    assert widget.selected == 0 and not selected
    assert widget.key_pressed(None, Gdk.KEY_Left, 0, 0)
    assert widget.selected == 7
    assert widget.key_pressed(None, Gdk.KEY_3, 0, 0)
    assert widget.selected == 2 and not selected
    assert widget.key_pressed(None, Gdk.KEY_Home, 0, 0)
    assert widget.selected == 0
    assert widget.key_pressed(None, Gdk.KEY_End, 0, 0)
    assert widget.selected == 7
    assert not widget.key_pressed(None, Gdk.KEY_1, 0, Gdk.ModifierType.CONTROL_MASK)
    assert widget.selected == 7
    assert Gtk.test_accessible_has_state(widget.buttons[7], Gtk.AccessibleState.PRESSED)


@pytest.mark.parametrize("activate_key", [Gdk.KEY_Return, Gdk.KEY_space])
def test_editor_preview_arrows_keep_focus_until_native_activation(preview, activate_key):
    from types import SimpleNamespace
    from mx4_linux.desktop_editor import DesktopEditor

    editor = DesktopEditor(None, SimpleNamespace(read=default_config), lambda: None)
    widget = editor.ring_preview
    editor.allocate(900, 760, -1, None)
    try:
        widget.buttons[0].grab_focus()
        assert editor.get_focus() is widget.buttons[0]
        for key, index in [(Gdk.KEY_Right, 1), (Gdk.KEY_Right, 2), (Gdk.KEY_8, 7),
                           (Gdk.KEY_Home, 0), (Gdk.KEY_End, 7), (Gdk.KEY_Left, 6)]:
            assert widget.key_pressed(None, key, 0, 0)
            assert widget.selected == index
            assert editor.get_focus() is widget.buttons[index]
        assert not widget.key_pressed(None, activate_key, 0, 0)
        # GTK's focused native button activates after this unhandled key.
        widget.buttons[6].emit("clicked")
        focus = editor.get_focus()
        assert focus is editor.ring_choices[6] or focus.is_ancestor(editor.ring_choices[6])
        assert not editor.dirty
    finally:
        editor.allow_close = True
        editor.destroy()


def test_preview_motion_is_finite_and_unmap_disconnects_settings(preview, monkeypatch):
    from mx4_linux import ring_preview
    widget, _ = preview
    original_motion = widget.settings.get_property("gtk-enable-animations")
    removed = []
    monkeypatch.setattr(widget, "get_mapped", lambda: True)
    monkeypatch.setattr(widget, "add_tick_callback", lambda _callback: 42)
    monkeypatch.setattr(widget, "remove_tick_callback", removed.append)
    try:
        widget.settings.set_property("gtk-enable-animations", True)
        widget.mapped()
        assert widget.tick_id == 42 and widget._settings_handler
        widget.select(2)
        deadline = max(t.started + t.duration for t in (widget.reveal, widget.selection_reveal)) + .01
        monkeypatch.setattr(ring_preview, "monotonic", lambda: deadline)
        assert widget.tick(None, None) is False
        assert widget.tick_id == 0
        assert all(button.get_opacity() == 1 for button in widget.buttons)
        widget.select(3)
        assert widget.tick_id == 42
        widget.settings.set_property("gtk-enable-animations", False)
        assert widget.tick_id == 0 and removed == [42]
        assert widget.has_css_class("reduced-motion")
        assert not widget.selection_reveal.active()
        assert all(label.get_opacity() == 1 for label in widget.labels)
        widget.unmapped()
        assert widget._settings_handler == 0
    finally:
        widget.unmapped()
        widget.settings.set_property("gtk-enable-animations", original_motion)
