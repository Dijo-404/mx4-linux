"""Exercise native ring previews without presenting or running desktop actions."""

from time import monotonic
from types import SimpleNamespace
import math

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
from mx4_linux.ring_model import BUTTON_RADIUS, ORBIT_RADIUS, button_center


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
    window.mapped()  # Simulate map without presenting a desktop overlay.
    yield SimpleNamespace(window=window, selected=selected, dismissed=dismissed, settings=settings)
    settings.set_property("gtk-enable-animations", original_motion)
    window.hide_ring()
    window.unmapped()
    window.destroy()


def controllers(widget, kind):
    model = widget.observe_controllers()
    return [model.get_item(index) for index in range(model.get_n_items()) if isinstance(model.get_item(index), kind)]


def main_position(window, index):
    dx, dy = button_center(index)
    return ring.CENTER_X + dx * window.spread, ring.CENTER_Y + dy * window.spread


def placed(window, widget):
    """Allocated logical center; Gtk.Fixed.get_child_position reports 0, 0 here."""
    window.allocate(window.geometry.size(ring.WIDTH), window.geometry.size(ring.HEIGHT), -1, None)
    ok, bounds = widget.compute_bounds(window.labels)
    assert ok
    return window.geometry.logical(bounds.get_x() + bounds.get_width() / 2,
                                   bounds.get_y() + bounds.get_height() / 2)


def settle(window):
    for transition in window.transitions():
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


def test_open_group_fans_children_on_an_outer_arc_and_leaves_them_unlabeled(preview):
    window = preview.window
    config = default_config()
    config["ring_groups"] = {"2": list(config["actions"])[:6]}
    window.show_ring(config, preview=True)
    settle(window)
    window.pointer_motion(None, *main_position(window, 2))
    settle(window)
    assert window.callout.get_opacity() == 0
    angles = [math.atan2(x - ring.CENTER_X, ring.CENTER_Y - y) for x, y in window.child_positions]
    for (x, y), angle in zip(window.child_positions, angles):
        assert math.hypot(x - ring.CENTER_X, y - ring.CENTER_Y) == pytest.approx(ring.CHILD_ORBIT)
    assert sum(angles) / len(angles) == pytest.approx(math.pi / 2)  # Centered on the parent at three o'clock.
    assert all(b - a == pytest.approx(ring.CHILD_SPACING) for a, b in zip(angles, angles[1:]))


def test_ring_has_no_bottom_instruction_bar(preview):
    window = preview.window
    assert not hasattr(window, "instructions")
    assert window.key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert preview.dismissed == [True]


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
    assert window.buttons[2].get_tooltip_text().endswith("Open action group")
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
    # Closing children shrink away in place but can no longer be hit.
    assert not any(button.get_visible() and button.get_can_target() for button in window.child_buttons)
    settle(window)
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
    for transition in window.transitions():
        assert not transition.active()
        assert transition.value() == transition.target
    assert all(window.child_buttons[index].get_opacity() == 1 for index in range(2))
    window.show_ring(default_config(), preview=True)
    assert window.tick_id == 0
    assert window.surface.get_opacity() == 1
    assert window.group_slot is None
    assert not window.child_positions


def test_unshown_popup_has_no_global_settings_handler_and_unmap_releases_it(preview):
    window = ring.ActionsRing(None, lambda *_: None, lambda: None)
    settings = preview.settings
    try:
        assert window._settings_handler == 0
        window.mapped()
        handler = window._settings_handler
        assert handler and settings.handler_is_connected(handler)
        window.mapped()
        assert window._settings_handler == handler
        window.unmapped()
        assert window._settings_handler == 0
        assert not settings.handler_is_connected(handler)
    finally:
        window.unmapped()
        window.destroy()


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
    deadline = max(t.started + t.duration for t in window.transitions()) + .01
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
        assert button.get_allocated_width() == round(BUTTON_RADIUS * 2 * scale / 100)
    x, y = window.geometry.point(ring.CENTER_X, ring.CENTER_Y - ORBIT_RADIUS)
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
def test_scaled_group_heading_and_faces_fit_allocation(preview, scale):
    window = preview.window
    config = default_config() | {'ring_scale': scale, 'ring_groups': {'2': list(default_config()['actions'])[:6]}}
    window.show_ring(config, preview=True)
    settle(window)
    window.commit(2)
    settle(window)
    window.update_labels()
    width, height = window.geometry.size(ring.WIDTH), window.geometry.size(ring.HEIGHT)
    window.allocate(width, height, -1, None)
    for child in [window.callout, window.close_button] + window.child_buttons:
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


