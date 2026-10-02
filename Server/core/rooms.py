import secrets

from Server.games import GAMES

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
CODE_LENGTH = 4
MAX_ROOMS = 1000
RECONNECT_GRACE = 60  # seconds a player has to come back before their seat is lost
IDLE_TIMEOUT = 30 * 60  # seconds without activity before a room is closed

# room phases
LOBBY = "lobby"
PLAYING = "playing"

# seat states sent to the clients
CONNECTED = "connected"
RECONNECTING = "reconnecting"
LEFT = "left"


class RoomError(Exception):
    """Raised when a room action is not allowed."""


class Seat:
    def __init__(self, player, socket):
        self.player = player  # 1..N, may be renumbered when the game starts
        self.token = secrets.token_urlsafe(16)
        self.socket = socket
        self.away_since = None
        self.left = False

    @property
    def status(self):
        if self.left:
            return LEFT
        return CONNECTED if self.socket is not None else RECONNECTING


class Room:
    """A lobby that turns into a game once the host starts it."""

    def __init__(self, code, game_cls, settings, now):
        self.code = code
        self.game_cls = game_cls
        self.settings = settings
        self.game = None  # created when the host starts
        self.seats = {}  # player number -> Seat
        self.host = None
        self.rematch = set()  # players who asked for a rematch
        self.closed = None  # reason, once the room has been shut down
        self.last_active = now

    @property
    def phase(self):
        return LOBBY if self.game is None else PLAYING

    # ---- seats

    def join(self, socket, now):
        if self.game is not None:
            raise RoomError("Game already started")
        for player in range(1, self.game_cls.max_players + 1):
            if player not in self.seats:
                seat = self.seats[player] = Seat(player, socket)
                if self.host is None:
                    self.host = seat
                self.last_active = now
                return seat
        raise RoomError("Room is full")

    def rejoin(self, token, socket, now):
        """Put a socket back in its seat. Returns (seat, old socket or None)."""
        if not isinstance(token, str) or not token.isascii():
            raise RoomError("Could not rejoin that game")
        for seat in self.seats.values():
            if not seat.left and secrets.compare_digest(seat.token, token):
                old, seat.socket, seat.away_since = seat.socket, socket, None
                self.last_active = now
                return seat, old
        raise RoomError("Could not rejoin that game")

    def disconnect(self, seat, socket, now):
        """Mark a seat as away, unless the socket has already been replaced."""
        if seat.socket is socket:
            seat.socket = None
            seat.away_since = now

    def leave(self, seat):
        """In the lobby the seat is freed (or the room closes if it was the host's). In a
        game the seat is kept but marked as left."""
        seat.socket = None
        self.rematch.discard(seat.player)
        if self.game is not None:
            seat.left = True
            self.game.player_left(seat.player)
        elif seat is self.host:
            self.closed = "The host left the game"
        elif self.seats.get(seat.player) is seat:
            del self.seats[seat.player]

    def expire_seats(self, now):
        """Remove seats that stayed away past the grace period. Returns True if any did."""
        changed = False
        for seat in list(self.seats.values()):
            if seat.status == RECONNECTING and now - seat.away_since > RECONNECT_GRACE:
                self.leave(seat)
                changed = True
        return changed

    # ---- playing

    def start(self, seat, now):
        if self.game is not None:
            raise RoomError("Game already started")
        if seat is not self.host:
            raise RoomError("Only the host can start the game")
        count = len(self.seats)
        if count < self.game_cls.min_players:
            raise RoomError(f"Need at least {self.game_cls.min_players} players")
        self._renumber()
        self.game = self.game_cls.create(self.settings, count)
        self.last_active = now

    def action(self, seat, action, now):
        if self.game is None:
            raise RoomError("Game has not started")
        if sum(1 for s in self.seats.values() if not s.left) < self.game_cls.min_players:
            raise RoomError("Not enough players left")
        self.game.apply(seat.player, action)
        self.last_active = now

    def request_rematch(self, seat, now):
        if self.game is None or not self.game.over:
            raise RoomError("Game is still going")
        if any(s.left for s in self.seats.values()):
            raise RoomError("A player has left")
        self.rematch.add(seat.player)
        if self.rematch == set(self.seats):
            self.game.restart()
            self.rematch.clear()
        self.last_active = now

    def _renumber(self):
        """Close gaps left by players who left the lobby, so players are 1..N."""
        seats = sorted(self.seats.values(), key=lambda s: s.player)
        for number, seat in enumerate(seats, 1):
            seat.player = number
        self.seats = {seat.player: seat for seat in seats}

    # ---- housekeeping

    def is_dead(self, now):
        if self.closed:
            return True
        if not any(s.status == CONNECTED for s in self.seats.values()):
            # nobody here: keep the room only while someone might still reconnect
            return all(s.left for s in self.seats.values())
        return now - self.last_active > IDLE_TIMEOUT

    def connected_seats(self):
        return [s for s in self.seats.values() if s.socket is not None]

    def state_for(self, seat):
        game = self.game
        return {
            "type": "state",
            "code": self.code,
            "game": self.game_cls.name,
            "phase": self.phase,
            "you": seat.player,
            "host": self.host.player,
            "settings": self.settings,
            "min_players": self.game_cls.min_players,
            "max_players": self.game_cls.max_players,
            "players": [{"id": p, "status": s.status} for p, s in sorted(self.seats.items())],
            "over": game.over if game else False,
            "rematch": sorted(self.rematch),
            "data": game.view(seat.player) if game else None,
        }


class RoomManager:
    def __init__(self):
        self.rooms = {}

    def create(self, now, game_name, settings):
        game_cls = GAMES.get(game_name) if isinstance(game_name, str) else None
        if game_cls is None:
            raise RoomError("Unknown game")
        if not isinstance(settings, dict):
            raise RoomError("Invalid settings")
        if len(self.rooms) >= MAX_ROOMS:
            raise RoomError("Server is full, try again later")
        settings = game_cls.validate_settings(settings)  # raises GameError if not allowed
        code = self._new_code()
        room = self.rooms[code] = Room(code, game_cls, settings, now)
        return room

    def get(self, code):
        room = self.rooms.get(str(code).strip().upper())
        if room is None:
            raise RoomError("Room not found")
        return room

    def remove(self, room):
        self.rooms.pop(room.code, None)

    def sweep(self, now):
        """Expire seats and remove dead rooms. Returns (changed rooms, removed rooms)."""
        changed, removed = [], []
        for room in list(self.rooms.values()):
            expired = room.expire_seats(now)
            if room.is_dead(now):
                self.remove(room)
                removed.append(room)
            elif expired:
                changed.append(room)
        return changed, removed

    def _new_code(self):
        while True:
            code = "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))
            if code not in self.rooms:
                return code
