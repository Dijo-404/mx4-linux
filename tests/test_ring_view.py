"""Exercise native ring previews without presenting or running desktop actions."""

from time import monotonic
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as exc:
    pytest.skip(str(exc), allow_module_level=True)

from mx4_linux import ring
from mx4_linux.actions import default_config
from mx4_linux.gtk_support import Adw, Gdk, Gtk
from mx4_linux.ring_model import BUTTON_RADIUS, button_center


@pytest.fixture
def preview(monkeypatch):
    if not Gtk.init_check():
        pytest.skip("A graphical session is needed for native ring interaction tests")
    Adw.init()
    monkeypatch.setattr(ring, "Layer", None)
    selected, dismissed = [], []
    window = ring.ActionsRing(None, lambda *args: selected.append(args), lambda: dismissed.append(True))
    # Allocate native widgets for hit testing without taking desktop focus.
    monkeypatch.setattr(window, "present", lambda: None)
    settings = Gtk.Settings.get_default()
    original_motion = settings.get_property("gtk-enable-animations")
    settings.set_property("gtk-enable-animations", True)
    window.show_ring(default_config(), preview=True)
    window.allocate(ring.WIDTH, ring.HEIGHT, -1, None)
    yield SimpleNamespace(window=window, selected=selected, dismissed=dismissed, settings=settings)
    settings.set_property("gtk-enable-animations", original_motion)
    window.hide_ring()
    window.destroy()


def controllers(widget, kind):
    model = widget.observe_controllers()
    return [model.get_item(index) for index in range(model.get_n_items()) if isinstance(model.get_item(index), kind)]


def main_position(window, index):
    dx, dy = button_center(index)
    return ring.CENTER_X + dx * window.spread, ring.CENTER_Y + dy * window.spread


def settle(window):
    for transition in (window.reveal, window.group_reveal, window.label_reveal):
        transition.move(transition.target, animate=False)
    window.apply_frame(monotonic())


def show_group(preview, *, held=False):
    config = default_config()
    config["ring_groups"] = {"2": ["copy", "paste"]}
    preview.window.show_ring(config, held=held, preview=True)
    settle(preview.window)
    return preview.window


def test_pointer_capture_selects_only_the_painted_circular_button(preview):
    window = preview.window
    gestures = controllers(window.surface, Gtk.GestureClick)
    assert len(gestures) == 1
    assert gestures[0].get_propagation_phase() == Gtk.PropagationPhase.CAPTURE
    window.pointer_click(None, 1, *main_position(window, 0))
    window.buttons[1].emit("clicked")  # A stale native activation must not run another action.
    assert preview.selected == [("overview", True)]
    assert not preview.dismissed


def test_corner_of_native_button_allocation_cannot_run_an_action(preview):
    window = preview.window
    x, y = main_position(window, 0)
    window.pointer_click(None, 1, x + BUTTON_RADIUS, y + BUTTON_RADIUS)
    assert preview.dismissed == [True]
    assert not preview.selected


def test_native_faces_have_the_same_diameter_as_pointer_hit_geometry(preview):
    for button in preview.window.buttons:
        assert button.get_allocated_width() == BUTTON_RADIUS * 2
        assert button.get_allocated_height() == BUTTON_RADIUS * 2
    assert preview.window.close_button.get_allocated_width() == ring.CENTER_RADIUS * 2
    assert preview.window.close_button.get_allocated_height() == ring.CENTER_RADIUS * 2


def test_group_heading_and_footer_do_not_cover_the_six_child_targets(preview):
    window = preview.window
    config = default_config()
    config["ring_groups"] = {"2": list(config["actions"])[:6]}
    config["actions"][config["ring"][2]]["label"] = "An action group with a longer name"
    window.show_ring(config, preview=True)
    settle(window)
    window.pointer_motion(None, *main_position(window, 2))
    settle(window)
    window.update_labels()
    window.allocate(ring.WIDTH, ring.HEIGHT, -1, None)
    heading = window.callout.get_allocation()
    footer = window.instructions.get_allocation()
    assert heading.y + heading.height < min(y - ring.CHILD_RADIUS for _x, y in window.child_positions)
    assert footer.y > max(y + ring.CHILD_RADIUS for _x, y in window.child_positions)
    assert footer.y + footer.height <= ring.HEIGHT


