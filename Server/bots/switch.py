"""Computer players for Donut Cards.

A bot is never handed the real game. guess() makes a copy where every card the bot can't see
(other hands and the pick-up pile) is shuffled and dealt again, so it can only play on what a
person in its seat would know: its own hand, the cards played so far, and how many cards
everyone holds."""
import functools
import math
import random
import time
from collections import Counter

from Server.games.switch import (
    JACK, POWER, SKIP, SUITS, TWO, Switch, connects, is_black_jack, new_deck, rank, suit,
)

PLAY_LIMIT = 400  # different plays listed at most, for a hand so big that listing all is slow
RUN_LIMIT = 6  # hands bigger than this are never checked for a way out in one go

# connects() for every pair of cards, looked up instead of worked out, as bots ask millions
# of times
LINKS = {(a, b): connects(a, b) for a in new_deck() for b in new_deck()}


# ---- what the bot knows

def unseen(game, player):
    """Cards the player can't see: everyone else's hand and the pick-up pile."""
    cards = Counter(new_deck() * game.decks)
    cards.subtract(game.hands[player])
    cards.subtract(game.discard)
    return list(cards.elements())


SURE_ENOUGH = 0.9  # how often a guess believes what a pick-up suggests but doesn't prove


def beliefs(game, player):
    """What the history says about each other player's cards: for every card they hold, the
    rules it must follow, as (suits, ranks, certain) it can't be. A pick-up puts a rule on the
    cards held before it, and new cards come in free of rules.

    Nobody knows which held card a played one was, so the most restricted one it could have
    been goes. Rules only ever pile up on cards held longer, so that never rules out a hand
    that is really possible. A card that breaks every rule it could have been under shows a
    pick-up that wasn't for want of a card, and those rules are dropped."""
    slots = {p: [[] for _ in range(n)] for p, n in game.history_hands.items()}
    for event in game.history:
        kind, p = event[0], event[1]
        if kind == "play":
            held = slots[p]
            for card in event[2]:
                if not held:
                    break
                fits = [s for s in held if not any(breaks(card, rule) for rule in s)]
                if not fits:
                    fits = [s for s in held if not any(breaks(card, rule) and rule[2]
                                                       for rule in s)] or held
                    wrong = [rule for rule in min(fits, key=len) if breaks(card, rule)]
                    for slot in held:
                        slot[:] = [rule for rule in slot if rule not in wrong]
                held.remove(max(fits, key=len))
        elif kind == "draw":
            _, p, count, without = event
            if without:
                for held in slots[p]:
                    held.append(without)
            slots[p].extend([] for _ in range(count))
        elif kind == "shuffle":
            # cards held now can't be ones that just went into the pile, unless another
            # copy of the same card is still out
            _, cards, decks = event
            gone = frozenset(c for c, n in Counter(cards).items() if n >= decks)
            if gone:
                rule = ((), (), True, gone)
                for held in slots.values():
                    for slot in held:
                        slot.append(rule)
        else:  # left
            slots[p] = []
    for p, hand in game.hands.items():
        if len(slots[p]) != len(hand):  # can't happen, but a wrong guess beats a crash
            slots[p] = [[] for _ in hand]
    del slots[player]
    return slots


def breaks(card, rule):
    """Whether a card can't be one the rule is on. A rule rules out suits and ranks, and
    after a shuffle also particular cards."""
    return suit(card) in rule[0] or rank(card) in rule[1] or len(rule) > 3 and card in rule[3]


def guess(game, player, rng, slots=None):
    """A copy of the game with the cards player can't see dealt again at random. With slots
    from beliefs(), each card dealt to someone follows the rules their history gives it, as
    far as the cards left allow."""
    copy = game.copy(rng=rng)
    cards = unseen(game, player)
    rng.shuffle(cards)
    if slots is None:
        for other, hand in copy.hands.items():
            if other != player:
                copy.hands[other] = [cards.pop() for _ in hand]
    else:
        believed = {}  # each rule is believed or not for the whole guess

        def rules(slot):
            kept = []
            for rule in slot:
                key = id(rule)
                if key not in believed:
                    believed[key] = rule[2] or rng.random() < SURE_ENOUGH
                if believed[key]:
                    kept.append(rule)
            return kept

        deal = [(other, rules(slot)) for other, held in slots.items() for slot in held]
        deal.sort(key=lambda d: -len(d[1]))  # the hardest to fill go first
        hands = {other: [] for other in copy.hands if other != player}
        for other, kept in deal:
            pick = next((i for i, c in enumerate(cards)
                         if not any(breaks(c, rule) for rule in kept)), len(cards) - 1)
            hands[other].append(cards.pop(pick))
        copy.hands.update(hands)
    copy.pile = cards
    copy._log = lambda *args: None  # nobody reads it, and building the text is slow
    return copy


