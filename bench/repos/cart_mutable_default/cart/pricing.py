def line_total(price: float, qty: int) -> float:
    if qty < 0:
        raise ValueError("quantity must be non-negative")
    return price * qty
