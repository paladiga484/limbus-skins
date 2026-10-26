"""Library of Ruina characters as repaint sources (Gebura, the Red Mist).

A Library of Ruina character draws each motion (Default, Slash, Guard, S1...) as a pose: a
prefab per motion whose children are separate sprites (body, hair, blade, mist), each with
its own transform. This assembles every pose into one image and hands repaint() frames
that look like Limbus sprites, so the identity's own body gets redrawn as that character.
Read from the player's own Library of Ruina install; nothing is shipped.
"""
import math
import os

from . import paths
from .game import unitypy


class LorError(Exception):
    pass


def bundle_path(name):
    """Path of a bundle under Library of Ruina's StreamingAssets/AssetBundles."""
    for root in paths.steam_roots():
        p = os.path.join(root, "steamapps", "common", "Library Of Ruina", "LibraryOfRuina_Data",
                         "StreamingAssets", "AssetBundles", *name.split("/"))
        if os.path.isfile(p):
            return p
    raise LorError(f"Library of Ruina's {name} wasn't found - is the game installed through Steam?")


def _trs(t):
    p, r, s = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
    ang = 2 * math.atan2(r["z"], r["w"])                  # sprites only turn about z
    c, sn = math.cos(ang), math.sin(ang)
    return (c * s["x"], sn * s["x"], -sn * s["y"], c * s["y"], p["x"], p["y"])


def _mul(a, b):
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def poses(bundle, prefix, ppu=100):
    """{motion: (RGBA image, (pivot x, pivot y))} for every GameObject named <prefix>_<motion>.
    The pivot is the character's origin (its feet), as a fraction of the image, bottom-up."""
    from PIL import Image
    env = unitypy().load(bundle)
    objs = {o.path_id: o for o in env.objects}

    def tt(pid):
        return objs[pid].read_typetree()

    out = {}
    for o in env.objects:
        if o.type.name != "GameObject":
            continue
        go = o.read_typetree()
        if not go["m_Name"].startswith(prefix + "_"):
            continue
        motion = go["m_Name"][len(prefix) + 1:]
        if motion in out:
            continue
        parts = []

        def walk(g, m, depth):
            tr = next(c["component"]["m_PathID"] for c in g["m_Component"]
                      if objs[c["component"]["m_PathID"]].type.name == "Transform")
            t = tt(tr)
            m = _mul(m, _trs(t))
            if not g.get("m_IsActive", 1):
                return
            for c in g["m_Component"]:
                co = objs[c["component"]["m_PathID"]]
                if co.type.name == "SpriteRenderer":
                    sr = co.read_typetree()
                    if sr["m_Sprite"]["m_PathID"] and sr.get("m_Enabled", 1):
                        parts.append((sr.get("m_SortingOrder", 0), depth, len(parts), sr["m_Sprite"]["m_PathID"],
                                      m, (sr.get("m_Color") or {}).get("a", 1), sr.get("m_FlipX", 0)))
            for ch in t["m_Children"]:
                walk(tt(tt(ch["m_PathID"])["m_GameObject"]["m_PathID"]), m, depth + 1)

        walk(go, (1, 0, 0, 1, 0, 0), 0)
        if not parts:
            continue
        W = H = 2400
        cx, cy = W // 2, int(H * 0.75)                     # the origin, with room above and below
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for _, _, _, sp, m, alpha, flip in sorted(parts):
            s = objs[sp].read()
            img = s.image.convert("RGBA")
            if flip:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            k = ppu / float(s.m_PixelsToUnits)
            sx, sy = math.hypot(m[0], m[1]), math.hypot(m[2], m[3])
            w, h = max(1, int(img.width * sx * k)), max(1, int(img.height * sy * k))
            im = img.resize((w, h), Image.LANCZOS)
            if alpha < 1:
                im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
            px, py = s.m_Pivot.x * w, (1 - s.m_Pivot.y) * h
            big = Image.new("RGBA", (w * 3, h * 3))
            big.alpha_composite(im, (w, h))
            big = big.rotate(math.degrees(math.atan2(m[1], m[0])), center=(w + px, h + py), resample=Image.BICUBIC)
            x, y = int(cx + m[4] * ppu - (w + px)), int(cy - m[5] * ppu - (h + py))
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            layer.paste(big, (x, y))                       # paste clips at the edges; compositing then blends
            canvas = Image.alpha_composite(canvas, layer)
        bb = canvas.getbbox()
        if bb:
            img = canvas.crop(bb)
            out[motion] = (img, ((cx - bb[0]) / img.width, (bb[3] - cy) / img.height))
    return out


class _Pivot:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Sprite:
    """What repaint reads from a source sprite: image, pivot, pixels per unit, name."""
    def __init__(self, name, image, pivot, ppu):
        self.m_Name, self.image, self.m_Pivot, self.m_PixelsToUnits = name, image, _Pivot(*pivot), ppu


class Frame:
    """A pose standing in for a Limbus sprite reader (repaint keys frames by path_id)."""
    _next = -1

    def __init__(self, name, image, pivot, ppu):
        self._sprite = _Sprite(name, image, pivot, ppu)
        self.path_id = Frame._next
        Frame._next -= 1

    def read(self):
        return self._sprite


def afterimage(img, pivot, dx, copies=3):
    """The pose with fading copies trailing behind it (dx pixels apart): a smear of speed.
    Library of Ruina characters face left, so behind is to the right; the canvas grows that
    way and the pivot stays on the body."""
    from PIL import Image
    pad = abs(dx) * copies
    out = Image.new("RGBA", (img.width + pad, img.height), (0, 0, 0, 0))
    for n in range(copies, 0, -1):
        ghost = img.copy()
        ghost.putalpha(ghost.getchannel("A").point(lambda v, n=n: int(v * 0.35 / n)))
        out.alpha_composite(ghost, (n * abs(dx), 0))
    out.alpha_composite(img, (0, 0))
    return out, (pivot[0] * img.width / out.width, pivot[1])


def source_table(spec, height=2.6):
    """repaint()'s source table from a skins.json "lor" entry: {"bundle", "prefab", "map":
    {kind: [motion, ...]}, "smear": [kind, ...]}. Pixels per unit are set so the standing
    pose is `height` units tall (a sinner's size)."""
    got = poses(bundle_path(spec["bundle"]), spec["prefab"])
    if "Default" not in got:
        raise LorError(f"{spec['prefab']} has no Default pose")
    ppu = got["Default"][0].height / height
    smear = set(spec.get("smear", ()))
    table = {}
    for kind, motions in spec["map"].items():
        frames = []
        for m in motions:
            if m not in got:
                raise LorError(f"{spec['prefab']} has no {m} pose (has {', '.join(sorted(got))})")
            img, pv = got[m]
            if kind in smear:                              # the run-up and swing get a smear first
                s_img, s_pv = afterimage(img, pv, max(8, img.width // 14))
                frames.append(Frame(f"{m}_smear", s_img, s_pv, ppu))
            frames.append(Frame(m, img, pv, ppu))
        table[kind] = frames
    table.setdefault("idle_time", 1.6)
    return table