def test_native_action_button_runs_once_in_preview(preview):
    window = preview.window
    window.buttons[3].emit("clicked")
    window.buttons[3].emit("clicked")
    assert preview.selected == [("workspace-next", True)]
    assert not window.state.visible


@pytest.mark.parametrize("coordinate", [(ring.CENTER_X, ring.CENTER_Y), (0, 0)])
def test_center_and_outside_clicks_dismiss_without_selecting(preview, coordinate):
    preview.window.pointer_click(None, 1, *coordinate)
    assert preview.dismissed == [True]
    assert not preview.selected


def test_arrows_keep_native_focus_with_highlight_and_enter_uses_it(preview):
    window = preview.window
    keys = controllers(window, Gtk.EventControllerKey)
    assert keys
    assert all(controller.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE for controller in keys)
    window.buttons[2].grab_focus()
    assert window.state.selected == 2
    assert window.key_pressed(None, Gdk.KEY_Right, 0, 0)
    assert window.state.selected == 3
    assert window.get_focus() == window.buttons[3]
    assert window.buttons[3].has_css_class("selected")
    assert not window.buttons[2].has_css_class("selected")
    assert window.callout_label.get_text() == "Next workspace"
    window.key_pressed(None, Gdk.KEY_Return, 0, 0)
    assert preview.selected == [("workspace-next", True)]


def test_hover_opens_group_and_keeps_parent_action_inert(preview):
    window = show_group(preview)
    window.pointer_motion(None, *main_position(window, 2))
    assert window.group_slot == 2
    assert window.children == ["copy", "paste"]
    assert len(window.child_positions) == 2
    assert not window.child_buttons[2].get_visible()
    assert "group action" in window.instructions.get_text()
    assert not preview.selected
    settle(window)
    window.pointer_motion(None, *window.child_positions[1])
    assert window.child_selected == 1
    assert window.callout_label.get_text() == "Paste"
    assert not preview.selected


def test_clicking_group_parent_during_entry_cannot_run_collapsed_child(preview):
    window = show_group(preview)
    parent = main_position(window, 2)
    window.pointer_motion(None, *parent)
    window.group_reveal.move(0, animate=False)
    window.apply_frame(monotonic())
    window.pointer_click(None, 1, *parent)
    assert window.state.visible and window.group_slot == 2
    assert window.child_selected == 0
    assert not preview.selected
    window.group_reveal.move(1, animate=False)
    window.apply_frame(monotonic())
    window.pointer_click(None, 1, *window.child_positions[1])
    assert preview.selected == [("paste", True)]


@pytest.mark.parametrize("selection", ["click", "number", "enter", "native"])
def test_group_child_selection_runs_exactly_once(preview, selection):
    window = show_group(preview)
    window.commit(2)
    assert window.get_focus() == window.child_buttons[0]
    assert window.child_selected == 0
    assert not preview.selected
    settle(window)
    if selection == "click":
        window.pointer_click(None, 1, *window.child_positions[1])
    elif selection == "number":
        window.key_pressed(None, Gdk.KEY_2, 0, 0)
    elif selection == "enter":
        window.key_pressed(None, Gdk.KEY_Down, 0, 0)
        assert window.get_focus() == window.child_buttons[1]
        window.key_pressed(None, Gdk.KEY_Return, 0, 0)
    else:
        window.child_buttons[1].emit("clicked")
    window.child_buttons[1].emit("clicked")
    assert preview.selected == [("paste", True)]
    assert not window.state.visible


def test_held_group_parent_release_latches_without_running_an_action(preview):
    window = show_group(preview, held=True)
    window.motion(100, 0)
    assert window.state.selected == 2
    window.release_button()
    assert window.state.visible and not window.state.held
    assert window.group_slot == 2 and window.child_selected == 0
    assert not preview.selected
    window.key_pressed(None, Gdk.KEY_Return, 0, 0)
    assert preview.selected == [("copy", True)]


@pytest.mark.parametrize("back_key", [Gdk.KEY_Escape, Gdk.KEY_BackSpace])
def test_escape_returns_to_group_parent_then_closes(preview, back_key):
    window = show_group(preview)
    window.commit(2)
    window.key_pressed(None, back_key, 0, 0)
    assert window.state.visible and window.group_slot is None
    assert window.state.selected == 2
    assert window.get_focus() == window.buttons[2]
    assert not window.child_positions
    assert not any(button.get_visible() for button in window.child_buttons)
    window.key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert preview.dismissed == [True]
    assert not preview.selected