@pytest.mark.parametrize('scale', [75, 100, 150])
def test_primary_horizontal_drag_pins_root_suppresses_release_and_scales(preview, scale):
    window = preview.window
    adjusted = []
    window.on_adjusted = lambda *args: adjusted.append(args) or True
    window.show_ring(adjustment_config() | {'ring_scale': scale}, preview=False)
    settle(window)
    x, y = window.geometry.point(*main_position(window, 0))
    window.pointer_press(None, 1, x, y)
    window.pointer_drag(None, 22 * scale / 100, 2 * scale / 100)
    window.pointer_motion(None, *window.geometry.point(*main_position(window, 2)))
    window.pointer_drag(None, 10 * scale / 100, 0)
    window.pointer_click(None, 1, x + 10 * scale / 100, y)
    assert adjusted == [('copy', 'increase', False), ('copy', 'increase', False), ('copy', 'decrease', False)]
    assert window.state.visible and not preview.selected


def test_below_threshold_primary_motion_retains_one_click(preview):
    window = preview.window
    window.show_ring(adjustment_config(), preview=False)
    settle(window)
    window.on_adjusted = lambda *_: pytest.fail('Below threshold is an ordinary click')
    x, y = main_position(window, 0)
    window.pointer_press(None, 1, x, y)
    window.pointer_drag(None, 9, 2)
    window.pointer_click(None, 1, x + 9, y + 2)
    window.buttons[0].emit('clicked')
    assert preview.selected == [('copy', False)]


def test_failed_drag_and_reversal_never_run_main_and_reopen_resets(preview):
    window = preview.window
    window.show_ring(adjustment_config(), preview=False)
    settle(window)
    window.on_adjusted = lambda *_: False
    x, y = main_position(window, 0)
    window.pointer_press(None, 1, x, y)
    window.pointer_drag(None, 30, 0)
    window.pointer_drag(None, 0, 0)
    window.pointer_click(None, 1, x, y)
    assert not preview.selected and window.state.visible
    window.hide_ring()
    window.show_ring(adjustment_config(), preview=False)
    settle(window)
    window.pointer_click(None, 1, *main_position(window, 0))
    assert preview.selected == [('copy', False)]


def test_child_drag_keeps_group_and_initial_action_across_hover_and_leave(preview):
    window = preview.window
    config = adjustment_config() | {'ring_groups': {'2': ['copy', 'paste']}}
    window.show_ring(config, preview=False)
    settle(window)
    window.commit(2)
    settle(window)
    adjusted = []
    window.on_adjusted = lambda *args: adjusted.append(args) or True
    x, y = window.child_positions[0]
    window.pointer_press(None, 1, x, y)
    window.pointer_drag(None, -22, 0)
    window.pointer_motion(None, *main_position(window, 6))
    window.pointer_leave(None)
    assert window.group_slot == 2
    window.pointer_click(None, 1, x - 22, y)
    assert adjusted == [('copy', 'decrease', False), ('copy', 'decrease', False)]
    assert not preview.selected and window.state.visible


def test_drag_preview_gap_group_head_emerging_and_nonfinite_are_inert(preview):
    window = preview.window
    config = adjustment_config() | {'ring_groups': {'2': ['copy', 'paste']}}
    window.on_adjusted = lambda *_: pytest.fail('Rejected drag cannot execute')
    for is_preview, where in [(True, 0), (False, 2), (False, None)]:
        window.show_ring(config, preview=is_preview)
        settle(window)
        x, y = main_position(window, where) if where is not None else (0, 0)
        window.pointer_press(None, 1, x, y)
        window.pointer_drag(None, 50, 0)
        window.cancel_pointer()
    window.show_ring(config, preview=False)
    settle(window)
    window.commit(2)
    window.group_reveal.move(0, animate=False)
    window.apply_frame(monotonic())
    window.pointer_press(None, 1, *window.child_positions[0])
    window.pointer_drag(None, 50, 0)
    window.cancel_pointer()
    window.pointer_press(None, 1, float('nan'), 0)
    window.pointer_drag(None, float('inf'), 0)
    assert not preview.selected


