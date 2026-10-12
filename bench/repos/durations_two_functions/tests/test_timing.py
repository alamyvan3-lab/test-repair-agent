import pytest

from timing import describe, elapsed_ms


def test_elapsed_ms():
    assert elapsed_ms(10.0, 11.5) == pytest.approx(1500.0)


def test_elapsed_ms_small():
    assert elapsed_ms(0.0, 0.25) == pytest.approx(250.0)


def test_describe_seconds():
    assert describe(10.0, 11.5) == "1.50 s"


def test_describe_millis():
    assert describe(0.0, 0.25) == "250 ms"


def test_end_before_start():
    with pytest.raises(ValueError):
        elapsed_ms(2.0, 1.0)
