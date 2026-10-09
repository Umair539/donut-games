"""The Connect Donut and Donut Checkers bots."""
import json
import random

import pytest

from Server.bots import BOTS
from Server.bots import checkers as ck
from Server.bots import connect4 as c4
from Server.core.rooms import Room
from Server.games.checkers import KING, SIZE, Checkers
from Server.games.connect4 import Connect4

# ---- Connect Donut


def drops(game, cols):
    for col in cols:
        game.move(game.turn, col)


def test_connect4_takes_a_win():
    game = Connect4()
    drops(game, [0, 6, 1, 6, 2])  # player 2 to move, player 1 threatens 3
    game.move(2, 5)
    assert c4.SearchBot(seconds=0.2).choose(game, 1) == [{"col": 3}]


def test_connect4_blocks_a_win():
    game = Connect4()
    drops(game, [0, 6, 1, 6, 2])
    assert c4.SearchBot(seconds=0.2).choose(game, 2) == [{"col": 3}]


def test_connect4_sees_a_double_threat_coming():
    # player 1 has _ X X _ on the bottom row with both ends open: 1 or 4 must be taken now,
    # or player 1 makes four with whichever end is left
    game = Connect4()
    drops(game, [2, 2, 3, 3])
    assert c4.SearchBot(seconds=0.5).choose(game, 1)[0]["col"] in (1, 4)


@pytest.mark.parametrize("rows, cols, amount", [(4, 4, 3), (12, 12, 5), (6, 7, 6)])
def test_connect4_any_board_size(rows, cols, amount):
    game = Connect4(rows=rows, cols=cols, amount=amount)
    bot = c4.SearchBot(rng=random.Random(1), seconds=0.05)
    while not game.over:
        game.apply(game.turn, bot.choose(game.copy(), game.turn)[0])


def test_connect4_beats_random():
    wins = 0
    for seed in range(6):
        game = Connect4()
        bots = {1: c4.SearchBot(random.Random(seed), seconds=0.05),
                2: c4.RandomBot(random.Random(seed))}
        if seed % 2:
            bots = {1: bots[2], 2: bots[1]}
        while not game.over:
            game.apply(game.turn, bots[game.turn].choose(game.copy(), game.turn)[0])
        wins += isinstance(bots.get(game.winner), c4.SearchBot)
    assert wins == 6


def test_connect4_copy_is_separate():
    game = Connect4()
    copy = game.copy()
    copy.move(1, 0)
    assert game.board[0][0] == 0 and game.turn == 1


# ---- Donut Checkers


def every_turn(game):
    """Every whole turn the game allows, worked out through the game itself, as the actions
    in JSON and the board it leaves."""
    found = set()
    player = game.turn

    def go(g, actions):
        if g.jumping and not g.forced:
            stopped = g.copy()
            stopped.stop(player)
            found.add((json.dumps(actions + [{"type": "stop"}]), str(stopped.board)))
        for r, c, r2, c2 in g.legal_moves():
            after = g.copy()
            move = {"from": [r, c], "to": [r2, c2]}
            after.move(player, [r, c], [r2, c2])
            if after.jumping and after.turn == player:
                go(after, actions + [move])
            else:
                found.add((json.dumps(actions + [move]), str(after.board)))

    go(game, [])
    return found


def bot_turns(game):
    board = [sq for row in game.board for sq in row]
    jumping = game.jumping[0] * SIZE + game.jumping[1] if game.jumping else None
    return {(json.dumps(actions), str([after[r * SIZE:(r + 1) * SIZE] for r in range(SIZE)]))
            for actions, after, _ in ck.turns(board, game.turn, game.forced, jumping)}


@pytest.mark.parametrize("forced", [True, False])
@pytest.mark.parametrize("seed", range(8))
def test_checkers_bot_knows_the_rules(forced, seed):
    """The bot's own quick move list is exactly the game's, in every position of random
    games, multi-jumps, crowning and stopping part way included."""
    rng = random.Random(seed)
    game = Checkers(forced=forced)
    while not game.over:
        assert bot_turns(game) == every_turn(game)
        actions = json.loads(rng.choice(sorted(every_turn(game)))[0])
        for action in actions:
            game.apply(game.turn, action)


