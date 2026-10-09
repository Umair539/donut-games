import json
import random

import pytest

from Server.core.base import GameError
from Server.games.checkers import DRAW, KING, PLAYING, QUIET_LIMIT, SIZE, WIN, Checkers


def empty(turn=1, forced=True, **pieces):
    """A game with only the given pieces, e.g. empty(a=(5, 0, 1)) puts player 1 on (5, 0)."""
    game = Checkers(forced=forced)
    game.board = [[0] * SIZE for _ in range(SIZE)]
    for r, c, square in pieces.values():
        game.board[r][c] = square
    game.turn = turn
    return game


def go(game, *moves):
    for r, c, r2, c2 in moves:
        game.move(game.turn, [r, c], [r2, c2])


def test_new_game():
    game = Checkers()
    assert game.turn == 1 and game.status == PLAYING
    for player, rows in ((2, range(3)), (1, range(5, 8))):
        squares = [(r, c) for r in rows for c in range(SIZE) if game.board[r][c] == player]
        assert len(squares) == 12
        assert all((r + c) % 2 for r, c in squares)
    assert len(game.legal_moves()) == 7


def test_pieces_only_step_forward():
    game = empty(a=(4, 3, 1), b=(0, 1, 2))
    assert sorted(game.legal_moves()) == [(4, 3, 3, 2), (4, 3, 3, 4)]
    with pytest.raises(GameError, match="can't move there"):
        go(game, (4, 3, 5, 2))


def test_not_your_turn():
    game = Checkers()
    with pytest.raises(GameError, match="Not your turn"):
        game.move(2, [2, 1], [3, 0])


def test_bad_squares_are_rejected():
    game = Checkers()
    for bad in (None, [5], [5, 8], [5, True], "5,0"):
        with pytest.raises(GameError):
            game.apply(1, {"from": bad, "to": [4, 1]})


def test_capturing_is_compulsory():
    game = empty(a=(5, 2, 1), b=(4, 3, 2), c=(5, 6, 1), d=(0, 1, 2))
    assert game.legal_moves() == [(5, 2, 3, 4)]
    with pytest.raises(GameError, match="have to jump"):
        go(game, (5, 6, 4, 5))
    go(game, (5, 2, 3, 4))
    assert game.board[4][3] == 0
    assert game.turn == 2


def test_without_forced_jumps_you_can_move_instead():
    game = empty(forced=False, a=(5, 2, 1), b=(4, 3, 2), c=(5, 6, 1), d=(0, 1, 2))
    assert (5, 2, 3, 4) in game.legal_moves() and (5, 6, 4, 5) in game.legal_moves()
    go(game, (5, 6, 4, 5))
    assert game.turn == 2 and game.board[4][3] == 2


def test_without_forced_jumps_you_can_stop_a_multi_jump():
    game = empty(forced=False, a=(7, 0, 1), b=(6, 1, 2), c=(4, 3, 2), d=(0, 7, 2))
    go(game, (7, 0, 5, 2))
    assert game.jumping == [5, 2]
    assert game.legal_moves() == [(5, 2, 3, 4)]  # only the same donut, and only jumps
    game.apply(1, {"type": "stop"})
    assert game.turn == 2 and game.jumping is None and game.board[4][3] == 2


def test_stopping_needs_a_jump_first_and_jumps_not_forced():
    game = empty(forced=False, a=(5, 2, 1), b=(0, 1, 2))
    with pytest.raises(GameError, match="haven't jumped"):
        game.apply(1, {"type": "stop"})
    forced = empty(a=(7, 0, 1), b=(6, 1, 2), c=(4, 3, 2), d=(0, 7, 2))
    go(forced, (7, 0, 5, 2))
    with pytest.raises(GameError, match="Keep jumping"):
        forced.apply(1, {"type": "stop"})
    with pytest.raises(GameError, match="Not your turn"):
        forced.apply(2, {"type": "stop"})


def test_forced_must_be_on_or_off():
    with pytest.raises(GameError):
        Checkers.validate_settings({"forced": "yes"})
    assert Checkers.validate_settings({})["forced"] is False


