"""Small, time-based transitions shared by native previews.

No frame counting: interrupted animations continue from their current position,
and a slow frame still reaches the target on time.
"""

import math
from time import monotonic


class Transition:
    def __init__(self, value=0.0, duration=0.16):
        self.start = self.target = float(value)
        self.started = 0.0
        self.duration = duration

    def value(self, now=None):
        now = monotonic() if now is None else now
        progress = min(1.0, max(0.0, (now - self.started) / self.duration))
        return self.start + (self.target - self.start) * (1 - (1 - progress) ** 3)

    def move(self, target, *, animate=True, now=None):
        now = monotonic() if now is None else now
        self.start = self.value(now) if animate else float(target)
        self.target, self.started = float(target), now

    def active(self, now=None):
        now = monotonic() if now is None else now
        return self.start != self.target and now < self.started + self.duration


class WheelMotion:
    """Finite wheel impulse with exponential slowdown, independent of frame rate."""

    def __init__(self):
        self.position = 0.0
        self.velocity = 0.0
        self.updated = None

    def impulse(self, amount):
        if math.isfinite(amount):
            self.velocity = max(-24.0, min(24.0, self.velocity + amount * 6))

    def advance(self, now, *, free_spin=False):
        if self.updated is not None:
            elapsed = max(0.0, now - self.updated)
            decay = 3.8 if free_spin else 8.0
            remaining = math.exp(-decay * elapsed)
            self.position += self.velocity * (1 - remaining) / decay
            self.velocity *= remaining
        self.updated = now
        if abs(self.velocity) < 0.025:
            # Settle the subpixel tail at the same position on every refresh rate.
            self.position += self.velocity / (3.8 if free_spin else 8.0)
            self.velocity = 0.0
        return self.velocity != 0.0

    def stop(self):
        self.velocity = 0.0
        self.updated = None
