"""An enemy's Library of Ruina page, shown as it attacks: framed like a Ruina combat page, then
burnt away from the bottom up - a glowing edge eating into it, embers lifting off.

The art is Ruina's own (data/art/pages/<Artwork>.png, shipped with the wheel, never in git).
Each frame is written as the client patch reads it (covers.dxt5_file); the patch shows them one
after another (myosotis StoryPatch, cards.txt)."""
import random

from PIL import Image, ImageDraw, ImageFilter

FRAMES = 6                        # the page whole, then 5 steps of its burning
SIN_GLOW = {"CRIMSON": (232, 52, 30), "SCARLET": (238, 92, 30), "AMBER": (250, 176, 40), "SHAMROCK": (120, 200, 60),
            "AZURE": (70, 160, 240), "INDIGO": (90, 90, 230), "VIOLET": (170, 80, 220)}


def face(art, sin="CRIMSON"):
    """The page: its art in a dark frame with a thin border in its sin's colour, 260x372."""
    W, H = 260, 372
    glow = SIN_GLOW.get(sin, SIN_GLOW["CRIMSON"])
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, W - 1, H - 1), 14, fill=(18, 14, 12, 255), outline=glow + (255,), width=3)
    d.rounded_rectangle((7, 7, W - 8, H - 8), 10, outline=(196, 160, 96, 255), width=1)
    a = art.convert("RGBA")
    k = (H - 30) / a.height                      # the art fills the page's height, cut to its width
    a = a.resize((max(1, int(a.width * k)), H - 30), Image.LANCZOS)
    x = (a.width - (W - 30)) // 2
    a = a.crop((x, 0, x + W - 30, a.height))
    mask = Image.new("L", a.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, a.width - 1, a.height - 1), 8, fill=255)
    card.paste(a, (15, 15), mask)
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))     # its sin glowing up from the bottom edge
    sd = ImageDraw.Draw(shade)
    for i in range(60):
        sd.line((15, H - 16 - i, W - 16, H - 16 - i), fill=glow + (int(90 * (1 - i / 60)),))
    return Image.alpha_composite(card, Image.composite(shade, Image.new("RGBA", (W, H)), card.getchannel("A")))


def _noise(w, h, seed):
    """Smooth noise 0-255: where the fire reaches first."""
    rnd = random.Random(seed)
    small = Image.new("L", (w // 18 + 2, h // 18 + 2))
    small.putdata([rnd.randrange(256) for _ in range(small.width * small.height)])
    return small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(4))


def burn(card, frames=FRAMES, seed=7):
    """[frame images]: the page whole, then burning away from the bottom up - charred edge, a
    glowing rim and embers drifting above it - to nothing."""
    w, h = card.size
    pad = 40                                     # room for embers above the page
    noise = _noise(w, h, seed)
    # each pixel's burn time: low rows first, the noise making the edge ragged
    t = Image.new("L", (w, h))
    t.putdata([min(255, int(0.78 * 255 * (h - 1 - i // w) / max(1, h - 1) + 0.22 * v)) for i, v in enumerate(noise.getdata())])
    rnd = random.Random(seed + 1)
    out = []
    for k in range(frames):
        frame = Image.new("RGBA", (w, h + pad), (0, 0, 0, 0))
        if k == 0:
            frame.paste(card, (0, pad))
            out.append(frame)
            continue
        cut = int(262 * k / (frames - 1)) - 4     # burnt where t < cut
        gone = t.point(lambda v, c=cut: 255 if v < c else 0)
        rim = t.point(lambda v, c=cut: 255 if c <= v < c + 9 else 0)
        char = t.point(lambda v, c=cut: 255 if c + 9 <= v < c + 22 else 0)
        alpha = Image.composite(Image.new("L", (w, h), 0), card.getchannel("A"), gone)
        body = card.copy()
        body = Image.composite(Image.new("RGBA", (w, h), (30, 14, 6, 255)), body, char.filter(ImageFilter.GaussianBlur(1.5)))
        body.putalpha(alpha)
        hot = Image.new("RGBA", (w, h), (255, 150, 40, 0))
        hot.putalpha(Image.composite(rim.filter(ImageFilter.GaussianBlur(2)), Image.new("L", (w, h), 0), card.getchannel("A")))
        glow = hot.filter(ImageFilter.GaussianBlur(6))
        layer = Image.alpha_composite(Image.alpha_composite(glow, body), hot)
        frame.alpha_composite(layer, (0, pad))
        d = ImageDraw.Draw(frame)
        edge = [(x, y) for y in range(0, h, 6) for x in range(0, w, 6) if cut <= t.getpixel((x, y)) < cut + 9
                and card.getpixel((x, y))[3]]
        for x, y in rnd.sample(edge, min(len(edge), 26)):
            up = rnd.randint(4, pad + 20)
            r = rnd.choice((1, 1, 2, 2, 3))
            col = rnd.choice(((255, 200, 90), (255, 140, 40), (255, 90, 30)))
            d.ellipse((x - r, y + pad - up - r, x + r, y + pad - up + r), fill=col + (rnd.randint(150, 255),))
        out.append(frame)
    return out


def bubble(words, frames=FRAMES):
    """[frame images]: a speech bubble holding words, crooked and red-edged, held then fading out
    (Nothing There's "Hello?")."""
    from PIL import ImageFont
    font = ImageFont.load_default(size=44)
    tw = int(font.getlength(words))
    W, H = tw + 90, 150
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((8, 8, W - 9, H - 46), 34, fill=(248, 244, 238, 245), outline=(150, 22, 18, 255), width=4)
    d.polygon([(W * 0.30, H - 48), (W * 0.22, H - 8), (W * 0.42, H - 48)], fill=(248, 244, 238, 245))
    d.line([(W * 0.30, H - 46), (W * 0.22, H - 8), (W * 0.42, H - 46)], fill=(150, 22, 18, 255), width=4)
    d.text((45, (H - 46) // 2 - 26), words, font=font, fill=(70, 8, 8, 255))
    base = base.rotate(-4, resample=Image.BICUBIC, expand=True)
    pad = Image.new("RGBA", (int(base.width * 1.1), int(base.height * 2.6)), (0, 0, 0, 0))
    pad.alpha_composite(base, (pad.width - base.width, 0))     # the page's place is tall: the bubble at its top
    base = pad
    out = []
    for k in range(frames):
        a = 1.0 if k < 2 else max(0.0, 1.0 - (k - 1) / (frames - 1))
        f = base.copy()
        f.putalpha(f.getchannel("A").point(lambda v, a=a: int(v * a)))
        out.append(f)
    return out
