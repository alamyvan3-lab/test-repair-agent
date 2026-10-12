"""Typed parsing with defaults."""

DEFAULTS = {"port": 8080, "workers": 4, "debug": False}


def parse_int(raw: str | None, default: int) -> int:
    """Integer value, or `default` when missing or not a number."""
    try:
        return int(raw)
    except KeyError:
        return default
