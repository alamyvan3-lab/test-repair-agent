"""Invoice totals."""

from dataclasses import dataclass

from .coupons import coupon_value


@dataclass(frozen=True)
class Line:
    unit_price: float
    quantity: int = 1


def subtotal(lines: list[Line]) -> float:
    return round(sum(line.unit_price * line.quantity for line in lines), 2)


def tax(amount: float, rate_percent: float) -> float:
    return round(amount * rate_percent / 100, 2)


def total(lines: list[Line], rate_percent: float = 0, coupon: str | None = None) -> float:
    """Subtotal, minus any coupon, plus tax on the discounted amount."""
    base = subtotal(lines)
    if coupon:
        base -= coupon_value(coupon, base)
    return round(base + tax(base, rate_percent), 2)
