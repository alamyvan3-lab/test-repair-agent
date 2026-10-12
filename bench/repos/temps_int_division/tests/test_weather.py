import pytest

from weather import average, c_to_f, daily_range


def test_average_whole():
    assert average([20, 22]) == 21


def test_average_fractional():
    assert average([20, 21]) == 20.5
    assert average([10, 10, 11]) == pytest.approx(10.333, abs=1e-3)


def test_average_in_fahrenheit():
    assert c_to_f(average([0, 1])) == pytest.approx(32.9)


def test_range():
    assert daily_range([3, 9, 4]) == 6
