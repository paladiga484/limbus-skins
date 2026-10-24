"""Your own head art: cut a picture out of its background, optionally invert it.

`limbus-skins art <skin> <picture> [--invert]` keeps the result in the data folder, and
head recipes use it instead of the in-game texture. Nothing is uploaded or shipped.
"""
import colorsys
import os


def cut_out(img, dark=60):
    """The main subject of a picture: its own transparency if it has some, otherwise
    everything the dark background (flood-filled in from the border) does not reach -
    keeping only the biggest piece, so corner decorations fall away."""
    from PIL import Image
    img = img.convert("RGBA")
    W, H = img.size
    if img.getchannel("A").getextrema()[0] < 250:
        return img.crop(img.getbbox())
    lum = img.convert("L").load()
    bg = bytearray(W * H)
    stack = [(x, y) for x in range(W) for y in (0, H - 1)] + [(x, y) for y in range(H) for x in (0, W - 1)]
    while stack:
        x, y = stack.pop()
        i = y * W + x
        if bg[i] or lum[x, y] > dark:
            continue
        bg[i] = 1
        if x:
            stack.append((x - 1, y))
        if x < W - 1:
            stack.append((x + 1, y))
        if y:
            stack.append((x, y - 1))
        if y < H - 1:
            stack.append((x, y + 1))
    # biggest connected piece of what is left
    seen = bytearray(W * H)
    best = []
    for start in range(W * H):
        if bg[start] or seen[start]:
            continue
        piece, stack = [], [start]
        seen[start] = 1
        while stack:
            i = stack.pop()
            piece.append(i)
            x, y = i % W, i // W
            for j in ((i - 1) if x else -1, (i + 1) if x < W - 1 else -1, i - W, i + W):
                if 0 <= j < W * H and not bg[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        if len(piece) > len(best):
            best = piece
    keep = bytearray(W * H)
    for i in best:
        keep[i] = 1
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    src, dst = img.load(), out.load()
    for i in best:
        dst[i % W, i // W] = src[i % W, i // W]
    # soft edge: background pixels touching the subject keep their brightness as alpha
    for i in best:
        x, y = i % W, i // W
        for j in ((i - 1) if x else -1, (i + 1) if x < W - 1 else -1, i - W, i + W):
            if 0 <= j < W * H and not keep[j]:
                r, g, b, _ = src[x, y]                     # the outline's colour, faded
                dst[j % W, j // W] = (r, g, b, min(255, lum[j % W, j // W] * 3))
    return out.crop(out.getbbox())


def invert(img):
    """Light and dark swap; hues stay (purple stays purple, white turns black)."""
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if not a:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            r, g, b = colorsys.hls_to_rgb(h, 1 - l, s)
            px[x, y] = (round(r * 255), round(g * 255), round(b * 255), a)
    return out


def path_for(data_dir, key):
    return os.path.join(data_dir, "art", f"{key}.png")


def save(picture, data_dir, key, do_invert=False):
    from PIL import Image
    with Image.open(picture) as im:
        im.load()
        src = im.convert("RGBA")
    src.thumbnail((2048, 2048))          # a head needs no more, and the cut-out is pure Python
    img = cut_out(src)
    if do_invert:
        img = invert(img)
    p = path_for(data_dir, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    img.save(p)
    return p, img.size


def halo(img, radius, color=(201, 182, 255), strength=0.6):
    """Just the soft glow around img's shape, on a canvas padded by `pad` on each side.
    Returns (glow image, pad)."""
    from PIL import Image, ImageFilter
    radius = max(1.0, radius)
    pad = int(radius * 2.5) + 2
    a = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 0)
    a.paste(img.getchannel("A"), (pad, pad))
    a = a.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength * 1.8)))
    out = Image.new("RGBA", a.size, tuple(color) + (0,))
    out.putalpha(a)
    return out, pad


def glow(img, radius, color=(201, 182, 255), strength=0.6):
    """img with its glow behind it. Returns (image, pad)."""
    out, pad = halo(img, radius, color, strength)
    out.alpha_composite(img, (pad, pad))
    return out, pad


def mote(size=48, color=(214, 200, 255)):
    """A small soft spark: a glowing dot with a thin four-point star through it."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    c, r, t = size / 2, size * 0.16, size * 0.05
    dot = Image.new("L", (size, size), 0)
    ImageDraw.Draw(dot).ellipse([c - r, c - r, c + r, c + r], fill=255)
    dot = dot.filter(ImageFilter.GaussianBlur(size * 0.07))
    star = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(star)
    d.polygon([(c, 1), (c + t, c), (c, size - 1), (c - t, c)], fill=210)
    d.polygon([(1, c), (c, c - t), (size - 1, c), (c, c + t)], fill=210)
    out = Image.new("RGBA", (size, size), tuple(color) + (0,))
    out.putalpha(ImageChops.lighter(dot, star))
    core = Image.new("L", (size, size), 0)
    ImageDraw.Draw(core).ellipse([c - r / 2, c - r / 2, c + r / 2, c + r / 2], fill=255)
    out.alpha_composite(Image.merge("RGBA", (core, core, core, core.filter(ImageFilter.GaussianBlur(1)))))
    return out
