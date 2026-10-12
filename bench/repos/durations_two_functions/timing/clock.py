"""Elapsed-time helpers. Timestamps are floats in seconds."""


def elapsed_ms(start: float, end: float) -> float:
    """Milliseconds between two timestamps."""
    if end < start:
        raise ValueError("end before start")
    return end - start
