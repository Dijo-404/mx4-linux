"""Time-based presentation state for the native, GLB-derived mouse views."""

from math import exp, isfinite, pi, sin


class OrbitMotion:
    """A bounded camera drift with gently damped pointer steering.

    Time advances only when the owner is mapped and playback is enabled. The
    model's physical buttons and wheel state are deliberately independent.
    """

    def __init__(self):
        self.phase = 0.0
        self.pose = 0.0
        self.pointer = 0.0
        self.updated = None

    def steer(self, value):
        if isfinite(value):
            self.pointer = max(-1.0, min(1.0, value))

    def suspend(self):
        self.updated = None

    def advance(self, now):
        if not isfinite(now):
            return self.pose
        if self.updated is None:
            self.updated = now
            return self.pose
        dt = max(0.0, min(0.1, now - self.updated))
        self.updated = now
        self.phase = (self.phase + dt * 2 * pi / 12) % (2 * pi)
        target = .68 * sin(self.phase) + .30 * self.pointer
        self.pose += (target - self.pose) * (1 - exp(-dt / .26))
        return self.pose


def sample_pose(frames, pose):
    """Return adjacent baked poses and their shared image/anchor interpolation."""
    index = (max(-1.0, min(1.0, pose)) + 1) * .5 * (len(frames) - 1)
    lower = min(len(frames) - 1, int(index))
    upper = min(len(frames) - 1, lower + 1)
    return frames[lower], frames[upper], index - lower


def anchor_position(frames, pose, name):
    first, second, blend = sample_pose(frames, pose)
    a, b = first['anchors'][name], second['anchors'][name]
    return (a[0] + (b[0] - a[0]) * blend, a[1] + (b[1] - a[1]) * blend)
