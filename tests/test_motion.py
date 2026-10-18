import pytest

from mx4_linux.motion import Transition, WheelMotion


def test_transition_retargets_without_a_jump_and_finishes_on_time():
    transition = Transition(duration=0.2)
    transition.move(1, now=10)
    halfway = transition.value(10.1)
    assert 0 < halfway < 1
    transition.move(0, now=10.1)
    assert transition.value(10.1) == halfway
    assert transition.value(10.3) == pytest.approx(0)
    assert not transition.active(10.31)


def test_reduced_motion_sets_the_final_value_immediately():
    transition = Transition()
    transition.move(1, animate=False, now=5)
    assert transition.value(5) == 1
    assert not transition.active(5)


def test_wheel_integrates_the_same_distance_at_different_frame_rates():
    positions = []
    for frames in (30, 60, 144):
        wheel = WheelMotion()
        wheel.impulse(1)
        wheel.advance(0)
        for frame in range(1, frames + 1):
            wheel.advance(frame / frames)
        positions.append(wheel.position)
    assert positions == pytest.approx([positions[0]] * 3)


def test_wheel_bounds_impulses_and_stops_after_settling():
    wheel = WheelMotion()
    wheel.impulse(10000)
    wheel.impulse(float('nan'))
    assert wheel.velocity == 24
    wheel.advance(1)
    assert not wheel.advance(10)
    wheel.stop()
    assert wheel.updated is None


def test_free_spin_coasts_farther_than_ratchet():
    positions = []
    for free_spin in (False, True):
        wheel = WheelMotion()
        wheel.impulse(1)
        wheel.advance(0)
        wheel.advance(1, free_spin=free_spin)
        positions.append(wheel.position)
    assert positions[1] > positions[0]
