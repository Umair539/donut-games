# Donut Games
Donut-themed multiplayer games played in the browser. The host picks a game, chooses its settings and gets a 4-letter code (or invite link) to share. Everyone waits in a lobby, and the host starts the game when they are ready. The Python server is the source of truth for every move and talks to the browsers over WebSockets.

The server and the page around each game are shared, so a new game only has to provide its rules and its board. Connect Donut (Connect Four) is the first game.

---

## Project Structure

### Server/core: the framework every game plugs into
* **`app.py`**: The FastAPI app. Serves the web frontend, `/api/games` and the `/ws` WebSocket endpoint.
* **`rooms.py`**: Rooms, room codes, the lobby, seats, host start, reconnecting and rematch votes. Knows nothing about any particular game.
* **`base.py`**: `BaseGame`, the interface a game implements.

### Server/games: one file per game
* **`connect4.py`**: Connect Donut rules, move validation and win checks.
* **`__init__.py`**: The list of games that can be hosted.

### web
* **`index.html`**, **`style.css`**, **`app.js`**: The shared shell: game picker, settings form, lobby, and the frame around a game.
* **`games/<name>/`**: One folder per game with its script and stylesheet.
* **`sprites/`**: The donut sprites.

### tests
* Tests for the games, the room framework (using a made-up 2-4 player game) and the server.

---

## Running locally

1. Create a virtual environment and install the project
    ```bash
    python -m venv .venv
    .venv\Scripts\activate        # Windows
    source .venv/bin/activate     # macOS / Linux
    pip install -e .
    ```
2. Start the server
    ```bash
    run_web
    ```
3. Open http://localhost:8000 in two browser tabs (or two devices on the same network), host a game in one and join with the code in the other.

The server listens on `0.0.0.0:8000` by default. Set the `HOST` and `PORT` environment variables to change this.

## Tests and linting
```bash
pytest
flake8
```
flake8 is set to a 100 character line limit in **`.flake8`**.

---

## How a game session works

1. The host picks a game and its settings, and gets a room code. The room starts in the **lobby**.
2. Others join with the code until the game's maximum is reached. The host can start once the minimum number of players is in.
3. After the host starts, the room is **playing**. The server checks every action and sends each player their own view of the game.
4. When the game is over, everyone has to agree for a rematch.

If a player disconnects they have 60 seconds to come back before their seat is lost. Rooms are removed when everyone has left, or after 30 minutes without activity. Rooms are kept in memory, so restarting the server ends any games in progress.

### Messages
All messages are JSON over a single WebSocket at `/ws`.

| Client sends | What it does |
|---|---|
| `{"type": "create", "game": "connect4", "settings": {"rows": 6}}` | Makes a room and seats you as the host. Missing settings use their defaults |
| `{"type": "join", "code": "K7QX"}` | Takes a free seat in a lobby |
| `{"type": "rejoin", "code": "K7QX", "token": "..."}` | Gets your seat back after a refresh or dropped connection |
| `{"type": "start"}` | Host only. Starts the game |
| `{"type": "action", "action": {"col": 3}}` | Plays a move. What an action looks like is up to the game |
| `{"type": "rematch"}` | Votes for a rematch |
| `{"type": "leave"}` | Gives up your seat. If the host leaves the lobby, the room closes |

The server replies with `joined` (your seat and reconnect token), `state`, `error` and `closed`. `state` holds the room's phase (`lobby` or `playing`), who you are, the settings, each player's connection status, and `data`, which is the game's view for you.

---

## Adding a game

1. **Server**: create `Server/games/<name>.py` with a class that extends `BaseGame` and add it to `GAMES` in `Server/games/__init__.py`. It sets `name`, `title`, `min_players`, `max_players` and a `settings_schema` (the host's options), and implements:
    * `create(settings, num_players)`: start a game, raising `GameError` for invalid settings
    * `apply(player, action)`: validate and apply one action, raising `GameError` if it is not allowed
    * `view(player)`: the state that player may see, so hidden information such as a hand of cards stays on the server
    * `over` and `restart()`: for the rematch flow
    * `player_left(player)`: optional, lets a game with more than two players skip someone who left
2. **Browser**: create `web/games/<name>/<name>.js` and `.css`, register the game in `window.Games` (see `web/games/connect4/connect4.js` for the interface), and add them to `index.html`.

The lobby, codes, reconnecting, the settings form and the game picker all come from the framework.

---

## Tools and techniques

* Python
* FastAPI and WebSockets
* asyncio
* HTML, CSS and JavaScript

---

## To-do

* **Deployment**: Dockerfile and deploying to Render.
* **Real-time games**: games such as air hockey will need the server to step the game on a timer and push state many times a second. `BaseGame` has no hook for that yet.
