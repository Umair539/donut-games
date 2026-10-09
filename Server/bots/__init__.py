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
    # alpha-beta search, a move deeper at a time for a second; the room's pause before a bot
    # moves hides most of it
    "connect4": {"hard": partial(connect4.SearchBot, seconds=1.0)},
    "checkers": {"hard": partial(checkers.SearchBot, seconds=1.0)},
}
