"""Leaderboard ordering."""

from .model import Player


def ranking(players: list[Player]) -> list[str]:
    """Names by score, highest first; equal scores ordered by name A-Z."""
    ordered = sorted(players, key=lambda p: (p.score, p.name), reverse=True)
    return [p.name for p in ordered]


def podium(players: list[Player]) -> list[str]:
    return ranking(players)[:3]
