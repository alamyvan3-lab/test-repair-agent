from dataclasses import dataclass


@dataclass
class User:
    first_name: str
    last_name: str
    nickname: str | None = None
