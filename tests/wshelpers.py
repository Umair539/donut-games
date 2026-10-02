"""Small helpers for talking to the server over a test WebSocket."""


def recv(ws, kind):
    """Read messages until one of the given type arrives."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


def create(ws, game="connect4", **settings):
    ws.send_json({"type": "create", "game": game, "settings": settings})
    joined = recv(ws, "joined")
    recv(ws, "state")
    return joined


def join(ws, code):
    """Join a room. Returns the joined message. The caller drains the host's state."""
    ws.send_json({"type": "join", "code": code})
    joined = recv(ws, "joined")
    recv(ws, "state")
    return joined


def start(host, *others):
    """The host starts the game. Returns the host's state, with the others drained too."""
    host.send_json({"type": "start"})
    state = recv(host, "state")
    for ws in others:
        recv(ws, "state")
    return state


def act(ws, **action):
    ws.send_json({"type": "action", "action": action})