def test_escape_and_cleanup_never_commit_an_action(preview):
    window = show_group(preview, held=True)
    window.motion(100, 0)
    window.key_pressed(None, Gdk.KEY_Escape, 0, 0)
    window.release_button()
    window.hide_ring()
    assert preview.dismissed == [True]
    assert not preview.selected
    assert window.group_slot is None
    assert window.tick_id == 0


@pytest.mark.parametrize("key", [Gdk.KEY_Return, Gdk.KEY_space])
def test_keyboard_activation_of_center_close_never_runs_selected_action(preview, key):
    window = preview.window
    window.buttons[3].grab_focus()
    window.close_button.grab_focus()
    assert window.get_focus() == window.close_button
    handled = window.key_pressed(None, key, 0, 0)
    if not handled:  # GTK activates the focused native button after capture.
        window.close_button.emit("clicked")
    assert preview.dismissed == [True]
    assert not preview.selected


def test_reduced_motion_snaps_all_transitions_and_reopen_leaves_no_tick(preview):
    window = show_group(preview)
    window.commit(2)
    assert window.tick_id
    preview.settings.set_property("gtk-enable-animations", False)
    assert window.tick_id == 0
    assert window.surface.get_opacity() == 1
    assert window.spread == 1
    for transition in (window.reveal, window.group_reveal, window.label_reveal):
        assert not transition.active()
        assert transition.value() == transition.target
    assert all(window.child_buttons[index].get_opacity() == 1 for index in range(2))
    window.show_ring(default_config(), preview=True)
    assert window.tick_id == 0
    assert window.surface.get_opacity() == 1
    assert window.group_slot is None
    assert not window.child_positions


def test_group_accessibility_does_not_leave_expandable_state_on_regular_actions(preview):
    window = show_group(preview)
    assert Gtk.test_accessible_has_state(window.buttons[2], Gtk.AccessibleState.EXPANDED)
    assert not Gtk.test_accessible_has_state(window.buttons[0], Gtk.AccessibleState.EXPANDED)
    window.commit(2)
    window.show_ring(default_config(), preview=True)
    assert not Gtk.test_accessible_has_state(window.buttons[2], Gtk.AccessibleState.EXPANDED)


@pytest.mark.parametrize("coordinates", [(float("nan"), 0), (0, float("inf"))])
def test_nonfinite_child_pointer_coordinates_never_select(preview, coordinates):
    window = show_group(preview)
    window.commit(2)
    settle(window)
    assert window.child_at(*coordinates) is None
    assert not preview.selected


def test_finite_group_animation_stops_after_all_transitions_finish(preview, monkeypatch):
    window = show_group(preview)
    window.commit(2)
    assert window.tick_id
    deadline = max(t.started + t.duration for t in (window.reveal, window.group_reveal, window.label_reveal)) + .01
    monkeypatch.setattr(ring, "monotonic", lambda: deadline)
    window.stop_animation()  # The unmapped fixture has no frame clock to invoke the registered callback.
    assert window.tick(None, None) is False
    assert window.tick_id == 0
    assert window.group_reveal.value(deadline) == 1
    assert window.child_positions[0] != window.child_positions[1]

@pytest.mark.parametrize('scale', [75, 100, 150])
def test_scaled_faces_hit_geometry_and_motion_share_logical_space(preview, scale):
    window = preview.window
    config = default_config() | {'ring_scale': scale, 'ring_motion_gain': 1, 'ring_deadzone': 50}
    window.show_ring(config, preview=True)
    settle(window)
    window.allocate(round(ring.WIDTH * scale / 100), round(ring.HEIGHT * scale / 100), -1, None)
    for button in window.buttons:
        assert button.get_allocated_width() == round(52 * scale / 100)
    x, y = window.geometry.point(ring.CENTER_X, ring.CENTER_Y - 116)
    window.pointer_motion(None, x, y)
    assert window.state.selected == 0
    window.pointer_click(None, 1, x, y)
    assert preview.selected == [('overview', True)]


