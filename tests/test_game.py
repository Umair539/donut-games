import pytest

from Server.core.base import GameError
from Server.games.connect4 import DRAW, PLAYING, WIN, Connect4


def play(game, cols):
    """Play columns in order, alternating players."""
    for col in cols:
        game.move(game.turn, col)


def test_new_game_defaults():
    game = Connect4()
    assert (game.rows, game.cols, game.amount) == (6, 7, 4)
    assert game.turn == 1
    assert game.status == PLAYING
    assert game.board == [[0] * 6 for _ in range(7)]


def test_piece_drops_to_lowest_free_spot():
    game = Connect4()
    play(game, [3, 3])
    assert game.board[3][:3] == [1, 2, 0]
    assert game.turn == 1


def test_horizontal_win():
    game = Connect4()
    play(game, [0, 0, 1, 1, 2, 2, 3])
    assert game.status == WIN
    assert game.winner == 1


def test_vertical_win():
    game = Connect4()
    play(game, [0, 1, 0, 1, 0, 1, 0])
    assert game.status == WIN
    assert game.winner == 1


def test_upward_diagonal_win():
    game = Connect4()
    play(game, [0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3])
    assert game.status == WIN
    assert game.winner == 1


def test_downward_diagonal_win():
    game = Connect4()
    play(game, [6, 5, 5, 4, 4, 3, 4, 3, 3, 0, 3])
    assert game.status == WIN
    assert game.winner == 1


def test_player_two_can_win():
    game = Connect4()
    play(game, [6, 0, 6, 1, 5, 2, 5, 3])
    assert game.status == WIN
    assert game.winner == 2


def test_no_win_with_three_in_a_row():
    game = Connect4()
    play(game, [0, 0, 1, 1, 2])
    assert game.status == PLAYING


def test_draw():
    game = Connect4(rows=4, cols=4, amount=4)
    # columns fill as 1,2,1,2 / 2,1,2,1 alternately, so no line of four
    play(game, [0, 1, 0, 1, 1, 0, 1, 0, 2, 3, 2, 3, 3, 2, 3, 2])
    assert game.status == DRAW
    assert game.winner is None


def test_custom_board_and_amount():
    game = Connect4(rows=8, cols=8, amount=5)
    play(game, [0, 0, 1, 1, 2, 2, 3, 3])
    assert game.status == PLAYING
    play(game, [4])
    assert game.status == WIN


def test_wrong_turn_rejected():
    game = Connect4()
    with pytest.raises(GameError, match="Not your turn"):
        game.move(2, 0)


@pytest.mark.parametrize("col", [-1, 7, "3", 1.0, True, None])
def test_invalid_column_rejected(col):
    game = Connect4()
    with pytest.raises(GameError, match="Invalid column"):
        game.move(1, col)


def test_full_column_rejected():
    game = Connect4()
    play(game, [0] * 6)
    with pytest.raises(GameError, match="Column is full"):
        game.move(game.turn, 0)


def test_move_after_game_over_rejected():
    game = Connect4()
    play(game, [0, 1, 0, 1, 0, 1, 0])
    with pytest.raises(GameError, match="Game is over"):
        game.move(2, 1)


@pytest.mark.parametrize(
    "settings",
    [
        {"rows": 3},
        {"rows": 13},
        {"cols": 3},
        {"cols": "7"},
        {"amount": 2},
        {"rows": 6, "cols": 7, "amount": 8},
        {"amount": True},
    ],
)
def test_invalid_settings_rejected(settings):
    with pytest.raises(GameError):
        Connect4(**settings)


def test_restart_alternates_starting_player():
    game = Connect4()
    play(game, [0, 1, 0, 1, 0, 1, 0])
    game.restart()
    assert game.status == PLAYING
    assert game.winner is None
    assert game.round == 2
    assert game.turn == 2
    assert all(c == [0] * 6 for c in game.board)
    game.restart()
    assert game.turn == 1


def test_to_dict():
    game = Connect4(rows=5, cols=6, amount=4)
    game.move(1, 2)
    state = game.to_dict()
    assert state["board"][2][0] == 1
    assert state["turn"] == 2
    assert state["status"] == PLAYING
    assert (state["rows"], state["cols"], state["amount"]) == (5, 6, 4)
