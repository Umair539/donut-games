# Donut Games
Donut-themed multiplayer games played in the browser. The host picks a game, chooses its settings and gets a 6-character code (or invite link) to share. Everyone waits in a lobby, and the host starts the game when they are ready. The Python server is the source of truth for every move and talks to the browsers over WebSockets.

The server and the page around each game are shared, so a new game only has to provide its rules and its board. There are three games so far: Connect Donut (Connect Four), Donut Cards (British Blackjack, the card game also called Switch, not the casino game) and Donut Checkers (English draughts).

**Play it:** https://donutgames.co.uk

---

## Project Structure

### Server/core: the framework every game plugs into
* **`app.py`**: The FastAPI app. Serves the web frontend, `/api/games` and the `/ws` WebSocket endpoint.
* **`rooms.py`**: Rooms, room codes, the lobby, seats, host start, reconnecting and rematch votes. Knows nothing about any particular game.
* **`base.py`**: `BaseGame`, the interface a game implements.

### Server/games: one file per game
* **`connect4.py`**: Connect Donut rules, move validation and win checks.
* **`checkers.py`**: Donut Checkers rules: multi-jumps, the forced jumps setting, kings and the 40-move draw.
* **`switch.py`**: Donut Cards rules: dealing, power cards, drawing and adding decks.
* **`__init__.py`**: The list of games that can be hosted.

