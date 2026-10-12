"""Letter grades from numeric scores."""

THRESHOLDS = [(90, "A"), (80, "B"), (70, "C"), (60, "D")]


def letter(score: float) -> str:
    """Letter grade; a score equal to a threshold earns that grade."""
    if not 0 <= score <= 100:
        raise ValueError(f"score out of range: {score}")
    for cutoff, grade in THRESHOLDS:
        if score > cutoff:
            return grade
    return "F"
