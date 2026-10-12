import pytest

from pager import page, total_pages

ITEMS = list(range(1, 11))


def test_first_page():
    assert page(ITEMS, 1, 3) == [1, 2, 3]


def test_middle_page():
    assert page(ITEMS, 2, 3) == [4, 5, 6]


def test_last_partial_page():
    assert page(ITEMS, 4, 3) == [10]


def test_past_end_is_empty():
    assert page(ITEMS, 5, 3) == []


def test_total_pages():
    assert total_pages(10, 3) == 4
    assert total_pages(9, 3) == 3
    assert total_pages(0, 3) == 0


def test_page_zero_is_invalid():
    with pytest.raises(ValueError):
        page(ITEMS, 0, 3)
