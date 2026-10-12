from cart import Cart


def test_add_and_total():
    cart = Cart("ann")
    cart.add("pen", 1.5, 2)
    cart.add("pad", 3.0)
    assert cart.total() == 6.0


def test_new_carts_are_independent():
    a = Cart("ann")
    a.add("pen", 1.5)
    b = Cart("bob")
    assert b.items == []
    assert b.total() == 0


def test_initial_items():
    cart = Cart("cy", [("mug", 4.0, 1)])
    assert cart.total() == 4.0