### Server/bots: computer players
* **`switch.py`**: The Donut Cards bots, see [Bots](#bots).
* **`connect4.py`**, **`checkers.py`**: The Connect Donut and Donut Checkers bots.
* **`__init__.py`**: Which bots each game offers, by level.

### web
* **`index.html`**, **`style.css`**, **`app.js`**: The shared shell: game picker, settings form, lobby, and the frame around a game.
* **`games/<name>/`**: One folder per game with its script and stylesheet.
* **`sprites/`**: The donut sprites.
* **`games/switch/cards/`**: The card images, drawn from the sprites by `scripts/make_cards.py`, which also draws the checkers kings in `games/checkers/`.

### scripts
* **`make_cards.py`**: Draws the Donut Cards faces from the donut sprites, one colour per suit. Needs Pillow. Run it again after changing a sprite.
* **`arena.py`**: Plays bots against each other and reports how often each wins, e.g. `python scripts/arena.py search heuristic --games 500 --workers 8`.

### tests
* Tests for the games, the room framework (using a made-up 2-4 player game), the server, and saving and restoring rooms across a restart.
* **`ui/`**: browser tests of the pages, see below.

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

The browser tests in **`tests/ui/`** drive the real pages in Chromium, with each player in their own browser, against a real server: hosting, the lobby, both games, leaving, rematches, chat, refreshing, and a server restart mid-game. They're skipped unless their extra packages are installed:
```bash
pip install -r requirements-ui.txt
playwright install chromium
pytest tests/ui                  # add --headed to watch
```
In GitHub Actions they run before every deploy (**`.github/workflows/ui-tests.yml`**).

---

## Deploying

The game server runs as one Docker container on a free-tier Google Cloud VM, reached at `server.donutgames.co.uk` through a Cloudflare Tunnel. The web pages are on Cloudflare Pages at `donutgames.co.uk`. **`infra/google/`** is the Terraform, and its [README](infra/google/README.md) covers setting it up and fixing it.

* **Code:** push to `main`. GitHub Actions (`deploy.yml`) runs flake8, the tests and the browser tests, then builds the server image and deploys it within seconds, then sends changes to `web/` to Pages. Nothing goes out unless every check passed, and the pages wait for the server, so new pages never talk to an old server. Games in progress carry on through a deploy, see below.
* **The VM's startup script** (`infra/google/startup.sh.tftpl`) isn't deployed by GitHub: run `terraform apply` in `infra/google`, then stop and start the VM so it runs the new script.

Run **one instance only**. Rooms live in the memory of that one process.

The server has been on both clouds: it started on AWS Lightsail and moved to Google Cloud. The AWS setup is retired and its resources are destroyed. **`infra/aws/`** and its workflow (`ecr.yml`, manual runs only) are kept for completeness, as a record of it.

---

## How a game session works

1. The host picks a game and its settings, and gets a room code. The room starts in the **lobby**.
2. Others join with the code until the game's maximum is reached. The host can start once the minimum number of players is in.
3. After the host starts, the room is **playing**. The server checks every action and sends each player their own view of the game.
4. When the game is over, everyone still in the room has to agree for a rematch. Anyone who left is dropped and the next game is dealt for the players who stayed, as long as there are still enough of them.

Settings are fixed when the room is made: for different ones, make a new room. The host's only extra powers are starting the game and adding bots. If the host leaves the lobby, the next person becomes host.

In a game that has bots, the host can fill seats with them in the lobby. A bot moves a moment after its turn comes, always agrees to a rematch, and comes back after a restart. Bots never keep a room open by themselves: once every person has left, the room closes.

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
| `{"type": "add_bot", "level": "medium"}` | Host only, in the lobby. Fills a seat with a computer player |
| `{"type": "remove_bot", "player": 3}` | Host only, in the lobby. Takes a bot back out |
| `{"type": "start"}` | Host only. Starts the game |
| `{"type": "action", "action": {"col": 3}}` | Plays a move. What an action looks like is up to the game |
| `{"type": "rematch"}` | Votes for a rematch |
| `{"type": "leave"}` | Gives up your seat. If you were the host, the next player takes over |

The server replies with `joined` (your seat and reconnect token), `state`, `error` and `closed`. `state` holds the room's phase (`lobby` or `playing`), who you are, the settings, each player's connection status, and `data`, which is the game's view for you.

---

## Donut Cards rules

2 to 10 players. Get rid of all your cards to win. 6 or more players always use 2 decks, and smaller games can choose 2. Who goes first is random each round.

Each donut colour is a suit: chocolate is spades ♠, blue is clubs ♣, pink is hearts ♥ and orange is diamonds ♦. Chocolate and blue are the dark suits (black on a normal deck), pink and orange the bright ones (red).

By default the first player out wins and the round ends. With *Keep playing for 2nd, 3rd...* switched on, players who go out watch while the rest play on for places, until only one is left.

On your turn you either **play** or **pick up**. Your first card must match the top card's suit or rank, or be an ace. You can then keep adding cards in the same turn as long as each one *connects* to the card before it: the same rank, or the same suit one step up or down (an ace sits next to both the 2 and the king). An ace is only wild as your first card. Later in the turn it has to connect like any other card.

| Card | Effect |
| --- | --- |
| **Ace** | Wild as the first card of your turn: play it on anything. If an ace is the last card of your turn, pick the suit the next player must follow. Otherwise keep going from it, e.g. A♠ then 2♠ or K♠. |
| **2** | The next player picks up 2, unless they play a 2 and pass on the total. |
| **8** | The next player misses a turn. Two 8s skip two players, and so on. What answering an 8 with your own 8s does is a setting: *Stack* (the default) adds yours on, so two 8s answered with one skip the next 3 players; *Replace* starts again from yours, so only the player after you is skipped; *Skip straight away* means an 8 can't be answered at all. |
| **Chocolate or blue jack** | The next player picks up 7 (the host can choose 1 to 7), unless they play a chocolate or blue jack to pass it on, or a pink or orange jack to cancel it. |
| **Queen** | Must be covered in the same turn by a card of its suit, or by another queen (which then needs covering too). If you can't cover it, you pick up 1. |
| **King** | Reverses play, but only if your turn ends on it. Kings at the end of a turn cancel out in pairs: K or K K K reverses, K K or K K K K doesn't, and K Q♠ 3♠ doesn't either. Whoever's turn it is is outlined in orange, and whoever plays next in faded orange. |

* An attack (2s, 8s or chocolate and blue jacks) only reaches the next player if it is at the end of your turn. If you answer one and then carry on with other cards, it stops there.
* Aces can't be used to answer an attack.
* You can't go out on a power card (A, 2, 8, J, Q, K). If you try, you pick up 1.
* **Cards!** Press the Cards button on the turn before the one you plan to go out on, then play all your remaining cards on your next go. Everyone sees a *Cards!* badge next to your name until then, so they can try to stop you. If you go out without having called on your previous turn, you pick up 1 instead. With *Pick up 1 for calling and not going out* switched on (it's off by default), calling and then not going out on your next go costs 1 too (just the 1 if you played your last card but couldn't go out on it). Calling and going out on the same turn doesn't count. Being skipped by an 8 doesn't use up your call or cost you a card.
* **Mistakes.** By default you can try to play a card that doesn't go: cards aren't greyed out in your hand. A wrong card comes back to you with a pick-up of 1, on top of any 2s, jacks or 8 you were facing, and your turn ends. The cards before it stay played (*Allowed, good cards stay*), or the host can choose *Allowed, whole play comes back*, or *Not allowed* to only let cards that go be picked.
* If you pick up instead of playing, you take 1 card and your turn ends. With *Must play if you can* switched on, you can only pick up when nothing in your hand can be played.
* When the pile runs out, the discards are shuffled back in. If there are none, a new deck is added, up to 3. The first time a deck is added, *Must play if you can* switches on for the rest of the round, since it means everyone has been picking up instead of playing.

---

## Bots

Connect Donut and Donut Checkers each have one bot, in **`Server/bots/`**. Both look ahead with alpha-beta search, a move deeper each time, for a second, then play the best move from the deepest search they finished. On a normal Connect Donut board that's 10 to 12 moves ahead. In checkers a multi-jump counts as one turn and it sees 6 to 10 turns ahead, and where its look ahead ends in the middle of an exchange it carries on through the jumps, so it doesn't stop just before losing a donut back. They play on their own quick copy of the board, and the tests check the checkers bot's list of moves matches the game's in every position of many random games.

Donut Cards has three computer players in **`Server/bots/switch.py`**. The lobby only offers the best, Hard, so it just says *Add a bot* and the bots are called Bot, Bot 2 and so on (with more than one level on offer, the buttons and names say which). The other two are kept because Hard is built on Medium, and to measure against.

* **Easy** plays a random legal move, nearly always playing rather than picking up.
* **Medium** scores every legal move and plays the best looking one. A move scores well if it leaves fewer cards, keeps aces, 2s, 8s and jacks for later, keeps cards that still fit the suit, attacks the next player (more so if they're close to going out), and leaves a hand that can all go next turn, in which case it calls cards.
* **Hard** searches. It takes Medium's 8 favourite moves and plays each one out to the end of the game many times, with Medium playing every seat, and picks the one that wins most. All the moves are tried in the same sampled games with the same luck after, so the difference between two moves is the moves and not the deal, and after each round the worse half is dropped. It thinks for up to 5 seconds, and stops early once one move is clearly ahead. A search starved of play-outs picks worse than Medium does, so if a busy server leaves it fewer than 100, it plays Medium's choice.

**Bots don't cheat.** A bot is only ever handed a copy of the game with every card it can't see dealt again at random, so it knows what a person in its seat would: its own hand, every card played, and how many cards everyone holds. Hard also deals those cards to fit what the round has shown, since picking up says something about your hand: with *Must play if you can* on, it proves you had nothing that could go. And when the discards are shuffled back into the pile, nobody holding cards then can have one of them (unless another copy of the card is still out). The game keeps a history of plays, pick-ups and shuffles for this.

**How strong they are**, from `scripts/arena.py` (seats rotate, so nobody keeps a lucky seat):

| Game | Result |
| --- | --- |
| Medium against Easy, 2 players | Medium wins 76% |
| Medium and 3 Easy, 4 players | Medium wins 49% (a fair share is 25%) |
| Hard against Medium, 2 players | Hard wins 55 to 59% |
| Hard against Medium, 2 players, 2 decks | Hard wins 51% |
| Hard, Medium and Easy, 3 players | Hard 44%, Medium 43%, Easy 14% |
| Hard and 3 Medium, 4 players | Hard wins 26 to 28% (25%) |
| Hard and 5 Medium, 6 players (2 decks) | Hard wins 17% (17%) |
| Hard on only 15 or 30 play-outs against Medium, 2 players | Hard wins 46% |

So Hard is clearly better one on one with a single deck, and as good as Medium everywhere else.

What limits Hard in bigger games is what it can't see. A test version that could see everyone's hand (but not the pile) won 72% of 4-player games against three Mediums, and the honest one wins about 26%. Pick-ups give away less than you'd think: they raise the share of an opponent's cards a guess gets right from 18% to 19%, which made no difference to winning that 400-game runs could measure. Medium's weights were tuned in 6,000-game runs, and no single change beat them clearly.

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
3. **Bots**, optional: add classes with `choose(game, player)`, returning the list of actions that make the bot's turn, to `BOTS` in `Server/bots/__init__.py`, by level. The game needs a `copy()` and a `turn` attribute, since each bot thinks about its own copy of the game in a thread.

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
