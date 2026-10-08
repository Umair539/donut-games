import random
from collections import deque

from Server.core.base import TIMER_SETTINGS, BaseGame, GameError, check_timer

SUITS = ("S", "H", "D", "C")
SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
DONUTS = {"S": "chocolate", "H": "pink", "D": "orange", "C": "blue"}  # each suit's donut
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
POWER = {"A", "2", "8", "J", "Q", "K"}  # you can't go out on one of these
MAX_DECKS = 3  # decks are added when the cards run out, up to this many
BIG_GAME = 6  # from this many players at least two decks are used
SHOWN_DISCARDS = 4
LOG_LENGTH = 6

# attacks the next player has to answer, and the rank that answers each
TWO = "two"
JACK = "jack"
SKIP = "skip"
COUNTERS = {TWO: "2", JACK: "J", SKIP: "8"}

# what answering an 8 with an 8 does: adds to the skips, starts them again, or can't be done
# because an 8 skips straight away
STACK = "stack"
REPLACE = "replace"
IMMEDIATE = "immediate"
EIGHTS = (STACK, REPLACE, IMMEDIATE)

PLAYING = "playing"
WIN = "win"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def rank(card):
    return card[:-1]


def suit(card):
    return card[-1]


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def is_black_jack(card):
    return rank(card) == "J" and suit(card) in ("S", "C")


def new_deck():
    return [r + s for s in SUITS for r in RANKS]


def show(cards):
    return " ".join(rank(c) + SYMBOLS[suit(c)] for c in cards)


def connects(prev, card):
    """Whether card may follow prev within one turn. An ace is only wild as the first card,
    so here it connects like any other."""
    if rank(prev) == "Q":  # a queen must be covered by its own suit or another queen
        return suit(card) == suit(prev) or rank(card) == "Q"
    if rank(card) == rank(prev):
        return True
    gap = abs(RANKS.index(rank(card)) - RANKS.index(rank(prev)))
    return suit(card) == suit(prev) and gap in (1, len(RANKS) - 1)  # A sits next to 2 and K


