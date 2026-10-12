"""How user names are shown in the UI."""

from .model import User


def display_name(user: User) -> str:
    """Nickname if set and not blank, otherwise 'First Last'."""
    nickname = user.nickname.strip()
    if nickname:
        return nickname
    return f"{user.first_name} {user.last_name}".strip()


def initials(user: User) -> str:
    return (user.first_name[:1] + user.last_name[:1]).upper()