def test_a_king_can_chain_jumps_backwards():
    king = 1 + KING
    game = empty(a=(3, 2, king), b=(4, 3, 2), c=(6, 3, 2), d=(0, 7, 2))
    go(game, (3, 2, 5, 4))
    assert game.jumping == [5, 4]
    go(game, (5, 4, 7, 2))
    assert game.turn == 2 and game.board[4][3] == game.board[6][3] == 0


def test_multi_jump_keeps_the_turn_with_the_same_piece():
    game = empty(a=(7, 0, 1), b=(6, 1, 2), c=(4, 3, 2), d=(5, 6, 1), e=(0, 7, 2))
    go(game, (7, 0, 5, 2))
    assert game.turn == 1 and game.jumping == [5, 2]
    with pytest.raises(GameError, match="same donut"):
        go(game, (5, 6, 4, 5))
    go(game, (5, 2, 3, 4))
    assert game.turn == 2 and game.jumping is None
    assert game.last == [[7, 0], [5, 2], [3, 4]]


def test_reaching_the_far_row_crowns_and_ends_the_move():
    # a king could jump on from (0, 3), but a piece that has just been crowned stops
    game = empty(a=(2, 5, 1), b=(1, 4, 2), c=(1, 2, 2), d=(7, 6, 2))
    go(game, (2, 5, 0, 3))
    assert game.board[0][3] == 1 + KING
    assert game.turn == 2


def test_kings_move_both_ways():
    game = empty(a=(4, 3, 1 + KING), b=(0, 1, 2))
    assert len(game.legal_moves()) == 4


def test_no_pieces_left_loses():
    game = empty(a=(5, 2, 1), b=(4, 3, 2))
    go(game, (5, 2, 3, 4))
    assert game.status == WIN and game.winner == 1 and game.over
    with pytest.raises(GameError, match="over"):
        go(game, (3, 4, 2, 3))


def test_no_moves_left_loses():
    # player 2's only piece is one row from the bottom with both squares ahead taken, and
    # player 1 can't jump it either, the squares behind it are taken too
    game = empty(a=(7, 0, 1), b=(7, 2, 1), c=(6, 1, 2), d=(5, 0, 1), e=(5, 2, 1),
                 f=(5, 4, 1))
    go(game, (5, 4, 4, 3))
    assert game.status == WIN and game.winner == 1


def test_draw_after_40_moves_each_with_kings_shuffling():
    game = empty(a=(7, 0, 1 + KING), b=(0, 7, 2 + KING))
    for i in range(QUIET_LIMIT // 2):
        if i % 2 == 0:
            go(game, (7, 0, 6, 1), (0, 7, 1, 6))
        else:
            go(game, (6, 1, 7, 0), (1, 6, 0, 7))
    assert game.status == DRAW


def test_timeout_plays_the_whole_turn():
    game = empty(a=(7, 0, 1), b=(6, 1, 2), c=(4, 3, 2), d=(0, 7, 2))
    game.timeout(1)
    assert game.turn == 2 and game.board[3][4] == 1


def test_restart_alternates_who_starts():
    game = Checkers()
    go(game, (5, 0, 4, 1))
    game.restart()
    assert game.round == 2 and game.turn == 2
    assert game.board == Checkers().board
    game.restart()
    assert game.turn == 1


def test_view_lists_moves_for_whoever_is_on():
    game = Checkers()
    assert len(game.view(2)["moves"]) == 7  # the same for both, nothing is hidden
    json.dumps(game.view(1))


@pytest.mark.parametrize("seed", range(20))
def test_random_games_end_and_survive_a_save(seed):
    rng = random.Random(seed)
    game = Checkers(forced=seed % 2 == 0)
    for _ in range(400):
        if game.over:
            break
        if game.jumping and not game.forced and rng.random() < 0.3:
            game.stop(game.turn)
        else:
            r, c, r2, c2 = rng.choice(game.legal_moves())
            game.move(game.turn, [r, c], [r2, c2])
        copy = Checkers.restore(json.loads(json.dumps(game.snapshot())))
        assert copy.snapshot() == game.snapshot()
        assert copy.legal_moves() == game.legal_moves()
    assert game.over