class Switch(BaseGame):
    name = "switch"
    title = "Donut Cards"
    description = "British Blackjack, also called Switch. Empty your hand first."
    min_players = 2
    max_players = 10

    settings_schema = [
        {"key": "hand_size", "label": "Starting cards", "type": "int", "min": 1, "max": 7,
         "default": 7},
        {"key": "jack_penalty", "label": "Chocolate/blue jack pick-up", "type": "int", "min": 5,
         "max": 7, "default": 5},
        {"key": "decks", "label": "Decks (6+ players always get 2)", "type": "int", "min": 1,
         "max": 2, "default": 1},
        {"key": "eights", "label": "Answering an 8 with an 8", "type": "choice",
         "default": STACK, "options": [
             {"value": STACK, "label": "Stack",
              "help": "The skips add up. Two 8s answered with one skip the next 3 players."},
             {"value": REPLACE, "label": "Replace",
              "help": "Your 8s start the skips again. Two 8s answered with one skip only the "
                      "player after you."},
             {"value": IMMEDIATE, "label": "Skip straight away",
              "help": "An 8 can't be answered: the next player is skipped at once. Two 8s skip "
                      "the next 2 players."},
         ]},
        {"key": "force_play", "label": "Must play if you can", "type": "bool",
         "default": False},
        {"key": "play_on", "label": "Keep playing for 2nd, 3rd...", "type": "bool",
         "default": False},
        {"key": "call_penalty", "label": "Pick up 1 for calling and not going out",
         "type": "bool", "default": False},
        *TIMER_SETTINGS,
    ]

    @classmethod
    def create(cls, settings, num_players):
        return cls(num_players, **settings)

    def __init__(self, players, hand_size=7, jack_penalty=5, decks=1, eights=STACK,
                 force_play=False, play_on=False, call_penalty=False, timer=False,
                 turn_seconds=20, rng=None):
        if not _is_int(players) or not self.min_players <= players <= self.max_players:
            raise GameError(f"Need {self.min_players} to {self.max_players} players")
        if not _is_int(hand_size) or not 1 <= hand_size <= 7:
            raise GameError("Starting cards must be between 1 and 7")
        if not _is_int(jack_penalty) or not 5 <= jack_penalty <= 7:
            raise GameError("Chocolate/blue jack pick-up must be between 5 and 7")
        if not _is_int(decks) or not 1 <= decks <= 2:
            raise GameError("Decks must be 1 or 2")
        if eights not in EIGHTS:
            raise GameError("Invalid value for answering an 8")
        if not isinstance(force_play, bool):
            raise GameError("Invalid value for must play if you can")
        if not isinstance(play_on, bool):
            raise GameError("Invalid value for keep playing")
        if not isinstance(call_penalty, bool):
            raise GameError("Invalid value for picking up after calling")
        check_timer(timer, turn_seconds)

        self.num_players = players
        self.hand_size = hand_size
        self.jack_penalty = jack_penalty
        self.start_decks = max(decks, 2 if players >= BIG_GAME else 1)
        self.eights = eights
        self.force_play = force_play
        self.play_on = play_on  # after someone goes out, the rest play on for places
        self.call_penalty = call_penalty  # calling cards and not going out costs a card
        self.rng = rng or random.Random()
        self.gone = set()  # players who left
        self.round = 1
        self._deal()

    def _deal(self):
        self.decks = self.start_decks
        self.force = self.force_play  # switched on for good once an extra deck comes in
        self.pile = [card for _ in range(self.decks) for card in new_deck()]
        self.rng.shuffle(self.pile)
        self.hands = {
            p: [self.pile.pop() for _ in range(self.hand_size)]
            for p in range(1, self.num_players + 1)
        }
        for _ in range(len(self.pile)):  # start on a plain card if there is one
            if rank(self.pile[-1]) not in POWER:
                break
            self.pile.insert(0, self.pile.pop())
        self.discard = [self.pile.pop()]
        self.suit = suit(self.discard[-1])  # the suit to follow, set by an ace
        self.pending = None  # an attack waiting for the player whose turn it is
        self.direction = 1
        self.turn = self.rng.randint(1, self.num_players)
        self.status = PLAYING
        self.winner = None
        self.places = []  # players who are out, in the order they went out
        self.log = deque(maxlen=LOG_LENGTH)
        # "Cards!" has to be called during the turn before the one you go out on
        self.calling = False  # the player whose turn it is has called this turn
        self.called = set()  # players who called on their last turn
        self._start_history()

    def _start_history(self):
        """What everyone at the table has seen happen this round, for bots to reason about:
        ("play", player, cards), ("draw", player, count, without), ("left", player) and
        ("shuffle", cards, decks) when the discards go back into the pile. without is what
        the cards held before drawing couldn't have been, as (suits, ranks, certain), or None
        if picking up says nothing. A game restored after a restart starts its history again
        from the hands as they are."""
        self.history = []
        self.history_hands = {p: len(hand) for p, hand in self.hands.items()}

    # ---- the framework interface

    @property
    def over(self):
        return self.status != PLAYING

    def apply(self, player, action):
        if self.over:
            raise GameError("Game is over")
        if player != self.turn:
            raise GameError("Not your turn")
        if not isinstance(action, dict):
            raise GameError("Invalid action")
        kind = action.get("type")
        if kind == "play":
            self._play(player, action.get("cards"), action.get("suit"))
        elif kind == "draw":
            self._draw_instead(player)
        elif kind == "call":
            if self.calling:
                raise GameError("You already called cards")
            self.calling = True
            self._log(player, "called cards!")
        else:
            raise GameError("Invalid action")

    def view(self, player):
        return {
            "hand": list(self.hands.get(player, [])),
            "counts": [
                {"id": p, "cards": len(hand)} for p, hand in sorted(self.hands.items())
            ],
            "discard": self.discard[-SHOWN_DISCARDS:],
            "suit": self.suit,
            "pending": dict(self.pending) if self.pending else None,
            "calling": self.calling,
            "called": sorted(self._called_now()),
            "turn": self.turn,
            "next": None if self.over else self._next(self.turn),
            "direction": self.direction,
            "pile": len(self.pile),
            "decks": self.decks,
            "force": self.force,
            "status": self.status,
            "winner": self.winner,
            "places": list(self.places),
            "round": self.round,
            "log": list(self.log),
        }

    def restart(self):
        self.round += 1
        self._deal()

    def player_left(self, player):
        self.gone.add(player)
        if self.over:
            return
        # their cards go back into the pick-up pile, each at a random place
        cards, self.hands[player] = self.hands[player], []
        self.history.append(("left", player))
        for card in cards:
            self.pile.insert(self.rng.randint(0, len(self.pile)), card)
        if len(self._active()) <= 1:
            self._end()
        elif self.turn == player:
            self.pending = None  # an attack on someone who left is dropped
            self._advance()

    def snapshot(self):
        return {
            "num_players": self.num_players,
            "hand_size": self.hand_size,
            "jack_penalty": self.jack_penalty,
            "start_decks": self.start_decks,
            "eights": self.eights,
            "force_play": self.force_play,
            "play_on": self.play_on,
            "call_penalty": self.call_penalty,
            "gone": sorted(self.gone),
            "round": self.round,
            "decks": self.decks,
            "force": self.force,
            "pile": list(self.pile),
            "hands": [[p, list(hand)] for p, hand in sorted(self.hands.items())],
            "discard": list(self.discard),
            "suit": self.suit,
            "pending": dict(self.pending) if self.pending else None,
            "direction": self.direction,
            "turn": self.turn,
            "status": self.status,
            "winner": self.winner,
            "places": list(self.places),
            "log": list(self.log),
            "calling": self.calling,
            "called": sorted(self.called),
        }

    def copy(self, rng=None):
        """A separate game in the same state, much faster than snapshot and restore. Bots use
        it to try moves out."""
        game = Switch.__new__(Switch)
        game.__dict__.update(self.__dict__)
        game.rng = rng or random.Random()
        game.gone = set(self.gone)
        game.pile = list(self.pile)
        game.hands = {p: list(hand) for p, hand in self.hands.items()}
        game.discard = list(self.discard)
        game.pending = dict(self.pending) if self.pending else None
        game.places = list(self.places)
        game.log = deque(self.log, maxlen=LOG_LENGTH)
        game.called = set(self.called)
        game.history = list(self.history)
        return game

    @classmethod
    def restore(cls, data):
        """The shuffle order isn't kept: a fresh random generator is as good as the old one."""
        game = cls(data["num_players"], hand_size=data["hand_size"],
                   jack_penalty=data["jack_penalty"], decks=data["start_decks"],
                   eights=data.get("eights", STACK),  # games saved before it was a setting
                   force_play=data["force_play"], play_on=data["play_on"],
                   # games saved before this was a setting always had the penalty
                   call_penalty=data.get("call_penalty", True))
        game.gone = set(data["gone"])
        game.round = data["round"]
        game.decks = data["decks"]
        game.force = data["force"]
        game.pile = list(data["pile"])
        game.hands = {p: list(hand) for p, hand in data["hands"]}
        game.discard = list(data["discard"])
        game.suit = data["suit"]
        game.pending = dict(data["pending"]) if data["pending"] else None
        game.direction = data["direction"]
        game.turn = data["turn"]
        game.status = data["status"]
        game.winner = data["winner"]
        game.places = list(data["places"])
        game.log = deque(data["log"], maxlen=LOG_LENGTH)
        game.calling = data["calling"]
        game.called = set(data["called"])
        game._start_history()
        if set(game.hands) != set(range(1, game.num_players + 1)):
            raise GameError("Saved hands don't match the players")
        return game

    # ---- moves

    def can_start(self, card):
        """Whether card may be the first one played this turn."""
        if self.pending:
            return rank(card) == COUNTERS[self.pending["kind"]]
        return rank(card) == "A" or suit(card) == self.suit or rank(card) == rank(self.discard[-1])

    def _play(self, player, cards, chosen):
        if not isinstance(cards, list) or not cards or not all(isinstance(c, str) for c in cards):
            raise GameError("Pick the cards to play")
        remaining = list(self.hands[player])
        for card in cards:
            if card not in remaining:
                raise GameError("You don't have that card")
            remaining.remove(card)
        if not self.can_start(cards[0]):
            raise GameError(self._hint())
        for prev, card in zip(cards, cards[1:]):
            if not connects(prev, card):
                raise GameError(f"{show([card])} can't follow {show([prev])}")
        last = cards[-1]
        if rank(last) == "A" and chosen not in SUITS:
            raise GameError("Choose a suit for your ace")

        # played in order: attacks build up, and any other card ends them
        pending = self._incoming()
        kings = 0  # kings at the end of the turn so far: an odd number reverses play
        for card in cards:
            kings = kings + 1 if rank(card) == "K" else 0
            pending = self._after(pending, card)
        skips = 0
        if self.eights == IMMEDIATE and pending and pending["kind"] == SKIP:
            skips, pending = pending["count"], None  # nobody gets the chance to answer

        self.hands[player] = remaining
        self.discard.extend(cards)
        self.history.append(("play", player, tuple(cards)))
        self.pending = pending
        self.suit = chosen if rank(last) == "A" else suit(last)

        text = f"played {show(cards)}"
        if rank(last) == "A":
            text += f" and asked for {DONUTS[chosen]}"
        if kings % 2:
            self.direction = -self.direction
            text += ", reversing play"
        self._log(player, text)

        tried = not remaining  # played their last card, before any pick-up below
        if not remaining and rank(last) in POWER:
            self._draw(player, 1)
            self._log(player, "can't go out on a power card, picked up 1")
        elif rank(last) == "Q":
            self._draw(player, 1)
            self._log(player, "didn't cover the queen, picked up 1")
        elif not remaining and player not in self.called:
            self._draw(player, 1)
            self._log(player, "didn't call cards last turn, picked up 1")

        if not self.hands[player]:
            self._went_out(player)
        else:
            self._advance(tried=tried, skips=skips)

    def timeout(self, player):
        self._draw_instead(player, timed_out=True)

    def _draw_instead(self, player, timed_out=False):
        pending = self.pending
        if pending and pending["kind"] == SKIP:
            left = pending["count"] - 1  # the rest of the skips move on to the next player
            self.pending = {"kind": SKIP, "count": left} if left else None
            if not timed_out:  # probably no 8 to pass it on with
                self.history.append(("draw", player, 0, ((), ("8",), False)))
            self._log(player, "missed a turn")
            self._advance(missed=True)
            return
        if pending:
            # probably nothing to answer it with
            without = None if timed_out else ((), (COUNTERS[pending["kind"]],), False)
            got = self._draw(player, pending["count"], without)
            self.pending = None
            self._log(player, f"picked up {got}")
        else:
            if self.force and not timed_out and any(self.can_start(c) for c in self.hands[player]):
                raise GameError("You have a card you can play")
            # nothing that could go down, for certain if they had to play one
            without = None if timed_out else \
                ((self.suit,), (rank(self.discard[-1]), "A"), self.force)
            got = self._draw(player, 1, without)
            text = "picked up a card" if got else "couldn't pick up, no cards left"
            self._log(player, "ran out of time and " + text if timed_out else text)
        self._advance()

    def _incoming(self):
        """The attack the first card of a turn builds on. When 8s replace, answering one
        starts the skips again from your own 8s."""
        if self.eights == REPLACE and self.pending and self.pending["kind"] == SKIP:
            return None
        return self.pending

    def _after(self, pending, card):
        """The attack waiting for the next player once card is played on top of pending."""
        if rank(card) == "2":
            return self._stack(pending, TWO, 2)
        if is_black_jack(card):
            return self._stack(pending, JACK, self.jack_penalty)
        if rank(card) == "8":
            return self._stack(pending, SKIP, 1)
        return None  # any other card ends it, a pink or orange jack cancelling a dark one too

    @staticmethod
    def _stack(pending, kind, amount):
        count = pending["count"] if pending and pending["kind"] == kind else 0
        return {"kind": kind, "count": count + amount}

    def _hint(self):
        pending = self.pending
        if pending and pending["kind"] == SKIP:
            return "Play an 8 or miss your turn"
        if pending:
            name = "a 2" if pending["kind"] == TWO else "a jack"
            return f"Play {name} or pick up {pending['count']}"
        return f"Play a {DONUTS[self.suit]} donut, a {rank(self.discard[-1])} or an ace"

    # ---- cards and turns

    def _draw(self, player, amount, without=None):
        """Move cards from the pile to a hand. Returns how many there were. without is what
        it says about the cards already held, for the history."""
        drawn = 0
        for _ in range(amount):
            if not self.pile and not self._refill():
                break
            self.hands[player].append(self.pile.pop())
            drawn += 1
        self.history.append(("draw", player, drawn, without))
        return drawn

    def _refill(self):
        """Shuffle the discards back in, or add a deck if there are none."""
        if len(self.discard) > 1:
            self.pile, self.discard = self.discard[:-1], self.discard[-1:]
            self.history.append(("shuffle", tuple(self.pile), self.decks))
        elif self.decks < MAX_DECKS:
            self.decks += 1
            self.pile = new_deck()
            self._log(None, "Out of cards, so a new deck was added")
            if not self.force:
                # the first extra deck means everyone has been picking up instead of playing
                self.force = True
                self._log(None, "From now on you must play if you can")
        else:
            return False
        self.rng.shuffle(self.pile)
        return True

    def _active(self):
        """Players still in the round: not out and not left."""
        return [p for p in self.hands if p not in self.gone and p not in self.places]

    def _called_now(self):
        """Players who have called cards and can still go out on it: on their last turn, or
        during this one. Everyone sees this, so they can try to stop them."""
        called = {p for p in self.called if p in self._active()}
        if self.calling:
            called.add(self.turn)
        return called

    def _next(self, player):
        """Who plays after player, going the way play is going and passing over anyone out."""
        for _ in range(self.num_players):
            player = (player - 1 + self.direction) % self.num_players + 1
            if player in self._active():
                break
        return player

    def _advance(self, missed=False, tried=False, skips=0):
        """Pass the turn on, past skips players when 8s skip straight away. A missed turn
        (from an 8) isn't a go, so a call made on the turn before it still counts for the
        player's next real go. Not going out on that go costs a card if the host chose that,
        unless the player already picked one up for trying (tried)."""
        player = self.turn
        if (self.call_penalty and player in self.called and not missed and not tried
                and self.hands[player]):
            got = self._draw(player, 1)
            self._log(player, f"called cards but didn't go out, picked up {got}")
        if self.calling:
            self.called.add(player)
        elif not missed:
            self.called.discard(player)
        self.calling = False
        player = self._next(player)
        for _ in range(skips):
            self._log(player, "missed a turn")
            player = self._next(player)
        self.turn = player

    def _went_out(self, player):
        self.places.append(player)
        self._log(player, "won!" if len(self.places) == 1 else f"came {ordinal(len(self.places))}")
        if not self.play_on or len(self._active()) <= 1:
            self._end()
        else:
            self._advance()

    def _end(self):
        """Finish the round. Whoever is still in takes the last place, or wins if everyone
        else left before anyone went out."""
        active = self._active()
        if active and (self.play_on or not self.places):
            if not self.places:
                self._log(active[0], "won!")
            self.places.extend(active)
        self.status = WIN
        self.winner = self.places[0] if self.places else None

    def _log(self, player, text):
        self.log.append({"player": player, "text": text})
