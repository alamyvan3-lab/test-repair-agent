from users import User, display_name, initials


def test_nickname():
    assert display_name(User("Ada", "Lovelace", "countess")) == "countess"


def test_blank_nickname():
    assert display_name(User("Ada", "Lovelace", "  ")) == "Ada Lovelace"


def test_no_nickname():
    assert display_name(User("Alan", "Turing")) == "Alan Turing"


def test_initials():
    assert initials(User("grace", "hopper")) == "GH"
