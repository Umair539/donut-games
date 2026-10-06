"""Saving rooms and bringing them back, so a server restart (a deploy) doesn't end games."""
import json
import random
import time

import pytest
from fastapi.testclient import TestClient
from wshelpers import act, create, join, recv, start

from Server.core import app as server
from Server.core.base import BaseGame, GameError
from Server.core.rooms import (
    GAME_IDLE_TIMEOUT,
    LEFT,
    RECONNECT_GRACE,
    RECONNECTING,
    RESTORE_GRACE,
    SNAPSHOT_VERSION,
    Room,
    RoomManager,
)
from Server.games import GAMES
from Server.games.connect4 import Connect4
from Server.games.switch import Switch


def through_json(data):
    """What actually survives a trip to the file."""
    return json.loads(json.dumps(data))


def random_switch_moves(game, rng, moves):
    """Play legal-ish moves: the first card that can start, else draw."""
    for _ in range(moves):
        if game.over:
            return
        player = game.turn
        hand = game.hands[player]
        card = next((c for c in hand if game.can_start(c)), None)
        try:
            if card is not None and rng.random() < 0.8:
                game.apply(player, {"type": "play", "cards": [card], "suit": rng.choice("SHDC")})
            else:
                if rng.random() < 0.2:
                    game.apply(player, {"type": "call"})
                game.apply(player, {"type": "draw"})
        except GameError:
            game.timeout(player)


# ---- games


def test_connect4_round_trip():
    game = Connect4(rows=5, cols=8, amount=3)
    for col in (0, 1, 1, 7, 2):
        game.move(game.turn, col)
    game.round = 4
    copy = Connect4.restore(through_json(game.snapshot()))
    assert copy.snapshot() == game.snapshot()
    copy.move(copy.turn, 3)  # and it carries on
    game.move(game.turn, 3)
    assert copy.snapshot() == game.snapshot()


def test_connect4_finished_game_round_trip():
    game = Connect4()
    for col in (0, 1, 0, 1, 0, 1, 0):
        game.move(game.turn, col)
    copy = Connect4.restore(through_json(game.snapshot()))
    assert copy.over and copy.winner == 1


def test_connect4_rejects_a_board_of_the_wrong_size():
    data = Connect4().snapshot()
    data["board"] = data["board"][:-1]
    with pytest.raises(GameError):
        Connect4.restore(data)


@pytest.mark.parametrize("seed", range(30))
def test_switch_round_trip_anywhere_in_a_game(seed):
    rng = random.Random(seed)
    players = rng.randint(2, 10)
    game = Switch(players, hand_size=rng.randint(1, 7), play_on=rng.random() < 0.5,
                  force_play=rng.random() < 0.5, rng=random.Random(seed))
    random_switch_moves(game, rng, rng.randint(0, 120))
    if not game.over and len(game._active()) > 2 and rng.random() < 0.5:
        game.player_left(rng.choice(game._active()))
    copy = Switch.restore(through_json(game.snapshot()))
    assert copy.snapshot() == game.snapshot()
    for p in range(1, players + 1):
        assert copy.view(p) == game.view(p)
    # the copy plays on by the same rules
    random_switch_moves(copy, random.Random(seed), 200)
    held = [c for hand in copy.hands.values() for c in hand]
    assert len(held) + len(copy.pile) + len(copy.discard) == 52 * copy.decks


def test_switch_keeps_types_through_json():
    game = Switch(3, rng=random.Random(0))
    game.player_left(2)
    game.called = {1}
    game.pending = {"kind": "two", "count": 4}
    copy = Switch.restore(through_json(game.snapshot()))
    assert set(copy.hands) == {1, 2, 3}  # int keys, not "1"
    assert copy.gone == {2} and copy.called == {1}
    assert copy.log.maxlen == game.log.maxlen
    assert copy.pending == {"kind": "two", "count": 4}


def test_switch_rejects_hands_that_dont_match_the_players():
    data = Switch(3).snapshot()
    data["hands"] = data["hands"][:2]
    with pytest.raises(GameError):
        Switch.restore(data)


