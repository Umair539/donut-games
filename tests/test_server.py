"""Connect Four played end to end over the real WebSocket endpoint."""
import pytest
from wshelpers import act, create, join, recv, start

from Server.core import app as server
from Server.core.rooms import CONNECTED, LEFT, LOBBY, PLAYING, RECONNECTING


def lobby(p1, p2, **settings):
    """Host creates a room and a second player joins. Returns the room code."""
    code = create(p1, **settings)["code"]
    join(p2, code)
    recv(p1, "state")
    return code


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_games_listing(client):
    games = client.get("/api/games").json()
    assert [g["name"] for g in games] == ["connect4"]
    game = games[0]
    assert (game["min_players"], game["max_players"]) == (2, 2)
    assert [s["key"] for s in game["settings"]] == ["cols", "rows", "amount"]


def test_create_room(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "create", "game": "connect4",
                      "settings": {"rows": 8, "cols": 9, "amount": 5}})
        joined = recv(ws, "joined")
        assert joined["player"] == 1
        assert len(joined["code"]) == 6
        state = recv(ws, "state")
        assert state["code"] == joined["code"]
        assert state["phase"] == LOBBY
        assert state["you"] == state["host"] == 1
        assert state["players"] == [{"id": 1, "name": "Player", "status": CONNECTED}]
        assert state["settings"] == {"rows": 8, "cols": 9, "amount": 5}
        assert state["data"] is None


def test_default_settings(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "create", "game": "connect4"})
        assert recv(ws, "state")["settings"] == {"cols": 7, "rows": 6, "amount": 4}


@pytest.mark.parametrize(
    "msg, error",
    [
        ({"game": "chess"}, "Unknown game"),
        ({"game": None}, "Unknown game"),
        ({"game": "connect4", "settings": [1]}, "Invalid settings"),
        ({"game": "connect4", "settings": {"rows": 100}}, "rows"),
        ({"game": "connect4", "settings": {"colour": "red"}}, "Unknown setting"),
    ],
)
def test_invalid_create(client, msg, error):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "create", **msg})
        assert error in recv(ws, "error")["message"]
    assert server.rooms.rooms == {}


def test_join_by_code_case_insensitive(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        code = create(p1)["code"]
        assert join(p2, f" {code.lower()} ")["player"] == 2
        state = recv(p1, "state")
        assert [p["id"] for p in state["players"]] == [1, 2]
        assert state["phase"] == LOBBY


def test_unknown_room(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "join", "code": "ZZZZ"})
        assert recv(ws, "error")["message"] == "Room not found"


def test_room_full(client):
    with (
        client.websocket_connect("/ws") as p1,
        client.websocket_connect("/ws") as p2,
        client.websocket_connect("/ws") as p3,
    ):
        code = lobby(p1, p2)
        p3.send_json({"type": "join", "code": code})
        assert recv(p3, "error")["message"] == "Room is full"


