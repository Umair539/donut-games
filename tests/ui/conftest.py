"""Browser tests: the real pages in Chromium against a real server.

The server runs on a thread in this process, so a test can set up a game exactly (a hand that
wins in one play, say) and can stop it cleanly and start a fresh one, like a deploy does.
Skipped unless requirements-ui.txt is installed, along with `playwright install chromium`.
"""
import asyncio
import inspect
import socket
import threading
import time

import pytest

pytest.importorskip("playwright", reason="needs requirements-ui.txt")

import uvicorn  # noqa: E402

from Server.core import app as server  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Uvicorn(uvicorn.Server):
    async def serve(self, sockets=None):
        self.loop = asyncio.get_running_loop()  # so tests can run code on the server's loop
        await super().serve(sockets)


class LiveServer:
    """The app on a background thread. stop() shuts it down the way docker stop does."""

    def __init__(self, port, snapshot_path=None):
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.snapshot_path = snapshot_path
        self.thread = None

    def start(self):
        server.SNAPSHOT_PATH = self.snapshot_path
        config = uvicorn.Config(server.app, host="127.0.0.1", port=self.port, log_level="warning",
                                ws_max_size=16 * 1024, timeout_graceful_shutdown=5)
        self.uvicorn = _Uvicorn(config)
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.uvicorn.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("server didn't start")
            time.sleep(0.02)

    def stop(self):
        if not self.thread.is_alive():
            return
        self.uvicorn.should_exit = True
        self.thread.join(15)
        assert not self.thread.is_alive(), "server didn't stop"
        server.rooms.rooms.clear()  # a new process starts with nothing in memory
        server.SNAPSHOT_PATH = None

    def call(self, fn):
        """Run fn on the server's event loop, awaiting it if it's async, and return the result.
        Rooms must only be touched there, never from the test's own thread."""
        async def run():
            result = fn()
            return await result if inspect.isawaitable(result) else result
        return asyncio.run_coroutine_threadsafe(run(), self.uvicorn.loop).result(10)

    def room(self, code):
        return self.call(lambda: server.rooms.get(code))

    def push(self, code, change):
        """Change a room on the server, then send everyone their new view, as a move would."""
        def apply():
            room = server.rooms.get(code)
            change(room)
            return server.broadcast(room)
        self.call(apply)


@pytest.fixture
def live_server(tmp_path):
    """Saves rooms like the real deploy, so stop() then start() is a restart that keeps games."""
    server.rooms.rooms.clear()
    live = LiveServer(free_port(), str(tmp_path / "rooms.json"))
    live.start()
    yield live
    live.stop()


@pytest.fixture
def player(browser, live_server):
    """Makes players: each is its own browser context, so they share nothing, like different
    people on different devices."""
    contexts = []

    def make(name=""):
        context = browser.new_context()
        context.set_default_timeout(10_000)
        contexts.append(context)
        page = context.new_page()
        page.goto(live_server.url)
        page.locator(".game-option").first.wait_for()
        if name:
            page.fill("#player-name", name)
        return page

    yield make
    for context in contexts:
        context.close()
