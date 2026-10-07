import itertools
import random
import time
from collections import Counter

import pytest

from Server.core.base import GameError
from Server.bots.switch import (
    HeuristicBot, RandomBot, SearchBot, after_card, beliefs, breaks, can_draw, guess, moves,
    plays, unseen, way_out,
)
from Server.games.switch import Switch, connects, new_deck, rank

MAX_ACTIONS = 3000


def outcome(game, cards):
    """What a play leaves behind, which is how plays() tells them apart."""
    pending = game.pending
    for card in cards:
        pending = after_card(game, pending, card)
    flipped = sum(rank(c) == "K" for c in cards) % 2 == 1
    return tuple(sorted(cards)), cards[-1], tuple(sorted((pending or {}).items())), flipped


def every_play(game):
    """Every valid play found the slow way, for small hands."""
    hand = game.hands[game.turn]
    found = set()
    for size in range(1, len(hand) + 1):
        for order in itertools.permutations(hand, size):
            if game.can_start(order[0]) and all(connects(a, b) for a, b in zip(order, order[1:])):
                found.add(outcome(game, order))
    return found


def random_games(count, seed=0):
    """Games at every point along the way, played out by random bots."""
    for n in range(count):
        rng = random.Random(seed + n)
        game = Switch(rng.randint(2, 6), hand_size=rng.randint(1, 7), decks=rng.randint(1, 2),
                      force_play=rng.random() < 0.3, play_on=rng.random() < 0.5,
                      rng=random.Random(rng.random()))
        bot = RandomBot(random.Random(seed + n))
        for _ in range(MAX_ACTIONS):
            if game.over:
                break
            yield game
            for action in bot.choose(game, game.turn):
                game.apply(game.turn, action)


def test_every_move_is_legal():
    checked = 0
    for game in random_games(40):
        for action in moves(game):
            game.copy().apply(game.turn, action)
            checked += 1
    assert checked > 10000


def test_finds_every_play():
    checked = 0
    for game in random_games(40):
        if len(game.hands[game.turn]) <= 6:
            assert {outcome(game, p) for p in plays(game)} == every_play(game)
            checked += 1
    assert checked > 1000


def test_draw_allowed_when_the_game_allows_it():
    for game in random_games(20):
        try:
            game.copy().apply(game.turn, {"type": "draw"})
            allowed = True
        except GameError:
            allowed = False
        assert can_draw(game) == allowed


def test_huge_hand_is_cut_short():
    game = Switch(2, rng=random.Random(0))
    game.hands[game.turn] = new_deck()
    assert 0 < len(plays(game, limit=50)) <= 50


def test_guess_only_moves_hidden_cards():
    for game in random_games(10):
        player = game.turn
        copy = guess(game, player, random.Random(1))
        assert copy.hands[player] == game.hands[player]
        assert copy.discard == game.discard
        assert {p: len(h) for p, h in copy.hands.items()} == \
            {p: len(h) for p, h in game.hands.items()}
        assert len(copy.pile) == len(game.pile)
        hidden = Counter(c for p, h in copy.hands.items() if p != player for c in h)
        assert hidden + Counter(copy.pile) == Counter(unseen(game, player))


def fits(hand, slots, certain_only=True):
    """Whether the cards can be given out so each follows its slot's rules. The rules on
    slots nest, so filling the most restricted first always finds a way if there is one."""
    cards = list(hand)
    for rules in sorted(slots, key=len, reverse=True):
        rules = [r for r in rules if r[2] or not certain_only]
        match = next((c for c in cards if not any(breaks(c, r) for r in rules)), None)
        if match is None:
            return False
        cards.remove(match)
    return not cards


@pytest.mark.parametrize("decks", [1, 2])
def test_beliefs_never_rule_out_the_real_hands(decks):
    """With must-play on, a pick-up proves the player couldn't play, so the real hands always
    fit what is believed for certain. So do the cards that went back into the pile."""
    checked = 0
    for n in range(30):
        rng = random.Random(n)
        game = Switch(rng.randint(2, 5), force_play=True, decks=decks, rng=random.Random(n))
        bot = HeuristicBot(random.Random(n))
        for _ in range(MAX_ACTIONS):
            if game.over:
                break
            slots = beliefs(game, game.turn)
            for p, held in slots.items():
                assert len(held) == len(game.hands[p])
                assert fits(game.hands[p], held)
                checked += sum(1 for slot in held if any(r[2] for r in slot))
            for action in bot.choose(game, game.turn):
                game.apply(game.turn, action)
    assert checked > 1000


def test_reshuffled_cards_are_in_the_pile():
    game = Switch(2, rng=random.Random(0))
    game.hands = {1: ["5H"], 2: ["9C", "QS"]}
    game.discard = ["2H", "3H", "4H", "7S"]
    game.suit, game.turn, game.pile = "S", 2, []
    game._start_history()
    game.apply(2, {"type": "draw"})  # the discards go back in, then 2 picks one up
    assert game.history[0] == ("shuffle", ("2H", "3H", "4H"), 1)
    old = [slot for slot in beliefs(game, 1)[2] if slot]
    assert len(old) == 2  # 9♣ and Q♠ can't be any of them, the new card can
    for seed in range(30):
        hand = guess(game, 1, random.Random(seed), beliefs(game, 1)).hands[2]
        assert sum(c in ("2H", "3H", "4H") for c in hand) <= 1