# ---- legal moves

def after_card(game, pending, card):
    """The attack waiting for the next player once card is played on top of pending."""
    if rank(card) == "2":
        return Switch._stack(pending, TWO, 2)
    if is_black_jack(card):
        return Switch._stack(pending, JACK, game.jack_penalty)
    if rank(card) == "8":
        return Switch._stack(pending, SKIP, 1)
    return None


def plays(game, limit=PLAY_LIMIT):
    """Every different play the player whose turn it is could make, as lists of cards. Two
    orders that leave the game the same way (same cards played, same last card, same attack,
    same direction) are only listed once."""
    hand = game.hands[game.turn]
    found = []
    seen = set()
    stack = [((card,), after_card(game, game.pending, card), rank(card) == "K")
             for card in sorted(set(hand)) if game.can_start(card)]
    while stack and len(found) < limit:
        cards, pending, flipped = stack.pop()
        left = Counter(hand)
        left.subtract(cards)
        key = (tuple(sorted(cards)), cards[-1], tuple(sorted((pending or {}).items())), flipped)
        if key in seen:
            continue
        seen.add(key)
        found.append(list(cards))
        for card in sorted(c for c, n in left.items() if n > 0):
            if LINKS[cards[-1], card]:
                stack.append((cards + (card,), after_card(game, pending, card),
                              flipped != (rank(card) == "K")))
    return found


def can_draw(game):
    """Whether picking up (or missing a turn) is allowed instead of playing."""
    if game.pending or not game.force:
        return True
    return not any(game.can_start(c) for c in game.hands[game.turn])


def moves(game, limit=PLAY_LIMIT):
    """Every legal move, as actions. An ace at the end comes once for each suit."""
    actions = []
    for cards in plays(game, limit):
        if rank(cards[-1]) == "A":
            actions.extend({"type": "play", "cards": cards, "suit": s} for s in SUITS)
        else:
            actions.append({"type": "play", "cards": cards})
    if can_draw(game):
        actions.append({"type": "draw"})
    return actions


def way_out(hand):
    """Whether the whole hand can be played in one go and end on a card you can go out on,
    whatever card it has to start from. Big hands are never checked."""
    if not hand or len(hand) > RUN_LIMIT:
        return False
    return _way_out(tuple(sorted(hand)))


@functools.lru_cache(maxsize=100_000)  # the same small hands come up over and over
def _way_out(hand):
    tried = set()

    def finish(last, left):
        if not left:
            return rank(last) not in POWER
        key = (last, left)
        if key in tried:
            return False
        tried.add(key)
        for i, card in enumerate(left):
            if LINKS[last, card] and finish(card, left[:i] + left[i + 1:]):
                return True
        return False

    return any(finish(card, hand[:i] + hand[i + 1:]) for i, card in enumerate(hand))


# With no penalty for a failed call, call on hands this small anyway. Calling more freely won
# about 1 to 2% more games; the size made no difference, so this keeps calls believable.
FREE_CALL_SIZE = 3


def leaves(game, player, action):
    """The cards a move leaves in hand, not counting any picked up."""
    left = Counter(game.hands[player])
    if action["type"] == "play":
        left.subtract(action["cards"])
    return list(left.elements())


def sure_out(game, player, action):
    """Whether this move leaves a hand that can all go next turn."""
    if action["type"] != "play" or rank(action["cards"][-1]) == "Q":  # Q: picks up 1
        return False
    left = leaves(game, player, action)
    return bool(left) and way_out(left)


def should_call(game, player, action):
    """Call cards when this move leaves a hand that can all go next turn. When a failed call
    costs nothing, small hands are worth a call too, in case the next go goes well."""
    if game.calling:
        return False
    if sure_out(game, player, action):
        return True
    left = leaves(game, player, action)
    return not game.call_penalty and 0 < len(left) <= FREE_CALL_SIZE


def turn(game, player, action):
    """The actions that make one turn: calling cards first if the bot wants to, then the move."""
    if should_call(game, player, action):
        return [{"type": "call"}, action]
    return [action]


# ---- bots

class RandomBot:
    """Plays a random legal move, playing rather than picking up most of the time."""

    name = "random"

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, game, player):
        seen = guess(game, player, self.rng)
        options = moves(seen)
        played = [a for a in options if a["type"] == "play"]
        if played and self.rng.random() < 0.9:
            return turn(seen, player, self.rng.choice(played))
        return turn(seen, player, self.rng.choice(options))


