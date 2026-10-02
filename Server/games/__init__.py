from Server.games.connect4 import Connect4

# every game that can be hosted, by name
GAMES = {game.name: game for game in (Connect4,)}