def test_every_game_can_be_saved():
    # a new game without snapshot() would quietly lose its rooms on every deploy
    for game_cls in GAMES.values():
        game = game_cls.create(game_cls.validate_settings({}), game_cls.min_players)
        restored = game_cls.restore(through_json(game.snapshot()))
        assert restored.snapshot() == game.snapshot()


# ---- rooms


def room_of(game="switch", players=3, **settings):
    cls = GAMES[game]
    room = Room("SAVE42", cls, cls.validate_settings(settings), now=0)
    names = ["Ann", "Bob", "Cat", "Dan"]
    seats = [room.join(object(), now=0, name=names[i]) for i in range(players)]
    return room, seats


def restored(room, now=1000):
    return Room.restore(through_json(room.snapshot()), now)


def test_lobby_round_trip():
    room, seats = room_of("connect4", players=1, rows=5)
    copy = restored(room)
    assert copy.code == "SAVE42" and copy.phase == "lobby"
    assert copy.settings == room.settings
    assert [(s.player, s.name, s.token) for s in copy.seats.values()] == [
        (1, "Ann", seats[0].token)]
    assert copy.host is copy.seats[1]
    assert copy.game is None


def test_game_round_trip_keeps_who_left_and_who_voted():
    room, (a, b, c) = room_of(players=3)
    room.start(a, now=0)
    room.leave(b)
    room.game._end()
    room.request_rematch(a, now=1)
    copy = restored(room)
    assert copy.phase == "playing" and copy.game.over
    assert [s.status for s in copy.seats.values()] == [RECONNECTING, LEFT, RECONNECTING]
    assert copy.rematch == {1}
    assert copy.host.player == 1
    assert copy.game.snapshot() == room.game.snapshot()
    # c agreeing now starts the next game for the two who stayed
    copy.request_rematch(copy.seats[3], now=1001)
    assert not copy.game.over and copy.game.num_players == 2


def test_restored_seats_can_rejoin_with_their_tokens():
    room, (a, b, c) = room_of()
    room.start(a, now=0)
    copy = restored(room)
    socket = object()
    seat, old = copy.rejoin(b.token, socket, now=1001)
    assert seat.player == 2 and seat.name == "Bob" and old is None
    assert seat.status == "connected"
    assert copy.state_for(seat)["data"]["hand"] == room.game.view(2)["hand"]


def test_left_seat_still_cannot_rejoin_after_restore():
    room, (a, b, c) = room_of()
    room.start(a, now=0)
    room.leave(b)
    copy = restored(room)
    with pytest.raises(Exception, match="Could not rejoin"):
        copy.rejoin(b.token, object(), now=1001)


def test_restored_seats_get_longer_to_come_back():
    room, seats = room_of()
    room.start(seats[0], now=0)
    copy = restored(room, now=1000)
    assert RESTORE_GRACE > RECONNECT_GRACE
    assert not copy.expire_seats(now=1000 + RESTORE_GRACE)
    copy.rejoin(seats[0].token, object(), now=1000 + RESTORE_GRACE)  # one makes it back
    assert copy.expire_seats(now=1001 + RESTORE_GRACE)  # the others don't
    assert [s.status for s in copy.seats.values()] == ["connected", LEFT, LEFT]


def test_grace_goes_back_to_normal_after_the_next_disconnect():
    room, seats = room_of("connect4", players=2)
    copy = restored(room, now=1000)
    seat = copy.seats[2]
    socket = object()
    copy.rejoin(seat.token, socket, now=1001)
    copy.disconnect(seat, socket, now=1002)
    assert copy.expire_seats(now=1003 + RECONNECT_GRACE)


def test_restored_room_gets_a_fresh_idle_clock():
    room, seats = room_of()
    room.start(seats[0], now=0)
    copy = restored(room, now=5000)
    assert copy.last_active == 5000