def test_reshuffle_with_two_decks_leaves_the_other_copy():
    game = Switch(2, decks=2, rng=random.Random(0))
    game.hands = {1: ["5H"], 2: ["9C", "QS"]}
    game.discard = ["2H", "3H", "3H", "7S"]
    game.suit, game.turn, game.pile = "S", 2, []
    game._start_history()
    game.apply(2, {"type": "draw"})
    rules = [r for slot in beliefs(game, 1)[2] for r in slot if len(r) > 3]
    assert rules and all(r[3] == {"3H"} for r in rules)  # the other 2♥ could be in a hand


def test_pick_up_under_must_play():
    game = Switch(2, force_play=True, rng=random.Random(0))
    game.hands = {1: ["5H"], 2: ["9C", "JD", "3S"]}
    game.discard, game.suit, game.turn = ["5D"], "D", 2
    game._start_history()
    with pytest.raises(GameError):
        game.apply(2, {"type": "draw"})  # JD can go
    game.hands[2] = ["9C", "QS", "3S"]
    game._start_history()
    game.apply(2, {"type": "draw"})
    held = beliefs(game, 1)[2]
    assert sorted(map(len, held)) == [0, 1, 1, 1]  # the new card could be anything
    for seed in range(50):
        hand = guess(game, 1, random.Random(seed), beliefs(game, 1)).hands[2]
        assert sum(1 for c in hand if c[-1] == "D" or c[:-1] in ("5", "A")) <= 1


def test_wrong_hunch_is_dropped():
    """Without must-play, picking up only suggests there was nothing to play. Playing a card
    that would have gone shows otherwise."""
    game = Switch(2, rng=random.Random(0))
    game.hands = {1: ["5H", "6C"], 2: ["9D", "10D"]}
    game.discard, game.suit, game.turn = ["5D"], "D", 2
    game._start_history()
    game.apply(2, {"type": "draw"})  # holding diamonds all along
    assert [len(slot) for slot in beliefs(game, 1)[2]] == [1, 1, 0]
    game.hands[2][-1] = "KS"  # whatever was drawn
    game.apply(1, {"type": "play", "cards": ["5H"]})
    game.suit = "D"
    # one of these could be the new card, but not both
    game.apply(2, {"type": "play", "cards": ["9D", "10D"]})
    assert beliefs(game, 1)[2] == [[]]


def test_copy_is_separate():
    game = Switch(3, rng=random.Random(4))
    copy = game.copy()
    assert copy.snapshot() == game.snapshot()
    copy.apply(copy.turn, {"type": "draw"})
    assert copy.snapshot() != game.snapshot()


@pytest.mark.parametrize("hand, out", [
    (["5H"], True),
    (["AH"], False),  # can't go out on a power card
    (["5H", "6H", "6C"], True),
    (["5H", "9C"], False),
    (["QH", "3H"], True),
    (["3H", "QH"], True),  # either order is fine to check, it finds QH 3H
    (["QH", "3C"], False),
    (["5H"] * 7, False),  # too big to check
])
def test_way_out(hand, out):
    assert way_out(hand) == out


def test_search_takes_a_winning_play():
    game = Switch(2, rng=random.Random(0))
    game.hands = {1: ["6C", "6H", "5H"], 2: ["9D", "9S"]}
    game.discard, game.suit, game.turn = ["4H"], "H", 1
    game.called = {1}
    for action in SearchBot(random.Random(0), playouts=30).choose(game, 1):
        game.apply(1, action)
    assert game.winner == 1  # only 5♥ 6♥ 6♣ goes out


def test_search_scores_losing_closer_higher():
    game = Switch(3, rng=random.Random(0))
    game.hands = {1: ["5H", "6H"], 2: [], 3: ["5C", "6C", "7C", "8C", "9C"]}
    game.places, game.status, game.winner = [2], "win", 2
    bot = SearchBot(random.Random(0))
    assert bot._result(game, 2) == 1
    assert bot._result(game, 1) == 0.5  # fewer cards than the other loser
    assert bot._result(game, 3) == 0


def test_search_stops_early_on_an_obvious_move():
    game = Switch(2, rng=random.Random(0))
    game.hands = {1: ["6C", "6H", "5H"], 2: ["9D", "9S"]}
    game.discard, game.suit, game.turn = ["4H"], "H", 1
    game.called = {1}
    start = time.perf_counter()
    actions = SearchBot(random.Random(0), seconds=5).choose(game, 1)
    assert time.perf_counter() - start < 2
    assert actions[-1]["cards"] == ["5H", "6H", "6C"]  # goes out


def test_search_short_of_time_trusts_the_heuristic(monkeypatch):
    game = Switch(4, rng=random.Random(5))
    bot = SearchBot(random.Random(0), seconds=0.001)
    seen = guess(game, game.turn, random.Random(0))
    favourite = HeuristicBot(random.Random(0)).ranked(seen, game.turn)[0][1]
    monkeypatch.setattr(SearchBot, "_settled", lambda *args: False)
    assert bot.choose(game, game.turn)[-1] == favourite


def test_search_with_a_time_limit():
    game = Switch(3, rng=random.Random(2))
    bot = SearchBot(random.Random(0), seconds=0.05)
    start = time.perf_counter()
    for action in bot.choose(game, game.turn):
        game.apply(game.turn, action)
    assert time.perf_counter() - start < 1


@pytest.mark.parametrize("bot_type", [RandomBot, HeuristicBot,
                                      lambda rng: SearchBot(rng, playouts=10)])
def test_bots_finish_games(bot_type):
    for seed in range(10):
        game = Switch(4, rng=random.Random(seed))
        bot = bot_type(random.Random(seed))
        for _ in range(MAX_ACTIONS):
            if game.over:
                break
            for action in bot.choose(game, game.turn):
                game.apply(game.turn, action)
        assert game.over
