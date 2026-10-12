from stock import Inventory, can_fulfil

INV = Inventory({"apple": 5, "pear": 1})


def test_all_in_stock():
    assert can_fulfil(INV, {"apple": 2, "pear": 1})


def test_one_line_short():
    assert not can_fulfil(INV, {"apple": 2, "pear": 3})


def test_unknown_item():
    assert not can_fulfil(INV, {"apple": 1, "kiwi": 1})


def test_empty_order():
    assert can_fulfil(INV, {})