def test_restored_turn_timer_waits_for_everyone():
    room, (a, b) = room_of("connect4", players=2, timer=True, turn_seconds=15)
    room.start(a, now=0)
    copy = restored(room, now=1000)
    assert copy.seconds_left(now=1000) is None
    assert not copy.check_timer(now=1100)  # a slow restart doesn't cost anyone their turn
    copy.rejoin(a.token, object(), now=1100)
    assert copy.seconds_left(now=1100) is None  # still waiting for b
    copy.rejoin(b.token, object(), now=1110)
    assert copy.seconds_left(now=1110) == 15  # a full turn once both are back
    assert not copy.check_timer(now=1124)
    assert copy.check_timer(now=1125)


def test_restored_turn_timer_resumes_when_a_missing_player_expires():
    room, (a, b, c) = room_of("switch", players=3, timer=True)
    room.start(a, now=0)
    copy = restored(room, now=1000)
    copy.rejoin(a.token, object(), now=1001)
    copy.rejoin(b.token, object(), now=1001)
    assert copy.seconds_left(now=1001) is None
    copy.expire_seats(now=1001 + RESTORE_GRACE)  # c never comes back
    assert copy.seats[3].status == LEFT
    assert copy.seconds_left(now=1001 + RESTORE_GRACE) == 20


def test_moves_while_paused_dont_start_the_timer():
    room, (a, b) = room_of("connect4", players=2, timer=True)
    room.start(a, now=0)
    copy = restored(room, now=1000)
    mover = copy.seats[copy.game.turn]
    copy.rejoin(mover.token, object(), now=1001)
    copy.action(mover, {"col": 0}, now=1002)
    assert copy.seconds_left(now=1002) is None
    other = copy.seats[copy.game.turn]
    copy.rejoin(other.token, object(), now=1003)
    assert copy.seconds_left(now=1003) == 20


def test_lobby_and_untimed_games_are_unaffected_by_the_pause():
    room, (a, b) = room_of("connect4", players=2)
    copy = restored(room, now=1000)
    copy.rejoin(a.token, object(), now=1001)
    copy.rejoin(b.token, object(), now=1001)
    copy.start(copy.seats[1], now=1002)
    assert copy.seconds_left(now=1002) is None and not copy.paused


def test_finished_game_has_no_timer_after_restore():
    room, seats = room_of("connect4", players=2, timer=True)
    room.start(seats[0], now=0)
    for col in (0, 1, 0, 1, 0, 1, 0):
        room.action(room.seats[room.game.turn], {"col": col}, now=1)
    copy = restored(room)
    assert copy.seconds_left(now=1000) is None


def test_host_restored_or_handed_on():
    room, (a, b, c) = room_of()
    room.start(a, now=0)
    data = through_json(room.snapshot())
    data["host"] = 9  # not a seat any more
    assert Room.restore(data, now=0).host.player == 1


def test_new_settings_get_their_defaults():
    room, seats = room_of("connect4", players=2)
    data = through_json(room.snapshot())
    del data["settings"]["timer"]  # as if the setting were added after this was saved
    assert Room.restore(data, now=0).settings["timer"] is False


def test_room_from_another_game_version_is_refused():
    room, seats = room_of()
    room.start(seats[0], now=0)
    data = through_json(room.snapshot())
    data["state"]["version"] += 1
    with pytest.raises(ValueError):
        Room.restore(data, now=0)


# ---- saving every room to a file


class Unsaveable(BaseGame):
    """A game that never added snapshot()."""
    name = "unsaveable"

    @classmethod
    def create(cls, settings, num_players):
        return cls()

    @property
    def over(self):
        return False


@pytest.fixture
def unsaveable(monkeypatch):
    monkeypatch.setitem(GAMES, "unsaveable", Unsaveable)


def manager_with_rooms():
    manager = RoomManager()
    lobby = manager.create(0, "connect4", {"cols": 9})
    lobby.join(object(), now=0, name="Sam")
    game = manager.create(0, "switch", {"hand_size": 4})
    for name in ("Ann", "Bob"):
        game.join(object(), now=0, name=name)
    game.start(game.host, now=0)
    return manager, lobby, game


