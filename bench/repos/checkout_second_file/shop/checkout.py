"""Order totals."""

from .catalog import price_of
from .discounts import discount_for


def order_total(customer_tier: str, skus: list[str]) -> float:
    """Sum of item prices after the tier discount, rounded to cents."""
    subtotal = sum(price_of(sku) for sku in skus)
    discount = discount_for(customer_tier)
    return round(subtotal * (1 - discount), 2)
