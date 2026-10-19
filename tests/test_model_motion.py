"""The live model can orbit fully and page animations have finite lifetimes."""

import math
from math import isfinite, pi, sqrt, tau

import pytest

from mx4_linux.model_motion import (
    DRAG_RADIANS_PER_PIXEL,
    IDENTITY,
    MAX_ZOOM,
    MIN_ZOOM,
    ModelMotion,
    axis_quaternion,
    normalize_quaternion,
)
from mx4_linux.gltf_model import BAKED_UNDERSIDE, POSES, load_model


def transformed(q, vector):
    """Rotate a vector independently, to assert visible physical orientation."""
    w, x, y, z = q
    a, b, c = vector
    return (
        (1 - 2 * (y * y + z * z)) * a + 2 * (x * y - z * w) * b + 2 * (x * z + y * w) * c,
        2 * (x * y + z * w) * a + (1 - 2 * (x * x + z * z)) * b + 2 * (y * z - x * w) * c,
        2 * (x * z - y * w) * a + 2 * (y * z + x * w) * b + (1 - 2 * (x * x + y * y)) * c,
    )


@pytest.mark.parametrize('axis', ['x', 'y'])
def test_drag_allows_underside_and_full_turns_in_both_axes(axis):
    motion = ModelMotion()
    motion.begin_drag()
    for turns in (.5, 1, 1.5, 2, -1.5, -2):
        offset = tau * turns / DRAG_RADIANS_PER_PIXEL
        motion.drag(offset if axis == 'y' else 0, offset if axis == 'x' else 0)
        assert transformed(motion.orientation, (0, 0, 1)) == pytest.approx(
            (0, 0, 1 if turns % 1 == 0 else -1), abs=1e-12)
        assert not motion.active
    motion.end_drag()
    assert not motion.dragging


def test_drag_offsets_are_cumulative_and_next_gesture_starts_at_displayed_pose():
    motion = ModelMotion()
    motion.begin_drag()
    motion.drag(35, -25)
    first = motion.orientation
    motion.drag(35, -25)
    assert motion.orientation == first
    motion.drag(0, 0)
    assert motion.orientation == IDENTITY
    motion.drag(35, -25)
    motion.end_drag()
    assert not motion.drag(100, 100)
    assert motion.orientation == first
    motion.begin_drag()
    motion.drag(0, 0)
    assert motion.orientation == pytest.approx(first)


def test_keyboard_rotates_all_three_scene_axes_and_reset_restores_home():
    home = axis_quaternion('y', pi / 4)
    motion = ModelMotion(home)
    motion.rotate(yaw=-pi / 4)
    motion.rotate(pitch=pi / 2)
    assert transformed(motion.orientation, (0, 0, 1)) == pytest.approx((0, -1, 0), abs=1e-12)
    motion.rotate(roll=pi / 2)
    assert transformed(motion.orientation, (0, 0, 1)) == pytest.approx((1, 0, 0), abs=1e-12)
    motion.scroll(-2)
    assert motion.reset(animate=False)
    assert motion.orientation == home
    assert motion.zoom == 1
    assert not motion.active


@pytest.mark.parametrize('fps', [30, 60, 144])
def test_transition_reaches_exact_pose_and_stops_ticking_at_any_frame_rate(fps):
    target = axis_quaternion('x', -pi * .8)
    motion = ModelMotion(duration=.65)
    assert motion.transition_to(target, now=0)
    assert motion.active
    for frame in range(1, int(fps * .65) + 1):
        motion.advance(frame / fps)
    assert not motion.advance(.65)
    assert motion.orientation == target
    assert not motion.advance(1000)
    assert motion.orientation == target


def test_animation_uses_elapsed_time_and_stays_on_the_backwards_arc():
    target = axis_quaternion('x', -pi * .8)
    a, b = ModelMotion(), ModelMotion()
    for motion in (a, b):
        motion.transition_to(target, now=10)
    for frame in range(1, 20):
        a.advance(10 + frame / 60)
    a.advance(10.325)
    b.advance(10.325)
    assert a.orientation == pytest.approx(b.orientation)
    # Halfway through an eased symmetric flip: +Y faces toward the viewer's far side.
    assert transformed(a.orientation, (0, 1, 0))[2] < -.9
    assert transformed(a.orientation, (0, 0, 1))[1] > .9