def adjustment_config():
    config = default_config()
    config['actions']['copy']['adjustment'] = {
        'increase': [{'type': 'text', 'text': '+'}], 'decrease': [{'type': 'text', 'text': '-'}]}
    config['ring'][0] = 'copy'
    return config


def test_scroll_uses_fresh_circle_hit_and_keeps_ring_open(preview):
    window = preview.window
    adjusted = []
    window.on_adjusted = lambda *args: adjusted.append(args) or True
    window.show_ring(adjustment_config(), preview=False)
    settle(window)
    x, y = main_position(window, 0)
    assert window.adjust_at(x, y, 0, -1)
    assert adjusted == [('copy', 'increase', False)]
    assert window.state.visible and not preview.selected
    assert not window.adjust_at(x + 26, y + 26, 0, 1)
    assert not window.adjust_at(0, 0, 0, 1)
    assert not window.adjust_at(x, y, 0, float('nan'))
    assert not window.adjust_at(x, y, 0, 0)
    assert 'Scroll to adjust' in window.buttons[0].get_tooltip_text()


def test_scroll_child_requires_revealed_nonoverlap_and_preview_is_inert(preview):
    window = preview.window
    adjusted = []
    window.on_adjusted = lambda *args: adjusted.append(args) or True
    config = adjustment_config()
    config['ring_groups'] = {'2': ['copy', 'paste']}
    window.show_ring(config, preview=False)
    settle(window)
    window.commit(2)
    window.group_reveal.move(0, animate=False)
    window.apply_frame(monotonic())
    assert not window.adjust_at(*window.child_positions[0], 0, -1)
    window.group_reveal.move(1, animate=False)
    settle(window)
    assert window.adjust_at(*window.child_positions[0], 1, 0)
    assert adjusted == [('copy', 'increase', False)]
    window.show_ring(config, preview=True)
    settle(window)
    assert not window.adjust_at(*main_position(window, 0), 0, -1)
    assert len(adjusted) == 1


def test_adjusted_hold_release_latches_and_next_open_restores_normal_release(preview):
    window = preview.window
    window.on_adjusted = lambda *_: True
    window.show_ring(adjustment_config(), held=True, preview=False)
    settle(window)
    window.motion(0, -100)
    assert window.adjust_at(*main_position(window, 0), 0, -1)
    window.release_button()
    assert window.state.visible and not window.state.held
    assert not preview.selected
    window.show_ring(adjustment_config(), held=True, preview=False)
    window.motion(0, -100)
    window.release_button()
    assert preview.selected == [('copy', False)]

@pytest.mark.parametrize('scale', [75, 150])
def test_scaled_group_heading_footer_and_faces_fit_allocation(preview, scale):
    window = preview.window
    config = default_config() | {'ring_scale': scale, 'ring_groups': {'2': list(default_config()['actions'])[:6]}}
    window.show_ring(config, preview=True)
    settle(window)
    window.commit(2)
    settle(window)
    window.update_labels()
    width, height = window.geometry.size(ring.WIDTH), window.geometry.size(ring.HEIGHT)
    window.allocate(width, height, -1, None)
    for child in [window.callout, window.instructions, window.close_button] + window.child_buttons:
        allocation = child.get_allocation()
        assert allocation.x >= 0 and allocation.y >= 0
        assert allocation.x + allocation.width <= width
        assert allocation.y + allocation.height <= height

def test_emerging_child_face_cannot_scroll_an_underlying_root(preview):
    window = preview.window
    config = adjustment_config()
    config['ring'][2] = 'copy'
    config['ring_groups'] = {'0': ['paste']}
    window.on_adjusted = lambda *_: pytest.fail('Overlapping painted child must remain inert')
    window.show_ring(config, preview=False)
    settle(window)
    window.commit(0)
    position = main_position(window, 2)
    window.child_positions = [position]
    assert window.child_at(*position) is None
    assert not window.adjust_at(*position, 0, -1)


def test_fallback_resume_does_not_request_another_gtk_activation(preview, monkeypatch):
    from unittest.mock import Mock
    window = preview.window
    present = Mock()
    monkeypatch.setattr(window, 'present', present)
    window.yield_keyboard()
    window.resume_keyboard()
    present.assert_not_called()  # Controller already restored the exact popup through Niri.
    assert not window._keyboard_yielded
