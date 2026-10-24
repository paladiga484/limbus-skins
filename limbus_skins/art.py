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
    img = cut_out(Image.open(picture))
    if do_invert:
        img = invert(img)
    p = path_for(data_dir, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    img.save(p)
    return p, img.size