def test_save_and_load_every_room(tmp_path):
    path = tmp_path / "rooms.json"
    manager, lobby, game = manager_with_rooms()
    assert manager.save(path) == 2
    assert not (tmp_path / "rooms.json.tmp").exists()

    fresh = RoomManager()
    assert fresh.load(path, now=50) == 2
    assert set(fresh.rooms) == {lobby.code, game.code}
    assert fresh.get(lobby.code).settings["cols"] == 9
    assert fresh.get(game.code).game.snapshot() == game.game.snapshot()
    # and the restored rooms keep being looked after: nobody comes back, so they go
    assert fresh.sweep(now=50 + RESTORE_GRACE) == ([], [])
    changed, removed = fresh.sweep(now=51 + RESTORE_GRACE)
    assert {r.code for r in removed} == {lobby.code, game.code}


def test_save_overwrites_the_last_save(tmp_path):
    path = tmp_path / "rooms.json"
    manager, lobby, game = manager_with_rooms()
    manager.save(path)
    manager.remove(lobby)
    manager.save(path)
    fresh = RoomManager()
    assert fresh.load(path, now=0) == 1


def test_closed_rooms_are_not_saved(tmp_path):
    manager, lobby, game = manager_with_rooms()
    lobby.leave(lobby.seats[1])
    assert lobby.closed
    assert manager.save(tmp_path / "rooms.json") == 1


def test_a_game_that_cant_be_saved_doesnt_stop_the_others(tmp_path, unsaveable):
    manager, lobby, game = manager_with_rooms()
    room = manager.create(0, "unsaveable", {})
    for _ in range(2):
        room.join(object(), now=0)
    room.start(room.host, now=0)
    assert manager.save(tmp_path / "rooms.json") == 2
    fresh = RoomManager()
    assert fresh.load(tmp_path / "rooms.json", now=0) == 2
    assert room.code not in fresh.rooms


def test_a_room_that_cant_be_loaded_is_dropped(tmp_path):
    path = tmp_path / "rooms.json"
    manager, lobby, game = manager_with_rooms()
    manager.save(path)
    data = json.loads(path.read_text())
    data["rooms"][0]["game"] = "chess"  # a game this version doesn't have
    data["rooms"].append({"nonsense": True})
    data["rooms"].append(None)
    path.write_text(json.dumps(data))
    fresh = RoomManager()
    assert fresh.load(path, now=0) == 1
    assert list(fresh.rooms) == [game.code]


@pytest.mark.parametrize("contents", [
    "", "{", "not json", "[]", "null", '{"version": 999, "rooms": []}',
    json.dumps({"version": SNAPSHOT_VERSION, "saved_at": 0, "rooms": []}),  # too old
    b"\xff\xfe\x00garbage",
])
def test_unusable_files_mean_starting_empty(tmp_path, contents):
    path = tmp_path / "rooms.json"
    if isinstance(contents, bytes):
        path.write_bytes(contents)
    else:
        path.write_text(contents)
    fresh = RoomManager()
    assert fresh.load(path, now=0) == 0
    assert fresh.rooms == {}


def test_no_file_means_starting_empty(tmp_path):
    assert RoomManager().load(tmp_path / "missing.json", now=0) == 0


def test_saves_older_than_a_game_could_last_are_ignored(tmp_path, monkeypatch):
    path = tmp_path / "rooms.json"
    manager, lobby, game = manager_with_rooms()
    manager.save(path)
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real + GAME_IDLE_TIMEOUT + 1)
    assert RoomManager().load(path, now=0) == 0
    monkeypatch.setattr(time, "time", lambda: real + GAME_IDLE_TIMEOUT - 1)
    assert RoomManager().load(path, now=0) == 2


def test_loading_doesnt_replace_a_room_already_running(tmp_path):
    path = tmp_path / "rooms.json"
    manager, lobby, game = manager_with_rooms()
    manager.save(path)
    assert manager.load(path, now=0) == 0
    assert manager.get(lobby.code) is lobby


