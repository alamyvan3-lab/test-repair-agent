from shop import discount_for, order_total


def test_standard_tier():
    assert order_total("standard", ["pen", "book"]) == 14.5


def test_gold_tier():
    assert order_total("gold", ["pen", "book"]) == 13.05


def test_silver_tier():
    assert order_total("silver", ["bag"]) == 28.5


def test_discount_policy():
    assert discount_for("gold") == 10
