# Donut Games
Donut-themed multiplayer games played in the browser. The host picks a game, chooses its settings and gets a 6-character code (or invite link) to share. Everyone waits in a lobby, and the host starts the game when they are ready. The Python server is the source of truth for every move and talks to the browsers over WebSockets.

The server and the page around each game are shared, so a new game only has to provide its rules and its board. There are two games so far: Connect Donut (Connect Four) and Donut Cards (an Uno-style game with a normal deck).

---

## Project Structure

### Server/core: the framework every game plugs into
* **`app.py`**: The FastAPI app. Serves the web frontend, `/api/games` and the `/ws` WebSocket endpoint.
* **`rooms.py`**: Rooms, room codes, the lobby, seats, host start, reconnecting and rematch votes. Knows nothing about any particular game.
* **`base.py`**: `BaseGame`, the interface a game implements.

### Server/games: one file per game
* **`connect4.py`**: Connect Donut rules, move validation and win checks.
* **`switch.py`**: Donut Cards rules: dealing, power cards, drawing and adding decks.
* **`__init__.py`**: The list of games that can be hosted.

### web
* **`index.html`**, **`style.css`**, **`app.js`**: The shared shell: game picker, settings form, lobby, and the frame around a game.
* **`games/<name>/`**: One folder per game with its script and stylesheet.
* **`sprites/`**: The donut sprites.
* **`games/switch/cards/`**: Placeholder card images, to be replaced with donut-themed ones.

### tests
* Tests for the games, the room framework (using a made-up 2-4 player game), the server, and saving and restoring rooms across a restart.

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

The server listens on `0.0.0.0:8000` by default. Set the `HOST` and `PORT` environment variables to change this. Rooms are only kept in memory unless `SNAPSHOT_PATH` is set (see below).

## Tests and linting
```bash
pytest
flake8
```
flake8 is set to a 100 character line limit in **`.flake8`**.

---

## Deploying

The game server runs as one Docker container on a free-tier Google Cloud VM, reached at `server.donutgames.co.uk` through a Cloudflare Tunnel. The web pages are on Cloudflare Pages at `donutgames.co.uk`. **`infra/google/`** is the Terraform, and its [README](infra/google/README.md) covers setting it up and fixing it.

* **Code:** push to `main`. GitHub Actions runs the tests, builds the image and deploys it within seconds (`gar.yml`), and changes to `web/` go to Pages (`pages.yml`). Games in progress carry on through a deploy, see below.
* **The VM's startup script** (`infra/google/startup.sh.tftpl`) isn't deployed by GitHub: run `terraform apply` in `infra/google`, then stop and start the VM so it runs the new script.

Run **one instance only**. Rooms live in the memory of that one process.

The server has been on both clouds. It started on AWS Lightsail and moved to Google Cloud. The AWS resources are torn down, but **`infra/aws/`** and its workflow (`ecr.yml`, manual runs only for now) are kept so it can move back. Its [README](infra/aws/README.md) says what to change.

---

## How a game session works

1. The host picks a game and its settings, and gets a room code. The room starts in the **lobby**.
2. Others join with the code until the game's maximum is reached. The host can start once the minimum number of players is in.
3. After the host starts, the room is **playing**. The server checks every action and sends each player their own view of the game.
4. When the game is over, everyone still in the room has to agree for a rematch. Anyone who left is dropped and the next game is dealt for the players who stayed, as long as there are still enough of them.

Settings are fixed when the room is made: for different ones, make a new room. The host's only extra power is starting the game. If the host leaves the lobby, the next player becomes host.

Names are optional. They are tidied up (no control characters, single spaces, at most 16 characters) and an empty name becomes "Player". If a name is already taken in the room, a number is added, so a second "Sam" becomes "Sam 2". Names only live as long as the room does, and nothing is stored afterwards.

If a player disconnects they have 60 seconds to come back before their seat is lost. Rooms are removed as soon as everyone has left, or after 10 minutes without activity in the lobby (15 once the game has started).

### Surviving a restart

Rooms are kept in memory. When `SNAPSHOT_PATH` is set (the Docker image sets it to `/data/rooms.json`), the server also saves them there every 30 seconds and when it shuts down, and loads them when it starts. A deploy then only shows players a few seconds of "reconnecting":

* Their browsers rejoin by themselves with their reconnect tokens.
* Everyone gets 2 minutes to come back instead of 1.
* The turn timer is paused until everyone still in the game is back, then the player whose turn it is gets a full turn.

