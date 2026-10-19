"""Finite 3D pose transitions, direct manipulation and home-card presentation.

Quaternions use ``(w, x, y, z)`` and rotate model coordinates into the scene.
Scene X points right, Y points up, and Z points toward the viewer. This module
does not depend on GTK or a renderer. Only an enabled ``Showcase`` sway runs
without an end; every other motion settles and stops.
"""

from math import acos, cos, exp, hypot, isfinite, pi, sin, tau


IDENTITY = (1.0, 0.0, 0.0, 0.0)
DRAG_RADIANS_PER_PIXEL = .008
MIN_ZOOM = .7
MAX_ZOOM = 1.6
FRICTION = 3.2       # 1/s; a released flick coasts for about a second
MIN_SPIN = .08       # rad/s; slower coasting stops
MAX_SPIN = 14.0      # rad/s
REVEAL_SECONDS = 1.3
SWAY_PERIOD = 12.0
SWAY_DELAY = 2.5     # idle seconds before the sway blends back in
SWAY_RAMP = 1.6      # seconds to blend the sway in or out
TILT = (.20, .14)    # pointer lean in radians at the viewport edge (yaw, pitch)
TILT_RATE = 8.0      # 1/s; how quickly the lean follows the pointer


def normalize_quaternion(value):
    """Return a unit quaternion, rejecting malformed or nonfinite input."""
    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Expected four finite quaternion components') from error
    if len(components) != 4 or not all(isfinite(component) for component in components):
        raise ValueError('Expected four finite quaternion components')
    # Scaling before computing length also handles very large finite inputs.
    scale = max(abs(component) for component in components)
    if not scale:
        raise ValueError('A zero quaternion has no orientation')
    scaled = tuple(component / scale for component in components)
    length = hypot(*scaled)
    return tuple(component / length for component in scaled)


def quaternion_multiply(first, second):
    """Compose unit rotations; ``second`` is applied before ``first``."""
    w, x, y, z = first
    a, b, c, d = second
    return normalize_quaternion((
        w * a - x * b - y * c - z * d,
        w * b + x * a + y * d - z * c,
        w * c - x * d + y * a + z * b,
        w * d + x * c - y * b + z * a,
    ))


def axis_quaternion(axis, angle):
    """Build a scene-axis rotation, with ``angle`` in radians."""
    half = (angle % tau) * .5
    components = [cos(half), 0.0, 0.0, 0.0]
    components['xyz'.index(axis) + 1] = sin(half)
    return tuple(components)


def quaternion_slerp(first, second, progress):
    """Interpolate the shortest orientation arc without Euler singularities."""
    dot = sum(a * b for a, b in zip(first, second))
    if dot < 0:
        second = tuple(-component for component in second)
        dot = -dot
    if dot > .9995:
        return normalize_quaternion(tuple(a + (b - a) * progress for a, b in zip(first, second)))
    angle = acos(max(-1.0, min(1.0, dot)))
    denominator = sin(angle)
    a = sin((1 - progress) * angle) / denominator
    b = sin(progress * angle) / denominator
    return normalize_quaternion(tuple(a * x + b * y for x, y in zip(first, second)))


def quaternion_bezier(controls, progress):
    """Sample a spherical cubic with continuous orientation and no waypoint stop."""
    points = controls
    while len(points) > 1:
        points = tuple(quaternion_slerp(a, b, progress) for a, b in zip(points, points[1:]))
    return points[0]


def _backward_controls(source, target):
    points = (
        source,
        quaternion_multiply(axis_quaternion('x', pi / 2), source),
        quaternion_multiply(axis_quaternion('x', -pi / 3), target),
        target,
    )
    # Pick a consistent quaternion lift along the control polygon. The shipped
    # page poses fit in one hemisphere, avoiding antipodal interpolation jumps.
    aligned = [points[0]]
    for point in points[1:]:
        if sum(a * b for a, b in zip(aligned[-1], point)) < 0:
            point = tuple(-value for value in point)
        aligned.append(point)
    return tuple(aligned)


def _finite(value):
    try:
        return isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _same_orientation(first, second):
    # q and -q describe the same orientation.
    return abs(sum(a * b for a, b in zip(first, second))) >= 1 - 1e-12


