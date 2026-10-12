PRICES = {"pen": 2.5, "book": 12.0, "bag": 30.0}


def price_of(sku: str) -> float:
    try:
        return PRICES[sku]
    except KeyError:
        raise ValueError(f"unknown sku {sku!r}") from None
