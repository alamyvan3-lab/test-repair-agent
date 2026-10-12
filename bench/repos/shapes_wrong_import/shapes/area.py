"""Areas of simple shapes, returned in square metres."""

import math

from .units import to_square_meters


def circle_area(radius: float, unit: str = "m") -> float:
    if radius < 0:
        raise ValueError("radius must be non-negative")
    return to_square_meters(math.pi * radius * radius, unit)


def rectangle_area(width: float, height: float, unit: str = "m") -> float:
    if width < 0 or height < 0:
        raise ValueError("sides must be non-negative")
    return to_square_meters(width * height, unit)
