from calendar_utils import days_in_year, is_leap_year


def test_common_leap_years():
    assert is_leap_year(2024)
    assert not is_leap_year(2023)


def test_century_rule():
    assert not is_leap_year(1900)
    assert is_leap_year(2000)


def test_days():
    assert days_in_year(2100) == 365
    assert days_in_year(2400) == 366
