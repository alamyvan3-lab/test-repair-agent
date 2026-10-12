"""Pagination helpers (pages are numbered from 1)."""

from .validate import check_page_args


def total_pages(count: int, size: int) -> int:
    """Number of pages needed to show `count` items, `size` per page."""
    check_page_args(1, size)
    return (count + size - 1) // size


def page(items: list, number: int, size: int) -> list:
    """Items on page `number` (1-based)."""
    check_page_args(number, size)
    start = number * size
    return items[start:start + size]
