from text import count_words, top


def test_counts():
    assert count_words("the cat and the hat") == {"the": 2, "cat": 1, "and": 1, "hat": 1}


def test_empty():
    assert count_words("") == {}


def test_top():
    assert top("b a b c a b", 2) == [("b", 3), ("a", 2)]
