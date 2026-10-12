def normalize_phone(raw: str) -> str:
    """Digits only, keeping a leading '+'."""
    raw = raw.strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" if raw.startswith("+") else "") + digits
