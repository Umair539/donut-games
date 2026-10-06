"""The room framework with a made-up 2-4 player game that hides state per player.

This stands in for something like a card game: it needs more than two seats, a host who
starts when ready, and a different view of the game for every player.
"""
from contextlib import ExitStack

import pytest
from wshelpers import act, create, join, recv, start

from Server.core import app as server
from Server.core.base import BaseGame, GameError
from Server.core.rooms import (
    GAME_IDLE_TIMEOUT,
    LEFT,
    LOBBY_IDLE_TIMEOUT,
    RECONNECT_GRACE,
    Room,
    RoomError,
    RoomManager,
)
from Server.games import GAMES


class Counting(BaseGame):
    """Players take turns adding 1 to a total. Whoever reaches the target wins."""

    name = "counting"
    title = "Counting"
    min_players = 2
    max_players = 4
    settings_schema = [
        {"key": "target", "label": "Target", "type": "int", "min": 1, "max": 10, "default": 3},
    ]

    @classmethod
    def create(cls, settings, num_players):
        if not 1 <= settings["target"] <= 10:
            raise GameError("target must be between 1 and 10")
        return cls(settings["target"], num_players)

    def __init__(self, target, players):
        self.target = target
        self.players = players
        self.gone = set()
        self.total = 0
        self.turn = 1
        self.winner = None

    @property
    def over(self):
        return self.winner is not None

    def apply(self, player, action):
        if self.over:
            raise GameError("Game is over")
        if player != self.turn:
            raise GameError("Not your turn")
        self.total += 1
        if self.total >= self.target:
            self.winner = player
        else:
            self._next()

    def _next(self):
        while True:
            self.turn = self.turn % self.players + 1
            if self.turn not in self.gone:
                return

    def view(self, player):
        # the "secret" is only ever shown to its owner
        return {"total": self.total, "turn": self.turn, "winner": self.winner,
                "secret": player * 100}

    def restart(self):
        self.total, self.winner, self.turn = 0, None, 1

    def player_left(self, player):
        self.gone.add(player)
        if self.turn == player and not self.over:
            self._next()


@pytest.fixture(autouse=True)
def register_counting(monkeypatch):
    monkeypatch.setitem(GAMES, "counting", Counting)


def lobby_of(clients, **settings):
    """The first client hosts, the rest join. Everyone's state queue is drained."""
    host, *others = clients
    code = create(host, "counting", **settings)["code"]
    for ws in others:
        join(ws, code)
    for _ in others:
        recv(host, "state")
    for i, ws in enumerate(others):  # later joiners were announced to earlier ones
        for _ in range(len(others) - 1 - i):
            recv(ws, "state")
    return code


def test_listed_with_its_own_player_range(client):
    games = {g["name"]: g for g in client.get("/api/games").json()}
    assert (games["counting"]["min_players"], games["counting"]["max_players"]) == (2, 4)


def test_three_players_each_see_only_their_own_secret(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c])
        a.send_json({"type": "start"})
        states = [recv(ws, "state") for ws in (a, b, c)]
        assert [s["you"] for s in states] == [1, 2, 3]
        assert [s["data"]["secret"] for s in states] == [100, 200, 300]
        assert all(s["max_players"] == 4 for s in states)


def test_host_can_start_with_fewer_than_max(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        lobby_of([a, b])
        state = start(a, b)
        assert [p["id"] for p in state["players"]] == [1, 2]


def test_lobby_fills_to_the_maximum_then_rejects(client):
    with ExitStack() as stack:
        wss = [stack.enter_context(client.websocket_connect("/ws")) for _ in range(5)]
        code = lobby_of(wss[:4])
        wss[4].send_json({"type": "join", "code": code})
        assert recv(wss[4], "error")["message"] == "Room is full"


def test_players_renumbered_when_someone_leaves_the_lobby(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c])
        b.send_json({"type": "leave"})
        recv(a, "state")
        recv(c, "state")
        a.send_json({"type": "start"})
        sa, sc = recv(a, "state"), recv(c, "state")
        assert (sa["you"], sc["you"]) == (1, 2)  # no gap where player 2 left
        assert sc["data"]["secret"] == 200
        # the old connection for player 3 still works under its new number
        act(a)
        recv(a, "state")
        recv(c, "state")
        act(c)
        assert recv(a, "state")["data"]["total"] == 2


def test_game_settings_reach_the_game(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        lobby_of([a, b], target=2)
        start(a, b)
        act(a)
        recv(a, "state")
        recv(b, "state")
        act(b)
        state = recv(a, "state")
        assert state["over"] is True
        assert state["data"]["winner"] == 2


def test_player_leaving_mid_game_is_skipped(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c], target=10)
        a.send_json({"type": "start"})
        for ws in (a, b, c):
            recv(ws, "state")
        act(a)  # turn passes to player 2
        for ws in (a, b, c):
            recv(ws, "state")

        b.send_json({"type": "leave"})  # it is player 2's turn
        sa = recv(a, "state")
        assert sa["players"][1]["status"] == LEFT
        assert sa["data"]["turn"] == 3  # skipped
        recv(c, "state")

        act(c)  # two players left, which is still enough
        assert recv(a, "state")["data"]["turn"] == 1


