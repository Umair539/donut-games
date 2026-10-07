"""Computer players taking seats in a room."""
import pytest
from wshelpers import create, recv

from Server.bots import BOTS
from Server.bots.switch import HeuristicBot, RandomBot
from Server.core import rooms as rooms_module
from Server.core.rooms import BOT_DELAY, CONNECTED, LEFT, Room, RoomError
from Server.games.switch import Switch


@pytest.fixture(autouse=True)
def quick_bots(monkeypatch):
    """The lobby only offers the hard bot, which thinks for seconds. Rooms work the same
    with any bot, so these tests use the quick ones."""
    monkeypatch.setitem(BOTS, "switch", {"easy": RandomBot, "medium": HeuristicBot})


def lobby(*bots, people=1):
    room = Room("ABCDEF", Switch, Switch.validate_settings({}), now=0)
    seats = [room.join(None, 0, f"Person {n}") for n in range(people)]
    for level in bots:
        room.add_bot(seats[0], level, 0)
    return room, seats


def play_bots(room):
    """Let every bot whose turn comes up move, until it's a person's turn or the game ends."""
    while True:
        turn = room.bot_ready()
        if turn is None:
            return
        seat, game, due, moves = turn
        assert room.bot_move(seat, seat.brain.choose(game, seat.player), moves, due)


def test_host_adds_bots():
    room, (host,) = lobby("medium", "medium", "easy")
    assert [s.info() for s in room.seats.values()][1:] == [
        {"id": 2, "name": "Medium Bot", "status": CONNECTED, "bot": "medium"},
        {"id": 3, "name": "Medium Bot 2", "status": CONNECTED, "bot": "medium"},
        {"id": 4, "name": "Easy Bot", "status": CONNECTED, "bot": "easy"},
    ]


@pytest.mark.parametrize("level", ["impossible", None, 3])
def test_unknown_bot(level):
    room, (host,) = lobby()
    with pytest.raises(RoomError):
        room.add_bot(host, level, 0)


def test_only_the_host_adds_or_removes_bots():
    room, (host, guest) = lobby("easy", people=2)
    with pytest.raises(RoomError):
        room.add_bot(guest, "easy", 0)
    with pytest.raises(RoomError):
        room.remove_bot(guest, 3, 0)


def test_no_bots_once_started_or_full():
    room, (host,) = lobby(*["easy"] * 9)
    with pytest.raises(RoomError):
        room.add_bot(host, "easy", 0)
    room.start(host, 0)
    with pytest.raises(RoomError):
        room.add_bot(host, "easy", 0)
    with pytest.raises(RoomError):
        room.remove_bot(host, 2, 0)


def test_remove_bot():
    room, (host, guest) = lobby("easy", people=2)
    with pytest.raises(RoomError):
        room.remove_bot(host, 2, 0)  # a person
    with pytest.raises(RoomError):
        room.remove_bot(host, True, 0)
    room.remove_bot(host, 3, 0)
    assert list(room.seats) == [1, 2]


def test_bots_play_until_its_your_turn():
    room, (host,) = lobby("medium", "medium")
    room.start(host, 0)
    play_bots(room)
    assert room.game.over or room.game.turn == host.player
    assert room.bot_ready() is None


def test_bot_is_handed_its_turn_once():
    room, (host,) = lobby("medium")
    room.start(host, 0)
    room.game.turn = 2
    room._arm(10)
    seat, game, due, _ = room.bot_ready()
    assert seat is room.seats[2] and due == 10 + BOT_DELAY
    assert game is not room.game and game.snapshot() == room.game.snapshot()
    assert room.bot_ready() is None  # it's already thinking


def test_late_answer_is_dropped():
    room, (host, guest) = lobby("medium", people=2)
    room.start(host, 0)
    room.game.turn = 3
    room._arm(0)
    seat, game, due, moves = room.bot_ready()
    room.leave(guest, 1)  # the game changes while the bot thinks
    assert not room.bot_move(seat, seat.brain.choose(game, 3), moves, 2)
    assert room.bot_ready()  # and it is asked again


@pytest.mark.parametrize("actions", [None, [{"type": "dance"}]])
def test_broken_bot_still_moves(actions):
    room, (host,) = lobby("medium")
    room.start(host, 0)
    room.game.turn = 2
    room._arm(0)
    seat, game, due, moves = room.bot_ready()
    hand = len(room.game.hands[2])
    assert room.bot_move(seat, actions, moves, BOT_DELAY)
    assert len(room.game.hands[2]) == hand + 1  # picked up, as if out of time
    assert room.game.turn == 1


def test_room_closes_when_only_bots_are_left():
    room, (host,) = lobby("medium", "easy")
    room.start(host, 0)
    room.leave(host, 1)
    assert room.closed


def test_person_becomes_host_not_a_bot():
    room, (host,) = lobby("easy")
    guest = room.join(None, 0, "Guest")  # sits after the bot
    room.leave(host, 1)
    assert room.host is guest


def test_bots_always_want_a_rematch():
    room, (host,) = lobby("medium")
    room.start(host, 0)
    room.game.hands[1] = []
    room.game._end()
    room.request_rematch(host, 1)
    assert not room.game.over


def test_room_with_bots_still_expires():
    room, (host,) = lobby("medium")
    room.start(host, 0)
    room.disconnect(host, None, 0)
    assert not room.is_dead(1)
    room.expire_seats(1000)
    assert host.status == LEFT and room.is_dead(1000)


def test_bots_survive_a_restart():
    room, (host,) = lobby("medium", "easy")
    room.start(host, 0)
    copy = Room.restore(room.snapshot(), 0)
    assert [s.info() for s in copy.seats.values()][1:] == \
        [s.info() for s in room.seats.values()][1:]
    copy.game.turn = 2
    assert copy.paused and copy.bot_ready() is None  # waits for the people to come back
    copy.rejoin(host.token, object(), 100)
    assert copy.bot_ready()


def test_bot_plays_over_websockets(client, monkeypatch):
    monkeypatch.setattr(rooms_module, "BOT_DELAY", 0)
    with client.websocket_connect("/ws") as ws:
        create(ws, "switch")
        ws.send_json({"type": "add_bot", "level": "medium"})
        state = recv(ws, "state")
        assert state["players"][1]["bot"] == "medium"
        ws.send_json({"type": "start"})
        state = recv(ws, "state")
        if state["data"]["turn"] == 1:
            ws.send_json({"type": "action", "action": {"type": "draw"}})
            recv(ws, "state")
        state = recv(ws, "state")  # sent by the server on its own, once the bot has moved
        assert state["data"]["log"][-1]["player"] == 2
        assert state["data"]["turn"] == 1 or state["over"]


def test_games_listing_has_bots(client, monkeypatch):
    monkeypatch.undo()  # the real list
    games = {g["name"]: g for g in client.get("/api/games").json()}
    assert games["switch"]["bots"] == ["hard"]
    assert games["connect4"]["bots"] == []