def test_easy_switch_starts_backward_and_finishes_with_the_upright_reference():
    overview = POSES['overview']
    inverse = (overview[0], *(-value for value in overview[1:]))
    # Locate model-space vectors corresponding to the initial screen axes.
    right = transformed(inverse, (1, 0, 0))
    up = transformed(inverse, (0, 1, 0))
    motion = ModelMotion(overview)
    motion.transition_to(POSES['underside'], backward=True, now=0)
    motion.advance(.65 * .05)
    assert transformed(motion.orientation, right) == pytest.approx((1, 0, 0), abs=.0001)
    assert transformed(motion.orientation, up)[2] > 0
    motion.advance(.65 * .25)
    assert transformed(motion.orientation, up)[2] > .4
    motion.advance(.65)
    assert motion.orientation == pytest.approx(BAKED_UNDERSIDE)


def test_backward_path_projects_the_real_switch_continuously_at_route_milestones():
    motion = ModelMotion(POSES['overview'])
    motion.transition_to(POSES['underside'], backward=True, now=0)
    model = load_model()
    expected = ((.452, .724), (.435, .677), (.268, .493), (.351, .533), (.365, .586))
    for progress, position in zip((0, .25, .5, .75, 1), expected):
        motion.advance(progress * .65)
        assert model.project_anchor('easy_switch', motion.orientation) == pytest.approx(position, abs=.001)
        assert sum(component ** 2 for component in motion.orientation) == pytest.approx(1)
    assert not motion.active


def test_backward_curve_has_no_internal_stops_or_orientation_jumps():
    motion = ModelMotion(POSES['overview'])
    motion.transition_to(POSES['underside'], backward=True, now=0)
    previous = transformed(motion.orientation, (0, 1, 0))
    for step in range(1, 241):
        motion.advance(step / 240 * .65)
        current = transformed(motion.orientation, (0, 1, 0))
        distance = sum((a - b) ** 2 for a, b in zip(previous, current)) ** .5
        assert distance < .06
        if 24 < step < 216:
            assert distance > 1e-6
        previous = current


def test_backward_transition_retarget_keeps_the_displayed_orientation():
    motion = ModelMotion(POSES['overview'])
    motion.transition_to(POSES['underside'], backward=True, now=0)
    motion.advance(.325)
    displayed = motion.orientation
    motion.transition_to(POSES['haptic'])
    assert motion.orientation == displayed
    motion.advance(10)
    assert motion.orientation == displayed
    motion.advance(10.7)
    assert motion.orientation == pytest.approx(POSES['haptic'])
    motion.transition_to(POSES['underside'], backward=True, animate=False)
    assert motion.orientation == pytest.approx(POSES['underside'])
    assert not motion.active


def test_haptic_pose_turns_sideways_toward_the_thumb_surface():
    overview = POSES['overview']
    inverse = (overview[0], *(-value for value in overview[1:]))
    facing = transformed(inverse, (0, 0, 1))
    x, _y, z = transformed(POSES['haptic'], facing)
    assert x > .6
    assert .6 < z < .85


def test_retarget_during_animation_continues_from_current_pose_without_jump():
    motion = ModelMotion()
    motion.transition_to(axis_quaternion('y', 1.5), now=0)
    motion.advance(.25)
    displayed = motion.orientation
    target = axis_quaternion('x', -2.5)
    motion.transition_to(target)
    assert motion.orientation == displayed
    assert motion.advance(20)
    assert motion.orientation == displayed
    assert motion.advance(20.2)
    assert motion.orientation != displayed
    assert not motion.advance(20.65)
    assert motion.orientation == target


def test_retarget_with_explicit_time_samples_the_current_animation_first():
    motion = ModelMotion()
    reference = ModelMotion()
    for instance in (motion, reference):
        instance.transition_to(axis_quaternion('y', 1), now=0)
    reference.advance(.3)
    motion.transition_to(axis_quaternion('x', 1), now=.3)
    assert motion.orientation == reference.orientation


