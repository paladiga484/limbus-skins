"""Tint skins: an identity keeps its own body and drawings, recolored (Greencliff).

Only the pixels of the body's own textures change - atlas pages and Spine idle pages -
so every frame keeps its exact geometry and nothing is re-packed.
"""
import colorsys

from . import spine
from .game import unitypy


def colorize(img, spec, pma=False):
    """Recolor to one hue, keeping each pixel's lightness and alpha. spec: {"hue": degrees,
    optional "saturation" 0..1}. pma: the pixels are premultiplied by alpha (and stay so)."""
    from PIL import Image, ImageChops, ImageMath, ImageOps
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    light = Image.merge("RGB", (r, g, b)).convert("L")
    if pma:                                       # lightness of the straight colour
        light = ImageMath.lambda_eval(
            lambda e: e["convert"](e["min"](e["l"] * 255 / e["max"](e["a"], 1), 255), "L"), l=light, a=a)
    mid = tuple(int(c * 255) for c in colorsys.hls_to_rgb(spec["hue"] / 360, 0.42, spec.get("saturation", 0.75)))
    hi = tuple(min(255, c + 200) for c in mid)
    out = ImageOps.colorize(light, black=(0, 0, 0), mid=mid, white=hi).split()
    if pma:
        out = [ImageChops.multiply(c, a) for c in out]
    return Image.merge("RGBA", (*out, a))


def _frame_rects(env, ids):
    """{page path_id: [(kind, (x, y, w, h) bottom-up, packing turn)]}: where each of the body's own
    frames (not its effect sprites) sits on a page - its own copy and its atlas's. A copy turned
    90 degrees in its atlas is left out."""
    from .repaint import fx_like, kind_of
    amap = {}
    for o in env.objects:
        if o.type.name == "SpriteAtlas":
            for k, v in o.read_typetree()["m_RenderDataMap"]:
                amap[(tuple(k[0].values()), k[1])] = v
    out, seen = {}, set()
    for o in env.objects:
        if o.type.name != "Sprite" or o.path_id not in ids:
            continue
        t = o.read_typetree()
        kind = kind_of(t["m_Name"])[0] or "frame"        # "skil1_1", "idel": the drawings' own typos
        if fx_like(t["m_Name"]) or (kind.startswith("S") and kind.endswith("e")):
            continue
        key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
        for rd in (t["m_RD"], amap.get(key)):           # the sprite's own copy and its atlas's: both get drawn
            turn = (rd["settingsRaw"] >> 2) & 0xF if rd else 0
            if not rd or turn not in FLIPS or not rd["texture"]["m_PathID"]:
                continue
            r = rd["textureRect"]
            rect = (round(r["x"]), round(r["y"]), round(r["width"]), round(r["height"]))
            page = rd["texture"]["m_PathID"]
            if rect[2] > 0 and rect[3] > 0 and (page, rect) not in seen:
                seen.add((page, rect))
                out.setdefault(page, []).append((kind, rect, turn))
    return out


# SpritePackingRotation: how a frame lies on its atlas page; each of these undoes itself
FLIPS = {0: None, 1: "FLIP_LEFT_RIGHT", 2: "FLIP_TOP_BOTTOM", 3: "ROTATE_180"}


def _turn(img, turn):
    from PIL import Image
    return img.transpose(getattr(Image.Transpose, FLIPS[turn])) if FLIPS.get(turn) else img


def _crop(img, rect):
    x, y, w, h = rect
    return (x, img.height - y - h, x + w, img.height - y)       # Unity rects are bottom-up


def stick_pages(env, ids, spec):
    """{page path_id: new picture} with every body frame on it drawn as a stick figure below its
    head (stick.py). Head and stroke sizes come from the idle frame's height."""
    from . import stick
    rects = _frame_rects(env, ids)
    pages = {o.path_id: o for o in env.objects if o.type.name == "Texture2D" and o.path_id in rects}
    images = {pid: o.read().image.convert("RGBA") for pid, o in pages.items()}
    stand = 0
    for pid, items in rects.items():
        for kind, rect, turn in items:
            if kind == "idle":
                box = _turn(images[pid].crop(_crop(images[pid], rect)), turn).getchannel("A").getbbox()
                if box:
                    stand = max(stand, box[3] - box[1])
    if not stand:
        raise ValueError("stick: the body has no idle frame to measure")
    head, width = spec.get("head", 0.45) * stand, max(2.0, spec.get("width", 0.035) * stand)
    color = tuple(spec.get("color", (60, 60, 60)))
    skip = spec.get("skip", 0.12) * stand
    for pid, items in rects.items():
        img = images[pid]
        for _, rect, turn in items:
            box = _crop(img, rect)
            upright = _turn(img.crop(box), turn)
            img.paste(_turn(stick.stickify(upright, head, width, color, skip), turn), box[:2])
    return images


def tint(target, locate, spec, stick=None):
    """Recolor every texture body `target` loads (its frames first drawn as a stick figure when
    `stick` is given: see stick.py). Returns (bundle bytes, notes); the bytes still need
    bundle.forge() to carry the stock CRC."""
    from .repaint import RepaintError, prefab_objects
    UnityPy = unitypy()
    env = UnityPy.load(locate(target))
    ids, roots = prefab_objects(env, target, forms=True)
    pma = {}
    for root in roots:                            # Spine pages say whether they are premultiplied
        for rig in spine.body_spines(env, root):
            for tex in rig["textures"].values():
                pma[tex.path_id] = bool(rig["pma"])
    sticks = {}
    if stick:
        try:
            sticks = stick_pages(env, ids, stick)
        except ValueError as e:
            raise RepaintError(f"{target}: {e}")
    done = 0
    for o in env.objects:
        if o.type.name != "Texture2D" or o.path_id not in ids:
            continue
        d = o.read()
        d.set_image(colorize(sticks.get(o.path_id) or d.image, spec, pma.get(o.path_id, False)),
                    target_format=UnityPy.enums.TextureFormat.RGBA32)
        d.save()
        done += 1
    if not done:
        raise RepaintError(f"{target}: no textures to recolor")
    notes = [f"{done} texture(s) recolored"]
    if sticks:
        notes.append(f"a stick figure below the head on {len(sticks)} page(s)")
    return env.file.save(packer="none"), notes
