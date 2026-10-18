import math

import pytest

from mx4_linux.ring_model import BUTTON_RADIUS, ORBIT_RADIUS, RingState, button_at, button_center, sector


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
    assert ring.point(*button_center(2)) == 2
    assert ring.choose(2) == 2
    assert not ring.visible


def test_second_press_cancels_latched_ring_and_release_does_nothing():
    ring = RingState()
    ring.press()
    ring.release()
    ring.point(*button_center(0))
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


@pytest.mark.parametrize("index", range(8))
def test_pointer_selects_each_circular_button(index):
    ring = RingState()
    ring.open()
    assert ring.point(*button_center(index)) == index


@pytest.mark.parametrize("index", range(8))
def test_button_centers_match_clockwise_orbit(index):
    x, y = button_center(index)
    assert math.hypot(x, y) == pytest.approx(ORBIT_RADIUS)
    assert sector(x, y) == index


@pytest.mark.parametrize("offset,expected", [(-26.01, None), (-26, 2), (0, 2), (26, 2), (26.01, None)])
def test_pointer_hit_area_stops_at_circle_boundary(offset, expected):
    assert button_at(ORBIT_RADIUS + offset, 0) == expected


@pytest.mark.parametrize("index", range(8))
def test_gaps_between_buttons_do_not_select_invisible_sector(index):
    angle = (index + .5) * math.pi / 4
    assert button_at(ORBIT_RADIUS * math.sin(angle), -ORBIT_RADIUS * math.cos(angle)) is None


@pytest.mark.parametrize("x,y", [(0, 0), (23, 0), (205, 0), (float("nan"), 100), (100, float("inf"))])
def test_pointer_center_outside_and_nonfinite_positions_clear_selection(x, y):
    ring = RingState()
    ring.open()
    ring.point(*button_center(0))
    assert ring.point(x, y) is None


def test_pointer_cannot_select_in_a_button_bounding_box_corner():
    assert button_at(ORBIT_RADIUS + BUTTON_RADIUS, BUTTON_RADIUS) is None


def test_pointer_is_ignored_when_closed():
    ring = RingState()
    assert ring.point(*button_center(0)) is None


def test_pointer_tracks_painted_button_positions_during_entry():
    ring = RingState()
    ring.open()
    x, y = button_center(0)
    spread = .78
    assert ring.point(x * spread, y * spread, spread=spread) == 0
    assert ring.point(x * spread, y * spread + BUTTON_RADIUS - 1, spread=spread) == 0
    assert ring.point(x, y - BUTTON_RADIUS, spread=spread) is None
    assert ring.point(x * spread + BUTTON_RADIUS, y * spread + BUTTON_RADIUS, spread=spread) is None


@pytest.mark.parametrize("spread", [0, -1, float("nan"), float("inf")])
def test_invalid_animation_spread_has_no_pointer_target(spread):
    assert button_at(*button_center(0), spread=spread) is None


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

@pytest.mark.parametrize('scale', [75, 100, 150])
def test_geometry_scales_faces_and_round_trips_coordinates(scale):
    from mx4_linux.ring_model import RingGeometry
    geometry = RingGeometry(scale)
    assert geometry.size(52) == round(52 * scale / 100)
    assert geometry.logical(*geometry.point(37, -21)) == pytest.approx((37, -21))

@pytest.mark.parametrize('kwargs', [{'scale': True}, {'scale': 74}, {'motion_gain': float('nan')},
                                   {'motion_gain': .09}, {'deadzone': float('inf')}, {'deadzone': 81}])
def test_invalid_model_options_raise_before_use(kwargs):
    from mx4_linux.ring_model import RingGeometry
    if 'scale' in kwargs:
        with pytest.raises(ValueError):
            RingGeometry(**kwargs)
    else:
        with pytest.raises(ValueError):
            RingState(**kwargs)

def test_configurable_motion_is_logical_and_nonfinite_deltas_do_not_poison_state():
    state = RingState(motion_gain=1, deadzone=50)
    state.open(held=True)
    assert state.motion(0, -30) is None
    assert state.motion(float('nan'), 1) is None
    assert state.motion(1, float('inf')) is None
    assert (state.x, state.y) == (0, -30)
    assert state.motion(0, -21) == 0

def test_finite_huge_raw_motion_cannot_overflow_state():
    state = RingState(motion_gain=2)
    state.open(held=True)
    state.motion(1e308, 1e308)
    assert math.isfinite(state.x) and math.isfinite(state.y)
    assert math.hypot(state.x, state.y) == pytest.approx(155)
    assert state.motion(0, -10) is not None
