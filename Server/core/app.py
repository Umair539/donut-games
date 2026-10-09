import asyncio
import json
import os
import socket
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from Server.bots import BOTS
from Server.core.base import GameError
from Server.core.limits import AddressLimits, MessageBucket, client_address
from Server.core.rooms import RoomError, RoomManager
from Server.games import GAMES

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
MAX_MESSAGE_SIZE = 1024  # characters, plenty for any valid message
SWEEP_INTERVAL = 1  # seconds between checks for turn timers, expired seats and dead rooms
BOT_INTERVAL = 0.2  # seconds between checks for bots whose turn it is
SAVE_INTERVAL = 30  # seconds between saves, which only matter if the server crashes
BOT_PROCESSES = 2  # bots thinking at once; any more wait their turn

# Where rooms are saved so a restart (a deploy) doesn't end everyone's game. Unset means rooms
# only live in memory, which is how local play and the tests work.
SNAPSHOT_PATH = os.environ.get("SNAPSHOT_PATH")

# Sites allowed to use this server when the web pages are hosted elsewhere (Cloudflare Pages),
# comma separated, e.g. "https://donutgames.co.uk". Unset means only the pages served from
# here, which is how local play works.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

rooms = RoomManager()
limits = AddressLimits()


async def send(socket, message):
    try:
        await socket.send_json(message)
    except Exception:
        pass  # socket already closed, its own handler cleans up


async def close(socket, message):
    await send(socket, {"type": "closed", "message": message})
    try:
        await socket.close()
    except Exception:
        pass


async def broadcast(room):
    """Send each connected player their own view of the room."""
    for seat in room.connected_seats():
        await send(seat.socket, room.state_for(seat))


async def shut_down(room, message):
    for seat in room.connected_seats():
        await close(seat.socket, message)


def save_rooms():
    if SNAPSHOT_PATH:
        try:
            rooms.save(SNAPSHOT_PATH)
        except OSError as e:
            print(f"Could not save rooms to {SNAPSHOT_PATH}: {e!r}")


async def sweep_forever():
    last_save = time.monotonic()
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        now = time.monotonic()
        changed, removed = rooms.sweep(now)
        limits.forget_old(now)
        for room in changed:
            await broadcast(room)
        for room in removed:
            await shut_down(room, room.closed or "Room closed after inactivity")
        if now - last_save >= SAVE_INTERVAL:
            save_rooms()
            last_save = now


thinking = set()  # bots working out a move, kept here so the tasks aren't garbage collected

# Bots think in other processes. In a thread, a bot thinking holds Python's lock nearly all
# the time, and every other player's move waits for it: in a load test, three bot games made
# everyone's moves take a second to come back. The processes run at a lower priority, so the
# server always goes first and bots get the CPU that's left.
bot_pool = None


def start_bot_process():
    if hasattr(os, "nice"):  # not on Windows, where only local play happens
        os.nice(10)


def bot_choose(brain, game, player):
    """Runs in a bot process, on a copy of the bot sent over each time, so it needs fresh
    luck or it would make the same random choices on every move."""
    if hasattr(brain, "rng"):
        brain.rng.seed()
    return brain.choose(game, player)


def new_bot_pool():
    return ProcessPoolExecutor(BOT_PROCESSES, initializer=start_bot_process)


async def think(room, seat, game, due, moves):
    """Let a bot choose its move in a bot process, so a slow one doesn't hold up everyone
    else, then play it when it's due, if the game hasn't moved on in the meantime."""
    global bot_pool
    pool = bot_pool
    try:
        actions = await asyncio.get_running_loop().run_in_executor(
            pool, bot_choose, seat.brain, game, seat.player)
    except Exception as e:
        print(f"Bot {seat.bot} in room {room.code} failed: {e!r}")
        actions = None
        if isinstance(e, BrokenProcessPool) and bot_pool is pool:  # a process died
            bot_pool = new_bot_pool()
    await asyncio.sleep(max(0, due - time.monotonic()))
    if not room.closed and room.bot_move(seat, actions, moves, time.monotonic()):
        await broadcast(room)


async def bots_forever():
    while True:
        await asyncio.sleep(BOT_INTERVAL)
        for turn in rooms.bots_ready():
            task = asyncio.create_task(think(*turn))
            thinking.add(task)
            task.add_done_callback(thinking.discard)


@asynccontextmanager
async def lifespan(app):
    global bot_pool
    bot_pool = new_bot_pool()
    if SNAPSHOT_PATH:
        count = rooms.load(SNAPSHOT_PATH, time.monotonic())
        print(f"Restored {count} room(s) from {SNAPSHOT_PATH}")
    tasks = [asyncio.create_task(sweep_forever()), asyncio.create_task(bots_forever())]
    yield
    for task in tasks:
        task.cancel()
    save_rooms()  # uvicorn has closed every connection by now, so nothing changes after this
    bot_pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Lets those sites read /api/games. Nothing here uses cookies, so no credentials.
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET"])


