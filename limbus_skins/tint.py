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


def tint(target, locate, spec):
    """Recolor every texture body `target` loads. Returns (bundle bytes, notes); the bytes
    still need bundle.forge() to carry the stock CRC."""
    from .repaint import RepaintError, prefab_objects
    UnityPy = unitypy()
    env = UnityPy.load(locate(target))
    ids, roots = prefab_objects(env, target, forms=True)
    pma = {}
    for root in roots:                            # Spine pages say whether they are premultiplied
        for rig in spine.body_spines(env, root):
            for tex in rig["textures"].values():
                pma[tex.path_id] = bool(rig["pma"])
    done = 0
    for o in env.objects:
        if o.type.name != "Texture2D" or o.path_id not in ids:
            continue
        d = o.read()
        d.set_image(colorize(d.image, spec, pma.get(o.path_id, False)),
                    target_format=UnityPy.enums.TextureFormat.RGBA32)
        d.save()
        done += 1
    if not done:
        raise RepaintError(f"{target}: no textures to recolor")
    return env.file.save(packer="none"), [f"{done} texture(s) recolored"]
