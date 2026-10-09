"""Computer players for Donut Checkers.

SearchBot looks ahead with alpha-beta search a whole turn at a time (a multi-jump is one
turn), going a turn deeper each time until its time is up. Where its look ahead ends in the
middle of an exchange of donuts, it carries on looking at jumps only, so it doesn't stop just
before losing one back. It works on its own flat copy of the board (square = row * 8 + col)
as the game's own checks are far too slow to search with."""
import random
import time

from Server.games.checkers import KING, QUIET_LIMIT, SIZE

WIN = 1_000_000  # less the turns it takes, so a quicker win scores higher
CHECK_EVERY = 1024  # nodes between looks at the clock
MAN, CROWN = 100, 170  # what a donut and a king are worth
QUIET_DEPTH = 8  # most turns of jumps looked at past the end of the search


class OutOfTime(Exception):
    pass


def _neighbours():
    """For each square and direction, the square next to it and the one after, or None."""
    table = {}
    for r in range(SIZE):
        for c in range(SIZE):
            for dr in (-1, 1):
                for dc in (-1, 1):
                    r1, c1, r2, c2 = r + dr, c + dc, r + 2 * dr, c + 2 * dc
                    near = r1 * SIZE + c1 if 0 <= r1 < SIZE and 0 <= c1 < SIZE else None
                    far = r2 * SIZE + c2 if 0 <= r2 < SIZE and 0 <= c2 < SIZE else None
                    table[r * SIZE + c, dr, dc] = (near, far)
    return table


NEXT = _neighbours()
CROWN_ROW = {1: 0, 2: SIZE - 1}
FORWARD = {1: -1, 2: 1}