def test_game_ends_when_too_few_players_remain(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c], target=10)
        a.send_json({"type": "start"})
        for ws in (a, b, c):
            recv(ws, "state")
        b.send_json({"type": "leave"})
        recv(a, "state")
        c.send_json({"type": "leave"})
        recv(a, "state")
        act(a)
        assert recv(a, "error")["message"] == "Not enough players left"


def finished_game(clients, target=1):
    """Everyone in a lobby, the game started and won by player 1 straight away."""
    code = lobby_of(clients, target=target)
    clients[0].send_json({"type": "start"})
    for ws in clients:
        recv(ws, "state")
    act(clients[0])
    for ws in clients:
        assert recv(ws, "state")["over"] is True
    return code


def test_rematch_drops_whoever_left(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        code = finished_game([a, b, c])
        b.send_json({"type": "leave"})
        recv(a, "state")
        recv(c, "state")
        a.send_json({"type": "rematch"})
        recv(a, "state")
        recv(c, "state")
        c.send_json({"type": "rematch"})
        sa, sc = recv(a, "state"), recv(c, "state")
        assert sa["over"] is False
        assert [p["id"] for p in sa["players"]] == [1, 2]  # b is gone, c moved up
        assert (sa["you"], sc["you"]) == (1, 2)
        assert sc["data"]["secret"] == 200
        assert sa["rematch"] == []
        assert server.rooms.get(code).game.players == 2  # a fresh game, dealt for two
        assert server.rooms.get(code).game.gone == set()


def test_rematch_starts_when_the_last_holdout_leaves(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        finished_game([a, b, c])
        for ws in (a, b):
            ws.send_json({"type": "rematch"})
            for other in (a, b, c):
                recv(other, "state")
        c.send_json({"type": "leave"})  # everyone still here has already agreed
        state = recv(a, "state")
        assert state["over"] is False
        assert [p["name"] for p in state["players"]] == ["Player", "Player 2"]


def test_no_rematch_without_enough_players(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        finished_game([a, b, c])
        for ws in (b, c):
            ws.send_json({"type": "leave"})
            recv(a, "state")
        a.send_json({"type": "rematch"})
        assert recv(a, "error")["message"] == "Not enough players left"


def test_rematch_needs_every_player(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c], target=1)
        a.send_json({"type": "start"})
        for ws in (a, b, c):
            recv(ws, "state")
        act(a)
        for ws in (a, b, c):
            recv(ws, "state")
        for ws in (a, b):
            ws.send_json({"type": "rematch"})
        for ws in (a, b, c):
            recv(ws, "state")
            state = recv(ws, "state")
        assert state["over"] is True  # c has not agreed yet
        assert state["rematch"] == [1, 2]
        c.send_json({"type": "rematch"})
        state = recv(a, "state")
        assert state["over"] is False
        assert state["data"]["total"] == 0


# room timing with a fake clock


def new_room(players=2):
    room = Room("ABCD", Counting, {"target": 3}, now=0)
    seats = [room.join(object(), now=0) for _ in range(players)]
    return room, seats


def test_lobby_seat_is_freed_after_the_grace_period():
    room, (host, guest) = new_room()
    room.disconnect(guest, guest.socket, now=10)
    assert not room.expire_seats(now=10 + RECONNECT_GRACE)
    assert room.expire_seats(now=11 + RECONNECT_GRACE)
    assert list(room.seats) == [1]
    assert room.closed is None


def test_host_going_away_hands_the_lobby_to_the_next_player():
    room, (host, guest, third) = new_room(players=3)
    room.disconnect(host, host.socket, now=10)
    assert room.expire_seats(now=11 + RECONNECT_GRACE)
    assert room.closed is None
    assert room.host is guest
    assert not room.is_dead(now=11 + RECONNECT_GRACE)
    # the new host can start, and the seats are renumbered from 1
    room.start(guest, now=12)
    assert (guest.player, third.player) == (1, 2)


def test_host_role_goes_to_the_lowest_seat_left():
    room, (host, guest, third) = new_room(players=3)
    room.leave(guest)
    room.leave(host)
    assert room.host is third
    with pytest.raises(RoomError):
        room.start(third, now=0)  # alone, so not enough players


def test_host_leaving_mid_game_passes_the_role_on():
    room, (host, guest, third) = new_room(players=3)
    room.start(host, now=0)
    room.leave(host)
    assert room.host is guest
    assert room.state_for(third)["host"] == 2


def test_lobby_closes_once_everyone_has_left():
    room, (host, guest) = new_room()
    room.leave(guest)
    room.leave(host)
    assert room.closed == "Everyone left"
    assert room.is_dead(now=0)


def test_game_closes_once_everyone_has_left():
    room, (host, guest) = new_room()
    room.start(host, now=0)
    room.leave(host)
    assert room.closed is None
    room.leave(guest)
    assert room.closed == "Everyone left"


def test_leaving_twice_in_a_game_is_harmless():
    room, (host, guest, third) = new_room(players=3)
    room.start(host, now=0)
    room.leave(guest)
    room.leave(guest)
    assert room.game.gone == {2}
    assert room.closed is None


def test_seat_in_a_game_is_kept_but_marked_left():
    room, (host, guest) = new_room()
    room.start(host, now=0)
    room.disconnect(guest, guest.socket, now=10)
    assert room.expire_seats(now=11 + RECONNECT_GRACE)
    assert guest.status == LEFT
    assert 2 in room.seats


def test_stale_disconnect_ignored():
    room, (host, guest) = new_room()
    new = object()
    old_socket = guest.socket
    room.rejoin(guest.token, new, now=5)
    room.disconnect(guest, old_socket, now=6)  # old socket closing late
    assert guest.socket is new


def test_sweep_removes_empty_and_idle_rooms():
    manager = RoomManager()
    room = manager.create(0, "counting", {})
    host = room.join(object(), now=0)
    assert manager.sweep(now=LOBBY_IDLE_TIMEOUT) == ([], [])
    assert manager.sweep(now=LOBBY_IDLE_TIMEOUT + 1) == ([], [room])

    room = manager.create(0, "counting", {})
    host = room.join(object(), now=0)
    room.disconnect(host, host.socket, now=10)
    assert manager.sweep(now=10 + RECONNECT_GRACE) == ([], [])
    assert manager.sweep(now=11 + RECONNECT_GRACE) == ([], [room])
    assert manager.rooms == {}


def test_lobby_and_game_idle_for_different_times():
    assert LOBBY_IDLE_TIMEOUT == 10 * 60 and GAME_IDLE_TIMEOUT == 15 * 60
    room, (host, guest) = new_room()
    assert not room.is_dead(now=LOBBY_IDLE_TIMEOUT)
    assert room.is_dead(now=LOBBY_IDLE_TIMEOUT + 1)
    room.start(host, now=0)
    assert not room.is_dead(now=GAME_IDLE_TIMEOUT)
    assert room.is_dead(now=GAME_IDLE_TIMEOUT + 1)


def test_every_seat_timing_out_removes_a_game():
    manager = RoomManager()
    room = manager.create(0, "counting", {})
    seats = [room.join(object(), now=0) for _ in range(2)]
    room.start(seats[0], now=0)
    for seat in seats:
        room.disconnect(seat, seat.socket, now=10)
    assert manager.sweep(now=10 + RECONNECT_GRACE) == ([], [])
    assert manager.sweep(now=11 + RECONNECT_GRACE) == ([], [room])


def test_room_codes_are_unique():
    manager = RoomManager()
    codes = {manager.create(0, "counting", {}).code for _ in range(200)}
    assert len(codes) == 200


# turn timer

def timed_room(game, **settings):
    cls = GAMES[game]
    room = Room("ABCD", cls, cls.validate_settings({"timer": True, **settings}), now=0)
    for _ in range(2):
        room.join(object(), now=0)
    room.start(room.host, now=0)
    return room


def test_connect4_timeout_drops_a_random_donut_and_passes_the_turn():
    room = timed_room("connect4", turn_seconds=15)
    assert not room.check_timer(now=14)
    assert room.check_timer(now=15)
    assert sum(1 for col in room.game.board for cell in col if cell == 1) == 1
    assert room.game.turn == 2
    assert room.seconds_left(now=15) == 15


def test_switch_timeout_picks_up_even_when_play_is_forced():
    room = timed_room("switch", force_play=True)
    player = room.game.turn
    before = len(room.game.hands[player])
    assert room.check_timer(now=20)
    assert len(room.game.hands[player]) == before + 1
    assert room.game.turn != player


def test_an_action_restarts_the_clock():
    room = timed_room("connect4")
    room.action(room.seats[1], {"col": 0}, now=10)
    assert room.seconds_left(now=10) == 20
    assert not room.check_timer(now=29)


def test_no_timer_means_no_deadline():
    cls = GAMES["connect4"]
    room = Room("ABCD", cls, cls.validate_settings({}), now=0)
    for _ in range(2):
        room.join(object(), now=0)
    room.start(room.host, now=0)
    assert room.seconds_left(now=0) is None
    assert not room.check_timer(now=9999)


def test_turn_seconds_must_be_15_to_30():
    for bad in (14, 31, True):
        with pytest.raises(GameError):
            GAMES["switch"].validate_settings({"turn_seconds": bad})


def test_calling_cards_does_not_reset_the_turn_clock():
    room = timed_room("switch")
    player = room.game.turn
    room.action(room.seats[player], {"type": "call"}, now=10)
    assert room.seconds_left(now=10) == 10


# chat

def test_chat_is_tidied_and_rate_limited():
    room, (host, guest) = new_room()
    line = room.chat(guest, "  hi\n  there ", now=1)
    assert line == {"type": "chat", "player": 2, "name": guest.name, "text": "hi there"}
    with pytest.raises(RoomError):
        room.chat(guest, "again", now=1.2)
    assert room.chat(guest, "x" * 500, now=2)["text"] == "x" * 200
    for bad in ("   ", None, 5):
        with pytest.raises(RoomError):
            room.chat(host, bad, now=10)
