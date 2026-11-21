"""Draws the two placeholder characters (Professor Pip and Dunk) as transparent PNGs.

Replace assets/characters/pip.png and dunk.png with your own art any time.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "characters"
S = 2  # draw at 2x and downscale for smooth edges
INK = (25, 20, 35, 255)


def ellipse(d, box, fill, width=10):
    d.ellipse([v * S for v in box], fill=fill, outline=INK, width=width * S)


def pip() -> Image.Image:
    img = Image.new("RGBA", (600 * S, 800 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ellipse(d, (90, 230, 510, 790), (138, 92, 246, 255))                       # body
    ellipse(d, (170, 420, 430, 760), (186, 160, 255, 255), width=0)            # belly
    for cx in (225, 375):                                                      # eyes + glasses
        ellipse(d, (cx - 62, 300, cx + 62, 424), (255, 255, 255, 255), width=12)
        ellipse(d, (cx - 22, 344, cx + 14, 388), INK, width=0)
    d.line([(287 * S, 360 * S), (313 * S, 360 * S)], fill=INK, width=12 * S)  # glasses bridge
    d.arc([250 * S, 430 * S, 370 * S, 500 * S], 20, 160, fill=INK, width=10 * S)  # smug smile
    d.polygon([(v[0] * S, v[1] * S) for v in [(300, 150), (520, 215), (300, 280), (80, 215)]],
              fill=(40, 40, 60, 255), outline=INK)                             # mortarboard
    d.rectangle([215 * S, 230 * S, 385 * S, 285 * S], fill=(40, 40, 60, 255))
    d.line([(300 * S, 215 * S), (470 * S, 300 * S)], fill=(255, 200, 40, 255), width=8 * S)  # tassel
    ellipse(d, (455, 290, 485, 330), (255, 200, 40, 255), width=0)
    return img.resize((600, 800), Image.LANCZOS)


def dunk() -> Image.Image:
    img = Image.new("RGBA", (600 * S, 800 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for x0, x1 in ((280, 300), (300, 330), (310, 350)):                        # hair tuft
        d.polygon([(x0 * S, 250 * S), (x1 * S, 160 * S), ((x1 + 30) * S, 255 * S)],
                  fill=(90, 60, 30, 255), outline=INK)
    ellipse(d, (70, 220, 530, 790), (255, 150, 50, 255))                       # body
    ellipse(d, (160, 300, 300, 450), (255, 255, 255, 255), width=12)           # big uneven eyes
    ellipse(d, (320, 320, 430, 430), (255, 255, 255, 255), width=12)
    ellipse(d, (215, 370, 265, 420), INK, width=0)
    ellipse(d, (340, 355, 380, 395), INK, width=0)
    ellipse(d, (220, 480, 380, 600), (120, 20, 30, 255), width=10)             # open mouth
    ellipse(d, (255, 545, 345, 600), (255, 110, 130, 255), width=0)            # tongue
    d.rectangle([255 * S, 488 * S, 345 * S, 515 * S], fill=(255, 255, 255, 255))  # teeth
    return img.resize((600, 800), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    pip().save(OUT / "pip.png")
    dunk().save(OUT / "dunk.png")
    print(f"Saved characters to {OUT}")
