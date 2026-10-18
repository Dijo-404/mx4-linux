import math

import pytest

from mx4_linux.ring_motion import OrbitMotion


def test_marker_crosses_twelve_oclock_by_the_shortest_arc_in_both_directions():
    marker = OrbitMotion()
    marker.select(7, animate=False, now=10)
    start = marker.angle.value(10)
    marker.select(0, now=11)
    assert marker.angle.target - start == pytest.approx(math.pi / 4)
    marker.select(7, now=12)
    assert marker.angle.target - marker.angle.start == pytest.approx(-math.pi / 4)


def test_interrupted_selection_continues_from_the_painted_angle():
    marker = OrbitMotion()
    marker.select(0, animate=False, now=10)
    marker.select(3, now=11)
    painted = marker.angle.value(11.06)
    marker.select(7, now=11.06)
    assert marker.angle.value(11.06) == painted
    assert abs(marker.angle.target - painted) <= math.pi
    assert marker.angle.value(11.25) == marker.angle.target
    assert not any(t.active(11.25) for t in marker.transitions())


def test_crossing_a_pointer_gap_preserves_motion_but_new_entry_appears_directly():
    marker = OrbitMotion()
    marker.select(0, animate=False, now=10)
    marker.select(None, now=11)
    marker.select(2, now=11.03)
    assert marker.angle.value(11.03) == 0
    assert marker.angle.active(11.03)
    marker.select(None, now=12)
    marker.select(5, now=13)
    assert not marker.angle.active(13)
    assert marker.opacity.active(13)
    assert marker.angle.value(13) % math.tau == pytest.approx(5 * math.pi / 4)


def test_reduced_motion_and_reset_leave_no_running_transition():
    marker = OrbitMotion()
    marker.select(1, animate=False, now=10)
    marker.select(6, animate=False, now=11)
    assert marker.opacity.value(11) == 1
    assert not any(t.active(11) for t in marker.transitions())
    marker.reset()
    assert marker.selected is None
    assert marker.opacity.value() == marker.angle.value() == 0
    assert not any(t.active() for t in marker.transitions())


def test_same_selection_does_not_restart_an_animation():
    marker = OrbitMotion()
    marker.select(0, animate=False, now=10)
    marker.select(1, now=11)
    marker.select(1, now=11.09)
    assert marker.angle.started == 11
    assert not marker.angle.active(11.19)


@pytest.mark.parametrize("index", [-1, 8, True, 1.5, "1"])
def test_non_ring_positions_are_rejected(index):
    with pytest.raises(ValueError):
        OrbitMotion().select(index)