def test_suspend_preserves_progress_without_hidden_time_jump():
    motion = ModelMotion()
    reference = ModelMotion()
    target = axis_quaternion('x', -2.5)
    for instance in (motion, reference):
        instance.transition_to(target, now=0)
        instance.advance(.2)
    displayed = motion.orientation
    motion.suspend()
    assert motion.advance(1000)
    assert motion.orientation == displayed
    motion.advance(1000.2)
    reference.advance(.4)
    assert motion.orientation == pytest.approx(reference.orientation)
    assert not motion.advance(1000.46)


@pytest.mark.parametrize('interaction', ['drag', 'keyboard'])
def test_direct_interaction_cancels_automatic_transition_without_a_jump(interaction):
    motion = ModelMotion()
    motion.transition_to(axis_quaternion('y', 1), now=0)
    motion.advance(.3)
    displayed = motion.orientation
    if interaction == 'drag':
        motion.begin_drag()
        motion.drag(0, 0)
    else:
        motion.rotate()
    assert not motion.active
    assert motion.orientation == pytest.approx(displayed)
    assert not motion.advance(5)
    assert motion.orientation == pytest.approx(displayed)


def test_reduced_motion_snaps_page_pose_but_still_allows_direct_rotation():
    motion = ModelMotion()
    target = axis_quaternion('x', -2)
    motion.transition_to(target, animate=False)
    assert motion.orientation == target
    assert not motion.active
    motion.begin_drag()
    motion.drag(20, 30)
    assert motion.orientation != target
    assert not motion.active


def test_equivalent_quaternion_sign_does_not_trigger_an_unnecessary_full_turn():
    target = axis_quaternion('y', 1)
    motion = ModelMotion(target)
    assert not motion.transition_to(tuple(-value for value in target))
    assert not motion.active
    assert transformed(motion.orientation, (0, 0, 1)) == pytest.approx(
        transformed(target, (0, 0, 1)))


def test_zoom_bounds_are_finite_and_reversible_with_extreme_scroll_values():
    motion = ModelMotion()
    motion.scroll(-1e308)
    assert motion.zoom == MAX_ZOOM
    assert not motion.scroll(-100)
    motion.scroll(1e308)
    assert motion.zoom == MIN_ZOOM
    assert not motion.scroll(100)
    motion.scroll(-1)
    assert MIN_ZOOM < motion.zoom < MAX_ZOOM


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf'), None, 'oops'])
def test_nonfinite_events_and_poses_do_not_corrupt_the_view(invalid):
    motion = ModelMotion()
    motion.begin_drag()
    assert not motion.drag(invalid, 0)
    assert not motion.drag(0, invalid)
    assert not motion.rotate(yaw=invalid)
    assert not motion.scroll(invalid)
    assert not motion.transition_to((invalid, 0, 0, 1))
    assert motion.orientation == IDENTITY
    assert motion.zoom == 1
    motion.transition_to(axis_quaternion('x', 1), now=0)
    assert motion.advance(invalid)
    assert motion.orientation == IDENTITY


def test_clock_regression_does_not_count_the_same_time_twice():
    motion = ModelMotion()
    motion.transition_to(axis_quaternion('x', 1), now=10)
    motion.advance(10.2)
    displayed = motion.orientation
    motion.advance(9)
    motion.advance(10.2)
    assert motion.orientation == displayed
    assert motion.active
    assert not motion.advance(10.7)


def test_large_finite_rotations_stay_normalized_and_zero_quaternion_is_rejected():
    assert normalize_quaternion((1e308, 1e308, 0, 0)) == pytest.approx((1 / sqrt(2), 1 / sqrt(2), 0, 0))
    motion = ModelMotion()
    motion.begin_drag()
    motion.drag(1e308, -1e308)
    motion.rotate(yaw=1e308, pitch=-1e308, roll=1e308)
    assert all(isfinite(component) for component in motion.orientation)
    assert sum(component ** 2 for component in motion.orientation) == pytest.approx(1)
    previous = motion.orientation
    assert not motion.transition_to((0, 0, 0, 0))
    assert motion.orientation == previous


