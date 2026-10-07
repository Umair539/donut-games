"""Random games of Donut Cards, checking the rules after every move. Set SWITCH_GAMES to play
more of them, and SWITCH_FIRST_SEED to play different ones, e.g.
SWITCH_GAMES=5000 SWITCH_FIRST_SEED=10000 pytest tests/test_switch_random.py -s"""
import itertools
import json
import os
import random
import re
from collections import Counter

import pytest

from Server.core.base import GameError
from Server.games.switch import POWER, SKIP, SUITS, Switch, connects, new_deck, rank

GAMES = int(os.environ.get("SWITCH_GAMES", 50))
FIRST_SEED = int(os.environ.get("SWITCH_FIRST_SEED", 0))
MAX_ACTIONS = 4000
PENALTY = "called cards but didn't go out, picked up"


def watch(game):
    """Record everything the game logs, which its own log keeps only the last few of."""
    entries = []
    log = game._log

    def record(player, text):
        entries.append((player, text))
        log(player, text)

    game._log = record
    return entries


def playable(game, cards):
    return game.can_start(cards[0]) and all(connects(a, b) for a, b in zip(cards, cards[1:]))


def way_out(game, hand):
    """An order that plays the whole hand and goes out, for small hands."""
    if not hand or len(hand) > 5:
        return None
    for order in itertools.permutations(hand):
        if playable(game, order) and rank(order[-1]) not in POWER:
            return list(order)
    return None


def random_run(game, hand, rng):
    starts = [c for c in hand if game.can_start(c)]
    if not starts:
        return None
    cards = [rng.choice(starts)]
    left = list(hand)
    left.remove(cards[0])
    while left and rng.random() < 0.7:
        following = [c for c in left if connects(cards[-1], c)]
        if not following:
            break
        cards.append(rng.choice(following))
        left.remove(cards[-1])
    return cards


def picked_up(entries, player):
    """How many cards the log says the player picked up."""
    total = 0
    for who, text in entries:
        if who == player:
            total += sum(int(n) for n in re.findall(r"picked up (\d+)", text))
            total += text.count("drew a card")
    return total


def all_cards(game):
    return Counter(c for hand in game.hands.values() for c in hand) + Counter(game.pile) \
        + Counter(game.discard)


def try_invalid(game, rng):
    """A move the game must turn down without changing anything."""
    p = game.turn
    other = next(q for q in game.hands if q != p)
    hand = game.hands[p]
    missing = next(c for c in new_deck() if c not in hand)
    action = rng.choice([
        (other, {"type": "draw"}),
        (other, {"type": "call"}),
        (p, {"type": "dance"}),
        (p, "draw"),
        (p, {"type": "play", "cards": []}),
        (p, {"type": "play", "cards": [missing]}),
        (p, {"type": "play", "cards": "5S"}),
        (p, {"type": "play", "cards": [None]}),
        (p, {"type": "play", "cards": hand + hand}),
    ] + ([(p, {"type": "call"})] if game.calling else []))
    before = game.snapshot()
    with pytest.raises(GameError):
        game.apply(*action)
    assert game.snapshot() == before


