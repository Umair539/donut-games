import random

from Server.core.base import TIMER_SETTINGS, BaseGame, GameError, check_timer

PLAYING = "playing"
WIN = "win"
DRAW = "draw"

SIZE = 8
# a square holds 0 when empty, the player's number for their piece, or KING + it for a king
KING = 2
# moves in a row, by both players together, with no capture and no piece moved forward
# before it's a draw: 40 each, as in English draughts
QUIET_LIMIT = 80


def owner(square):
    return 0 if square == 0 else (square - 1) % 2 + 1


def is_king(square):
    return square > KING


def _is_square(value):
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < SIZE
                    for v in value))


class Checkers(BaseGame):
    """English draughts. Row 0 is the top, player 2's side, and player 1 starts at the bottom
    and goes first. Pieces only stand on the dark squares, where row + column is odd.

    A move is one step or one jump, {"from": [row, col], "to": [row, col]}. After a jump
    that can carry on, the same player moves again with the same piece, so a multi-jump is
    a few moves in a row. With forced jumps off (the default), a player may move instead of
    jumping, and may end their turn part way through a multi-jump with {"type": "stop"}.
    With them on, a player who can jump has to, all the way to the end of a multi-jump."""

    name = "checkers"
    title = "Donut Checkers"
    description = "Draughts, with donuts. Jump them all."
    min_players = 2
    max_players = 2

    settings_schema = [
        {"key": "forced", "label": "Forced jumps", "type": "bool", "default": False},
        *TIMER_SETTINGS,
    ]

    @classmethod
    def create(cls, settings, num_players):
        return cls(**settings)

    def __init__(self, forced=False, timer=False, turn_seconds=20):
        if not isinstance(forced, bool):
            raise GameError("Invalid value for forced jumps")
        check_timer(timer, turn_seconds)
        self.forced = forced
        self.round = 1
        self._reset(first=1)

    def _reset(self, first):
        self.board = [[0] * SIZE for _ in range(SIZE)]
        for r in range(SIZE):
            for c in range(SIZE):
                if (r + c) % 2:
                    if r < 3:
                        self.board[r][c] = 2
                    elif r >= SIZE - 3:
                        self.board[r][c] = 1
        self.turn = first
        self.status = PLAYING
        self.winner = None
        self.jumping = None  # the piece part way through a multi-jump, as [row, col]
        self.last = []  # the squares the last turn's piece passed through, to show it
        self.quiet = 0

    # ---- rules

    @staticmethod
    def _forward(player):
        return -1 if player == 1 else 1

    def _directions(self, square):
        player = owner(square)
        rows = (-1, 1) if is_king(square) else (self._forward(player),)
        return [(dr, dc) for dr in rows for dc in (-1, 1)]

    def _piece_moves(self, r, c):
        """(steps, jumps) the piece on (r, c) could make, each as (row, col, to_row, to_col)."""
        square = self.board[r][c]
        steps, jumps = [], []
        for dr, dc in self._directions(square):
            r1, c1, r2, c2 = r + dr, c + dc, r + 2 * dr, c + 2 * dc
            if not (0 <= r1 < SIZE and 0 <= c1 < SIZE):
                continue
            if self.board[r1][c1] == 0:
                steps.append((r, c, r1, c1))
            elif (owner(self.board[r1][c1]) not in (0, owner(square))
                  and 0 <= r2 < SIZE and 0 <= c2 < SIZE and self.board[r2][c2] == 0):
                jumps.append((r, c, r2, c2))
        return steps, jumps

    def legal_moves(self, player=None):
        """Every move the player whose turn it is can make now."""
        player = player or self.turn
        if self.status != PLAYING or player != self.turn:
            return []
        if self.jumping:
            return self._piece_moves(*self.jumping)[1]
        steps, jumps = [], []
        for r in range(SIZE):
            for c in range(SIZE):
                if owner(self.board[r][c]) == player:
                    s, j = self._piece_moves(r, c)
                    steps += s
                    jumps += j
        return (jumps or steps) if self.forced else jumps + steps

    def move(self, player, start, end):
        if self.status != PLAYING:
            raise GameError("Game is over")
        if player != self.turn:
            raise GameError("Not your turn")
        if not _is_square(start) or not _is_square(end):
            raise GameError("Invalid move")
        r, c = start
        r2, c2 = end
        legal = self.legal_moves()
        if (r, c, r2, c2) not in legal:
            if self.jumping and [r, c] != self.jumping:
                raise GameError("Keep jumping with the same donut")
            if any(abs(m[0] - m[2]) == 2 for m in legal) and abs(r - r2) == 1:
                raise GameError("You have to jump")
            raise GameError("You can't move there")

        piece = self.board[r][c]
        self.board[r][c] = 0
        self.board[r2][c2] = piece
        jumped = abs(r2 - r) == 2
        if jumped:
            self.board[(r + r2) // 2][(c + c2) // 2] = 0
        self.last = (self.last if self.jumping else [[r, c]]) + [[r2, c2]]
        self.quiet = 0 if jumped or not is_king(piece) else self.quiet + 1

        crowned = not is_king(piece) and r2 == (0 if player == 1 else SIZE - 1)
        if crowned:
            self.board[r2][c2] = piece + KING  # and that ends the move
        if jumped and not crowned and self._piece_moves(r2, c2)[1]:
            self.jumping = [r2, c2]
            return
        self._end_turn(player)

    def stop(self, player):
        """End the turn part way through a multi-jump, when jumps aren't forced."""
        if self.status != PLAYING:
            raise GameError("Game is over")
        if player != self.turn:
            raise GameError("Not your turn")
        if not self.jumping:
            raise GameError("You haven't jumped yet")
        if self.forced:
            raise GameError("Keep jumping with the same donut")
        self._end_turn(player)

    def _end_turn(self, player):
        self.jumping = None
        self.turn = 2 if player == 1 else 1
        if not self.legal_moves():  # no pieces left, or all of them stuck
            self.status = WIN
            self.winner = player
        elif self.quiet >= QUIET_LIMIT:
            self.status = DRAW

    # ---- the framework

    @property
    def over(self):
        return self.status != PLAYING

    def apply(self, player, action):
        if not isinstance(action, dict):
            raise GameError("Invalid action")
        if action.get("type") == "stop":
            return self.stop(player)
        self.move(player, action.get("from"), action.get("to"))

    def timeout(self, player):
        # play the whole turn, a multi-jump included, so the clock goes to the other player
        while self.status == PLAYING and self.turn == player:
            r, c, r2, c2 = random.choice(self.legal_moves())
            self.move(player, [r, c], [r2, c2])

    def view(self, player):
        view = self.snapshot()  # nothing is hidden in checkers
        view["moves"] = [list(m) for m in self.legal_moves()]
        return view

    def restart(self):
        self.round += 1
        self._reset(first=(self.round + 1) % 2 + 1)  # alternate who starts

    def copy(self):
        """A separate game in the same state, for a bot to think about."""
        game = Checkers.__new__(Checkers)
        game.__dict__.update(self.__dict__)
        game.board = [list(row) for row in self.board]
        game.jumping = list(self.jumping) if self.jumping else None
        game.last = [list(s) for s in self.last]
        return game

    def snapshot(self):
        return {
            "forced": self.forced,
            "board": self.board,
            "turn": self.turn,
            "status": self.status,
            "winner": self.winner,
            "round": self.round,
            "jumping": self.jumping,
            "last": self.last,
            "quiet": self.quiet,
        }

    @classmethod
    def restore(cls, data):
        game = cls(forced=data["forced"])
        if len(data["board"]) != SIZE or any(len(row) != SIZE for row in data["board"]):
            raise GameError("Saved board isn't 8 by 8")
        game.board = [list(row) for row in data["board"]]
        game.turn, game.status = data["turn"], data["status"]
        game.winner, game.round = data["winner"], data["round"]
        game.jumping, game.last = data["jumping"], [list(s) for s in data["last"]]
        game.quiet = data["quiet"]
        return game