class ModelMotion:
    """Own one mouse's orientation, bounded zoom and optional pose animation.

    ``advance(now)`` accepts monotonic seconds and returns whether another frame
    is needed. Call ``suspend()`` when hidden; the first frame after resuming
    establishes a fresh timestamp without including hidden time. Drag offsets
    are cumulative GTK gesture offsets, not deltas between successive events.
    """

    def __init__(self, orientation=IDENTITY, duration=.65):
        self.orientation = normalize_quaternion(orientation)
        self.home = self.orientation
        self.zoom = 1.0
        self.duration = float(duration) if _finite(duration) and duration > 0 else .65
        self.active = False
        self.dragging = False
        self.spin = None
        self._source = self._target = self.orientation
        self._controls = None
        self._drag_source = self.orientation
        self._elapsed = 0.0
        self._updated = None

    def transition_to(self, orientation, *, animate=True, now=None, backward=False):
        """Retarget from the displayed pose, or snap when animation is disabled.

        An optional timestamp first samples an in-flight transition. Without
        one, interpolation starts at the last displayed pose on the next frame.
        ``backward`` starts around screen X, then curves into the target pose;
        this exposes the underside while ending with its labels upright.
        Invalid target values leave the current view unchanged.
        """
        try:
            target = normalize_quaternion(orientation)
        except ValueError:
            return False
        if now is not None:
            self.advance(now)
        self.dragging = False
        self.spin = None
        self._source = self.orientation
        self._target = target
        self._controls = _backward_controls(self.orientation, target) if backward else None
        self._elapsed = 0.0
        self._updated = float(now) if _finite(now) else None
        changed = not _same_orientation(self.orientation, target)
        self.active = bool(animate and changed)
        if not self.active:
            self.orientation = target
            self._updated = None
        return changed

    @property
    def moving(self):
        return self.active or self.spin is not None

    def fling(self, yaw_rate, pitch_rate):
        """Coast after a released drag; rates are scene-axis radians per second."""
        if self.dragging or self.active or not (_finite(yaw_rate) and _finite(pitch_rate)):
            return False
        speed = hypot(yaw_rate, pitch_rate)
        if speed < MIN_SPIN * 4:
            return False
        limit = min(1.0, MAX_SPIN / speed)
        self.spin = (yaw_rate * limit, pitch_rate * limit)
        self._updated = None
        return True

    def advance(self, now):
        """Update a finite transition or coast; report whether more frames are needed."""
        if not self.moving or not _finite(now):
            return self.moving
        now = float(now)
        if self._updated is None:
            self._updated = now
            return True
        if now <= self._updated:
            return True
        delta = now - self._updated
        self._updated = now
        if not self.active:
            yaw, pitch = self.spin
            self.orientation = quaternion_multiply(quaternion_multiply(
                axis_quaternion('y', yaw * delta), axis_quaternion('x', pitch * delta)), self.orientation)
            decay = exp(-FRICTION * delta)
            self.spin = (yaw * decay, pitch * decay) if hypot(yaw, pitch) * decay >= MIN_SPIN else None
            if self.spin is None:
                self._updated = None
            return self.spin is not None
        self._elapsed += delta
        progress = min(1.0, self._elapsed / self.duration)
        if self._elapsed >= self.duration - 1e-9:
            progress = 1.0
        # Zero velocity and acceleration at both endpoints, independent of FPS.
        eased = progress ** 3 * (progress * (progress * 6 - 15) + 10)
        self.orientation = (quaternion_bezier(self._controls, eased) if self._controls
                            else quaternion_slerp(self._source, self._target, eased))
        if progress >= 1:
            self.orientation = self._target
            self.active = False
            self._updated = None
        return self.active

    def suspend(self):
        """Pause the transition clock while retaining its elapsed fraction."""
        self._updated = None
        self.dragging = False

    def stop(self):
        """Cancel a transition or coast at its current displayed orientation."""
        self.active = False
        self.spin = None
        self._source = self._target = self.orientation
        self._controls = None
        self._updated = None

    def begin_drag(self):
        self.stop()
        self.dragging = True
        self._drag_source = self.orientation

    def drag(self, dx, dy):
        """Orbit freely around scene X/Y; neither axis has a pitch/yaw limit."""
        if not self.dragging or not (_finite(dx) and _finite(dy)):
            return False
        rotation = quaternion_multiply(
            axis_quaternion('y', dx * DRAG_RADIANS_PER_PIXEL),
            axis_quaternion('x', dy * DRAG_RADIANS_PER_PIXEL),
        )
        self.orientation = quaternion_multiply(rotation, self._drag_source)
        return True

    def end_drag(self):
        self.dragging = False

    def rotate(self, yaw=0.0, pitch=0.0, roll=0.0):
        """Apply keyboard/direct rotation in radians around the scene axes."""
        if not all(_finite(angle) for angle in (yaw, pitch, roll)):
            return False
        self.stop()
        self.dragging = False
        rotation = quaternion_multiply(
            axis_quaternion('y', yaw), axis_quaternion('x', pitch))
        rotation = quaternion_multiply(axis_quaternion('z', roll), rotation)
        self.orientation = quaternion_multiply(rotation, self.orientation)
        return True

    def scroll(self, delta):
        """Positive scroll zooms out; return whether the bounded zoom changed."""
        if not _finite(delta):
            return False
        previous = self.zoom
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * exp(-max(-100, min(100, delta)) * .1)))
        return previous != self.zoom

    def reset(self, *, animate=True, now=None):
        """Restore the constructor's pose and default magnification."""
        zoom_changed = self.zoom != 1.0
        self.zoom = 1.0
        changed = self.transition_to(self.home, animate=animate, now=now)
        return changed or zoom_changed


