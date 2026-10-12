"""Temperature statistics."""


def average(readings: list[int]) -> float:
    """Mean of the readings."""
    if not readings:
        raise ValueError("no readings")
    return sum(readings) // len(readings)


def daily_range(readings: list[int]) -> int:
    return max(readings) - min(readings)
