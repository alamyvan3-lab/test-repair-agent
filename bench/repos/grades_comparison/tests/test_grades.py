import pytest

from grades import distribution, letter


def test_clear_grades():
    assert letter(95) == "A"
    assert letter(85) == "B"
    assert letter(10) == "F"


def test_boundaries_earn_the_grade():
    assert letter(90) == "A"
    assert letter(80) == "B"
    assert letter(60) == "D"


def test_just_below_boundary():
    assert letter(89.9) == "B"
    assert letter(59.5) == "F"


def test_out_of_range():
    with pytest.raises(ValueError):
        letter(101)


def test_distribution():
    assert distribution([90, 85, 70, 10]) == {"A": 1, "B": 1, "C": 1, "D": 0, "F": 1}
