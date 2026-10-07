"""Draw the Donut Cards faces into web/games/switch/cards/ from the sprites in web/sprites/.

Each suit is a donut colour. Ace to 10 lay out donuts with holes like the pips on a normal
card; jack, queen and king show two full donuts, one turned upside down, under a big letter.
The back is left alone. Needs Pillow: pip install pillow, then python scripts/make_cards.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "web" / "sprites"
OUT = ROOT / "web" / "games" / "switch" / "cards"

# suit name: (donut with a hole, full donut, colour of the rank and letters)
SUITS = {
    "spades": ("choccy.png", "choc_full.png", (20, 20, 20)),
    "clubs": ("blue.png", "blue_full.png", (20, 20, 20)),
    "hearts": ("pink.png", "pink_full.png", (200, 30, 50)),
    "diamonds": ("orange.png", "orange_full.png", (200, 30, 50)),
}
RANKS = ["ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "jack", "queen", "king"]

W, H = 400, 560  # the same shape the page lays cards out in
LEFT, MID, RIGHT = 120, 200, 280  # pip columns
TOP, BOTTOM = 100, 460  # centres of the top and bottom pip rows

# where the pips go, as (column, how far down from TOP to BOTTOM)
PIPS = {
    "2": [(MID, 0), (MID, 1)],
    "3": [(MID, 0), (MID, 0.5), (MID, 1)],
    "4": [(LEFT, 0), (RIGHT, 0), (LEFT, 1), (RIGHT, 1)],
}
PIPS["5"] = PIPS["4"] + [(MID, 0.5)]
PIPS["6"] = [(x, y) for x in (LEFT, RIGHT) for y in (0, 0.5, 1)]
PIPS["7"] = PIPS["6"] + [(MID, 0.25)]
PIPS["8"] = PIPS["7"] + [(MID, 0.75)]
PIPS["9"] = [(x, y) for x in (LEFT, RIGHT) for y in (0, 1 / 3, 2 / 3, 1)] + [(MID, 0.5)]
PIPS["10"] = PIPS["9"][:-1] + [(MID, 1 / 6), (MID, 5 / 6)]


def font(size):
    for name in ("calibrib.ttf", "Calibri Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def write(image, centre, text, size, **style):
    """Text with its letters, not its line box, centred on a point. Q is centred on its
    round part, as if it were an O, so its tail hangs out to the side."""
    face = font(size)
    left, top, right, bottom = face.getbbox(text.replace("Q", "O"), anchor="ls")
    x = centre[0] - (left + right) / 2
    y = centre[1] - (top + bottom) / 2
    ImageDraw.Draw(image).text((x, y), text, font=face, anchor="ls", **style)


def icing(donut):
    """The donut's main colour: the commonest solid one in the sprite."""
    colours = donut.getcolors(donut.width * donut.height)
    count, colour = max(c for c in colours if c[1][3] == 255)
    return colour


def blank(border):
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, W - 1, H - 1), radius=24, fill="white", outline=border, width=10)
    return card


def paste(card, sprite, cx, cy, scale=1, flip=False):
    if scale != 1:
        sprite = sprite.resize((sprite.width * scale, sprite.height * scale), Image.NEAREST)
    if flip:
        sprite = sprite.rotate(180)
    card.alpha_composite(sprite, (round(cx - sprite.width / 2), round(cy - sprite.height / 2)))


def corners(card, label, colour):
    """The rank in the top left corner, and upside down in the bottom right."""
    tag = Image.new("RGBA", (70, 70), (0, 0, 0, 0))
    write(tag, (35, 35), label, 52 if len(label) == 1 else 44, fill=colour)
    card.alpha_composite(tag, (6, 8))
    card.alpha_composite(tag.rotate(180), (W - 76, H - 78))


def number_card(rank, holed, border):
    card = blank(border)
    if rank == "ace":
        paste(card, holed, W / 2, H / 2, scale=2)
    else:
        for x, y in PIPS[rank]:
            paste(card, holed, x, TOP + y * (BOTTOM - TOP), flip=y > 0.5)
    return card


def face_card(rank, full, colour, border):
    """A full donut with the letter on it, readable in the top right, and the same again
    upside down in the bottom left."""
    donut = full.resize((full.width * 2, full.height * 2), Image.NEAREST)
    write(donut, (100, 100), rank[0].upper(), 95, fill=colour, stroke_width=6,
          stroke_fill="white")
    card = blank(border)
    paste(card, donut, 260, 190)
    paste(card, donut, 140, 370, flip=True)
    return card


def main():
    for suit, (holed_name, full_name, colour) in SUITS.items():
        holed = Image.open(SPRITES / holed_name).convert("RGBA")
        full = Image.open(SPRITES / full_name).convert("RGBA")
        border = icing(full)
        for rank in RANKS:
            if rank in ("jack", "queen", "king"):
                card = face_card(rank, full, colour, border)
            else:
                card = number_card(rank, holed, border)
            corners(card, rank if rank.isdigit() else rank[0].upper(), colour)
            card.save(OUT / f"{rank}_of_{suit}.png", optimize=True)
    print(f"Drew {len(SUITS) * len(RANKS)} cards into {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
