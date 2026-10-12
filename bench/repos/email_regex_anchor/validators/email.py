import re

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")


def is_valid_email(address: str) -> bool:
    """The whole string must be one address; any surrounding text makes it invalid."""
    return _EMAIL.match(address.lower()) is not None