def checkers_from(rows, forced=True, turn=1):
    """A game from a picture of the board: . empty, b and p donuts, B and P kings."""
    game = Checkers(forced=forced)
    marks = {".": 0, "b": 1, "p": 2, "B": 1 + KING, "P": 2 + KING}
    game.board = [[marks[ch] for ch in row.split()] for row in rows]
    game.turn = turn
    return game


def test_checkers_takes_the_double_jump():
    game = checkers_from([
        ". . . . . . . p",
        ". . . . . . . .",
        ". . . . . . . .",
        ". . . . . . . .",
        ". . . p . . . .",
        ". . . . . . . .",
        ". p . . . . . .",
        "b . . . . . . .",
    ], forced=False)
    # stopping after the first jump is allowed, but taking both is clearly better
    actions = ck.SearchBot(seconds=0.3).choose(game.copy(), 1)
    assert actions == [{"from": [7, 0], "to": [5, 2]}, {"from": [5, 2], "to": [3, 4]}]


def test_checkers_doesnt_walk_into_a_jump():
    # moving c3 to b4 or d4 hands pink a free donut; the bot should move the other one
    game = checkers_from([
        ". . . . . . . .",
        ". . . . . . . .",
        ". . . . . . . .",
        ". . p . . . . .",
        ". . . . . . . .",
        ". . b . . . . .",
        ". . . . . . . .",
        ". . . . . . b .",
    ])
    for seed in range(5):
        actions = ck.SearchBot(rng=random.Random(seed), seconds=0.2).choose(game.copy(), 1)
        assert actions[0]["from"] == [7, 6]


def test_checkers_finishes_a_turn_already_part_way_through_a_jump():
    game = checkers_from([
        ". . . . . . . p",
        ". . . . . . . .",
        ". . . . . . . .",
        ". . . . . . . .",
        ". . . p . . . .",
        ". . . . . . . .",
        ". p . . . . . .",
        "b . . . . . . .",
    ])
    game.move(1, [7, 0], [5, 2])
    actions = ck.SearchBot(seconds=0.2).choose(game.copy(), 1)
    assert actions == [{"from": [5, 2], "to": [3, 4]}]


@pytest.mark.parametrize("forced", [True, False])
def test_checkers_beats_random(forced):
    wins = 0
    for seed in range(4):
        game = Checkers(forced=forced)
        bots = {1: ck.SearchBot(random.Random(seed), seconds=0.03),
                2: ck.RandomBot(random.Random(seed))}
        if seed % 2:
            bots = {1: bots[2], 2: bots[1]}
        while not game.over:
            for action in bots[game.turn].choose(game.copy(), game.turn):
                game.apply(game.turn, action)
        wins += isinstance(bots.get(game.winner), ck.SearchBot)
    assert wins == 4


def test_checkers_copy_is_separate():
    game = Checkers()
    copy = game.copy()
    copy.move(1, [5, 0], [4, 1])
    assert game.board[5][0] == 1 and game.turn == 1 and game.last == []


# ---- in a room


@pytest.mark.parametrize("name, cls", [("connect4", Connect4), ("checkers", Checkers)])
def test_a_bot_plays_its_turn_in_a_room(monkeypatch, name, cls):
    quick = {"connect4": c4.SearchBot, "checkers": ck.SearchBot}[name]
    monkeypatch.setitem(BOTS, name, {"hard": lambda rng: quick(rng, seconds=0.05)})
    room = Room("ABCDEF", cls, cls.validate_settings({}), now=0)
    host = room.join(None, 0, "Ann")
    room.add_bot(host, "hard", 0)
    room.start(host, now=0)
    first = room.game.turn
    if first == 1:
        action = {"col": 3} if name == "connect4" else {"from": [5, 0], "to": [4, 1]}
        room.action(host, action, now=1)
    seat, game, due, moves = room.bot_ready()
    assert seat.name == "Bot"
    assert room.bot_move(seat, seat.brain.choose(game, seat.player), moves, due)
    assert room.game.turn == 1
