"""Limits on what one address or one connection can ask of the server."""
from types import SimpleNamespace

import pytest
from wshelpers import create, recv

from Server.core import limits as L
from Server.core.limits import AddressLimits, MessageBucket, client_address
from Server.core.rooms import RoomError


def fake_socket(peer, forwarded=None):
    headers = {"cf-connecting-ip": forwarded} if forwarded else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_the_tunnels_header_is_trusted():
    assert client_address(fake_socket("172.17.0.1", "203.0.113.9")) == "203.0.113.9"
    assert client_address(fake_socket("127.0.0.1", "2001:db8::1")) == "2001:db8::1"


def test_a_public_address_cant_pretend_to_be_someone_else():
    assert client_address(fake_socket("8.8.8.8", "203.0.113.9")) == "8.8.8.8"


def test_without_the_header_the_address_itself_is_used():
    assert client_address(fake_socket("192.168.1.20")) == "192.168.1.20"


def test_connections_per_address():
    limits = AddressLimits()
    for _ in range(L.MAX_CONNECTIONS):
        assert limits.connect("a")
    assert not limits.connect("a")
    assert limits.connect("b")  # someone else is fine
    limits.disconnect("a")
    assert limits.connect("a")


def test_open_rooms_per_address():
    limits = AddressLimits()
    limits.check_new_room("a", L.MAX_OPEN_ROOMS - 1, now=0)
    with pytest.raises(RoomError, match="too many games open"):
        limits.check_new_room("a", L.MAX_OPEN_ROOMS, now=0)


def test_new_rooms_per_address_over_time():
    limits = AddressLimits()
    for n in range(L.MAX_NEW_ROOMS):
        limits.check_new_room("a", 0, now=n)
        limits.room_made("a", now=n)
    with pytest.raises(RoomError, match="wait a few minutes"):
        limits.check_new_room("a", 0, now=L.MAX_NEW_ROOMS)
    limits.check_new_room("b", 0, now=L.MAX_NEW_ROOMS)
    limits.check_new_room("a", 0, now=L.NEW_ROOMS_WINDOW + 1)  # the first has dropped off


def test_old_addresses_are_forgotten():
    limits = AddressLimits()
    limits.room_made("a", now=0)
    limits.forget_old(now=L.NEW_ROOMS_WINDOW - 1)
    assert "a" in limits.made
    limits.forget_old(now=L.NEW_ROOMS_WINDOW)
    assert "a" not in limits.made


def test_message_bucket_allows_a_burst_then_a_steady_rate():
    bucket = MessageBucket(now=0)
    assert all(bucket.take(now=0) for _ in range(L.MESSAGE_BURST))
    assert not bucket.take(now=0)
    assert bucket.take(now=1 / L.MESSAGE_RATE)
    assert not bucket.take(now=1 / L.MESSAGE_RATE)


# through the server


def test_too_many_open_rooms_from_one_address(client, monkeypatch):
    monkeypatch.setattr(L, "MAX_OPEN_ROOMS", 2)
    sockets = [client.websocket_connect("/ws") for _ in range(3)]
    try:
        first, second, third = [s.__enter__() for s in sockets]
        create(first)
        create(second)
        third.send_json({"type": "create", "game": "connect4", "settings": {}})
        assert recv(third, "error")["message"] == "You have too many games open, close one first"
        first.send_json({"type": "leave"})  # closing one makes room for another
        first.send_json({"type": "chat", "text": "hi"})  # answered in order, so the leave is done
        assert recv(first, "error")["message"] == "Not in a game"
        create(third)
    finally:
        for s in sockets:
            s.__exit__(None, None, None)


def test_rooms_from_another_address_dont_count(client, monkeypatch):
    monkeypatch.setattr(L, "MAX_OPEN_ROOMS", 1)
    with client.websocket_connect("/ws", headers={"cf-connecting-ip": "203.0.113.1"}) as a, \
            client.websocket_connect("/ws", headers={"cf-connecting-ip": "203.0.113.2"}) as b:
        create(a)
        create(b)


def test_too_many_connections_from_one_address(client, monkeypatch):
    monkeypatch.setattr(L, "MAX_CONNECTIONS", 1)
    with client.websocket_connect("/ws") as first:
        with client.websocket_connect("/ws") as second:
            closed = second.receive_json()
            assert closed == {"type": "closed",
                              "message": "Too many connections from your network, try again later"}
        create(first)  # the first still works


def test_flooding_messages_is_slowed_down(client):
    with client.websocket_connect("/ws") as ws:
        for _ in range(L.MESSAGE_BURST):
            ws.send_json({"type": "chat", "text": "hi"})
        ws.send_json({"type": "chat", "text": "hi"})
        messages = [ws.receive_json()["message"] for _ in range(L.MESSAGE_BURST + 1)]
        assert messages[:-1] == ["Not in a game"] * L.MESSAGE_BURST
        assert messages[-1] == "Slow down"