@pytest.mark.parametrize('fps', [30, 60, 144])
def test_released_flick_coasts_then_stops_in_finite_time(fps):
    from mx4_linux.model_motion import MAX_SPIN
    motion = ModelMotion()
    assert motion.fling(6.0, -2.0)
    assert motion.moving and not motion.active
    frames = 0
    motion.advance(0)
    while motion.advance(frames / fps):
        frames += 1
        assert frames < fps * 4
    turned = motion.orientation
    assert turned != IDENTITY and motion.spin is None and not motion.moving
    assert not motion.advance(100)
    assert motion.orientation == turned
    assert motion.fling(1e6, 0)
    assert sqrt(sum(rate ** 2 for rate in motion.spin)) == pytest.approx(MAX_SPIN)


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), None])
def test_flick_rejects_wobble_nonfinite_held_and_animated_states(invalid):
    motion = ModelMotion()
    assert not motion.fling(.1, .1)
    assert not motion.fling(invalid, 5)
    motion.begin_drag()
    assert not motion.fling(5, 0)
    motion.end_drag()
    motion.transition_to(axis_quaternion('x', 1), now=0)
    assert not motion.fling(5, 0)
    for cancel in (motion.stop, motion.begin_drag, lambda: motion.rotate(yaw=.1),
                   lambda: motion.transition_to(IDENTITY, animate=False)):
        motion.end_drag()
        motion.stop()
        assert motion.fling(5, 0)
        cancel()
        assert motion.spin is None


def test_showcase_reveal_overshoots_settles_and_parks_without_sway():
    from mx4_linux.model_motion import REVEAL_SECONDS, Showcase
    showcase = Showcase()
    assert not showcase.active and showcase.offset() == IDENTITY and showcase.scale() == 1
    showcase.start_reveal()
    assert showcase.active and showcase.offset() != IDENTITY and showcase.scale() < 1
    angles = []
    step = 0
    showcase.advance(0)
    while showcase.advance(step / 60):
        step += 1
        assert step < 60 * 3
        w, _x, y, _z = showcase.offset()
        angles.append((2 * math.atan2(y, w) + pi) % tau - pi)
    assert step / 60 == pytest.approx(REVEAL_SECONDS, abs=.05)
    assert max(angles) > 0 > angles[0]  # Swings past its pose, then returns.
    assert showcase.offset() == pytest.approx(IDENTITY) and showcase.scale() == 1


def test_showcase_sway_waits_for_idle_blends_in_and_bakes_without_a_jump():
    from mx4_linux.model_motion import SWAY_DELAY, Showcase, quaternion_multiply
    showcase = Showcase()
    showcase.sway = True
    t = 0.0
    showcase.advance(t)
    for _ in range(60 * 4):
        t += 1 / 60
        assert showcase.advance(t)  # An enabled sway never parks on its own.
    assert showcase.weight == 1 and showcase.steady and showcase.offset() != IDENTITY
    base = axis_quaternion('x', .4)
    visible = quaternion_multiply(showcase.offset(), base)
    baked = quaternion_multiply(showcase.bake(), base)
    assert baked == pytest.approx(visible)
    assert showcase.offset() == pytest.approx(IDENTITY) and showcase.idle == 0
    for _ in range(int(60 * SWAY_DELAY) - 2):
        t += 1 / 60
        showcase.advance(t)
    assert showcase.weight == 0  # Manipulation earns a quiet pause before the sway returns.
    showcase.sway = False
    t += 1 / 60
    assert not showcase.advance(t)


def test_showcase_pointer_lean_is_bounded_follows_and_ignores_hidden_time():
    from mx4_linux.model_motion import TILT, Showcase
    showcase = Showcase()
    showcase.point(50, -50)
    assert showcase.target == (TILT[0], -TILT[1])
    showcase.point(float('nan'), 0)
    assert showcase.target == (TILT[0], -TILT[1])
    showcase.advance(0)
    showcase.advance(1 / 60)
    leaning = showcase.tilt
    assert 0 < leaning[0] < TILT[0]
    showcase.suspend()
    showcase.advance(1000)
    assert showcase.tilt == leaning
    showcase.point(0, 0)
    step = 0
    while showcase.advance(1000 + step / 60):
        step += 1
        assert step < 120
    assert showcase.tilt == (0, 0) and not showcase.active
