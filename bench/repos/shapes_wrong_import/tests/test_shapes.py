import math

import pytest

from shapes import circle_area, rectangle_area


def test_circle():
    assert circle_area(1) == pytest.approx(math.pi)


def test_rectangle_in_cm():
    assert rectangle_area(200, 50, "cm") == pytest.approx(1.0)


def test_negative_radius():
    with pytest.raises(ValueError):
        circle_area(-1)


def test_unknown_unit():
    with pytest.raises(ValueError):
        rectangle_area(1, 1, "ft")
