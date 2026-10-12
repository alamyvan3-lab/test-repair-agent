"""Word frequency counting."""

from .tokenize import words


def count_words(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for word in words(text):
        counts[word] += 1
    return counts


def top(text: str, n: int) -> list[tuple[str, int]]:
    """n most common words; ties broken alphabetically."""
    counts = count_words(text)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
