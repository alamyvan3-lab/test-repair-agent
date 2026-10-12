import re

_WORD = re.compile(r"[a-z']+")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())
