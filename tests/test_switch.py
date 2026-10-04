import random

import pytest

from Server.core.base import GameError
from Server.games.switch import MAX_DECKS, WIN, Switch, connects


def setup(players=2, hands=None, top="5H", pile=None, called=True, **settings):
    """A game with known cards. Player 1 starts. Everyone has called cards unless called is
    False, so tests about other rules can go out straight away."""
    game = Switch(players, rng=random.Random(0), **settings)
    for player, hand in (hands or {}).items():
        game.hands[player] = list(hand)
    game.discard = [top]
    game.suit = top[-1]
    if pile is not None:
        game.pile = list(pile)
    game.turn = 1
    if called:
        game.called = set(game.hands)
    return game


def play(game, *cards, suit=None):
    action = {"type": "play", "cards": list(cards)}
    if suit:
        action["suit"] = suit
    game.apply(game.turn, action)


def draw(game):
    game.apply(game.turn, {"type": "draw"})


# ---- dealing and settings

def test_deal():
    game = Switch(4, hand_size=5, rng=random.Random(1))
    assert all(len(hand) == 5 for hand in game.hands.values())
    assert len(game.pile) + len(game.discard) + 20 == 52
    assert game.discard[-1][:-1] not in {"A", "2", "8", "J", "Q", "K"}


def test_random_first_player():
    starters = {Switch(4, rng=random.Random(seed)).turn for seed in range(40)}
    assert starters == {1, 2, 3, 4}


def test_big_games_use_two_decks():
    assert Switch(5).decks == 1
    assert Switch(5, decks=2).decks == 2
    assert Switch(6).decks == 2


@pytest.mark.parametrize("settings", [
    {"hand_size": 0}, {"hand_size": 8}, {"jack_penalty": 4}, {"jack_penalty": 8},
    {"decks": 3}, {"force_play": 1}, {"hand_size": True},
])
def test_bad_settings(settings):
    with pytest.raises(GameError):
        Switch.validate_settings(settings)


def test_view_hides_other_hands():
    game = setup(hands={1: ["3H"], 2: ["4H", "9C"]})
    view = game.view(1)
    assert view["hand"] == ["3H"]
    assert view["counts"] == [{"id": 1, "cards": 1}, {"id": 2, "cards": 2}]


# ---- matching and connecting

def test_must_match_suit_or_rank():
    game = setup(hands={1: ["9C", "5S", "7H"], 2: ["3C"]})
    with pytest.raises(GameError):
        play(game, "9C")
    play(game, "5S")
    assert game.turn == 2 and game.suit == "S"


def test_cards_you_dont_have():
    game = setup(hands={1: ["5S", "3C"], 2: ["3C"]})
    with pytest.raises(GameError):
        play(game, "5D")
    with pytest.raises(GameError):
        play(game, "5S", "5S")


def test_connections():
    assert connects("5H", "5S")  # same rank
    assert connects("5H", "6H") and connects("5H", "4H")  # next to it, same suit
    assert not connects("5H", "6S")
    assert connects("AS", "2S") and connects("AS", "KS")  # ace sits between K and 2
    assert not connects("9D", "AC")  # an ace is only wild as the first card
    assert connects("2C", "AC") and connects("KC", "AC") and connects("AD", "AC")
    assert connects("QH", "3H") and connects("QH", "QS")  # covering a queen
    assert not connects("QH", "KS") and not connects("QH", "AS")


def test_run_in_one_turn():
    game = setup(hands={1: ["5H", "6H", "6C", "7C", "3D"], 2: ["3C"]})
    play(game, "5H", "6H", "6C", "7C")
    assert game.hands[1] == ["3D"]
    assert game.discard[-1] == "7C"
    with pytest.raises(GameError):
        play(game, "3C", "9D")


def test_ace_changes_suit():
    game = setup(hands={1: ["AS", "3D"], 2: ["9C", "9D"]})
    with pytest.raises(GameError):
        play(game, "AS")  # needs a suit
    play(game, "AS", suit="C")
    assert game.suit == "C"
    with pytest.raises(GameError):
        play(game, "9D")
    play(game, "9C")


def test_ace_mid_turn_must_connect():
    game = setup(hands={1: ["5H", "AC", "2C", "3D"], 2: ["4C"]})
    with pytest.raises(GameError):
        play(game, "5H", "AC", suit="D")
    play(game, "5H")


def test_ace_last_after_a_run_changes_suit():
    game = setup(top="3C", hands={1: ["2C", "AC", "3D"], 2: ["4C"]})
    play(game, "2C", "AC", suit="H")
    assert game.suit == "H"


