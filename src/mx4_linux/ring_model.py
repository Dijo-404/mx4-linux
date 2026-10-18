"""Interaction and geometry for an eight-position Actions Ring."""

import math


ORBIT_RADIUS = 116
BUTTON_RADIUS = 26
CENTER_RADIUS = 16
_BUTTON_CENTERS = tuple(
    (ORBIT_RADIUS * math.sin(index * math.pi / 4), -ORBIT_RADIUS * math.cos(index * math.pi / 4))
    for index in range(8)
)


def button_center(index):
    """Return a button center relative to the middle of the ring."""
    return _BUTTON_CENTERS[index]


def button_at(x, y, *, spread=1.0):
    """Select only a painted button circle, leaving the gaps inert."""
    if not math.isfinite(x) or not math.isfinite(y) or not math.isfinite(spread) or spread <= 0:
        return None
    for index, (cx, cy) in enumerate(_BUTTON_CENTERS):
        if math.hypot(x - cx * spread, y - cy * spread) <= BUTTON_RADIUS:
            return index
    return None


def sector(x, y, deadzone=24):
    if not math.isfinite(x) or not math.isfinite(y) or math.hypot(x, y) < deadzone:
        return None
    # Start at twelve o'clock; proceed clockwise.
    return int((math.atan2(x, -y) + math.pi / 8) % (2 * math.pi) / (math.pi / 4))


class RingGeometry:
    """One logical layout; scale only when crossing the native widget boundary."""

    def __init__(self, scale=100):
        if type(scale) is not int or not 75 <= scale <= 150:
            raise ValueError("Ring scale must be an integer from 75 through 150.")
        self.scale = scale / 100

    def point(self, x, y):
        return x * self.scale, y * self.scale

    def logical(self, x, y):
        return x / self.scale, y / self.scale

    def size(self, value):
        return round(value * self.scale)


class RingState:
    def __init__(self, motion_gain=.45, deadzone=24):
        for name, value, low, high in (("motion gain", motion_gain, .1, 2), ("deadzone", deadzone, 8, 80)):
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Ring {name} must be finite and between {low} and {high}.")
        self.motion_gain, self.deadzone = motion_gain, deadzone
        self.visible = False
        self.held = False
        self.selected = None
        self.x = self.y = 0.0

    def open(self, held=False):
        self.visible, self.held = True, held
        self.selected = None
        self.x = self.y = 0.0

    def press(self):
        if self.visible:
            self.cancel()
            return "cancel"
        self.open(held=True)
        return "open"

    def motion(self, dx, dy):
        if not self.visible or not self.held:
            return self.selected
        if (type(dx) not in (int, float) or type(dy) not in (int, float)
                or not math.isfinite(dx) or not math.isfinite(dy)):
            return self.selected
        # Raw motion stays logical so visual size never multiplies sensitivity.
        self.x += max(-1e12, min(1e12, dx)) * self.motion_gain
        self.y += max(-1e12, min(1e12, dy)) * self.motion_gain
        radius = math.hypot(self.x, self.y)
        if radius > 155:
            self.x *= 155 / radius
            self.y *= 155 / radius
        self.selected = sector(self.x, self.y, self.deadzone)
        return self.selected

    def point(self, x, y, *, spread=1.0):
        if self.visible:
            self.selected = button_at(x, y, spread=spread)
        return self.selected

    def release(self):
        if not self.visible or not self.held:
            return None
        self.held = False
        if self.selected is not None:
            return self.choose(self.selected)
        # A short click opens a latched ring for pointer or keyboard selection.
        return None

    def choose(self, index):
        if type(index) is not int or not 0 <= index < 8 or not self.visible:
            return None
        self.cancel()
        return index

    def cancel(self):
        self.visible = self.held = False
        self.selected = None