class Showcase:
    """Home-card presentation layered over the user's orientation.

    An entrance turn and pointer lean settle and stop; the slow sway runs only
    while ``sway`` is enabled and the viewer has been idle. ``offset()`` never
    changes ``ModelMotion``, so drag, keyboard rotation and reset keep their
    meaning. ``bake()`` hands the visible offset to the direct pose before a
    manipulation, so taking hold of the model never makes it jump.
    """

    def __init__(self):
        self.sway = False
        self.reveal = REVEAL_SECONDS
        self.phase = 0.0
        self.weight = 0.0
        self.idle = SWAY_DELAY
        self.tilt = self.target = (0.0, 0.0)
        self._updated = None

    @property
    def active(self):
        return (self.sway or self.reveal < REVEAL_SECONDS or self.weight > 0
                or self.tilt != self.target)

    @property
    def steady(self):
        """Only the slow sway is moving; hosts may update it at a lower rate."""
        return self.weight == 1 and self.reveal >= REVEAL_SECONDS and self.tilt == self.target

    def start_reveal(self):
        self.reveal = 0.0

    def point(self, x, y):
        """Lean toward a pointer at normalized viewport coordinates in [-1, 1]."""
        if _finite(x) and _finite(y):
            self.target = (max(-1.0, min(1.0, x)) * TILT[0], max(-1.0, min(1.0, y)) * TILT[1])

    def suspend(self):
        self._updated = None

    def advance(self, now):
        if not _finite(now) or not self.active:
            return self.active
        if self._updated is None or now <= self._updated:
            self._updated = max(float(now), self._updated or 0.0)
            return True
        # A stalled frame never throws the sway or lean forward in one jump.
        delta = min(float(now) - self._updated, .1)
        self._updated = float(now)
        self.reveal = min(REVEAL_SECONDS, self.reveal + delta)
        self.idle += delta
        goal = 1.0 if self.sway and self.idle >= SWAY_DELAY and self.reveal >= REVEAL_SECONDS else 0.0
        step = delta / SWAY_RAMP
        self.weight = min(goal, self.weight + step) if goal > self.weight else max(goal, self.weight - step)
        if self.weight:
            self.phase = (self.phase + delta * tau / SWAY_PERIOD) % tau
        follow = 1 - exp(-TILT_RATE * delta)
        self.tilt = tuple(b if abs(b - a) < 1e-4 else a + (b - a) * follow
                          for a, b in zip(self.tilt, self.target))
        return self.active

    def _remaining(self):
        # Ease-out-back: the entrance turn overshoots by a few degrees, then settles.
        p = self.reveal / REVEAL_SECONDS - 1
        return -(1.9 * p ** 3 + .9 * p ** 2)

    def offset(self):
        turn = self._remaining()
        w = self.weight * self.weight * (3 - 2 * self.weight)
        yaw = -1.1 * turn + w * .30 * sin(self.phase) + self.tilt[0]
        pitch = .35 * turn + w * .06 * sin(2 * self.phase) + self.tilt[1]
        return quaternion_multiply(axis_quaternion('y', yaw), axis_quaternion('x', pitch))

    def scale(self):
        p = min(1.0, self.reveal / REVEAL_SECONDS)
        return 1 - .12 * (1 - p) ** 3

    def bake(self):
        """Return the visible offset and clear it, before direct manipulation."""
        offset = self.offset()
        self.reveal = REVEAL_SECONDS
        self.weight = self.phase = self.idle = 0.0
        self.tilt = self.target = (0.0, 0.0)
        return offset