def test_ace_chained_keeps_its_own_suit():
    game = setup(hands={1: ["AS", "2S", "KS", "3D"], 2: ["4C"]})
    play(game, "AS", "KS")
    assert game.suit == "S"


# ---- attacks

def test_twos_stack():
    game = setup(hands={1: ["2H", "4C"], 2: ["2S", "5C"]})
    play(game, "2H")
    assert game.pending == {"kind": "two", "count": 2}
    with pytest.raises(GameError):
        play(game, "5C")  # must answer the 2
    play(game, "2S")
    assert game.pending["count"] == 4
    draw(game)
    assert len(game.hands[1]) == 1 + 4
    assert game.pending is None and game.turn == 2


def test_carrying_on_after_a_counter_ends_the_attack():
    game = setup(hands={1: ["2H", "4C"], 2: ["2S", "3S", "9C"]})
    play(game, "2H")
    play(game, "2S", "3S")
    assert game.pending is None


def test_black_jack_and_red_jack():
    game = setup(top="5S", hands={1: ["JS", "4C"], 2: ["JH", "JC", "9C"]},
                 jack_penalty=7)
    play(game, "JS")
    assert game.pending == {"kind": "jack", "count": 7}
    play(game, "JH")  # red cancels
    assert game.pending is None

    game = setup(top="5S", hands={1: ["JS", "4C"], 2: ["JH", "JC", "9C"]})
    play(game, "JS")
    play(game, "JH", "JC")  # cancel, then attack back
    assert game.pending == {"kind": "jack", "count": 5}


def test_black_jacks_stack():
    game = setup(top="5S", hands={1: ["JS", "4C"], 2: ["JC", "9C"]})
    play(game, "JS")
    play(game, "JC")
    assert game.pending["count"] == 10


def test_eights_skip_and_pass_on():
    game = setup(players=3, hands={1: ["8H", "4C"], 2: ["8S", "9C"], 3: ["3D", "4D"]})
    play(game, "8H")
    assert game.turn == 2 and game.pending == {"kind": "skip", "count": 1}
    play(game, "8S")
    assert game.turn == 3 and game.pending["count"] == 2
    draw(game)  # player 3 misses a turn, one skip still to go
    assert game.turn == 1 and game.pending["count"] == 1
    draw(game)
    assert game.turn == 2 and game.pending is None
    assert len(game.hands[3]) == 2  # missing a turn doesn't pick up


# ---- kings and queens

def test_kings_reverse():
    game = setup(players=3, top="5S", hands={1: ["KS", "KH", "KD", "KC", "4C"]})
    play(game, "KS")
    assert game.direction == -1 and game.turn == 3


@pytest.mark.parametrize("kings, direction", [(2, 1), (3, -1), (4, 1)])
def test_several_kings(kings, direction):
    hand = ["KS", "KH", "KD", "KC"][:kings] + ["4C"]
    game = setup(players=3, top="5S", hands={1: hand})
    play(game, *hand[:kings])
    assert game.direction == direction


def test_queen_must_be_covered():
    game = setup(hands={1: ["QH", "3H", "QS", "3S", "4C"], 2: ["9C"]})
    with pytest.raises(GameError):
        play(game, "QH", "QS", "3H")  # the second queen needs a spade
    play(game, "QH", "QS", "3S")
    assert game.hands[1] == ["3H", "4C"]


def test_uncovered_queen_picks_up():
    game = setup(hands={1: ["QH", "4C"], 2: ["9C"]}, pile=["7D"])
    play(game, "QH")
    assert sorted(game.hands[1]) == ["4C", "7D"]


# ---- finishing

def test_win_on_plain_card():
    game = setup(hands={1: ["5S"], 2: ["9C"]})
    play(game, "5S")
    assert game.status == WIN and game.winner == 1 and game.over
    with pytest.raises(GameError):
        draw(game)


def test_cant_go_out_on_a_power_card():
    game = setup(hands={1: ["2H"], 2: ["9C"]}, pile=["7D"])
    play(game, "2H")
    assert not game.over
    assert game.hands[1] == ["7D"]
    assert game.pending == {"kind": "two", "count": 2}  # the 2 still counts


def test_restart_deals_again():
    game = setup(hands={1: ["5S"], 2: ["9C"]})
    play(game, "5S")
    game.restart()
    assert not game.over and game.round == 2 and game.places == []
    assert all(len(hand) == 7 for hand in game.hands.values())


