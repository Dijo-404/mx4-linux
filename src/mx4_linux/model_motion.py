"""Finite 3D pose transitions and unrestricted direct manipulation.

Quaternions use ``(w, x, y, z)`` and rotate model coordinates into the scene.
Scene X points right, Y points up, and Z points toward the viewer. This module
does not depend on GTK or a renderer and never advances an idle animation.
"""

from math import acos, cos, exp, hypot, isfinite, pi, sin, tau


IDENTITY = (1.0, 0.0, 0.0, 0.0)
DRAG_RADIANS_PER_PIXEL = .008
MIN_ZOOM = .7
MAX_ZOOM = 1.6


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

    def advance(self, now):
        """Update one finite transition and report whether it needs more frames."""
        if not self.active or not _finite(now):
            return self.active
        now = float(now)
        if self._updated is None:
            self._updated = now
            return True
        if now <= self._updated:
            return True
        self._elapsed += now - self._updated
        self._updated = now
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
        """Cancel a transition at its current displayed orientation."""
        self.active = False
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
