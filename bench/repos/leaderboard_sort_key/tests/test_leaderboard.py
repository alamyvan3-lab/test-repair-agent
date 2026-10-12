from games import Player, podium, ranking


def test_distinct_scores():
    players = [Player("amy", 5), Player("bob", 9), Player("cat", 7)]
    assert ranking(players) == ["bob", "cat", "amy"]


def test_ties_sorted_by_name():
    players = [Player("dan", 3), Player("bob", 8), Player("amy", 8)]
    assert ranking(players) == ["amy", "bob", "dan"]


def test_podium():
    players = [Player(n, s) for n, s in [("e", 1), ("d", 4), ("c", 4), ("b", 2)]]
    assert podium(players) == ["c", "d", "b"]
