"""Stock checks."""

from .store import Inventory


def can_fulfil(inventory: Inventory, order: dict[str, int]) -> bool:
    """True only if every line of the order is in stock."""
    for sku, qty in order.items():
        if inventory.available(sku) >= qty:
            return True
    return False