# ---- a restart of the whole server, through its startup and shutdown


@pytest.fixture
def snapshot_path(tmp_path, monkeypatch):
    path = tmp_path / "rooms.json"
    monkeypatch.setattr(server, "SNAPSHOT_PATH", str(path))
    server.rooms.rooms.clear()
    yield path
    server.rooms.rooms.clear()


def restart():
    """What a deploy does to the process: everything in memory is gone."""
    server.rooms.rooms.clear()


def test_game_carries_on_after_a_restart(snapshot_path):
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
            first = create(p1, "switch", hand_size=5)
            code = first["code"]
            second = join(p2, code)
            recv(p1, "state")
            start(p1, p2)
            p1.send_json({"type": "chat", "text": "hi"})
            recv(p1, "chat")
            hands = {1: server.rooms.get(code).game.hands[1][:], 2: None}
            hands[2] = server.rooms.get(code).game.hands[2][:]
            turn = server.rooms.get(code).game.turn
    # leaving the TestClient ran the shutdown, which saved
    assert snapshot_path.exists()
    restart()

    with TestClient(server.app) as client:  # the startup loads
        with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
            p1.send_json({"type": "rejoin", "code": code, "token": first["token"]})
            assert recv(p1, "joined")["player"] == 1
            state = recv(p1, "state")
            assert state["phase"] == "playing"
            assert state["data"]["hand"] == hands[1]
            assert state["data"]["turn"] == turn
            assert [p["status"] for p in state["players"]] == ["connected", "reconnecting"]

            p2.send_json({"type": "rejoin", "code": code, "token": second["token"]})
            assert recv(p2, "joined")["name"] == "Player 2"
            assert recv(p2, "state")["data"]["hand"] == hands[2]
            assert [p["status"] for p in recv(p1, "state")["players"]] == ["connected"] * 2

            # whoever's turn it is can play on
            mover = p1 if turn == 1 else p2
            act(mover, type="draw")
            assert recv(p1, "state")["data"]["turn"] != turn


def test_lobby_survives_a_restart_and_can_start(snapshot_path):
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1:
            first = create(p1, "connect4", rows=5)
            code = first["code"]
    restart()
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
            p1.send_json({"type": "rejoin", "code": code, "token": first["token"]})
            recv(p1, "joined")
            assert recv(p1, "state")["settings"]["rows"] == 5
            join(p2, code)  # new players can still join a restored lobby
            recv(p1, "state")
            state = start(p1, p2)
            assert state["phase"] == "playing" and len(state["data"]["board"][0]) == 5


def test_unknown_token_after_restart_is_refused(snapshot_path):
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1:
            code = create(p1)["code"]
    restart()
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1:
            p1.send_json({"type": "rejoin", "code": code, "token": "wrong"})
            assert recv(p1, "error")["message"] == "Could not rejoin that game"


def test_saves_while_running(snapshot_path, monkeypatch):
    monkeypatch.setattr(server, "SAVE_INTERVAL", 0)
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1:
            code = create(p1)["code"]
            deadline = time.monotonic() + 5
            while not snapshot_path.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            # saved by the sweep, before any shutdown: what a crash would leave behind
            saved = json.loads(snapshot_path.read_text())
            assert [r["code"] for r in saved["rooms"]] == [code]


def test_no_path_means_nothing_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SNAPSHOT_PATH", None)
    monkeypatch.chdir(tmp_path)
    server.rooms.rooms.clear()
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws") as p1:
            create(p1)
    assert list(tmp_path.iterdir()) == []
    server.rooms.rooms.clear()


def test_save_failure_doesnt_crash_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SNAPSHOT_PATH", str(tmp_path / "no" / "such" / "dir.json"))
    server.rooms.rooms.clear()
    with TestClient(server.app) as client:
        assert client.get("/healthz").json() == {"ok": True}
    server.rooms.rooms.clear()
