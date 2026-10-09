from functools import partial

from Server.bots import checkers, connect4, switch

# the computer players each game offers in the lobby, by game name and then level, easiest
# first. A bot has choose(game, player), which returns the actions that make its turn, and is
# handed its own copy of the game (game.copy()), so games with bots need copy().
#
# Donut Cards only offers its best. The easy (random) and medium (heuristic) bots are still in
# Server/bots/switch.py: the hard one plays medium in every seat when it searches, and both
# are there to measure against with scripts/arena.py.
BOTS = {
    "switch": {
        # up to 5 seconds, which buys the free-tier server about the play-outs it needs to
        # beat medium; obvious moves stop early
        "hard": partial(switch.SearchBot, seconds=5.0),
    },
    # Hard searches as deep as it can in a second, which the room's pause before a bot moves
    # mostly hides. Easy and Medium look a set number of moves ahead and blur their scores
    # with some noise, so they make mistakes; each level beats the one below most games
    # (Connect Donut: Easy beats random play 39 of 40, Medium beats Easy 24-12, Hard beats
    # Medium 36-2; checkers: 40-0, about 31-2 and 36-1, forced jumps on or off)
    "connect4": {
        "easy": partial(connect4.SearchBot, max_depth=2, noise=12),
        "medium": partial(connect4.SearchBot, max_depth=3, noise=5),
        "hard": partial(connect4.SearchBot, seconds=1.0),
    },
    "checkers": {
        "easy": partial(checkers.SearchBot, max_depth=2, noise=40, settle=0),
        "medium": partial(checkers.SearchBot, max_depth=3, noise=15, settle=2),
        "hard": partial(checkers.SearchBot, seconds=1.0),
    },
}
