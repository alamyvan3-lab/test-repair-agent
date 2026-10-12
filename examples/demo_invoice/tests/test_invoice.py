import pytest

from invoice import Line, total


def test_plain_total():
    assert total([Line(10.0, 2), Line(5.0)]) == 25.0


def test_tax():
    assert total([Line(100.0)], rate_percent=20) == 120.0


def test_percent_coupon():
    assert total([Line(50.0)], coupon="save10") == 45.0


def test_fixed_coupon():
    assert total([Line(20.0)], coupon="FIVEOFF") == 15.0


def test_fixed_coupon_never_makes_total_negative():
    assert total([Line(3.0)], coupon="FIVEOFF") == 0.0


def test_fixed_coupon_capped_before_tax():
    assert total([Line(4.0)], rate_percent=10, coupon="FIVEOFF") == 0.0


def test_unknown_coupon():
    with pytest.raises(ValueError):
        total([Line(1.0)], coupon="NOPE")
