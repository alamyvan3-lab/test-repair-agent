"""Coupon codes: a percentage off, or a fixed amount off."""

COUPONS = {"SAVE10": ("percent", 10), "FIVEOFF": ("fixed", 5.0)}


def coupon_value(code: str, amount: float) -> float:
    """Money taken off `amount` by `code`. Never more than the amount itself."""
    try:
        kind, value = COUPONS[code.upper()]
    except KeyError:
        raise ValueError(f"unknown coupon {code!r}") from None
    if kind == "percent":
        return round(amount * value / 100, 2)
    return value
