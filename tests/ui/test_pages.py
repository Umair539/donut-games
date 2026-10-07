"""The pages as players use them: hosting, the lobby, playing, leaving, rematches, chat and
getting back into a game after a refresh or a server restart."""
import re

from playwright.sync_api import expect


def host(page, title, toggles=(), **sliders):
    """Pick a game on the home screen, set it up and create it. Returns the room code."""
    page.locator(".game-option", has_text=title).click()
    for label in toggles:
        page.get_by_label(label).check()
    for key, value in sliders.items():
        page.eval_on_selector(
            f"#set-{key}",
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')); }",
            value,
        )
    page.get_by_role("button", name="Create").click()
    expect(page.locator("#screen-lobby")).to_be_visible()
    code = page.locator("#lobby-code")
    expect(code).to_have_text(re.compile(r"^[A-Z0-9]{6}$"))
    return code.inner_text()


def join(page, code):
    page.fill("#join-code", code)
    page.locator("#join-form button").click()
    expect(page.locator("#screen-lobby")).to_be_visible()


def start(host_page, *others):
    host_page.click("#start")
    for page in (host_page, *others):
        expect(page.locator("#screen-game")).to_be_visible()


def status(page):
    return page.locator("#game-root [data-status]")


def column(page, n):
    return page.get_by_role("button", name=f"Column {n + 1}")


def leave_game(page):
    page.click("#game-leave")
    expect(page.locator("#confirm-leave")).to_be_visible()
    page.click("#confirm-go")
    expect(page.locator("#screen-home")).to_be_visible()


def card(page, label):
    return page.locator(".sw-card", has=page.locator(f"img[alt='{label}']"))


def connect4_game(player):
    ann, bob = player("Ann"), player("Bob")
    code = host(ann, "Connect Donut")
    join(bob, code)
    start(ann, bob)
    return ann, bob, code


def cards_game(player, live_server, names, hands, top, pile=None, called=()):
    """A Donut Cards game where player 1 goes first and everyone holds the given cards."""
    pages = [player(name) for name in names]
    code = host(pages[0], "Donut Cards")
    for page in pages[1:]:
        join(page, code)
    start(*pages)

    def setup(room):
        game = room.game
        game.hands = {p: list(cards) for p, cards in hands.items()}
        game.discard = [top]
        game.suit = top[-1]
        if pile is not None:
            game.pile = list(pile)
        game.turn, game.direction, game.pending = 1, 1, None
        game.calling, game.called = False, set(called)
        game.log.clear()

    live_server.push(code, setup)
    expect(status(pages[0])).to_contain_text("Your turn")
    return pages, code


# ---- home


def test_home_lists_the_games(player):
    page = player()
    expect(page.locator(".game-option")).to_have_count(2)
    expect(page.locator(".game-option", has_text="Connect Donut")).to_contain_text("2 players")
    cards = page.locator(".game-option", has_text="Donut Cards")
    expect(cards).to_contain_text("British Blackjack")
    expect(cards).to_contain_text("2–10 players")


def test_instructions_open_and_close(player):
    page = player()
    page.get_by_role("button", name="Instructions for Donut Cards").click()
    expect(page.locator("#help")).to_be_visible()
    expect(page.locator("#help-body")).to_contain_text("empty your hand")
    page.keyboard.press("Escape")
    expect(page.locator("#help")).to_be_hidden()


def test_unknown_code_shows_an_error(player):
    page = player()
    page.fill("#join-code", "zzzz99")
    expect(page.locator("#join-code")).to_have_value("ZZZZ99")  # typed codes are tidied
    page.locator("#join-form button").click()
    expect(page.locator("#home-error")).to_have_text("Room not found")


def test_back_from_settings(player):
    page = player()
    page.locator(".game-option", has_text="Connect Donut").click()
    expect(page.locator("#create-title")).to_have_text("Connect Donut")
    page.click("#create-back")
    expect(page.locator("#screen-home")).to_be_visible()


# ---- lobby