@pytest.mark.parametrize('start,end', [(None, 0), (1, 0), (0, None)])
def test_captured_click_needs_the_same_initial_painted_face(preview, start, end):
    window = preview.window
    settle(window)
    a = main_position(window, start) if start is not None else (0, 0)
    b = main_position(window, end) if end is not None else (0, 0)
    window.pointer_press(None, 1, *a)
    window.pointer_click(None, 1, *b)
    assert not preview.selected and window.state.visible


def test_cancelled_drag_and_late_native_release_are_inert(preview):
    window = preview.window
    window.on_adjusted = lambda *_: True
    window.show_ring(adjustment_config(), preview=False)
    settle(window)
    x, y = main_position(window, 0)
    window.pointer_press(None, 1, x, y)
    window.pointer_drag(None, 22, 0)
    window.cancel_pointer()
    window.pointer_click(object(), 1, x, y)
    window.buttons[0].emit('clicked')
    assert not preview.selected and window.state.visible


def test_switching_group_cancels_captured_child_before_more_adjustment(preview):
    window = preview.window
    config = adjustment_config() | {'ring_groups': {'2': ['copy'], '3': ['copy']}}
    window.show_ring(config, preview=False)
    settle(window)
    window.commit(2)
    settle(window)
    adjusted = []
    window.on_adjusted = lambda *args: adjusted.append(args) or True
    window.pointer_press(None, 1, *window.child_positions[0])
    window.pointer_drag(None, 10, 0)
    window.open_group(3)
    window.pointer_drag(None, 22, 0)
    assert adjusted == [('copy', 'increase', False)]


@pytest.mark.parametrize("elapsed", [0, .03, .10, .22])
def test_opening_targets_are_visible_and_stay_at_their_final_positions(preview, elapsed):
    window = preview.window
    positions = [placed(window, button) for button in window.buttons]
    window.apply_frame(window.reveal.started + elapsed)
    assert window.surface.get_opacity() == 1
    for index, button in enumerate(window.buttons):
        dx, dy = button_center(index)
        x, y = ring.CENTER_X + dx, ring.CENTER_Y + dy
        assert window.pointer_target(x, y) == ("root", index)
        assert .72 <= button.get_opacity() <= 1
        assert placed(window, button) == pytest.approx(positions[index])
        assert placed(window, button) == pytest.approx((x, y), abs=1)  # Allocations are whole pixels.
    window.pointer_click(None, 1, ring.CENTER_X, ring.CENTER_Y - ORBIT_RADIUS)
    assert preview.selected == [("overview", True)]


def test_center_dot_is_red_only_while_nothing_would_be_chosen(preview):
    window = preview.window
    assert window.close_button.has_css_class("cancel")
    window.pointer_motion(None, *main_position(window, 0))
    assert not window.close_button.has_css_class("cancel")
    assert window.buttons[0].has_css_class("selected")
    window.pointer_motion(None, ring.CENTER_X, ring.CENTER_Y)
    assert window.close_button.has_css_class("cancel")
    window.key_pressed(None, Gdk.KEY_Left, 0, 0)
    assert window.state.selected is not None and not window.close_button.has_css_class("cancel")


def test_captured_press_feedback_clears_on_cancel_and_never_moves_targets(preview):
    window = preview.window
    settle(window)
    position = main_position(window, 1)
    before = placed(window, window.buttons[1])
    window.pointer_press(None, 1, *position)
    assert window.buttons[1].has_css_class("pressed")
    window.apply_frame(monotonic())
    assert placed(window, window.buttons[1]) == pytest.approx(before)
    assert placed(window, window.buttons[1]) == pytest.approx(position, abs=1)
    assert window.pointer_target(*position) == ("root", 1)
    window.cancel_pointer()
    assert not window.buttons[1].has_css_class("pressed")
    assert not preview.selected


def test_held_motion_tracks_selection_and_hiding_stops_frames(preview):
    window = preview.window
    window.show_ring(default_config(), held=True, preview=True)
    window.motion(100, 0)
    assert window.state.selected == 2 and window.buttons[2].has_css_class("selected")
    assert window.state.x > 0
    window.hide_ring()
    assert not window.state.visible
    assert not window.tick_id