# what holding on to a card is worth, against CARD for each card in the hand
CARD = 10
KEEP = {"A": 4, "2": 3, "8": 2, "Q": -2}
KEEP_BLACK_JACK = 3
KEEP_RED_JACK = 1


class Weights:
    """What the heuristic bot cares about, and how much. CARD is what each card in hand
    costs, and the rest are measured against it."""

    DEFAULTS = {
        "card": CARD,
        "keep": dict(KEEP, black_jack=KEEP_BLACK_JACK, red_jack=KEEP_RED_JACK),
        "link": 1, "link_cap": 2,  # sharing a suit or rank with other cards in hand
        "fit": 1, "fit_cap": 3,  # cards that still go if the suit is left alone
        "hurt": {TWO: 1, JACK: 1, SKIP: 3}, "hurt_cap": 10,  # an attack on the next player
        "threat": 2,  # how much more it's worth on someone close to going out
        "call": 15,  # calling cards, so likely to go out next turn
    }

    def __init__(self, **changes):
        self.__dict__.update(self.DEFAULTS)
        self.__dict__.update(changes)
        self.keeps = {card: self._keep(card) for card in new_deck()}

    def _keep(self, card):
        if rank(card) == "J":
            return self.keep["black_jack" if is_black_jack(card) else "red_jack"]
        return self.keep.get(rank(card), 0)


DEFAULT_WEIGHTS = Weights()


def hand_value(hand, w=DEFAULT_WEIGHTS):
    """How useful the cards are to still have: aces change the suit, 2s, 8s and jacks answer
    attacks, queens are awkward. A card that shares a suit or rank with none of the others is
    hard to get rid of."""
    suits = Counter(suit(c) for c in hand)
    ranks = Counter(rank(c) for c in hand)
    same = Counter(hand)
    value = 0
    for card in hand:
        links = suits[suit(card)] + ranks[rank(card)] - same[card] - 1  # others it shares with
        value += w.keeps[card] + w.link * (min(links, w.link_cap) - 1)
    return value


def score(before, player, action, calls, w=DEFAULT_WEIGHTS):
    """How good the position is for player after this move, using only what they can see.
    Cards picked up are unknown, so they only count against the hand size."""
    game = before.copy(rng=before.rng)  # a new generator for each would cost a seeding
    game._log = lambda *args: None
    if calls:
        game.apply(player, {"type": "call"})
    game.apply(player, action)
    if player in game.places:
        return 1000
    hand = game.hands[player]
    known = Counter(before.hands[player])
    if action["type"] == "play":
        known.subtract(action["cards"])
    known = list(known.elements())
    value = -w.card * len(hand) + hand_value(known, w)

    # cards that will still fit if the suit is left alone
    top = game.discard[-1]
    fit = sum(1 for c in known if suit(c) == game.suit or rank(c) == rank(top))
    value += w.fit * min(fit, w.fit_cap)

    # an attack on the next player, worth more when they are close to going out
    victim = game.turn
    if game.pending and victim != player:
        hurt = w.hurt[game.pending["kind"]] * game.pending["count"]
        threat = w.threat if len(game.hands[victim]) <= 2 or victim in game.called else 1
        value += min(hurt, w.hurt_cap) * threat
    if calls and sure_out(before, player, action):
        value += w.call
    return value


class HeuristicBot:
    """Picks the move that leaves the best looking hand."""

    name = "heuristic"

    def __init__(self, rng=None, weights=DEFAULT_WEIGHTS):
        self.rng = rng or random.Random()
        self.weights = weights

    def choose(self, game, player):
        seen = guess(game, player, self.rng)
        return turn(seen, player, self.pick(seen, player))

    def ranked(self, seen, player):
        """Every legal move with how good it looks, best first."""
        options = []
        for action in moves(seen):
            calls = should_call(seen, player, action)
            value = score(seen, player, action, calls, self.weights)
            options.append((value + self.rng.random() * 0.01, action))
        options.sort(key=lambda option: -option[0])
        return options

    def pick(self, seen, player):
        """The best looking move, in a game where player can only see their own cards."""
        return self.ranked(seen, player)[0][1]


