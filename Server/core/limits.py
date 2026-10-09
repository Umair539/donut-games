"""Limits on how much one address or one connection can ask of the server, so a single person
(or script) can't fill it with lobbies, hold hundreds of connections open, or flood it with
messages. They're generous, as a whole school or house can share one address."""
import ipaddress
from collections import Counter, defaultdict, deque

from Server.core.rooms import RoomError

MAX_CONNECTIONS = 40  # open at once from one address
MAX_OPEN_ROOMS = 10  # rooms one address has made that are still open
MAX_NEW_ROOMS = 30  # rooms one address can make in NEW_ROOMS_WINDOW seconds
NEW_ROOMS_WINDOW = 10 * 60
MESSAGE_BURST = 20  # messages one connection can send at once
MESSAGE_RATE = 5  # and then each second


def client_address(socket):
    """Who's on the other end. In production every connection comes through the Cloudflare
    Tunnel, from a private address (the server only listens on 127.0.0.1), and Cloudflare
    says who the real visitor is in a header. A connection straight from a public address
    could set that header to anything, so then the address itself is used."""
    peer = socket.client.host if socket.client else ""
    forwarded = socket.headers.get("cf-connecting-ip")
    try:
        public = ipaddress.ip_address(peer).is_global
    except ValueError:
        public = False  # not an address at all, like the tests' "testclient"
    if forwarded and not public:
        return forwarded.strip()
    return peer


class AddressLimits:
    def __init__(self):
        self.connections = Counter()
        self.made = defaultdict(deque)  # when each address made its recent rooms

    def connect(self, address):
        """Count a new connection. Returns False if this address already has too many."""
        if self.connections[address] >= MAX_CONNECTIONS:
            return False
        self.connections[address] += 1
        return True

    def disconnect(self, address):
        self.connections[address] -= 1
        if self.connections[address] <= 0:
            del self.connections[address]

    def check_new_room(self, address, open_rooms, now):
        """Raise RoomError if this address may not make another room now. open_rooms is how
        many of the rooms it made are still open."""
        if open_rooms >= MAX_OPEN_ROOMS:
            raise RoomError("You have too many games open, close one first")
        made = self.made[address]
        while made and now - made[0] >= NEW_ROOMS_WINDOW:
            made.popleft()
        if len(made) >= MAX_NEW_ROOMS:
            raise RoomError("You've made a lot of games, wait a few minutes")

    def room_made(self, address, now):
        self.made[address].append(now)

    def forget_old(self, now):
        """Drop addresses that haven't made a room in a while, so the record doesn't grow."""
        for address in [a for a, made in self.made.items()
                        if not made or now - made[-1] >= NEW_ROOMS_WINDOW]:
            del self.made[address]


class MessageBucket:
    """How many messages a connection may send: up to MESSAGE_BURST at once, refilling at
    MESSAGE_RATE a second."""

    def __init__(self, now):
        self.tokens = MESSAGE_BURST
        self.updated = now

    def take(self, now):
        self.tokens = min(MESSAGE_BURST, self.tokens + (now - self.updated) * MESSAGE_RATE)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
