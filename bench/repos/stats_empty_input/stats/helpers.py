def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def spread(values: list[float]) -> float:
    """max - min, or 0.0 for fewer than two values."""
    if len(values) < 2:
        return 0.0
    return max(values) - min(values)
