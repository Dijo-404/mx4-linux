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
from mx4_linux.gtk_support import Adw, Gtk
from mx4_linux.ring_icons import action_icon
from mx4_linux.ring_model import BUTTON_RADIUS, button_center
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
