import json
import os
import secrets
import time

from Server.games import GAMES

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
CODE_LENGTH = 6
MAX_NAME_LENGTH = 16
DEFAULT_NAME = "Player"
MAX_ROOMS = 1000
RECONNECT_GRACE = 60  # seconds a player has to come back before their seat is lost
RESTORE_GRACE = 120  # the same after a restart, which takes everyone's connection down at once
LOBBY_IDLE_TIMEOUT = 10 * 60  # seconds without activity before a room is closed
GAME_IDLE_TIMEOUT = 15 * 60
SNAPSHOT_VERSION = 1  # bump when the room part of a snapshot changes shape
MAX_CHAT_LENGTH = 200
CHAT_GAP = 0.5  # seconds between one player's chat messages

# room phases
LOBBY = "lobby"
PLAYING = "playing"

# seat states sent to the clients
CONNECTED = "connected"
RECONNECTING = "reconnecting"
LEFT = "left"


class RoomError(Exception):
    """Raised when a room action is not allowed."""


def _tidy(raw):
    """No control characters, single spaces."""
    return " ".join("".join(ch for ch in raw if ch.isprintable() or ch.isspace()).split())


def clean_name(raw):
    """Tidy a name typed by a player: no control characters, single spaces, limited length."""
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise RoomError("Invalid name")
    return _tidy(raw)[:MAX_NAME_LENGTH].rstrip() or DEFAULT_NAME


