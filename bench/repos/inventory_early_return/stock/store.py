class Inventory:
    def __init__(self, levels: dict[str, int]):
        self._levels = dict(levels)

    def available(self, sku: str) -> int:
        return self._levels.get(sku, 0)