For this to work the container has to be stopped rather than killed (`docker stop`, which lets it save), and `/data` has to be a volume. The deploy script on the VM does both. If the server crashes instead, rooms come back from the last 30-second save. Saved rooms that don't fit the new code, because a game's saved state changed shape, are dropped instead of loaded.

### Messages
All messages are JSON over a single WebSocket at `/ws`.

| Client sends | What it does |
|---|---|
| `{"type": "create", "game": "connect4", "name": "Sam", "settings": {"rows": 6}}` | Makes a room and seats you as the host. Missing settings use their defaults |
| `{"type": "join", "code": "K7QX4M", "name": "Sam"}` | Takes a free seat in a lobby |
| `{"type": "rejoin", "code": "K7QX4M", "token": "..."}` | Gets your seat back after a refresh or dropped connection |
| `{"type": "start"}` | Host only. Starts the game |
| `{"type": "action", "action": {"col": 3}}` | Plays a move. What an action looks like is up to the game |
| `{"type": "rematch"}` | Votes for a rematch |
| `{"type": "leave"}` | Gives up your seat. If you were the host, the next player takes over |

The server replies with `joined` (your seat and reconnect token), `state`, `error` and `closed`. `state` holds the room's phase (`lobby` or `playing`), who you are, the settings, each player's connection status, and `data`, which is the game's view for you.

---

## Donut Cards rules

2 to 10 players. Get rid of all your cards to win. 6 or more players always use 2 decks, and smaller games can choose 2. Who goes first is random each round.

By default the first player out wins and the round ends. With *Keep playing for 2nd, 3rd...* switched on, players who go out watch while the rest play on for places, until only one is left.

On your turn you either **play** or **draw**. Your first card must match the top card's suit or rank, or be an ace. You can then keep adding cards in the same turn as long as each one *connects* to the card before it: the same rank, or the same suit one step up or down (an ace sits next to both the 2 and the king). An ace is only wild as your first card. Later in the turn it has to connect like any other card.

| Card | Effect |
| --- | --- |
| **Ace** | Wild as the first card of your turn: play it on anything. If an ace is the last card of your turn, pick the suit the next player must follow. Otherwise keep going from it, e.g. A♠ then 2♠ or K♠. |
| **2** | The next player picks up 2, unless they play a 2 and pass on the total. |
| **8** | The next player misses a turn, unless they play an 8. Each 8 skips one more player. |
| **Black jack** | The next player picks up 5 (the host can choose 5 to 7), unless they play a black jack to pass it on or a red jack to cancel it. |
| **Queen** | Must be covered in the same turn by a card of its suit, or by another queen (which then needs covering too). If you can't cover it, you pick up 1. |
| **King** | Reverses play. Two kings in one turn keep the same direction, three reverse it, four keep it. |

* An attack (2s, 8s or black jacks) only reaches the next player if it is at the end of your turn. If you answer one and then carry on with other cards, it stops there.
* Aces can't be used to answer an attack.
* You can't go out on a power card (A, 2, 8, J, Q, K). If you try, you pick up 1.
* **Cards!** Press the Cards button on the turn before the one you plan to go out on, then play all your remaining cards on your next go. Everyone sees a *Cards!* badge next to your name until then, so they can try to stop you. If you go out without having called on your previous turn, you pick up 1 instead. Calling and going out on the same turn doesn't count. Being skipped by an 8 doesn't use up your call.
* If you draw instead of playing, you take 1 card and your turn ends. With *Must play if you can* switched on, you can only draw when nothing in your hand can be played.
* When the pile runs out, the discards are shuffled back in. If there are none, a new deck is added, up to 3. The first time a deck is added, *Must play if you can* switches on for the rest of the round, since it means everyone has been drawing instead of playing.

---

## Adding a game

1. **Server**: create `Server/games/<name>.py` with a class that extends `BaseGame` and add it to `GAMES` in `Server/games/__init__.py`. It sets `name`, `title`, `min_players`, `max_players` and a `settings_schema` (the host's options), and implements:
    * `create(settings, num_players)`: start a game, raising `GameError` for invalid settings
    * `apply(player, action)`: validate and apply one action, raising `GameError` if it is not allowed
    * `view(player)`: the state that player may see, so hidden information such as a hand of cards stays on the server
    * `over` and `restart()`: for the rematch flow
    * `player_left(player)`: optional, lets a game with more than two players skip someone who left
    * `snapshot()` and `restore(data)`: the full state as JSON and back, so games survive a server restart. Bump `snapshot_version` when the shape changes. A game without them still works, its rooms are just lost on a restart
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

* **Real-time games**: games such as air hockey will need the server to step the game on a timer and push state many times a second. `BaseGame` has no hook for that yet.