class Seat:
    def __init__(self, player, name, socket):
        self.player = player  # 1..N, may be renumbered when the game starts
        self.name = name
        self.token = secrets.token_urlsafe(16)
        self.socket = socket
        self.away_since = None
        self.grace = RECONNECT_GRACE  # how long this seat is kept while away
        self.left = False
        self.last_chat = None

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
        self.deadline = None  # when the current turn runs out, if the game has a turn timer
        self.paused = False  # the turn timer waits for everyone to reconnect after a restart

    @property
    def phase(self):
        return LOBBY if self.game is None else PLAYING

    # ---- seats

    def join(self, socket, now, name=None):
        name = clean_name(name)
        if self.game is not None:
            raise RoomError("Game already started")
        for player in range(1, self.game_cls.max_players + 1):
            if player not in self.seats:
                seat = self.seats[player] = Seat(player, self._unique_name(name), socket)
                if self.host is None:
                    self.host = seat
                self.last_active = now
                return seat
        raise RoomError("Room is full")

    def _unique_name(self, name):
        """If someone here already has this name, add 2, 3, 4... until it is free."""
        taken = {seat.name.casefold() for seat in self.seats.values()}
        candidate, number = name, 1
        while candidate.casefold() in taken:
            number += 1
            suffix = f" {number}"
            candidate = name[: MAX_NAME_LENGTH - len(suffix)].rstrip() + suffix
        return candidate

    def rejoin(self, token, socket, now):
        """Put a socket back in its seat. Returns (seat, old socket or None)."""
        if not isinstance(token, str) or not token.isascii():
            raise RoomError("Could not rejoin that game")
        for seat in self.seats.values():
            if not seat.left and secrets.compare_digest(seat.token, token):
                old, seat.socket, seat.away_since = seat.socket, socket, None
                self.last_active = now
                self._resume(now)
                return seat, old
        raise RoomError("Could not rejoin that game")

    def disconnect(self, seat, socket, now):
        """Mark a seat as away, unless the socket has already been replaced."""
        if seat.socket is socket:
            seat.socket = None
            seat.away_since = now
            seat.grace = RECONNECT_GRACE

    def leave(self, seat, now=None):
        """In the lobby the seat is freed. In a game it is kept but marked as left. If it was
        the host's, the next player becomes host, and once everyone has gone the room closes."""
        seat.socket = None
        self.rematch.discard(seat.player)
        now = time.monotonic() if now is None else now
        was_here = not seat.left
        if self.game is not None:
            seat.left = True
        elif self.seats.get(seat.player) is seat:
            del self.seats[seat.player]
        present = self._present()
        if not present:
            self.closed = "Everyone left"  # and the game isn't told, nobody is left to play
            return
        if seat is self.host:
            self.host = present[0]
        if self.game is not None and was_here:
            self.game.player_left(seat.player)
            self._arm(now)  # the turn may have moved on
            self._maybe_rematch(now)  # everyone still here may already have asked
        self._resume(now)  # whoever was still awaited may have been the one who left

    def _present(self):
        """Seats that haven't left, lowest number first."""
        return sorted((s for s in self.seats.values() if not s.left), key=lambda s: s.player)

    def expire_seats(self, now):
        """Remove seats that stayed away past the grace period. Returns True if any did."""
        changed = False
        for seat in list(self.seats.values()):
            if seat.status == RECONNECTING and now - seat.away_since > seat.grace:
                self.leave(seat, now)
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
        self._arm(now)

    def action(self, seat, action, now):
        if self.game is None:
            raise RoomError("Game has not started")
        if sum(1 for s in self.seats.values() if not s.left) < self.game_cls.min_players:
            raise RoomError("Not enough players left")
        turn = getattr(self.game, "turn", None)
        self.game.apply(seat.player, action)
        self.last_active = now
        if self.game.over or getattr(self.game, "turn", None) != turn:
            self._arm(now)  # some actions, like calling cards in Switch, don't end the turn

    def chat(self, seat, text, now):
        """Check a chat line and return the message to send to everyone. Nothing is stored,
        so only players connected at the time see it."""
        if not isinstance(text, str):
            raise RoomError("Invalid message")
        text = _tidy(text)[:MAX_CHAT_LENGTH].rstrip()
        if not text:
            raise RoomError("Type a message first")
        if seat.last_chat is not None and now - seat.last_chat < CHAT_GAP:
            raise RoomError("Slow down a little")
        seat.last_chat = now
        self.last_active = now
        return {"type": "chat", "player": seat.player, "name": seat.name, "text": text}

    def _arm(self, now):
        """Restart the turn clock, or stop it if there is no timer or the game is over."""
        game = self.game
        if self.settings.get("timer") and game is not None and not game.over and not self.paused:
            self.deadline = now + self.settings["turn_seconds"]
        else:
            self.deadline = None

    def _resume(self, now):
        """After a restart, start the turn clock once everyone still in the game is back. If
        someone never comes back, their seat expires and that resumes it too."""
        if self.paused and all(s.status == CONNECTED for s in self._present()):
            self.paused = False
            self._arm(now)

    def seconds_left(self, now):
        return None if self.deadline is None else max(0.0, self.deadline - now)

    def check_timer(self, now):
        """Make a move for the player whose time ran out. Returns True if that happened.
        This doesn't count as activity, so a room where everyone walked away still expires."""
        if self.deadline is None or now < self.deadline:
            return False
        if sum(1 for s in self.seats.values() if not s.left) < self.game_cls.min_players:
            self.deadline = None  # nobody left to play against
            return False
        self.game.timeout(self.game.turn)
        self._arm(now)
        return True

    def request_rematch(self, seat, now):
        if self.game is None or not self.game.over:
            raise RoomError("Game is still going")
        if len(self._present()) < self.game_cls.min_players:
            raise RoomError("Not enough players left")
        self.rematch.add(seat.player)
        self._maybe_rematch(now)
        self.last_active = now

    def _maybe_rematch(self, now):
        """Start the next game once everyone still here has asked. Players who left are
        dropped first, and then the game is dealt fresh for whoever is left."""
        present = self._present()
        if not self.game.over or len(present) < self.game_cls.min_players:
            return
        if self.rematch != {s.player for s in present}:
            return
        self.rematch.clear()
        if len(present) == len(self.seats):
            self.game.restart()
        else:
            self.seats = {s.player: s for s in present}
            self._renumber()
            self.game = self.game_cls.create(self.settings, len(present))
        self._arm(now)

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
        timeout = LOBBY_IDLE_TIMEOUT if self.game is None else GAME_IDLE_TIMEOUT
        return now - self.last_active > timeout

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
            "host": self.host.player if self.host else None,
            "settings": self.settings,
            "min_players": self.game_cls.min_players,
            "max_players": self.game_cls.max_players,
            "players": [
                {"id": p, "name": s.name, "status": s.status}
                for p, s in sorted(self.seats.items())
            ],
            "over": game.over if game else False,
            "rematch": sorted(self.rematch),
            "seconds_left": self.seconds_left(time.monotonic()),
            "data": game.view(seat.player) if game else None,
        }

    # ---- surviving a restart

    def snapshot(self):
        """Everything needed to rebuild this room in a new process, as JSON-safe data.
        Sockets can't be kept, so everyone comes back as reconnecting."""
        game = self.game
        return {
            "code": self.code,
            "game": self.game_cls.name,
            "settings": self.settings,
            "host": self.host.player if self.host else None,
            "seats": [
                {"player": s.player, "name": s.name, "token": s.token, "left": s.left}
                for s in self.seats.values()
            ],
            "rematch": sorted(self.rematch),
            "state": None if game is None else {
                "version": self.game_cls.snapshot_version,
                "data": game.snapshot(),
            },
        }

    @classmethod
    def restore(cls, data, now):
        """Rebuild a room from snapshot(). Raises if it no longer fits this version of the
        code, e.g. the game was removed or its state changed shape."""
        game_cls = GAMES[data["game"]]
        settings = game_cls.validate_settings(data["settings"])
        room = cls(data["code"], game_cls, settings, now)
        for saved in data["seats"]:
            seat = Seat(saved["player"], saved["name"], None)
            seat.token, seat.left = saved["token"], saved["left"]
            seat.away_since, seat.grace = now, RESTORE_GRACE
            room.seats[seat.player] = seat
        room.host = room.seats.get(data["host"]) or (room._present() or [None])[0]
        room.rematch = set(data["rematch"]) & set(room.seats)
        state = data["state"]
        if state is not None:
            if state["version"] != game_cls.snapshot_version:
                raise ValueError(f"{game_cls.name} state is version {state['version']}")
            room.game = game_cls.restore(state["data"])
            room.paused = True  # nobody is connected yet, so nobody's time should run out
        return room


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
            elif room.check_timer(now) or expired:
                changed.append(room)
        return changed, removed

    def save(self, path):
        """Write every room to a file, replacing it in one step so a crash mid-write can't
        leave half a file. A room that can't be saved is left out. Returns how many were."""
        saved = []
        for room in self.rooms.values():
            if room.closed:
                continue
            try:
                saved.append(room.snapshot())
            except Exception as e:
                print(f"Not saving room {room.code}: {e!r}")
        temp = f"{path}.tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"version": SNAPSHOT_VERSION, "saved_at": time.time(), "rooms": saved}, f)
        os.replace(temp, path)
        return len(saved)

    def load(self, path, now):
        """Bring back the rooms from save(). A missing, old or broken file means starting
        empty, and a room that doesn't fit this version of the code is dropped, so a bad
        snapshot can never stop the server starting. Returns how many came back."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as e:
            print(f"Ignoring saved rooms, could not read {path}: {e!r}")
            return 0
        if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
            print("Ignoring saved rooms from a different version")
            return 0
        if time.time() - data.get("saved_at", 0) > GAME_IDLE_TIMEOUT:
            print("Ignoring saved rooms, they are too old")  # they would all have expired
            return 0
        restored = 0
        for saved in data.get("rooms", []):
            try:
                room = Room.restore(saved, now)
            except Exception as e:
                print(f"Dropping a saved room: {e!r}")
                continue
            if room.code not in self.rooms and len(self.rooms) < MAX_ROOMS:
                self.rooms[room.code] = room
                restored += 1
        return restored

    def _new_code(self):
        while True:
            code = "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))
            if code not in self.rooms:
                return code