def test_lobby_shows_who_is_here_and_only_the_host_can_start(player):
    ann, bob = player("Ann"), player("Bob")
    code = host(ann, "Connect Donut", rows=5)
    expect(ann.locator("#start")).to_be_disabled()
    expect(ann.locator("#lobby-wait")).to_have_text("Waiting for players to join…")
    expect(ann.locator("#lobby-settings")).to_contain_text("Rows: 5")

    join(bob, code)
    expect(ann.locator("#lobby-heading")).to_have_text("Players (2/2)")
    expect(ann.locator("#start")).to_be_enabled()
    expect(ann.locator("#lobby-wait")).to_have_text("Start when everyone is here")
    expect(bob.locator("#start")).to_be_hidden()
    expect(bob.locator("#lobby-wait")).to_have_text("Waiting for the host to start…")
    expect(bob.locator("#lobby-players li", has_text="Ann")).to_contain_text("Host")
    expect(bob.locator("#lobby-players li", has_text="Bob")).to_contain_text("(you)")


def test_invite_link(player, live_server):
    ann, bob = player("Ann"), player()
    code = host(ann, "Connect Donut")

    # someone new is asked for a name first, and Enter joins
    bob.goto(f"{live_server.url}?code={code}")
    expect(bob.locator("#join-code")).to_have_value(code)
    expect(bob.locator("#player-name")).to_be_focused()
    bob.keyboard.type("Bob")
    bob.keyboard.press("Enter")
    expect(bob.locator("#lobby-code")).to_have_text(code)
    expect(ann.locator("#lobby-players li", has_text="Bob")).to_be_visible()


def test_invite_link_with_a_remembered_name_joins_straight_away(player, live_server):
    ann, bob = player("Ann"), player("Bob")
    code = host(ann, "Donut Cards")
    join(bob, code)  # Bob's name is remembered in this browser now
    bob.click("#lobby-leave")
    expect(bob.locator("#screen-home")).to_be_visible()
    bob.goto(f"{live_server.url}?code={code}")
    expect(bob.locator("#lobby-code")).to_have_text(code)
    expect(ann.locator("#lobby-players li", has_text="Bob")).to_be_visible()


def test_host_leaving_the_lobby_hands_it_over(player):
    ann, bob, cat = player("Ann"), player("Bob"), player("Cat")
    code = host(ann, "Donut Cards")
    join(bob, code)
    join(cat, code)
    expect(bob.locator("#lobby-heading")).to_have_text("Players (3/10)")

    ann.click("#lobby-leave")
    expect(ann.locator("#screen-home")).to_be_visible()
    expect(bob.locator("#lobby-heading")).to_have_text("Players (2/10)")
    expect(bob.locator("#lobby-players li", has_text="Bob")).to_contain_text("Host")
    expect(bob.locator("#start")).to_be_visible()
    expect(cat.locator("#start")).to_be_hidden()
    expect(cat.locator("#lobby-players li", has_text="Bob")).to_contain_text("Host")

    bob.click("#start")  # and the new host can start
    expect(cat.locator("#screen-game")).to_be_visible()


def test_host_plays_against_bots(player):
    ann, bob = player("Ann"), player("Bob")
    code = host(ann, "Donut Cards")
    join(bob, code)
    expect(bob.locator("#add-bots")).to_be_hidden()  # only the host adds bots
    ann.get_by_role("button", name="Add a bot").click()
    ann.get_by_role("button", name="Add a bot").click()
    expect(bob.locator("#lobby-heading")).to_have_text("Players (4/10)")
    ann.get_by_role("button", name="Remove Bot 2").click()
    expect(ann.locator("#lobby-heading")).to_have_text("Players (3/10)")

    start(ann, bob)
    bot = bob.locator("[data-log] li", has_text="Bot")
    while not bot.count():  # the people pick up until the bot has had a go
        for page in (ann, bob):
            draw = page.locator("[data-draw]")
            if draw.is_enabled():
                draw.click()
        bob.wait_for_timeout(300)
    expect(bot.first).to_be_visible()


