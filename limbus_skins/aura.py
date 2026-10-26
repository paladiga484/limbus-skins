"""Auras: effect sprites from the game's own bundles drifting around a repainted body - the
Roach Emperor's flies, Sancho's blood mist.

A boss's aura is particle systems on its prefab, which a repaint can't carry: the identity
keeps its own prefab. So the aura is drawn into the pictures instead. Every new frame gets
the sprites scattered around the body a little differently, so they buzz and seethe as the
animation plays; an idle that is a borrowed Spine rig gets them as extra moving parts.

skins.json "aura": {"from": body whose bundle holds the textures, "textures": [names],
"split": {name: [cols, rows]} (flipbook sheets), "style": "swarm" | "mist", "count": n,
"size": fraction of the body's height, "color": [r, g, b] (tints a grey texture),
"soft": [names] (fade to nothing at the edges: a cloud the particle shape used to cut)}.
Nothing is shipped: the textures are read from the player's own cache.
"""
import math
import random

from .game import unitypy


class AuraError(Exception):
    pass


def textures(spec, locate):
    """The aura's sprites as RGBA images (flipbook cells in order), tinted when asked."""
    env = unitypy().load(locate(spec["from"]))
    found = {}
    for o in env.objects:
        if o.type.name == "Texture2D":
            t = o.read()
            if t.m_Name in spec["textures"] and t.m_Name not in found:
                found[t.m_Name] = t.image.convert("RGBA")
    missing = [n for n in spec["textures"] if n not in found]
    if missing:
        raise AuraError(f"{spec['from']} has no {', '.join(missing)}")
    out = []
    for n in spec["textures"]:
        img = found[n]
        cols, rows = spec.get("split", {}).get(n, (1, 1))
        w, h = img.width // cols, img.height // rows
        for r in range(rows):
            for c in range(cols):
                cell = img.crop((c * w, r * h, (c + 1) * w, (r + 1) * h))
                if spec.get("color"):
                    cell = tint(cell, tuple(spec["color"]))
                if n in spec.get("soft", ()):
                    cell = soften(cell)
                box = cell.getchannel("A").getbbox()
                if box:
                    out.append(cell.crop(box))
    if not out:
        raise AuraError("the aura's textures are empty")
    return out


def tint(img, color):
    """A grey effect texture as colour, its brightness above the background's for alpha
    (how the game's additive particles read it: the corners' grey is see-through)."""
    from PIL import Image, ImageChops
    lum = img.convert("L")
    w, h = lum.size
    lo = min(lum.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
    hi = max(lum.getextrema()[1], lo + 1)
    alpha = ImageChops.multiply(lum.point(lambda v: max(0, v - lo) * 255 // (hi - lo)), img.getchannel("A"))
    out = Image.new("RGBA", img.size, color + (0,))
    out.putalpha(alpha)
    return out


def soften(img):
    """The image fading out towards its edges, along an ellipse."""
    from PIL import Image, ImageChops
    w, h = img.size
    mask = Image.new("L", (w, h))
    mask.putdata([max(0, int(255 * (1 - min(1.0, math.hypot((x - w / 2) / (w / 2), (y - h / 2) / (h / 2)))) ** 1.5))
                  for y in range(h) for x in range(w)])
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return out


def _placements(spec, n_sprites, ref, seed):
    """[(sprite index, x, y, size, degrees, flip, alpha, in front)] around a body of height
    ref whose feet are at (0, 0), y up. Deterministic for a seed."""
    rnd = random.Random(seed)
    style = spec.get("style", "swarm")
    count = int(spec.get("count", 6))
    size = float(spec.get("size", 0.12)) * ref
    out = []
    if style == "mist":
        # one big cloud behind the body, swelling a little, and droplets rising off it
        k = 1 + 0.06 * math.sin(seed * 1.7)
        out.append((0, rnd.uniform(-0.03, 0.03) * ref, 0.5 * ref, 1.25 * ref * k, rnd.uniform(-8, 8), False,
                    float(spec.get("alpha", 0.6)), False))
        for i in range(count):
            out.append((1 + rnd.randrange(max(1, n_sprites - 1)) if n_sprites > 1 else 0,
                        rnd.uniform(-0.45, 0.45) * ref, rnd.uniform(0.15, 1.0) * ref,
                        size * rnd.uniform(0.6, 1.2), rnd.uniform(-40, 40), rnd.random() < 0.5,
                        rnd.uniform(0.5, 0.9), rnd.random() < 0.4))
    else:
        # flies on loose orbits about the chest, a step further round each frame
        for i in range(count):
            a = 2 * math.pi * (i / count + 0.11 * seed) + rnd.uniform(-0.3, 0.3)
            x = math.cos(a) * 0.55 * ref * rnd.uniform(0.75, 1.1)
            y = (0.55 + math.sin(a) * 0.38 * rnd.uniform(0.7, 1.1)) * ref
            going_left = math.sin(a) > 0
            out.append(((seed + i) % n_sprites, x, y, size * rnd.uniform(0.75, 1.2), rnd.uniform(-25, 25),
                        going_left, 1.0, math.cos(a) < 0))
    return out


def bake(img, pivot, sprites, spec, ref, seed):
    """The frame with the aura drawn behind and in front of the body. pivot = (x, y) as
    fractions, bottom-up (the feet); ref = the body's standing height in this frame's
    pixels. Returns (image, pivot) - the canvas grows to fit and the pivot stays on the feet."""
    from PIL import Image
    fx, fy = pivot[0] * img.width, (1 - pivot[1]) * img.height     # feet, top-down pixels
    behind, front = [], []
    for idx, x, y, size, deg, flip, alpha, ahead in _placements(spec, len(sprites), ref, seed):
        s = sprites[idx]
        k = size / max(s.width, s.height)
        im = s.resize((max(1, round(s.width * k)), max(1, round(s.height * k))), Image.LANCZOS)
        if flip:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        if deg:
            im = im.rotate(deg, resample=Image.BICUBIC, expand=True)
        if alpha < 1:
            im.putalpha(im.getchannel("A").point(lambda v, a=alpha: int(v * a)))
        (front if ahead else behind).append((im, round(fx + x - im.width / 2), round(fy - y - im.height / 2)))
    every = behind + front
    left = max([0] + [-x for _, x, _ in every])
    top = max([0] + [-y for _, _, y in every])
    right = max([img.width] + [x + im.width for im, x, _ in every]) + left
    bottom = max([img.height] + [y + im.height for im, _, y in every]) + top
    out = Image.new("RGBA", (right, bottom), (0, 0, 0, 0))
    for im, x, y in behind:
        out.alpha_composite(im, (x + left, y + top))
    out.alpha_composite(img, (left, top))
    for im, x, y in front:
        out.alpha_composite(im, (x + left, y + top))
    return out, ((fx + left) / out.width, 1 - (fy + top) / out.height)
