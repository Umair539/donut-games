"""The room framework with a made-up 2-4 player game that hides state per player.

This stands in for something like a card game: it needs more than two seats, a host who
starts when ready, and a different view of the game for every player.
"""
from contextlib import ExitStack

import pytest
from wshelpers import act, create, join, recv, start

from Server.core.base import BaseGame, GameError
from Server.core.rooms import (
    IDLE_TIMEOUT,
    LEFT,
    RECONNECT_GRACE,
    Room,
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


def test_no_rematch_once_a_player_has_left(client):
    with (
        client.websocket_connect("/ws") as a,
        client.websocket_connect("/ws") as b,
        client.websocket_connect("/ws") as c,
    ):
        lobby_of([a, b, c], target=1)
        a.send_json({"type": "start"})
        for ws in (a, b, c):
            recv(ws, "state")
        act(a)  # a wins immediately
        for ws in (a, b, c):
            assert recv(ws, "state")["over"] is True
        c.send_json({"type": "leave"})
        recv(a, "state")
        a.send_json({"type": "rematch"})
        assert recv(a, "error")["message"] == "A player has left"


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


def test_host_going_away_closes_the_lobby():
    room, (host, guest) = new_room()
    room.disconnect(host, host.socket, now=10)
    assert room.expire_seats(now=11 + RECONNECT_GRACE)
    assert room.closed == "The host left the game"
    assert room.is_dead(now=11 + RECONNECT_GRACE)


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
    assert manager.sweep(now=IDLE_TIMEOUT) == ([], [])
    assert manager.sweep(now=IDLE_TIMEOUT + 1) == ([], [room])

    room = manager.create(0, "counting", {})
    host = room.join(object(), now=0)
    room.disconnect(host, host.socket, now=10)
    assert manager.sweep(now=10 + RECONNECT_GRACE) == ([], [])
    assert manager.sweep(now=11 + RECONNECT_GRACE) == ([], [room])
    assert manager.rooms == {}


def test_room_codes_are_unique():
    manager = RoomManager()
    codes = {manager.create(0, "counting", {}).code for _ in range(200)}
    assert len(codes) == 200