# ---- Connect Donut


def test_connect4_win_and_rematch(player):
    ann, bob, _ = connect4_game(player)
    expect(status(ann)).to_have_text("Your turn")
    expect(status(bob)).to_have_text("Their turn")
    expect(column(bob, 0)).to_be_disabled()

    for page, col in [(ann, 0), (bob, 1)] * 3 + [(ann, 0)]:
        column(page, col).click()  # waits until it's that player's turn
    expect(status(ann)).to_have_text("You win! 🎉")
    expect(status(bob)).to_have_text("You lose")
    expect(ann.locator("img[alt='your donut']")).to_have_count(4)
    expect(ann.locator("img[alt=\"opponent's donut\"]")).to_have_count(3)

    ann.click("#rematch")
    expect(ann.locator("#rematch")).to_have_text("Waiting for others…")
    expect(bob.locator("#rematch")).to_have_text("Others want a rematch!")
    bob.click("#rematch")
    expect(bob.locator("#game-root [data-round]")).to_have_text("Round 2")
    expect(status(bob)).to_have_text("Your turn")  # the other player starts round 2
    expect(ann.locator("img[alt='your donut']")).to_have_count(0)
    expect(ann.locator("#rematch")).to_be_hidden()


def test_turn_timer_is_shown(player):
    ann, bob = player("Ann"), player("Bob")
    code = host(ann, "Connect Donut", toggles=["Turn timer"])
    join(bob, code)
    start(ann, bob)
    expect(ann.locator("#turn-timer")).to_be_visible()
    expect(ann.locator("#turn-timer")).to_contain_text("⏱")


def test_leaving_mid_game_asks_first(player):
    ann, bob, _ = connect4_game(player)
    bob.click("#game-leave")
    expect(bob.locator("#confirm-leave")).to_be_visible()
    bob.click("#confirm-stay")
    expect(bob.locator("#confirm-leave")).to_be_hidden()
    expect(bob.locator("#screen-game")).to_be_visible()

    leave_game(bob)
    expect(ann.locator("#banner")).to_have_text("Bob left the game.")
    expect(status(ann)).to_have_text("Game over")
    expect(ann.locator("#rematch")).to_be_hidden()  # one player can't have a rematch

    ann.click("#game-leave")  # nobody left to upset, so no question
    expect(ann.locator("#screen-home")).to_be_visible()


def test_chat(player):
    ann, bob, _ = connect4_game(player)
    ann.click("#chat-open")
    ann.fill("#chat-input", "good luck")
    ann.get_by_role("button", name="Send").click()
    expect(ann.locator("#chat-log li").last).to_have_text("You good luck")
    expect(bob.locator("#chat-unread")).to_have_text("1")
    bob.click("#chat-open")
    expect(bob.locator("#chat-unread")).to_be_hidden()
    expect(bob.locator("#chat-log li").last).to_have_text("Ann good luck")


# ---- getting back in


def test_refresh_keeps_your_seat(player):
    ann, bob, _ = connect4_game(player)
    column(ann, 3).click()
    expect(status(bob)).to_have_text("Your turn")
    bob.reload()
    expect(bob.locator("#screen-game")).to_be_visible()
    expect(status(bob)).to_have_text("Your turn")
    expect(bob.locator("img[alt=\"opponent's donut\"]")).to_have_count(1)
    column(bob, 3).click()
    expect(status(ann)).to_have_text("Your turn")


def test_game_carries_on_through_a_server_restart(player, live_server):
    ann, bob, _ = connect4_game(player)
    column(ann, 0).click()
    expect(status(bob)).to_have_text("Your turn")

    live_server.stop()  # what a deploy does: the old server saves and goes
    for page in (ann, bob):
        expect(page.locator("#banner")).to_have_text("Connection lost, reconnecting…")
    live_server.start()  # and the new one loads the saved rooms

    for page in (ann, bob):  # both pages rejoin by themselves
        expect(page.locator("#banner")).to_be_hidden(timeout=20_000)
        expect(page.locator("#screen-game")).to_be_visible()
    expect(status(bob)).to_have_text("Your turn")
    expect(ann.locator("img[alt='your donut']")).to_have_count(1)
    column(bob, 1).click()
    expect(status(ann)).to_have_text("Your turn")
    expect(ann.locator("img[alt=\"opponent's donut\"]")).to_have_count(1)


