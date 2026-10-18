"""Finite paint motion for the ring; input targets never depend on this state."""

import math
from time import monotonic

from .motion import Transition


class OrbitMotion:
    """A selection marker that takes the shortest route around the ring.

    Keep the angle unwrapped so an interrupted move continues from the painted
    position, including when crossing twelve o'clock. A brief pointer gap fades
    the marker without losing this continuity; a fully hidden marker appears at
    the new selection directly.
    """

    def __init__(self):
        self.angle = Transition(0, duration=.18)
        self.opacity = Transition(0, duration=.10)
        self.selected = None

    def select(self, index, *, animate=True, now=None):
        if index is not None and (type(index) is not int or not 0 <= index < 8):
            raise ValueError("Orbit selection must be a ring position from 0 through 7.")
        if index == self.selected:
            return
        now = monotonic() if now is None else now
        if index is not None:
            angle = index * math.pi / 4
            current = self.angle.value(now)
            delta = (angle - current + math.pi) % math.tau - math.pi
            self.angle.move(current + delta, animate=animate and self.opacity.value(now) > .01, now=now)
        self.opacity.move(float(index is not None), animate=animate, now=now)
        self.selected = index

    def reset(self):
        self.selected = None
        self.angle.move(0, animate=False)
        self.opacity.move(0, animate=False)

    def transitions(self):
        return self.angle, self.opacity