def play_game(seed, stats):
    rng = random.Random(seed)
    players = rng.randint(2, 10)
    game = Switch(players, hand_size=rng.randint(1, 7), jack_penalty=rng.randint(5, 7),
                  decks=rng.randint(1, 2), force_play=rng.random() < 0.3,
                  play_on=rng.random() < 0.5, call_penalty=rng.random() < 0.7,
                  rng=random.Random(seed + 1))
    entries = watch(game)
    caller = rng.choice([0.05, 0.3, 0.7])  # how keen these players are to call

    for _ in range(MAX_ACTIONS):
        if game.over:
            stats["finished"] += 1
            return
        p = game.turn
        hand = list(game.hands[p])

        roll = rng.random()
        if roll < 0.02:  # save and load, as a server restart does
            data = json.loads(json.dumps(game.snapshot()))
            game = Switch.restore(data)
            assert game.snapshot() == data
            game.rng = random.Random(rng.random())
            entries = watch(game)
            continue
        if roll < 0.05:
            try_invalid(game, rng)
            continue
        if roll < 0.055:
            leaver = rng.choice(game._active())
            entries.clear()
            before = all_cards(game)
            game.player_left(leaver)
            assert not any(t.startswith(PENALTY) for _, t in entries)  # leaving never costs
            assert all_cards(game) == before
            assert game.over or game.turn in game._active()
            stats["left"] += 1
            continue

        # pick a move
        out = way_out(game, hand)
        if out and rng.random() < 0.85:
            kind, cards = "play", out
        elif rng.random() < 0.7:
            cards = random_run(game, hand, rng)
            kind = "play" if cards else "draw"
        else:
            kind, cards = "draw", None
        if kind == "draw" and game.force and not game.pending and any(map(game.can_start, hand)):
            with pytest.raises(GameError):  # must play if you can
                game.apply(p, {"type": "draw"})
            kind, cards = "play", random_run(game, hand, rng)
        if kind == "draw" and rng.random() < 0.1:
            kind = "timeout"

        # maybe call first, which doesn't end the turn
        left_after = [c for c in hand if c not in (cards or [])] if kind == "play" else hand
        keen = caller if len(left_after) <= 3 else caller / 5
        if not game.calling and rng.random() < keen:
            entries.clear()
            game.apply(p, {"type": "call"})
            assert game.turn == p and game.calling and game.hands[p] == hand
            assert p in game.view(p)["called"]
            assert not any(t.startswith(PENALTY) for _, t in entries)
            stats["calls"] += 1

        # the move itself, with everything needed to check it afterwards
        was_called = p in game.called
        calling = game.calling
        missed = kind != "play" and bool(game.pending) and game.pending["kind"] == SKIP
        places = list(game.places)
        entries.clear()
        if kind == "play":
            action = {"type": "play", "cards": cards}
            if rank(cards[-1]) == "A":
                action["suit"] = rng.choice(SUITS)
            game.apply(p, action)
        elif kind == "draw":
            game.apply(p, {"type": "draw"})
        else:
            game.timeout(p)

        tried = kind == "play" and len(cards) == len(hand)
        went_out = p in game.places and p not in places
        penalties = [(who, t) for who, t in entries if t.startswith(PENALTY)]
        should_pay = game.call_penalty and was_called and not missed and not tried \
            and not went_out

        assert len(penalties) == should_pay, (kind, cards, hand, was_called, missed, entries)
        assert all(who == p for who, _ in penalties)
        assert went_out == (tried and was_called and rank(cards[-1]) not in POWER)
        assert Counter(new_deck() * game.decks) == all_cards(game)
        if game.pile and not went_out:  # nothing ran short, so every pick-up landed
            played = len(cards) if kind == "play" else 0
            assert len(game.hands[p]) == len(hand) - played + picked_up(entries, p)
        if not game.over:
            assert not game.calling and game.turn in game._active()
            if not went_out:  # a call carries over a skipped turn, even one made during it
                assert (p in game.called) == (was_called or calling if missed else calling)
        for q in game.hands:
            json.dumps(game.view(q))

        stats["actions"] += 1
        stats["penalties"] += len(penalties)
        stats["outs after calling"] += went_out
        stats["calls kept through a skip"] += was_called and missed
        stats["power card go-outs"] += tried and was_called and not went_out
    stats["unfinished"] += 1


def test_random_games():
    stats = Counter()
    for seed in range(FIRST_SEED, FIRST_SEED + GAMES):
        try:
            play_game(seed, stats)
        except AssertionError as error:
            raise AssertionError(f"game with seed {seed}: {error}") from error
    print(f"\n{GAMES} games: {dict(stats)}")
    # every part of the calling rule actually came up
    for key in ("penalties", "outs after calling", "calls kept through a skip",
                "power card go-outs", "left"):
        assert stats[key], key
    assert stats["unfinished"] <= GAMES // 50