class Connection:
    """One browser tab's WebSocket and the seat it holds, if any."""

    def __init__(self, socket, address):
        self.socket = socket
        self.address = address
        self.room = None
        self.seat = None
        self.bucket = MessageBucket(time.monotonic())

    async def handle(self, msg):
        kind = msg.get("type")
        now = time.monotonic()
        if kind in ("create", "join", "rejoin"):
            if self.room is not None:
                raise RoomError("Already in a game")
        elif self.room is None:
            raise RoomError("Not in a game")

        if kind == "create":
            limits.check_new_room(self.address, rooms.made_by(self.address), now)
            room = rooms.create(now, msg.get("game"), msg.get("settings", {}))
            room.creator = self.address
            limits.room_made(self.address, now)
            await self._seated(room, room.join(self.socket, now, msg.get("name")))

        elif kind == "join":
            room = rooms.get(msg.get("code"))
            await self._seated(room, room.join(self.socket, now, msg.get("name")))

        elif kind == "rejoin":
            room = rooms.get(msg.get("code"))
            seat, old = room.rejoin(msg.get("token"), self.socket, now)
            if old is not None:
                await close(old, "Game opened somewhere else")
            await self._seated(room, seat)

        elif kind == "add_bot":
            self.room.add_bot(self.seat, msg.get("level"), now)
            await broadcast(self.room)

        elif kind == "remove_bot":
            self.room.remove_bot(self.seat, msg.get("player"), now)
            await broadcast(self.room)

        elif kind == "start":
            self.room.start(self.seat, now)
            await broadcast(self.room)

        elif kind == "action":
            self.room.action(self.seat, msg.get("action"), now)
            await broadcast(self.room)

        elif kind == "chat":
            line = self.room.chat(self.seat, msg.get("text"), now)
            for seat in self.room.connected_seats():
                await send(seat.socket, line)

        elif kind == "rematch":
            self.room.request_rematch(self.seat, now)
            await broadcast(self.room)

        elif kind == "leave":
            room, seat = self.room, self.seat
            self.room = self.seat = None
            room.leave(seat)
            if room.closed:
                rooms.remove(room)
                await shut_down(room, room.closed)
            else:
                await broadcast(room)

        else:
            raise RoomError("Unknown message")

    async def _seated(self, room, seat):
        self.room, self.seat = room, seat
        await send(
            self.socket,
            {
                "type": "joined",
                "code": room.code,
                "player": seat.player,
                "name": seat.name,
                "token": seat.token,
            },
        )
        await broadcast(room)

    async def disconnected(self):
        if self.room is not None:
            self.room.disconnect(self.seat, self.socket, time.monotonic())
            await broadcast(self.room)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/games")
def list_games():
    return [{**game.describe(), "bots": list(BOTS.get(name, {}))} for name, game in GAMES.items()]


def origin_allowed(socket):
    """Browsers don't apply CORS to WebSockets, so any site could connect without this check.
    A page served from here sends its own address as the origin, which is always fine."""
    origin = socket.headers.get("origin")
    if origin is None or not ALLOWED_ORIGINS:
        return True
    host = socket.headers.get("host")
    return origin in ALLOWED_ORIGINS or origin.split("://", 1)[-1] == host


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket):
    if not origin_allowed(socket):
        await socket.close(code=1008)
        return
    address = client_address(socket)
    await socket.accept()
    if not limits.connect(address):
        await close(socket, "Too many connections from your network, try again later")
        return
    conn = Connection(socket, address)
    try:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None or len(text) > MAX_MESSAGE_SIZE:
                await socket.close(code=1009)
                break
            try:
                msg = json.loads(text)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                await send(socket, {"type": "error", "message": "Invalid message"})
                continue
            if not conn.bucket.take(time.monotonic()):
                await send(socket, {"type": "error", "message": "Slow down"})
                continue
            try:
                await conn.handle(msg)
            except (RoomError, GameError) as e:
                await send(socket, {"type": "error", "message": str(e)})
    finally:
        limits.disconnect(address)
        await conn.disconnected()


class FreshStaticFiles(StaticFiles):
    """Static files the browser must check before reusing. Without this it can keep an old
    app.js next to a new index.html after an update; the check is a cheap 304 when nothing
    changed."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Registered last so /ws, /healthz and /api take priority. Only when the web folder is there:
# in production Cloudflare Pages serves the pages instead.
if WEB_DIR.is_dir():
    app.mount("/", FreshStaticFiles(directory=WEB_DIR, html=True), name="web")


def lan_address():
    """This machine's address on the local network, or None if it can't be found."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # picks the outgoing interface, sends nothing
            return probe.getsockname()[0]
    except OSError:
        return None


def run():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    # uvicorn prints the address it listens on, and 0.0.0.0 can't be opened in a browser
    print(f"Open http://localhost:{port} to play")
    lan = lan_address() if host == "0.0.0.0" else None
    if lan:
        print(f"Other devices on your network can use http://{lan}:{port}")
    # On shutdown open games are saved after the connections close, so don't wait long for them
    uvicorn.run(app, host=host, port=port, ws_max_size=16 * 1024, timeout_graceful_shutdown=5)


if __name__ == "__main__":
    run()
