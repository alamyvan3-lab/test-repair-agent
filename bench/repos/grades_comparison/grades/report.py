from collections import Counter

from .scale import letter


def distribution(scores: list[float]) -> dict[str, int]:
    """Count of each letter grade in A-F order, zeros included."""
    counts = Counter(letter(s) for s in scores)
    return {g: counts.get(g, 0) for g in "ABCDF"}
