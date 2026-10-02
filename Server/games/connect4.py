from Server.core.base import BaseGame, GameError

PLAYING = "playing"
WIN = "win"
DRAW = "draw"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Connect4(BaseGame):
    name = "connect4"
    title = "Connect Donut"
    description = "Four in a row, with donuts."
    min_players = 2
    max_players = 2

    MIN_SIZE = 4
    MAX_SIZE = 12
    MIN_AMOUNT = 3

    settings_schema = [
        {"key": "cols", "label": "Columns", "type": "int", "min": MIN_SIZE, "max": MAX_SIZE,
         "default": 7},
        {"key": "rows", "label": "Rows", "type": "int", "min": MIN_SIZE, "max": MAX_SIZE,
         "default": 6},
        {"key": "amount", "label": "Donuts in a row to win", "type": "int", "min": MIN_AMOUNT,
         "max_of": ["cols", "rows"], "default": 4},
    ]

    @classmethod
    def create(cls, settings, num_players):
        return cls(**settings)

    def __init__(self, rows=6, cols=7, amount=4):
        for name, value in (("rows", rows), ("cols", cols)):
            if not _is_int(value) or not self.MIN_SIZE <= value <= self.MAX_SIZE:
                raise GameError(f"{name} must be between {self.MIN_SIZE} and {self.MAX_SIZE}")
        max_amount = max(rows, cols)
        if not _is_int(amount) or not self.MIN_AMOUNT <= amount <= max_amount:
            raise GameError(f"amount must be between {self.MIN_AMOUNT} and {max_amount}")

        self.rows = rows
        self.cols = cols
        self.amount = amount  # how many in a row to win

        self.turn = 1  # whose turn
        self.status = PLAYING
        self.winner = None
        self.round = 1
        self.board = self._empty_board()  # board[col][row], row 0 is the bottom

    def _empty_board(self):
        return [[0] * self.rows for _ in range(self.cols)]

    def move(self, player, col):
        if self.status != PLAYING:
            raise GameError("Game is over")
        if player != self.turn:
            raise GameError("Not your turn")
        if not _is_int(col) or not 0 <= col < self.cols:
            raise GameError("Invalid column")
        if self.board[col][-1] != 0:
            raise GameError("Column is full")

        self.board[col][self.board[col].index(0)] = player
        self._check(col)
        if self.status == WIN:
            self.winner = player
        elif self.status == PLAYING:
            self.turn = 2 if self.turn == 1 else 1

    @property
    def over(self):
        return self.status != PLAYING

    def apply(self, player, action):
        if not isinstance(action, dict):
            raise GameError("Invalid action")
        self.move(player, action.get("col"))

    def view(self, player):
        return self.to_dict()  # nothing is hidden in Connect Four

    def restart(self):
        self.status = PLAYING
        self.winner = None
        self.board = self._empty_board()
        self.round += 1
        self.turn = (self.round + 1) % 2 + 1  # alternate who starts

    def to_dict(self):
        return {
            "rows": self.rows,
            "cols": self.cols,
            "amount": self.amount,
            "board": self.board,
            "turn": self.turn,
            "status": self.status,
            "winner": self.winner,
            "round": self.round,
        }

    def _check(self, col):
        lst = [i for i in self.board[col] if i != 0]  # remove zeros at end
        x, y = col, len(lst) - 1  # coordinate of last piece placed

        def diagonal(cols, rows, b, am, x, y):
            # downward diagonal
            dcol = [[x, y]]  # list of diagonal

            a, c = x, y
            while True:
                a = a + 1
                c = c - 1
                if a == cols or c == -1:
                    break
                dcol.append([a, c])

            a, c = x, y
            while True:
                a = a - 1
                c = c + 1
                if a == -1 or c == rows:
                    break
                dcol.append([a, c])

            dcol = sorted(dcol, key=lambda p: p[0])
            for i in range(len(dcol) + 1 - am):
                r = [b[dcol[i + j][0]][dcol[i + j][1]] for j in range(am)]  # list of 'am' spots
                if len(set(r)) == 1 and r[0] != 0:  # check if they are all the same
                    return True

            # upwards diagonal
            ucol = [[x, y]]  # list of diagonal

            a, c = x, y
            while True:  # forwards/upwards
                a = a + 1
                c = c + 1
                if a == cols or c == rows:
                    break
                ucol.append([a, c])

            a, c = x, y
            while True:  # backwards/downwards
                a = a - 1
                c = c - 1
                if a == -1 or c == -1:  # out of bounds
                    break
                ucol.append([a, c])

            ucol = sorted(ucol, key=lambda p: p[0])  # ascending order
            for i in range(len(ucol) + 1 - am):  # possible runs of 'am' in the diagonal
                r = [b[ucol[i + j][0]][ucol[i + j][1]] for j in range(am)]
                if len(set(r)) == 1 and r[0] != 0:
                    return True

            return False

        def row(b, cols, lst, am):
            h = len(lst) - 1  # row to check
            # for n cols, only n + 1 - 'am' placements, indexing backwards
            for i in range(1, cols + 2 - am):
                r = [b[-i - j][h] for j in range(am)]  # list of 'am' spots being checked
                if len(set(r)) == 1 and r[0] != 0:
                    return True

            return False

        def column(lst, am):
            # last 'am' pieces in the column are the same
            return len(lst) >= am and len(set(lst[-am:])) == 1

        if (
            row(self.board, self.cols, lst, self.amount)
            or column(lst, self.amount)
            or diagonal(self.cols, self.rows, self.board, self.amount, x, y)
        ):
            self.status = WIN

        elif all(c[-1] != 0 for c in self.board):  # if full and no winner: draw
            self.status = DRAW
