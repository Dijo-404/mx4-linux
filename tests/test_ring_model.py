import math

import pytest

from mx4_linux.ring_model import RingState, sector


@pytest.mark.parametrize("index", range(8))
def test_sector_starts_at_top_and_runs_clockwise(index):
    angle = index * math.pi / 4
    assert sector(100 * math.sin(angle), -100 * math.cos(angle)) == index


@pytest.mark.parametrize("index", range(8))
def test_sector_boundaries_choose_adjacent_slices(index):
    boundary = (index + .5) * math.pi / 4
    assert sector(100 * math.sin(boundary - 1e-6), -100 * math.cos(boundary - 1e-6)) == index
    assert sector(100 * math.sin(boundary + 1e-6), -100 * math.cos(boundary + 1e-6)) == (index + 1) % 8


@pytest.mark.parametrize("x,y", [(0, 0), (23, 0), (float("nan"), 100), (100, float("inf"))])
def test_deadzone_and_nonfinite_positions_have_no_selection(x, y):
    assert sector(x, y) is None


def test_hold_move_release_commits_exactly_once_and_closes():
    ring = RingState()
    assert ring.press() == "open"
    assert ring.visible and ring.held
    assert ring.motion(0, -100) == 0
    assert ring.release() == 0
    assert not ring.visible and not ring.held and ring.selected is None
    assert ring.release() is None


def test_short_click_latches_then_pointer_choose_closes():
    ring = RingState()
    ring.press()
    assert ring.release() is None
    assert ring.visible and not ring.held
    assert ring.point(140, 0) == 2
    assert ring.choose(2) == 2
    assert not ring.visible


def test_second_press_cancels_latched_ring_and_release_does_nothing():
    ring = RingState()
    ring.press()
    ring.release()
    ring.point(0, -140)
    assert ring.press() == "cancel"
    assert not ring.visible and not ring.held
    assert ring.release() is None


def test_cancel_during_hold_never_commits_on_later_release():
    ring = RingState()
    ring.press()
    ring.motion(100, 0)
    ring.cancel()
    assert ring.release() is None
    assert ring.choose(2) is None


def test_raw_motion_is_ignored_when_closed_or_latched_and_radius_is_bounded():
    ring = RingState()
    assert ring.motion(1e6, 1e6) is None
    assert ring.x == ring.y == 0
    ring.open(held=True)
    assert ring.motion(10000, 10000) == 3
    assert math.hypot(ring.x, ring.y) == pytest.approx(155)
    ring.release()
    ring.open(held=False)
    assert ring.motion(100, 100) is None
    assert ring.x == ring.y == 0


@pytest.mark.parametrize("radius,expected", [(0, None), (65, None), (71, None), (75.99, None), (76, 2), (140, 2), (205, 2), (205.01, None)])
def test_pointer_hit_area_matches_visible_ring(radius, expected):
    ring = RingState()
    ring.open()
    assert ring.point(radius, 0) == expected


@pytest.mark.parametrize("index", [-1, 8, 3.2, True, "1", None])
def test_invalid_choice_preserves_visible_ring(index):
    ring = RingState()
    ring.open()
    assert ring.choose(index) is None
    assert ring.visible


def test_open_resets_previous_selection_motion_and_hold():
    ring = RingState()
    ring.open(held=True)
    ring.motion(100, 100)
    ring.open()
    assert ring.visible and not ring.held
    assert ring.x == ring.y == 0 and ring.selected is None
