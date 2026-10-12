"""File name helpers."""


def _base(filename: str) -> str:
    return filename.rsplit("/", 1)[-1]


def extension(filename: str) -> str:
    """Extension without the dot, lower-cased; '' if there is none."""
    base = _base(filename)
    dot = base.rfind(".")
    if dot <= 0:
        return ""
    return base[dot:].lower()


def stem(filename: str) -> str:
    """File name without directory and extension."""
    base = _base(filename)
    dot = base.rfind(".")
    return base if dot <= 0 else base[:dot]
