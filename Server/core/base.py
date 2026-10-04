class GameError(ValueError):
    """Raised when a setting or an action is not allowed."""


MIN_TURN_SECONDS = 15
MAX_TURN_SECONDS = 30

# settings any turn-based game can add to its schema to get a per-turn timer; the room enforces
# it by calling the game's timeout() when a player runs out of time
TIMER_SETTINGS = [
    {"key": "timer", "label": "Turn timer", "type": "bool", "default": False},
    {"key": "turn_seconds", "label": "Seconds per turn", "type": "int",
     "min": MIN_TURN_SECONDS, "max": MAX_TURN_SECONDS, "default": 20},
]


def check_timer(timer, turn_seconds):
    if not isinstance(timer, bool):
        raise GameError("Invalid value for turn timer")
    if (not isinstance(turn_seconds, int) or isinstance(turn_seconds, bool)
            or not MIN_TURN_SECONDS <= turn_seconds <= MAX_TURN_SECONDS):
        raise GameError(
            f"Seconds per turn must be between {MIN_TURN_SECONDS} and {MAX_TURN_SECONDS}")


class BaseGame:
    """What a game must provide to be played through the room framework.

    The framework owns codes, seats, the lobby, reconnecting and rematch votes. A game only
    owns its rules. Players are numbered 1..N, and actions are free-form dicts sent by the
    browser, so each game defines what its own actions look like.
    """

    name = ""  # id used in messages and urls, e.g. "connect4"
    title = ""  # shown to players
    description = ""
    min_players = 2
    max_players = 2

    # What the host can choose. Each entry is
    # {"key", "label", "type": "int", "min", "max", "default"}, and "max_of": [keys] makes
    # the maximum follow the largest of those other settings. {"key", "label", "type": "bool",
    # "default"} is an on/off switch.
    settings_schema = []

    @classmethod
    def create(cls, settings, num_players):
        """Start a game for this many players. Raise GameError if the settings are invalid."""
        raise NotImplementedError

    @classmethod
    def validate_settings(cls, settings):
        """Fill in defaults and reject anything invalid. Returns the settings to store."""
        known = {s["key"]: s for s in cls.settings_schema}
        unknown = set(settings) - set(known)
        if unknown:
            raise GameError(f"Unknown setting: {sorted(unknown)[0]}")
        merged = {key: s["default"] for key, s in known.items()}
        merged.update(settings)
        cls.create(merged, cls.min_players)  # raises GameError if the values are not allowed
        return merged

    @classmethod
    def describe(cls):
        return {
            "name": cls.name,
            "title": cls.title,
            "description": cls.description,
            "min_players": cls.min_players,
            "max_players": cls.max_players,
            "settings": cls.settings_schema,
        }

    @property
    def over(self):
        """True once the game has finished (a win, a draw, ...)."""
        raise NotImplementedError

    def apply(self, player, action):
        """Apply one action from a player. Raise GameError if it is not allowed."""
        raise NotImplementedError

    def view(self, player):
        """The JSON-safe state this player may see. Hide anything secret, e.g. other hands."""
        raise NotImplementedError

    def timeout(self, player):
        """The player ran out of time: make a move for them. Only games with the timer
        settings need this."""
        raise NotImplementedError

    def restart(self):
        """Start a new round with the same players and settings."""
        raise NotImplementedError

    def player_left(self, player):
        """Called when a player has left for good during a game. Games with more than two
        players can use this to skip them."""
