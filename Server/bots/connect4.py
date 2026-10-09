"""Computer players for Connect Donut.

SearchBot looks ahead with alpha-beta search, going a move deeper each time until its time
is up, and plays the best move from the deepest search it finished. Positions are scored by
every line of `amount` squares that only one player has donuts in: the more donuts in it,
the more it's worth. It works on its own flat copy of the board, which it changes and changes
back as it searches, as the game's own checks are far too slow to search with."""
import functools
import random
import time

WIN = 1_000_000  # less the moves it takes, so a quicker win scores higher
CHECK_EVERY = 2048  # nodes between looks at the clock


class OutOfTime(Exception):
    pass


@functools.lru_cache(maxsize=32)
def lines(cols, rows, amount):
    """Every line of `amount` squares, as square numbers (col * rows + row), and the lines
    through each square."""
    found = []
    for c in range(cols):
        for r in range(rows):
            for dc, dr in ((1, 0), (0, 1), (1, 1), (1, -1)):
                end_c, end_r = c + dc * (amount - 1), r + dr * (amount - 1)
                if 0 <= end_c < cols and 0 <= end_r < rows:
                    found.append(tuple((c + dc * i) * rows + r + dr * i for i in range(amount)))
    through = [[] for _ in range(cols * rows)]
    for n, line in enumerate(found):
        for square in line:
            through[square].append(n)
    return found, through


class RandomBot:
    """Drops in any column with room. Only for tests and comparing bots."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, game, player):
        open_cols = [c for c in range(game.cols) if game.board[c][-1] == 0]
        return [{"col": self.rng.choice(open_cols)}]


class SearchBot:
    def __init__(self, rng=None, seconds=1.0, max_depth=42):
        self.rng = rng or random.Random()
        self.seconds = seconds
        self.max_depth = max_depth

    def choose(self, game, player):
        self.cols, self.rows, self.amount = game.cols, game.rows, game.amount
        self.lines, self.through = lines(game.cols, game.rows, game.amount)
        self.worth = [0] + [4 ** k for k in range(game.amount)]  # by donuts in a line
        self.squares = [game.board[c][r] for c in range(game.cols) for r in range(game.rows)]
        self.heights = [col.index(0) if 0 in col else game.rows for col in game.board]
        self.counts = {1: [0] * len(self.lines), 2: [0] * len(self.lines)}
        for n, line in enumerate(self.lines):
            for square in line:
                if self.squares[square]:
                    self.counts[self.squares[square]][n] += 1
        self.score = sum(self._line_score(n) for n in range(len(self.lines)))
        self.left = sum(game.rows - h for h in self.heights)

        # middle columns first, as they're in more lines; ties broken at random for variety
        middle = (game.cols - 1) / 2
        self.order = sorted(range(game.cols),
                            key=lambda c: (abs(c - middle), self.rng.random()))
        self.table = {}
        self.nodes = 0
        self.deadline = time.monotonic() + self.seconds

        best = next(c for c in self.order if self.heights[c] < self.rows)
        for depth in range(1, min(self.max_depth, self.left) + 1):
            try:
                value, move = self._root(player, depth, best)
            except OutOfTime:
                break
            best = move
            if abs(value) >= WIN - 1000:  # a forced win or loss is found, looking on won't help
                break
        return [{"col": best}]

    # ---- the position, from player 1's side

    def _line_score(self, n):
        mine, theirs = self.counts[1][n], self.counts[2][n]
        if mine and theirs:
            return 0
        return self.worth[mine] - self.worth[theirs]

    def _play(self, player, col):
        """Drop a donut. Returns True if it wins."""
        square = col * self.rows + self.heights[col]
        self.heights[col] += 1
        self.left -= 1
        self.squares[square] = player
        counts = self.counts[player]
        won = False
        for n in self.through[square]:
            before = self._line_score(n)
            counts[n] += 1
            self.score += self._line_score(n) - before
            if counts[n] == self.amount:
                won = True
        return won

    def _undo(self, player, col):
        self.heights[col] -= 1
        self.left += 1
        square = col * self.rows + self.heights[col]
        self.squares[square] = 0
        counts = self.counts[player]
        for n in self.through[square]:
            before = self._line_score(n)
            counts[n] -= 1
            self.score += self._line_score(n) - before

    # ---- search

    def _moves(self, first):
        moves = [c for c in self.order if self.heights[c] < self.rows]
        if first in moves:
            moves.remove(first)
            moves.insert(0, first)
        return moves

    def _root(self, player, depth, first):
        best_value, best = -WIN * 2, first
        alpha, beta = -WIN * 2, WIN * 2
        for col in self._moves(first):
            if self._play(player, col):
                value = WIN
            else:
                value = -self._search(3 - player, depth - 1, -beta, -alpha, 1)
            self._undo(player, col)
            if value > best_value:
                best_value, best = value, col
            alpha = max(alpha, value)
        return best_value, best

    def _search(self, player, depth, alpha, beta, ply):
        """The value of the position for `player`, who is about to move."""
        self.nodes += 1
        if self.nodes % CHECK_EVERY == 0 and time.monotonic() > self.deadline:
            raise OutOfTime
        if self.left == 0:
            return 0
        if depth == 0:
            return self.score if player == 1 else -self.score

        key = (bytes(self.squares), player)
        saved = self.table.get(key)
        first = None
        if saved:
            saved_depth, low, high, first = saved
            if saved_depth >= depth:
                if low >= beta:
                    return low
                if high <= alpha:
                    return high
                alpha, beta = max(alpha, low), min(beta, high)

        start_alpha = alpha
        best_value, best = -WIN * 2, None
        for col in self._moves(first):
            if self._play(player, col):
                value = WIN - ply
            else:
                value = -self._search(3 - player, depth - 1, -beta, -alpha, ply + 1)
            self._undo(player, col)
            if value > best_value:
                best_value, best = value, col
            alpha = max(alpha, value)
            if alpha >= beta:
                break

        # what the search proved: the exact value, or only a bound on it
        low = best_value if best_value > start_alpha else -WIN * 2
        high = best_value if best_value < beta else WIN * 2
        self.table[key] = (depth, low, high, best)
        return best_value
