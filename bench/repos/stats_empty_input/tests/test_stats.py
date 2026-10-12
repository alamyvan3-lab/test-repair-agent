from stats import mean, spread, summarize


def test_summary():
    assert summarize([1, 2, 3, 6]) == {"count": 4, "mean": 3, "min": 1, "max": 6}


def test_summary_of_empty_list():
    assert summarize([]) == {"count": 0, "mean": None, "min": None, "max": None}


def test_single_value():
    assert summarize([5]) == {"count": 1, "mean": 5, "min": 5, "max": 5}


def test_helpers():
    assert mean([2, 4]) == 3
    assert spread([3, 9, 4]) == 6
    assert spread([]) == 0.0
