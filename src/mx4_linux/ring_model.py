"""Interaction and geometry for an eight-position Actions Ring."""

import math


def sector(x, y, deadzone=24):
    if not math.isfinite(x) or not math.isfinite(y) or math.hypot(x, y) < deadzone:
        return None
    # Start at twelve o'clock; proceed clockwise.
    return int((math.atan2(x, -y) + math.pi / 8) % (2 * math.pi) / (math.pi / 4))


class RingState:
    def __init__(self):
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
        self.x += dx * 0.45
        self.y += dy * 0.45
        radius = math.hypot(self.x, self.y)
        if radius > 155:
            self.x *= 155 / radius
            self.y *= 155 / radius
        self.selected = sector(self.x, self.y)
        return self.selected

    def point(self, x, y):
        if self.visible:
            self.selected = sector(x, y, 76) if math.hypot(x, y) <= 205 else None
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