def test_child_selection_keeps_its_group_parent_filled(preview):
    window = show_group(preview)
    window.commit(2)
    settle(window)
    window.focus_child(1)
    assert window.state.selected == 2
    assert window.buttons[2].has_css_class("selected") and window.child_buttons[1].has_css_class("selected")
    window.key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert window.state.selected == 2 and window.buttons[2].has_css_class("selected")


def test_opening_faces_burst_from_the_center_while_hit_regions_stay_final(preview):
    window = preview.window
    start = window.reveal.started
    window.apply_frame(start)
    for index, button in enumerate(window.buttons):
        dx, dy = button_center(index)
        assert window.labels.poses[button].shift == pytest.approx((-dx, -dy))
        assert window.pointer_target(ring.CENTER_X + dx, ring.CENTER_Y + dy) == ("root", index)
    window.apply_frame(start + ring.OPEN_DURATION)
    assert all(window.labels.poses[button].shift == pytest.approx((0, 0)) for button in window.buttons)


def test_choosing_flashes_the_face_collapses_then_hides_after_running(preview, monkeypatch):
    window = preview.window
    settle(window)
    window.pointer_click(None, 1, *main_position(window, 3))
    # The action is handed over at once; only the paint lingers, without input.
    assert preview.selected == [("workspace-next", True)]
    assert not window.state.visible and window._exiting
    assert window.buttons[3].has_css_class("chosen")
    start = window.exit.started
    window.apply_frame(start + ring.EXIT_DURATION / 2)
    dx, dy = button_center(3)
    shift = window.labels.poses[window.buttons[3]].shift
    assert 0 < shift[0] / -dx < 1 and 0 < shift[1] / -dy < 1
    assert not window.pointer_target(*main_position(window, 3))
    monkeypatch.setattr(ring, "monotonic", lambda: start + ring.EXIT_DURATION + .01)
    window.stop_animation()
    assert window.tick(None, None) is False
    assert not window._exiting and not window.get_visible()
    assert not window.buttons[3].has_css_class("chosen")


def test_reduced_motion_dismisses_without_a_collapse(preview):
    preview.settings.set_property("gtk-enable-animations", False)
    window = preview.window
    window.show_ring(default_config(), preview=True)
    window.dismiss()
    assert preview.dismissed == [True]
    assert not window._exiting and not window.tick_id


def test_labels_wait_before_fading_in_on_the_outward_side(preview):
    window = preview.window
    window.pointer_motion(None, *main_position(window, 2))
    assert window.callout_label.get_text() == window.config["actions"][window.config["ring"][2]]["label"]
    started = window.label_reveal.started
    assert started > monotonic() + ring.LABEL_DELAY / 2
    window.apply_frame(started - .01)
    assert window.callout.get_opacity() == 0
    window.apply_frame(started + ring.LABEL_FADE)
    assert window.callout.get_opacity() == 1
    window.allocate(ring.WIDTH, ring.HEIGHT, -1, None)
    ok, bounds = window.callout.compute_bounds(window.labels)
    face_x, face_y = main_position(window, 2)
    assert ok and bounds.get_x() >= face_x + BUTTON_RADIUS
    assert bounds.get_y() < face_y < bounds.get_y() + bounds.get_height()


def test_tick_paints_every_display_frame_and_stops_once_settled(preview, monkeypatch):
    window = preview.window
    start = window.reveal.started
    painted = []
    monkeypatch.setattr(window, "apply_frame", lambda now: painted.append(now))
    window.stop_animation()
    for elapsed in (0, .005, .010, .017):
        monkeypatch.setattr(ring, "monotonic", lambda elapsed=elapsed: start + elapsed)
        assert window.tick(None, None)
    assert painted == [start, start + .005, start + .010, start + .017]
    monkeypatch.setattr(ring, "monotonic", lambda: start + 5)
    assert not window.tick(None, None)
    assert painted[-1] == start + 5


@pytest.mark.parametrize("index", [6, 7])
def test_left_side_labels_end_before_their_face(preview, index):
    window = preview.window
    window.pointer_motion(None, *main_position(window, index))
    window.allocate(ring.WIDTH, ring.HEIGHT, -1, None)
    ok, bounds = window.callout.compute_bounds(window.labels)
    face_x, face_y = main_position(window, index)
    assert ok and bounds.get_width() > 0
    assert bounds.get_x() + bounds.get_width() <= face_x - BUTTON_RADIUS
    assert bounds.get_y() < face_y < bounds.get_y() + bounds.get_height()
