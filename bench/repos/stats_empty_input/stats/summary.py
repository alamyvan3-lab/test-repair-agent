"""Summary statistics."""

from .helpers import mean


def summarize(values: list[float]) -> dict:
    """count, mean, min, max. An empty list gives count 0 and None for the rest."""
    return {
        "count": len(values),
        "mean": mean(values),
        "min": min(values),
        "max": max(values),
    }
