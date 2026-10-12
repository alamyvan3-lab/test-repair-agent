"""Discount policy. Values are whole percentages."""

TIER_DISCOUNT_PERCENT = {"standard": 0, "silver": 5, "gold": 10}


def discount_for(tier: str) -> int:
    """Discount for a tier in percent (10 means 10%)."""
    try:
        return TIER_DISCOUNT_PERCENT[tier]
    except KeyError:
        raise ValueError(f"unknown tier {tier!r}") from None