def test_first_out_ends_the_game_by_default():
    game = setup(players=3, hands={1: ["5S"], 2: ["9C"], 3: ["9D"]})
    play(game, "5S")
    assert game.over and game.places == [1]


def test_play_on_for_places():
    game = setup(players=3, play_on=True,
                 hands={1: ["5S"], 2: ["9C", "3S"], 3: ["3H", "9H"]})
    play(game, "5S")
    assert not game.over and game.places == [1] and game.winner is None
    assert game.turn == 2
    with pytest.raises(GameError):
        game.apply(1, {"type": "draw"})  # out players only watch
    play(game, "3S")
    call(game)
    play(game, "3H")
    assert game.turn == 2  # player 1 is skipped
    draw(game)
    play(game, "9H")
    assert game.over and game.places == [1, 3, 2] and game.winner == 1


def test_last_player_standing_wins():
    game = Switch(3, rng=random.Random(0))
    game.player_left(2)
    assert not game.over
    game.player_left(1)
    assert game.winner == 3 and game.places == [3]


def test_leaving_during_play_on():
    game = setup(players=3, play_on=True, hands={1: ["5S"], 2: ["9C"], 3: ["9D"]})
    play(game, "5S")
    game.player_left(2)
    assert game.over and game.places == [1, 3]


def test_turn_skips_players_who_left():
    game = setup(players=3, hands={1: ["5S", "4C"]})
    game.player_left(2)
    play(game, "5S")
    assert game.turn == 3


# ---- drawing and running out

def test_draw_one_and_pass():
    game = setup(hands={1: ["9C"], 2: ["9D"]}, pile=["3S"])
    draw(game)
    assert game.hands[1] == ["9C", "3S"] and game.turn == 2


def test_force_play():
    game = setup(hands={1: ["5S", "9C"], 2: ["9D"]}, force_play=True)
    with pytest.raises(GameError):
        draw(game)


def test_discards_shuffle_back_in():
    game = setup(hands={1: ["9C"], 2: ["9D"]}, pile=[])
    game.discard = ["3S", "4S", "5H"]
    draw(game)
    assert len(game.hands[1]) == 2 and game.discard == ["5H"] and len(game.pile) == 1


def test_first_extra_deck_forces_play_and_decks_stop_at_three():
    game = setup(hands={1: ["9C"], 2: ["9D"]}, pile=[])
    assert game.decks == 1 and not game.force
    draw(game)
    assert game.decks == 2 and len(game.pile) == 51
    assert game.force  # one deck ran out, so from now on you must play if you can
    game.pile = []
    draw(game)
    assert game.decks == MAX_DECKS
    game.pile = []
    draw(game)  # no more decks: nothing to draw, but the turn still passes
    assert game.decks == MAX_DECKS and game.turn == 2


# ---- calling cards


def call(game):
    game.apply(game.turn, {"type": "call"})


def test_going_out_without_calling_picks_up_one():
    game = setup(hands={1: ["5S"], 2: ["9C"]}, pile=["7D"], called=False)
    play(game, "5S")
    assert not game.over
    assert game.hands[1] == ["7D"]
    assert game.turn == 2


def test_calling_on_the_turn_before_lets_you_go_out():
    game = setup(hands={1: ["5S", "6S"], 2: ["9C", "9D"]}, pile=["7D"], called=False)
    call(game)
    assert game.calling
    play(game, "5S")
    assert not game.calling and game.turn == 2
    draw(game)
    play(game, "6S")
    assert game.over and game.winner == 1


def test_a_call_only_lasts_one_turn():
    game = setup(hands={1: ["5S", "6S", "7S"], 2: ["9C", "9D", "9H"]}, pile=["2D", "3D"],
                 called=False)
    call(game)
    play(game, "5S")
    draw(game)
    play(game, "6S")  # didn't call again
    draw(game)
    play(game, "7S")
    assert not game.over
    assert len(game.hands[1]) == 1


def test_calling_doesnt_end_the_turn_and_only_once():
    game = setup(hands={1: ["5S", "6S"], 2: ["9C"]}, called=False)
    call(game)
    assert game.turn == 1
    with pytest.raises(GameError):
        call(game)
    with pytest.raises(GameError):
        game.apply(2, {"type": "call"})


def test_two_starting_decks_force_play_when_the_third_comes_in():
    game = setup(hands={1: ["9C"], 2: ["9D"]}, pile=[], decks=2)
    assert game.decks == 2 and not game.force
    draw(game)
    assert game.decks == 3 and game.force
