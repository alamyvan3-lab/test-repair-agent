"""Shopping cart."""

from .pricing import line_total


class Cart:
    def __init__(self, owner: str, items: list = []):
        self.owner = owner
        self.items = items

    def add(self, sku: str, price: float, qty: int = 1) -> None:
        self.items.append((sku, price, qty))

    def total(self) -> float:
        return round(sum(line_total(p, q) for _, p, q in self.items), 2)