# ---- Donut Cards


def test_cards_only_playable_ones_light_up_and_drawing_passes(player, live_server):
    (ann, bob), _ = cards_game(player, live_server, ["Ann", "Bob"],
                               {1: ["5S", "KD"], 2: ["9C"]}, top="4S", pile=["7H", "3C"])
    expect(card(ann, "K♦")).to_have_class("sw-card dim")  # can't go on 4♠
    card(ann, "5♠").click()
    expect(card(ann, "5♠")).to_contain_text("1")  # shows the order cards will be played in
    ann.locator("[data-play]").click()

    expect(status(bob)).to_contain_text("Your turn")
    expect(bob.locator("[data-log] li").first).to_have_text("Ann played 5")
    expect(bob.locator("[data-log] li").first.locator("img")).to_have_attribute(
        "src", "sprites/choccy.png")  # the suit as its donut, not ♠
    expect(card(bob, "9♣")).to_have_class("sw-card dim")
    bob.locator("[data-draw]").click()
    expect(bob.locator(".sw-card")).to_have_count(2)  # picked up the 3♣
    expect(card(bob, "3♣")).to_be_visible()
    expect(status(ann)).to_contain_text("Your turn")
    expect(ann.locator(".sw-player", has_text="Bob")).to_contain_text("2")


def test_cards_ace_asks_for_a_suit(player, live_server):
    (ann, bob), _ = cards_game(player, live_server, ["Ann", "Bob"],
                               {1: ["AH", "9C"], 2: ["9D"]}, top="4S")
    card(ann, "A♥").click()
    expect(ann.locator("[data-suits]")).to_be_visible()
    expect(ann.locator("[data-play]")).to_be_disabled()  # pick a suit first
    expect(ann.locator("[data-suits] img")).to_have_count(4)  # the donuts, not ♠♥♦♣
    ann.get_by_role("button", name="blue").click()
    ann.locator("[data-play]").click()
    expect(bob.locator("[data-suit]")).to_have_text("asked")
    expect(bob.locator("[data-suit] img")).to_have_attribute("src", "sprites/blue.png")


def test_cards_calling_shows_everyone(player, live_server):
    (ann, bob), _ = cards_game(player, live_server, ["Ann", "Bob"],
                               {1: ["5S", "6S"], 2: ["9D"]}, top="4S")
    ann.locator("[data-call]").click()
    expect(ann.locator("[data-call]")).to_have_text("Called!")
    expect(bob.locator(".sw-player", has_text="Ann")).to_contain_text("Cards!")


def test_cards_rematch_goes_ahead_without_the_player_who_left(player, live_server):
    (ann, bob, cat), code = cards_game(
        player, live_server, ["Ann", "Bob", "Cat"],
        {1: ["5S"], 2: ["9C", "9D"], 3: ["9H", "3C"]}, top="4S", called=[1])
    card(ann, "5♠").click()
    ann.locator("[data-play]").click()
    expect(status(ann)).to_have_text("You win! 🎉")
    expect(status(bob)).to_have_text("Ann wins")

    leave_game(cat)
    for page in (ann, bob):
        expect(page.locator("#banner")).to_have_text("Cat left the game.")
        expect(page.locator("#rematch")).to_be_visible()
    ann.click("#rematch")
    bob.click("#rematch")

    for page in (ann, bob):  # a fresh game for the two who stayed
        expect(page.locator(".sw-player")).to_have_count(2)
        expect(page.locator(".sw-card")).to_have_count(7)
        expect(page.locator("#banner")).to_be_hidden()
    assert live_server.room(code).game.num_players == 2
