from validators import is_valid_email, normalize_phone


def test_valid():
    assert is_valid_email("ada@example.com")
    assert is_valid_email("X.Y@sub.example.co")


def test_trailing_text_is_invalid():
    assert not is_valid_email("ada@example.com extra")
    assert not is_valid_email("ada@example.com;bob@example.com")


def test_missing_tld():
    assert not is_valid_email("ada@example")


def test_phone():
    assert normalize_phone(" +1 (555) 010-9999 ") == "+15550109999"