def _square(n):
    return [n // SIZE, n % SIZE]


def _dirs(piece):
    player = (piece - 1) % 2 + 1
    rows = (-1, 1) if piece > KING else (FORWARD[player],)
    return [(dr, dc) for dr in rows for dc in (-1, 1)]


def _jumps_from(board, start, piece):
    """Squares (over, to) the piece on start can jump."""
    player = (piece - 1) % 2 + 1
    found = []
    for dr, dc in _dirs(piece):
        near, far = NEXT[start, dr, dc]
        if (far is not None and board[far] == 0 and board[near]
                and (board[near] - 1) % 2 + 1 != player):
            found.append((near, far))
    return found


def turns(board, player, forced, jumping=None):
    """Every turn the player can take, as (actions, board after, quiet) where quiet is False
    when the turn took a donut or moved an ordinary one, which restarts the draw count.
    jumping is the square of a donut part way through a multi-jump, which must go on."""
    jumps = []

    def chain(b, at, actions):
        piece = b[at]
        more = _jumps_from(b, at, piece)
        if actions and (not more or not forced):
            jumps.append((actions + ([{"type": "stop"}] if more else []), b, False))
        for over, to in more:
            after = list(b)
            after[at], after[over], after[to] = 0, 0, piece
            done = actions + [{"from": _square(at), "to": _square(to)}]
            if piece <= KING and to // SIZE == CROWN_ROW[player]:
                after[to] = piece + KING
                jumps.append((done, after, False))  # being crowned ends the move
            else:
                chain(after, to, done)

    if jumping is not None:
        chain(board, jumping, [])
        if not forced:
            jumps.append(([{"type": "stop"}], board, False))
        return jumps

    steps = []
    for at in range(SIZE * SIZE):
        piece = board[at]
        if not piece or (piece - 1) % 2 + 1 != player:
            continue
        chain(board, at, [])
        for dr, dc in _dirs(piece):
            near, _ = NEXT[at, dr, dc]
            if near is not None and board[near] == 0:
                after = list(board)
                after[at] = 0
                after[near] = piece + KING if (
                    piece <= KING and near // SIZE == CROWN_ROW[player]) else piece
                steps.append(([{"from": _square(at), "to": _square(near)}], after,
                              piece > KING))
    if forced and jumps:
        return jumps
    return jumps + steps


def evaluate(board, player):
    """The position for player: donuts and kings, how far donuts have come, and when ahead,
    a nudge towards swapping donuts off, as a lead counts for more with fewer left."""
    score = {1: 0, 2: 0}
    count = {1: 0, 2: 0}
    for at, piece in enumerate(board):
        if not piece:
            continue
        side = (piece - 1) % 2 + 1
        count[side] += 1
        row, col = divmod(at, SIZE)
        if piece > KING:
            value = CROWN + (3 if 2 <= row <= 5 and 2 <= col <= 5 else 0)
        else:
            advanced = SIZE - 1 - row if side == 1 else row
            value = MAN + 3 * advanced
            if advanced == 0:
                value += 4  # guarding the back row keeps the other side from crowning
        score[side] += value
    other = 3 - player
    diff = score[player] - score[other]
    total = count[1] + count[2]
    if diff:
        diff += (1 if diff > 0 else -1) * (24 - total) * 4
    return diff


class RandomBot:
    """Takes any turn at random. Only for tests and comparing bots."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, game, player):
        board = [sq for row in game.board for sq in row]
        jumping = game.jumping[0] * SIZE + game.jumping[1] if game.jumping else None
        return self.rng.choice(turns(board, player, game.forced, jumping))[0]


class SearchBot:
    def __init__(self, rng=None, seconds=1.0, max_depth=30):
        self.rng = rng or random.Random()
        self.seconds = seconds
        self.max_depth = max_depth

    def choose(self, game, player):
        self.forced = game.forced
        board = [sq for row in game.board for sq in row]
        jumping = game.jumping[0] * SIZE + game.jumping[1] if game.jumping else None
        options = turns(board, player, game.forced, jumping)
        if len(options) == 1:
            return options[0][0]
        self.rng.shuffle(options)  # so equal turns aren't always picked the same way

        self.table = {}
        self.nodes = 0
        self.deadline = time.monotonic() + self.seconds
        best = options[0]
        for depth in range(1, self.max_depth + 1):
            try:
                value, best = self._root(options, player, game.quiet, depth, best)
            except OutOfTime:
                break
            if abs(value) >= WIN - 1000:
                break
        return best[0]

    def _root(self, options, player, quiet, depth, first):
        options = [first] + [o for o in options if o is not first]
        best_value, best = -WIN * 2, first
        alpha, beta = -WIN * 2, WIN * 2
        for actions, after, is_quiet in options:
            left = quiet + 1 if is_quiet else 0
            value = -self._search(after, 3 - player, left, depth - 1, -beta, -alpha, 1)
            if value > best_value:
                best_value, best = value, (actions, after, is_quiet)
            alpha = max(alpha, value)
        return best_value, best

    def _search(self, board, player, quiet, depth, alpha, beta, ply):
        """The value of the position for `player`, who is about to move."""
        self.nodes += 1
        if self.nodes % CHECK_EVERY == 0 and time.monotonic() > self.deadline:
            raise OutOfTime
        if quiet >= QUIET_LIMIT:
            return 0
        if depth <= 0:
            return self._settle(board, player, alpha, beta, ply, QUIET_DEPTH)

        key = (bytes(board), player)
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

        options = turns(board, player, self.forced)
        if not options:
            return -(WIN - ply)
        if first is not None:
            options.sort(key=lambda o: o[1] != first)
        else:
            options.sort(key=lambda o: o[2])  # turns that take donuts first

        start_alpha = alpha
        best_value, best = -WIN * 2, None
        for actions, after, is_quiet in options:
            left = quiet + 1 if is_quiet else 0
            value = -self._search(after, 3 - player, left, depth - 1, -beta, -alpha, ply + 1)
            if value > best_value:
                best_value, best = value, after
            alpha = max(alpha, value)
            if alpha >= beta:
                break

        low = best_value if best_value > start_alpha else -WIN * 2
        high = best_value if best_value < beta else WIN * 2
        self.table[key] = (depth, low, high, best)
        return best_value

    def _settle(self, board, player, alpha, beta, ply, left):
        """Past the end of the search: if a jump is there to be had, look at jumps until the
        board is quiet. Without forced jumps, the player could also not jump, so the board as
        it stands is a floor on what they get."""
        self.nodes += 1
        if self.nodes % CHECK_EVERY == 0 and time.monotonic() > self.deadline:
            raise OutOfTime
        options = turns(board, player, self.forced)
        if not options:
            return -(WIN - ply)
        captures = [o for o in options if not o[2] and _took(board, o[1])]
        stand = evaluate(board, player)
        if not captures or left == 0:
            return stand
        if self.forced and len(captures) == len(options):
            best = -WIN * 2  # has to jump, so standing still isn't on offer
        else:
            best = stand
            if best >= beta:
                return best
            alpha = max(alpha, best)
        for _, after, _ in captures:
            value = -self._settle(after, 3 - player, -beta, -alpha, ply + 1, left - 1)
            if value > best:
                best = value
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best


def _took(before, after):
    return sum(1 for sq in after if sq) < sum(1 for sq in before if sq)