def test_only_host_can_start(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        p2.send_json({"type": "start"})
        assert recv(p2, "error")["message"] == "Only the host can start the game"


def test_cannot_start_alone(client):
    with client.websocket_connect("/ws") as p1:
        create(p1)
        p1.send_json({"type": "start"})
        assert recv(p1, "error")["message"] == "Need at least 2 players"


def test_cannot_play_before_start(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        act(p1, col=3)
        assert recv(p1, "error")["message"] == "Game has not started"


def test_start_moves_everyone_to_the_game(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2, rows=5, cols=6)
        p1.send_json({"type": "start"})
        s1, s2 = recv(p1, "state"), recv(p2, "state")
        assert s1["phase"] == s2["phase"] == PLAYING
        assert (s1["you"], s2["you"]) == (1, 2)
        assert s1["data"]["turn"] == 1
        assert (s1["data"]["rows"], s1["data"]["cols"]) == (5, 6)


def test_cannot_join_after_start(client):
    with (
        client.websocket_connect("/ws") as p1,
        client.websocket_connect("/ws") as p2,
        client.websocket_connect("/ws") as p3,
    ):
        code = lobby(p1, p2)
        start(p1, p2)
        p3.send_json({"type": "join", "code": code})
        assert recv(p3, "error")["message"] == "Game already started"


def test_full_game_and_rematch(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        start(p1, p2)

        for ws, col in [(p1, 0), (p2, 1), (p1, 0), (p2, 1), (p1, 0), (p2, 1), (p1, 0)]:
            act(ws, col=col)
            state = recv(p1, "state")
            recv(p2, "state")
        assert state["over"] is True
        assert state["data"]["status"] == "win"
        assert state["data"]["winner"] == 1

        # one request is not enough
        p1.send_json({"type": "rematch"})
        state = recv(p2, "state")
        assert state["rematch"] == [1]
        assert state["data"]["status"] == "win"

        p2.send_json({"type": "rematch"})
        state = recv(p2, "state")
        assert state["rematch"] == []
        assert state["over"] is False
        assert state["data"]["status"] == "playing"
        assert state["data"]["round"] == 2
        assert state["data"]["turn"] == 2


def test_wrong_turn_rejected(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        start(p1, p2)
        act(p2, col=3)
        assert recv(p2, "error")["message"] == "Not your turn"


@pytest.mark.parametrize("action", [{}, {"col": "3"}, {"col": 99}, {"col": None}])
def test_bad_actions_rejected(client, action):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        start(p1, p2)
        act(p1, **action)
        assert recv(p1, "error")["message"] == "Invalid column"


def test_action_must_be_a_dict(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        start(p1, p2)
        p1.send_json({"type": "action", "action": 3})
        assert recv(p1, "error")["message"] == "Invalid action"


def test_rematch_needs_finished_game(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        lobby(p1, p2)
        start(p1, p2)
        p1.send_json({"type": "rematch"})
        assert recv(p1, "error")["message"] == "Game is still going"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", "42"])
def test_invalid_message(client, raw):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(raw)
        assert recv(ws, "error")["message"] == "Invalid message"


def test_unknown_and_out_of_order_messages(client):
    with client.websocket_connect("/ws") as ws:
        act(ws, col=0)
        assert recv(ws, "error")["message"] == "Not in a game"
        create(ws)
        ws.send_json({"type": "create", "game": "connect4"})
        assert recv(ws, "error")["message"] == "Already in a game"
        ws.send_json({"type": "dance"})
        assert recv(ws, "error")["message"] == "Unknown message"


def test_reconnect_in_game(client):
    with client.websocket_connect("/ws") as p1:
        with client.websocket_connect("/ws") as p2:
            code = create(p1)["code"]
            token = join(p2, code)["token"]
            recv(p1, "state")
            start(p1, p2)
        state = recv(p1, "state")
        assert state["players"][1]["status"] == RECONNECTING

        with client.websocket_connect("/ws") as p2:
            p2.send_json({"type": "rejoin", "code": code, "token": token})
            assert recv(p2, "joined")["player"] == 2
            assert recv(p2, "state")["data"]["turn"] == 1
            assert recv(p1, "state")["players"][1]["status"] == CONNECTED
            act(p1, col=2)
            assert recv(p2, "state")["data"]["board"][2][0] == 1


@pytest.mark.parametrize("token", ["wrong", "ünïcode", None, 5])
def test_rejoin_with_bad_token(client, token):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        code = create(p1)["code"]
        p2.send_json({"type": "rejoin", "code": code, "token": token})
        assert recv(p2, "error")["message"] == "Could not rejoin that game"


def test_rejoin_from_second_tab_closes_first(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as tab1:
        code = create(p1)["code"]
        token = join(tab1, code)["token"]
        with client.websocket_connect("/ws") as tab2:
            tab2.send_json({"type": "rejoin", "code": code, "token": token})
            assert recv(tab1, "closed")["message"] == "Game opened somewhere else"
            assert recv(tab2, "joined")["player"] == 2
            players = server.rooms.get(code).state_for(server.rooms.get(code).host)["players"]
            assert [p["status"] for p in players] == [CONNECTED, CONNECTED]


def test_leaving_the_lobby_frees_the_seat(client):
    with (
        client.websocket_connect("/ws") as p1,
        client.websocket_connect("/ws") as p2,
        client.websocket_connect("/ws") as p3,
    ):
        code = lobby(p1, p2)
        p2.send_json({"type": "leave"})
        state = recv(p1, "state")
        assert state["players"] == [{"id": 1, "name": "Player", "status": CONNECTED}]
        assert join(p3, code)["player"] == 2


def test_host_leaving_the_lobby_closes_the_room(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        code = lobby(p1, p2)
        p1.send_json({"type": "leave"})
        assert recv(p2, "closed")["message"] == "The host left the game"
        assert code not in server.rooms.rooms


def test_leaving_mid_game(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        code = create(p1)["code"]
        token = join(p2, code)["token"]
        recv(p1, "state")
        start(p1, p2)
        p2.send_json({"type": "leave"})
        assert recv(p1, "state")["players"][1]["status"] == LEFT

        act(p1, col=0)
        assert recv(p1, "error")["message"] == "Not enough players left"

        # a left seat cannot be taken back, even with its real token
        p2.send_json({"type": "rejoin", "code": code, "token": token})
        assert recv(p2, "error")["message"] == "Could not rejoin that game"


# names


def names(state):
    return [p["name"] for p in state["players"]]


def test_names_shown_to_everyone(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        p1.send_json({"type": "create", "game": "connect4", "name": "Sam"})
        assert recv(p1, "joined")["name"] == "Sam"
        code = recv(p1, "state")["code"]
        p2.send_json({"type": "join", "code": code, "name": "Alex"})
        assert recv(p2, "joined")["name"] == "Alex"
        assert names(recv(p2, "state")) == ["Sam", "Alex"]
        assert names(recv(p1, "state")) == ["Sam", "Alex"]


def test_unnamed_players_get_default_names(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        code = create(p1)["code"]
        assert join(p2, code)["name"] == "Player 2"
        assert names(recv(p1, "state")) == ["Player", "Player 2"]


def test_taken_name_gets_a_number(client):
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        p1.send_json({"type": "create", "game": "connect4", "name": "Sam"})
        code = recv(p1, "state")["code"]
        p2.send_json({"type": "join", "code": code, "name": "sam"})  # not case sensitive
        assert recv(p2, "joined")["name"] == "sam 2"


def test_name_kept_when_rejoining(client):
    with client.websocket_connect("/ws") as p1:
        p1.send_json({"type": "create", "game": "connect4", "name": "Sam"})
        code = recv(p1, "state")["code"]
        with client.websocket_connect("/ws") as p2:
            p2.send_json({"type": "join", "code": code, "name": "Sam"})
            token = recv(p2, "joined")["token"]
        with client.websocket_connect("/ws") as p2:
            p2.send_json({"type": "rejoin", "code": code, "token": token})
            assert recv(p2, "joined")["name"] == "Sam 2"
            assert names(recv(p2, "state")) == ["Sam", "Sam 2"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Sam   Smith  ", "Sam Smith"),
        ("Sa\x00m\x07", "Sam"),
        ("a" * 40, "a" * 16),
        ("", "Player"),
        ("   ", "Player"),
        ("\n\t", "Player"),
        ("<b>Sam</b>", "<b>Sam</b>"),  # shown as plain text by the page, so left alone
        ("Donut \U0001f369", "Donut \U0001f369"),
    ],
)
def test_names_are_tidied(client, raw, expected):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "create", "game": "connect4", "name": raw})
        assert recv(ws, "joined")["name"] == expected


@pytest.mark.parametrize("raw", [5, ["Sam"], {"a": 1}, True])
def test_name_must_be_text(client, raw):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "create", "game": "connect4", "name": raw})
        assert recv(ws, "error")["message"] == "Invalid name"


def test_number_suffix_keeps_name_within_length_limit(client):
    long_name = "a" * 16
    with client.websocket_connect("/ws") as p1, client.websocket_connect("/ws") as p2:
        p1.send_json({"type": "create", "game": "connect4", "name": long_name})
        code = recv(p1, "state")["code"]
        p2.send_json({"type": "join", "code": code, "name": long_name})
        name = recv(p2, "joined")["name"]
        assert len(name) == 16
        assert name.endswith(" 2")


def test_leaving_the_lobby_frees_the_name(client):
    with (
        client.websocket_connect("/ws") as p1,
        client.websocket_connect("/ws") as p2,
        client.websocket_connect("/ws") as p3,
    ):
        p1.send_json({"type": "create", "game": "connect4", "name": "Sam"})
        code = recv(p1, "state")["code"]
        p2.send_json({"type": "join", "code": code, "name": "Alex"})
        recv(p2, "state")
        recv(p1, "state")
        p2.send_json({"type": "leave"})
        recv(p1, "state")
        p3.send_json({"type": "join", "code": code, "name": "Alex"})
        assert recv(p3, "joined")["name"] == "Alex"
