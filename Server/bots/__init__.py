from functools import partial

from Server.bots import switch

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
}
