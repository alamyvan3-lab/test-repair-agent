from .clock import elapsed_ms


def describe(start: float, end: float) -> str:
    """Human-readable duration: '1.50 s' from one second up, else '250 ms'."""
    ms = elapsed_ms(start, end)
    if ms >= 1:
        return f"{ms:.2f} s"
    return f"{ms * 1000:.0f} ms"
