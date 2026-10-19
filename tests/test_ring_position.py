"""Pointer placement uses output-local logical coordinates and bounded probes."""

from types import SimpleNamespace

import pytest

pytest.importorskip("gi")

from mx4_linux import ring_position
from mx4_linux.ring_position import PointerLocator, ring_origin


@pytest.mark.parametrize("pointer, center, radius, monitor, expected", [
    ((1000, 600), (390, 220), 96, (2560, 1440), (610, 380)),
    # GTK coordinates already account for this output's device scale.
    ((325.13, 633.79), (390, 220), 96, (1440, 900), (-65, 414)),
    # The configured ring scale changes its visible radius and layout center.
    ((900, 600), (585, 330), 144, (1440, 900), (315, 270)),
    ((0, 0), (390, 220), 96, (2560, 1440), (-294, -124)),
    ((2559, 1439), (390, 220), 96, (2560, 1440), (2074, 1124)),
    # Transparent layout padding may extend beyond the screen; faces may not.
    ((200, 200), (585, 330), 144, (800, 600), (-385, -130)),
    # A pathologically small output centers the ring instead of overshooting.
    ((0, 0), (390, 220), 96, (100, 80), (-340, -180)),
])
def test_ring_origin_clamps_visible_faces_instead_of_transparent_padding(pointer, center, radius, monitor, expected):
    assert ring_origin(pointer, center, radius, monitor) == expected


@pytest.fixture
def probe(monkeypatch):
    destroyed, removed, found = [], [], []
    locator = PointerLocator(lambda *args: found.append(args))
    locator.active = True
    locator.timeout = 17
    locator.windows = [SimpleNamespace(destroy=lambda i=i: destroyed.append(i)) for i in range(2)]
    monkeypatch.setattr(ring_position.GLib, "source_remove", lambda source: removed.append(source))
    return SimpleNamespace(locator=locator, destroyed=destroyed, removed=removed, found=found)


def test_pointer_enter_cleans_every_output_and_presents_once(probe):
    monitor = object()
    probe.locator._entered(None, 325.13, 633.79, monitor)
    probe.locator._entered(None, 1, 2, object())
    assert probe.found == [(monitor, 325.13, 633.79)]
    assert probe.destroyed == [0, 1]
    assert probe.removed == [17]
    assert not probe.locator.windows
    assert not probe.locator.active


def test_cancel_during_placement_cannot_reopen_a_dismissed_ring(probe):
    probe.locator.cancel()
    probe.locator._entered(None, 1, 2, object())
    probe.locator._expired()
    probe.locator.cancel()
    assert not probe.found
    assert probe.destroyed == [0, 1]
    assert probe.removed == [17]


def test_unavailable_pointer_falls_back_once_after_bounded_timeout(probe):
    assert probe.locator.timeout_ms == 100
    assert probe.locator._expired() is False
    probe.locator._entered(None, 1, 2, object())
    probe.locator._expired()
    assert probe.found == [(None, None, None)]
    assert probe.destroyed == [0, 1]
    assert not probe.removed  # The timeout is already dispatching, so do not remove it.


def test_failed_setup_cleans_partially_mapped_outputs_before_fallback(monkeypatch):
    monitors, mapped, destroyed, found = [object(), object()], [], [], []

    class Window:
        def __init__(self, **_kwargs):
            self.index = len(created)
            created.append(self)

        def add_css_class(self, _name):
            pass

        def set_child(self, _child):
            pass

        def add_controller(self, _controller):
            pass

        def present(self):
            mapped.append(self.index)

        def destroy(self):
            destroyed.append(self.index)

    def set_monitor(_window, monitor):
        if monitor is monitors[1]:
            raise RuntimeError("Output disappeared while mapping the second probe")

    created = []
    monkeypatch.setattr(ring_position.Gdk.Display, "get_default",
                        lambda: SimpleNamespace(get_monitors=lambda: monitors))
    monkeypatch.setattr(ring_position.Gtk, "Window", Window)
    monkeypatch.setattr(ring_position, "Layer", SimpleNamespace(
        init_for_window=lambda *_: None, set_namespace=lambda *_: None, set_monitor=set_monitor,
        set_layer=lambda *_: None, set_keyboard_mode=lambda *_: None, set_exclusive_zone=lambda *_: None,
        set_anchor=lambda *_: None, Layer=SimpleNamespace(OVERLAY=1),
        KeyboardMode=SimpleNamespace(NONE=0), Edge=SimpleNamespace(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)))
    monkeypatch.setattr(ring_position.Gtk, "DrawingArea", lambda: SimpleNamespace(set_draw_func=lambda *_: None))
    monkeypatch.setattr(ring_position.Gtk, "EventControllerMotion", lambda: SimpleNamespace(connect=lambda *_: None))
    locator = PointerLocator(lambda *args: found.append(args))
    locator.locate()
    locator._entered(None, 1, 2, monitors[0])
    assert mapped == [0]
    assert destroyed == [0, 1]
    assert found == [(None, None, None)]
    assert not locator.active and not locator.windows and not locator.timeout
