# Donut Games
Donut-themed multiplayer games played in the browser. The host picks a game, chooses its settings and gets a 6-character code (or invite link) to share. Everyone waits in a lobby, and the host starts the game when they are ready. The Python server is the source of truth for every move and talks to the browsers over WebSockets.

The server and the page around each game are shared, so a new game only has to provide its rules and its board. There are two games so far: Connect Donut (Connect Four) and Donut Switch (an Uno-style game with a normal deck).

---

## Project Structure

### Server/core: the framework every game plugs into
* **`app.py`**: The FastAPI app. Serves the web frontend, `/api/games` and the `/ws` WebSocket endpoint.
* **`rooms.py`**: Rooms, room codes, the lobby, seats, host start, reconnecting and rematch votes. Knows nothing about any particular game.
* **`base.py`**: `BaseGame`, the interface a game implements.

### Server/games: one file per game
* **`connect4.py`**: Connect Donut rules, move validation and win checks.
* **`switch.py`**: Donut Switch rules: dealing, power cards, drawing and adding decks.
* **`__init__.py`**: The list of games that can be hosted.

### web
* **`index.html`**, **`style.css`**, **`app.js`**: The shared shell: game picker, settings form, lobby, and the frame around a game.
* **`games/<name>/`**: One folder per game with its script and stylesheet.
* **`sprites/`**: The donut sprites.
* **`games/switch/cards/`**: Placeholder card images, to be replaced with donut-themed ones.

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

## Deploying on AWS Lightsail

The **`Dockerfile`** runs the server and web pages as one container on a Lightsail container service (about $7/month, `nano`). Lightsail gives it an `https://...cs.amazonaws.com` address, and WebSockets work over it, so no domain is needed. **`infra/main.tf`** is the Terraform.

Needs Terraform, Docker, the AWS CLI configured with credentials, and the [Lightsail plugin](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-install-software.html) (`lightsailctl`) for pushing images.

```bash
cd infra
terraform init
terraform apply                      # 1. creates the empty service
cd ..
docker build -t donut-games .
aws lightsail push-container-image --region us-east-1 --service-name donut-games --label app --image donut-games:latest
                                     # 2. prints an image name like :donut-games.app.1
cd infra
terraform apply -var image=:donut-games.app.1    # 3. runs it; the URL is printed
```

For new code: rebuild, push again, and apply with the new image name.

Run **one instance only**. Rooms live in the memory of that one process, and a restart or redeploy ends games in progress.

---

## How a game session works

1. The host picks a game and its settings, and gets a room code. The room starts in the **lobby**.
2. Others join with the code until the game's maximum is reached. The host can start once the minimum number of players is in.
3. After the host starts, the room is **playing**. The server checks every action and sends each player their own view of the game.
4. When the game is over, everyone has to agree for a rematch.

Names are optional. They are tidied up (no control characters, single spaces, at most 16 characters) and an empty name becomes "Player". If a name is already taken in the room, a number is added, so a second "Sam" becomes "Sam 2". Names only live as long as the room does, and nothing is stored afterwards.

If a player disconnects they have 60 seconds to come back before their seat is lost. Rooms are removed when everyone has left, or after 30 minutes without activity. Rooms are kept in memory, so restarting the server ends any games in progress.

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
| `{"type": "leave"}` | Gives up your seat. If the host leaves the lobby, the room closes |

The server replies with `joined` (your seat and reconnect token), `state`, `error` and `closed`. `state` holds the room's phase (`lobby` or `playing`), who you are, the settings, each player's connection status, and `data`, which is the game's view for you.

---

## Donut Switch rules

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
* If you draw instead of playing, you take 1 card and your turn ends. With *Must play if you can* switched on, you can only draw when nothing in your hand can be played.
* When the pile runs out, the discards are shuffled back in. If there are none, a new deck is added, up to 3. Once the third deck is in, *Must play if you can* switches on for the rest of the round.

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

* **Real-time games**: games such as air hockey will need the server to step the game on a timer and push state many times a second. `BaseGame` has no hook for that yet.