class SearchBot:
    """Tries out the most promising moves many times over. Each try deals the cards it can't
    see a new way, makes the move, and plays the game out with every player using the
    heuristic bot. The move that wins most often is played."""

    name = "search"
    MAX_TURNS = 400  # a play-out this long is stopped and scored on hand sizes
    use_beliefs = True  # deal hidden cards to fit what each player's pick-ups gave away

    def __init__(self, rng=None, playouts=150, candidates=8, seconds=None,
                 weights=DEFAULT_WEIGHTS):
        self.rng = rng or random.Random()
        self.playouts = playouts
        self.candidates = candidates
        self.seconds = seconds  # a time limit instead of a number of play-outs
        self.weights = weights
        self.policy = HeuristicBot(self.rng, weights)

    def choose(self, game, player):
        """Every move still in the running is played out in the same worlds, with the same
        luck after, so the difference between two moves is the moves and not the deal. After
        each round the worse half is dropped, and the time goes on the close calls."""
        seen = guess(game, player, self.rng)
        options = [action for _, action in self.policy.ranked(seen, player)[:self.candidates]]
        if len(options) == 1:
            return turn(seen, player, options[0])

        slots = beliefs(game, player) if self.use_beliefs else None
        alive = list(range(len(options)))
        results = [[] for _ in options]  # each move's score in every world it was tried in
        rounds = math.ceil(math.log2(len(options)))
        settled = False
        start = time.perf_counter()
        for r in range(rounds):
            until = start + self.seconds * (r + 1) / rounds if self.seconds else None
            worlds = max(1, self.playouts // (rounds * len(alive)))
            for n in range(10 ** 9):
                if until is None and n >= worlds or until and n and time.perf_counter() > until:
                    break
                world = guess(game, player, self.rng, slots)
                seed = self.rng.random()
                for i in alive:
                    results[i].append(self._play_out(world, player, options[i], seed))
                if n % 4 == 3 and self._settled(alive, results):
                    settled = True
                    break
            alive.sort(key=lambda i: -sum(results[i]))
            alive = alive[:1] if settled else alive[:max(1, len(alive) // 2)]
            if len(alive) == 1:
                break
        if self.seconds and not settled and sum(map(len, results)) < self.MIN_PLAYOUTS:
            # out of time before the play-outs could be trusted, as on a busy server: a
            # search that short picks worse than the heuristic's own favourite
            return turn(seen, player, options[0])
        return turn(seen, player, options[alive[0]])

    MIN_PLAYOUTS = 100  # with a time limit, fewer than this and the heuristic's choice stands

    CLEAR = 3  # standard errors the best move must be ahead by to stop thinking early
    CLEAR_WORLDS = 12  # and in at least this many worlds

    def _settled(self, alive, results):
        """Whether the best move is so far ahead of the next best, in the worlds both were
        tried in, that more thinking won't change the choice. Puts the best first if so."""
        alive.sort(key=lambda i: -sum(results[i]))
        best, second = results[alive[0]], results[alive[1]]
        shared = min(len(best), len(second))
        if shared < self.CLEAR_WORLDS:
            return False
        gaps = [a - b for a, b in zip(best[-shared:], second[-shared:])]
        mean = sum(gaps) / shared
        if mean <= 0:  # level so far, which may only mean the worlds haven't told them apart
            return False
        spread = math.sqrt(sum((g - mean) ** 2 for g in gaps) / (shared - 1))
        return spread == 0 or mean / (spread / math.sqrt(shared)) > self.CLEAR

    def _play_out(self, world, player, action, seed):
        """Make the move in a copy of the world, then play to the end with the heuristic bot
        in every seat. The same seed gives the same luck after, whatever the move."""
        world = world.copy(rng=random.Random(seed))
        policy = HeuristicBot(random.Random(seed), self.weights)
        for a in turn(world, player, action):
            world.apply(player, a)
        for _ in range(self.MAX_TURNS):
            if world.over:
                break
            p = world.turn
            for a in turn(world, p, policy.pick(world, p)):
                world.apply(p, a)
        return self._result(world, player)

    def _result(self, world, player):
        """1 for a win. Losing scores up to a half for how few cards were left against the
        others who lost, which tells moves apart in big games where a win is rare. With play
        on, places count instead."""
        if world.over and world.play_on and player in world.places:
            others = len(world.places) - 1
            return 1 - world.places.index(player) / others if others else 1.0
        if world.over and world.winner == player:
            return 1.0
        mine = len(world.hands[player])
        rivals = [len(world.hands[p]) for p in world._active() if p != player]
        if not rivals:
            return 0.0
        if not world.over:  # stopped short: whoever has the fewest cards is ahead
            return 1.0 if mine < min(rivals) else 0.0
        behind = sum(1 for n in rivals if n > mine) + 0.5 * sum(1 for n in rivals if n == mine)
        return 0.5 * behind / len(rivals)  # never as good as winning


# every bot by name, for scripts/arena.py
BOTS = {bot.name: bot for bot in (RandomBot, HeuristicBot, SearchBot)}
