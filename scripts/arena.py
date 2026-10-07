"""Play Donut Cards bots against each other and report how often each one wins.

    python scripts/arena.py heuristic random --games 1000
    python scripts/arena.py search heuristic heuristic --games 200 --workers 8

Each game deals the named bots into seats in a rotating order, so no bot keeps the luck of a
seat. A bot that wins as often as chance allows wins 1 game in (number of players)."""
import argparse
import math
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Server.bots.switch import BOTS  # noqa: E402
from Server.games.switch import Switch  # noqa: E402

MAX_ACTIONS = 5000


def play(args):
    names, seed, settings = args
    rng = random.Random(seed)
    order = names[seed % len(names):] + names[:seed % len(names)]  # rotate seats
    game = Switch(len(order), rng=random.Random(rng.random()), **settings)
    bots = {p: BOTS[name](random.Random(rng.random())) for p, name in enumerate(order, 1)}
    thinking = Counter()
    for _ in range(MAX_ACTIONS):
        if game.over:
            break
        player = game.turn
        start = time.perf_counter()
        actions = bots[player].choose(game, player)
        thinking[order[player - 1]] += time.perf_counter() - start
        for action in actions:
            game.apply(player, action)
    winner = order[game.winner - 1] if game.winner else None
    return winner, thinking


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bots", nargs="+", choices=sorted(BOTS), help="one per seat")
    parser.add_argument("--games", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--hand-size", type=int, default=7)
    parser.add_argument("--force-play", action="store_true")
    parser.add_argument("--decks", type=int, default=1, help="6 or more players always get 2")
    args = parser.parse_args()
    settings = {"hand_size": args.hand_size, "force_play": args.force_play, "decks": args.decks}

    jobs = [(args.bots, args.seed + n, settings) for n in range(args.games)]
    wins, thinking = Counter(), Counter()
    start = time.perf_counter()
    with ProcessPoolExecutor(args.workers) as pool:
        for winner, spent in pool.map(play, jobs, chunksize=max(1, args.games // 50)):
            wins[winner] += 1
            thinking.update(spent)
    took = time.perf_counter() - start

    seats = Counter(args.bots)
    fair = 1 / len(args.bots)
    print(f"{args.games} games, {len(args.bots)} players, {took:.0f}s")
    for name, count in seats.items():
        rate = wins[name] / (args.games * count)  # per seat, so two copies of a bot share
        spread = 2 * math.sqrt(fair * (1 - fair) / (args.games * count))
        print(f"  {name:10} wins {rate:6.1%} per seat (chance {fair:.1%}, +-{spread:.1%})"
              f"  {thinking[name] / args.games * 1000 / count:7.0f} ms thinking per game")
    if wins[None]:
        print(f"  unfinished {wins[None]}")


if __name__ == "__main__":
    main()
