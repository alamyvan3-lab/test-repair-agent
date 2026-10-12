from appconfig import load, parse_int


def test_explicit_values():
    assert load(["port=9000", "workers=2", "debug=true"]) == {
        "port": 9000, "workers": 2, "debug": True}


def test_defaults_when_missing():
    assert load([]) == {"port": 8080, "workers": 4, "debug": False}


def test_bad_number_falls_back():
    assert load(["port=abc"])["port"] == 8080


def test_parse_int():
    assert parse_int("12", 0) == 12
