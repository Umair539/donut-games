"""Load test a game server: many people playing at once, and bots thinking, while it measures
how long the server takes to answer.

    python scripts/stress.py --url ws://127.0.0.1:8000/ws --rooms 200 --bot-rooms 10

Each room is two simulated people playing Connect Donut, making a move every --pace seconds
(a person takes a few seconds), and starting a rematch when a game ends. Each bot room is one
person against a Hard bot, moving as soon as it's their turn, which keeps the bots thinking
all the time. Every simulated person comes from their own made-up address, so the per-address
limits don't get in the way. That needs the server to trust the address header, which it
does for connections from this machine. Never point this at the real server: it's a lot of
load from one place, and real people are playing there.

It reports how long a move takes to come back as the new state (the server's answer time
under load), how many moves went through, and any errors."""
import argparse
import asyncio
import itertools
import json
import random
import statistics
import time

from websockets.asyncio.client import connect

addresses = (f"198.18.{n // 250}.{n % 250 + 1}" for n in itertools.count())


class Stats:
    def __init__(self):
        self.latency = []  # seconds from sending a move to getting the state back
        self.bot_turns = []  # seconds from a person's move to the bot's answer
        self.errors = {}
        self.moves = 0
        self.failed_rooms = 0

    def error(self, message):
        self.errors[message] = self.errors.get(message, 0) + 1


async def open_socket(url):
    return await connect(url, additional_headers={"cf-connecting-ip": next(addresses)},
                         open_timeout=30, ping_interval=None, max_size=2 ** 20)


async def receive(ws, kind, stats):
    while True:
        msg = json.loads(await ws.recv())
        if msg["type"] == kind:
            return msg
        if msg["type"] == "error":
            stats.error(msg["message"])


async def setup(url, stats, bot):
    host = await open_socket(url)
    await host.send(json.dumps({"type": "create", "game": "connect4", "settings": {}}))
    code = (await receive(host, "joined", stats))["code"]
    await receive(host, "state", stats)
    guest = None
    if bot:
        await host.send(json.dumps({"type": "add_bot", "level": "hard"}))
    else:
        guest = await open_socket(url)
        await guest.send(json.dumps({"type": "join", "code": code}))
        await receive(guest, "joined", stats)
    await host.send(json.dumps({"type": "start"}))
    return host, guest


def column(state):
    board = state["data"]["board"]
    return random.choice([c for c, col in enumerate(board) if 0 in col])


async def latest(ws, stats, wait):
    """Read messages for up to `wait` seconds and return the last state."""
    state, deadline = None, time.monotonic() + wait
    while True:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), deadline - time.monotonic()))
        except TimeoutError:
            return state
        if msg["type"] == "state":
            state = msg
        elif msg["type"] == "error":
            stats.error(msg["message"])


async def person_room(url, stats, pace, until):
    try:
        host, guest = await setup(url, stats, bot=False)
    except Exception as e:
        stats.failed_rooms += 1
        stats.error(f"setup: {type(e).__name__}")
        return
    players = {1: host, 2: guest}
    try:
        state, _ = await asyncio.gather(latest(host, stats, 0.5), latest(guest, stats, 0.5))
        while time.monotonic() < until:
            await asyncio.sleep(pace * random.uniform(0.5, 1.5))
            game = state["data"]
            if game["status"] != "playing":
                for ws in players.values():
                    await ws.send(json.dumps({"type": "rematch"}))
                # read everything both have been sent, so neither is left behind
                state, _ = await asyncio.gather(latest(host, stats, 1.0),
                                                latest(guest, stats, 1.0))
                continue
            ws = players[game["turn"]]
            sent = time.monotonic()
            await ws.send(json.dumps({"type": "action", "action": {"col": column(state)}}))
            state = await receive(ws, "state", stats)
            stats.latency.append(time.monotonic() - sent)
            stats.moves += 1
            other = players[3 - game["turn"]]
            state = await receive(other, "state", stats)
    except Exception as e:
        stats.error(f"playing: {type(e).__name__}")
    finally:
        for ws in players.values():
            await ws.close()


async def bot_room(url, stats, until):
    try:
        host, _ = await setup(url, stats, bot=True)
    except Exception as e:
        stats.failed_rooms += 1
        stats.error(f"setup: {type(e).__name__}")
        return
    try:
        state = await latest(host, stats, 0.5)
        while time.monotonic() < until:
            game = state["data"]
            if game["status"] != "playing":
                await host.send(json.dumps({"type": "rematch"}))
                state = await receive(host, "state", stats)
                continue
            if game["turn"] != state["you"]:
                state = await receive(host, "state", stats)
                continue
            sent = time.monotonic()
            await host.send(json.dumps({"type": "action", "action": {"col": column(state)}}))
            state = await receive(host, "state", stats)  # our move
            stats.latency.append(time.monotonic() - sent)
            stats.moves += 1
            if state["data"]["status"] == "playing":
                state = await receive(host, "state", stats)  # the bot's answer
                stats.bot_turns.append(time.monotonic() - sent)
    except Exception as e:
        stats.error(f"bot room: {type(e).__name__}")
    finally:
        await host.close()


def percentile(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--rooms", type=int, default=100, help="rooms of two people")
    parser.add_argument("--bot-rooms", type=int, default=0, help="rooms of a person and a bot")
    parser.add_argument("--pace", type=float, default=3.0, help="seconds between moves")
    parser.add_argument("--seconds", type=float, default=60, help="how long to play for")
    parser.add_argument("--ramp", type=float, default=10, help="seconds to open the rooms over")
    args = parser.parse_args()

    stats = Stats()
    start = time.monotonic()
    until = start + args.ramp + args.seconds
    tasks = []
    total = args.rooms + args.bot_rooms
    for n in range(total):
        await asyncio.sleep(args.ramp / max(total, 1))
        if n < args.bot_rooms:
            tasks.append(asyncio.create_task(bot_room(args.url, stats, until)))
        else:
            tasks.append(asyncio.create_task(person_room(args.url, stats, args.pace, until)))
    await asyncio.gather(*tasks)
    took = time.monotonic() - start - args.ramp

    ms = [x * 1000 for x in stats.latency]
    print(f"{args.rooms} person rooms, {args.bot_rooms} bot rooms, {took:.0f}s of play")
    print(f"  moves: {stats.moves} ({stats.moves / took:.1f} a second), "
          f"rooms that failed to start: {stats.failed_rooms}")
    if ms:
        print(f"  move answered in: median {statistics.median(ms):.0f} ms, "
              f"95% {percentile(ms, 0.95):.0f} ms, 99% {percentile(ms, 0.99):.0f} ms, "
              f"worst {max(ms):.0f} ms")
    if stats.bot_turns:
        bt = stats.bot_turns
        print(f"  bot answered in: median {statistics.median(bt):.1f} s, "
              f"95% {percentile(bt, 0.95):.1f} s (the room waits 1.2 s on purpose)")
    if stats.errors:
        print("  errors:", ", ".join(f"{k} x{v}" for k, v in sorted(stats.errors.items())))


if __name__ == "__main__":
    asyncio.run(main())
