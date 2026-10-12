"""Unit conversion for areas."""

FACTORS = {"m": 1.0, "cm": 0.01, "mm": 0.001}


def to_square_metres(value: float, unit: str) -> float:
    """Convert an area given in unit^2 to m^2."""
    try:
        factor = FACTORS[unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r}") from None
    return value * factor * factor
