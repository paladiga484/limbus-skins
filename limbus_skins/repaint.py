"""Repaint an identity's own battle body with another character's drawings.

Swapping an identity onto another character's body crashes battles (those bodies lack the
per-uptie data an identity needs). So the identity keeps its own body - animations,
timelines, skills and upties exactly as the game expects - and only the pictures change:
every frame is pointed at the matching frame of the other character (idle->idle,
guard->guard, skill 2 frame 7 -> the frame at the same point of their skill 2), packed
into the body's own atlas pages. A head recipe can instead paint a new head over the
identity's own frames.
"""
import json
import math
import os
import re
import struct

from . import art as artfx
from . import spine
from .game import unitypy


class RepaintError(Exception):
    pass


KINDS = ("idle", "move", "gud", "miss", "dmg", "dead")
CLIP_KIND = {"Default": "idle", "Move": "move", "Guard": "gud", "Evade": "miss",
             "Damaged": "dmg", "Dead": "dead", "Retire": "dead"}


def bundle_assets(env):
    """{prefab name: (root GameObject id, {ids of every object it loads})}, from the
    AssetBundle's own preload table - so a bundle holding several characters gives each
    one only its own clips and sprites."""
    out = {}
    for o in env.objects:
        if o.type.name != "AssetBundle":
            continue
        t = o.read_typetree()
        pre = t["m_PreloadTable"]
        objs = o.assets_file.objects
        for name, info in t["m_Container"]:
            root = objs.get(info["asset"]["m_PathID"])
            if root is None or root.type.name != "GameObject":
                continue
            ids = {p["m_PathID"] for p in pre[info["preloadIndex"]:info["preloadIndex"] + info["preloadSize"]]
                   if p["m_FileID"] == 0}
            out[os.path.basename(name).rsplit(".", 1)[0]] = (root.path_id, ids | {root.path_id})
    return out


def prefab_objects(env, appearance, forms=False):
    """(ids of every object the body loads, [root ids]). With forms, the identity's other
    bodies in the bundle count too (e.g. "10215_..._CastOffAppearance")."""
    assets = bundle_assets(env)
    if appearance not in assets:
        raise RepaintError(f"{appearance}: body not found in its bundle")
    root, ids = assets[appearance]
    roots = [root]
    if forms:
        prefix = appearance.split("_", 1)[0] + "_"
        for name, (r, more) in assets.items():
            if name.startswith(prefix) and r != root:
                ids = ids | more
                roots.append(r)
    return ids, roots


def main_clips(env, root_id):
    """Ids of the clips in the body's own animator (its appearance's `_anim`, else an
    Animator on the root) - not those of a helper, summon or prop drawn alongside it.
    None when there is no such animator."""
    objs = {o.path_id: o for o in env.objects}
    root = objs.get(root_id)
    if root is None:
        return None
    anim = None
    comps = [objs.get(c["component"]["m_PathID"]) for c in root.read_typetree()["m_Component"]]
    for o in comps:
        if o is not None and o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            if "motionList" in t and t.get("_anim", {}).get("m_PathID"):
                anim = objs.get(t["_anim"]["m_PathID"])
    if anim is None:
        anim = next((o for o in comps if o is not None and o.type.name == "Animator"), None)
    if anim is None:
        return None
    ctrl = objs.get(anim.read_typetree()["m_Controller"]["m_PathID"])
    out = set()
    while ctrl is not None:
        t = ctrl.read_typetree()
        if ctrl.type.name == "AnimatorOverrideController":
            out |= {c["m_OverrideClip"]["m_PathID"] for c in t.get("m_Clips", []) if c["m_OverrideClip"]["m_PathID"]}
            ctrl = objs.get(t["m_Controller"]["m_PathID"])
        else:
            out |= {c["m_PathID"] for c in t.get("m_AnimationClips", [])}
            ctrl = None
    return out or None


def clip_frames(env, ids, wanted=None):
    """{clip name: [sprite reader, ...]} in play order, for clips the body loads."""
    out = {}
    for o in env.objects:
        if o.type.name != "AnimationClip" or o.path_id not in ids:
            continue
        t = o.read_typetree()
        name = t["m_Name"]
        if name.upper().startswith("FX") or (wanted and not wanted(name)):
            continue
        pm = t.get("m_ClipBindingConstant", {}).get("pptrCurveMapping", [])
        sp = [o.assets_file.objects.get(p["m_PathID"]) for p in pm if p.get("m_FileID") == 0]
        sp = [s for s in sp if s is not None and s.type.name == "Sprite"]
        if sp:
            out.setdefault(name, (sp, t["m_MuscleClip"]["m_StopTime"] - t["m_MuscleClip"]["m_StartTime"]))
    return out


def kind_of(sprite_name):
    n = sprite_name.lower()
    m = re.match(r"^skill(\d+)_(\d+)", n)
    if m:
        fx = "e" if re.search(r"_e(\b|_|$)|_eff", n) else ""
        return "S" + m.group(1) + fx, int(m.group(2))
    for k in KINDS:
        if n == k or n.startswith(k):
            return k, 0
    for word, k in CLIP_KIND.items():                    # some bodies name frames "Default", "Damaged"
        if n.startswith(word.lower()):
            return k, 0
    return None, 0


def split_body(sprites):
    """(body frames, effect frames); unnamed effect sprites ("1", "2"...) are dropped."""
    kinds = [(x, kind_of(x.read().m_Name)[0]) for x in sprites]
    fx = lambda k: k.startswith("S") and k.endswith("e")  # noqa: E731  ("idle" ends in e too)
    return [x for x, k in kinds if k and not fx(k)], [x for x, k in kinds if k and fx(k)]


def fx_like(name):
    n = name.lower()
    return n.isdigit() or n.startswith(("fx", "eff")) or "_eff" in n or n.endswith("_e")


def motion_word(clip_name):
    """The motion a clip plays, from the last word of its name: "Meursault_LeiHeng_Reload"
    and "X_Reload_Off" -> "reload"."""
    words = [w for w in re.split(r"[_\s]+", clip_name.lower()) if w and w not in ("off", "on") and not w.isdigit()]
    return words[-1] if words else ""


def special_table(env, ids, prefix=None):
    """{motion word: [sprites]} for the source's clips that aren't a standard motion
    (reloads, parries, duels...), in play order, effect sprites left out."""
    out = {}
    for name, (sprites, _) in clip_frames(env, ids, (lambda n: n.startswith(prefix)) if prefix else None).items():
        body = [x for x in sprites if not fx_like(x.read().m_Name)]
        if body:
            out.setdefault(motion_word(name), body)
    return out


def source_table(env, ids, prefix=None, overrides=None, main=None):
    """Source frames by kind: {"idle": [sprite...], "S1": [...], ...}, plus
    "idle_time": the idle clip's length in seconds. main: the body's own animator
    clips - a helper or summon drawn alongside it never lends its frames."""
    clips = clip_frames(env, main or ids, (lambda n: n.startswith(prefix)) if prefix else None)
    table = {}

    def put(kind, sprites, length):
        body, fx = split_body(sprites)
        if not body:
            return
        table[kind] = body
        if kind == "idle":
            table["idle_time"] = length
        if kind.startswith("S") and fx:
            table[kind + "e"] = fx

    for name, (sprites, length) in clips.items():
        tail = (name[len(prefix):] if prefix else name).lstrip("_")
        kind = None
        for word, k in CLIP_KIND.items():
            if tail.endswith(word):
                kind = k
        m = re.search(r"(?:^|_)S?(\d)$", tail)
        if kind is None and m:
            kind = "S" + m.group(1)
        if kind and kind not in table:
            put(kind, sprites, length)
    every = clip_frames(env, ids) if overrides else {}
    for k, clip in (overrides or {}).items():
        if clip not in every:
            raise RepaintError(f"clip {clip} is not in the source body")
        table.pop(k, None)
        put(k, *every[clip])
    skills = sorted(k for k in table if re.fullmatch(r"S\d", k))
    for n in (1, 2, 3):                      # a source with fewer skills reuses the ones it has
        if f"S{n}" not in table and skills:
            table[f"S{n}"] = table[skills[(n - 1) % len(skills)]]
    return table


def lunge(src, frames_per_skill=6):
    """The source's most dash-like drawing, for an identity's run up to the enemy when the
    source has none (or only a standing one): of the opening frames of its skills, the
    one whose outline is widest for its height - a body thrown forward."""
    best, score = None, 0.0
    seen = set()
    for k in ("S1", "S2", "S3"):
        for sp in src.get(k, [])[:frames_per_skill]:
            if sp.path_id in seen:
                continue
            seen.add(sp.path_id)
            try:
                box = sp.read().image.getchannel("A").getbbox()
            except Exception:
                continue
            if box and box[3] > box[1]:
                ratio = (box[2] - box[0]) / (box[3] - box[1])
                if ratio > score:
                    best, score = sp, ratio
    return best


def render(sprite_reader, mirror=False):
    """(image, sprite[, pivot]). mirror: flip left-right, pivot with it - enemies face
    the other way from the sinners."""
    s = sprite_reader.read()
    img = s.image.convert("RGBA")
    if mirror:
        from PIL import Image
        return img.transpose(Image.FLIP_LEFT_RIGHT), s, (1 - s.m_Pivot.x, s.m_Pivot.y)
    return img, s


def quad(w, h, px, py, ppu):
    """Positions of a full-rect quad around the pivot, and its 6 u16 indices."""
    x0, y0 = -px * w / ppu, -py * h / ppu
    x1, y1 = x0 + w / ppu, y0 + h / ppu
    verts = struct.pack("<12f", x0, y0, 0, x1, y0, 0, x0, y1, 0, x1, y1, 0)
    idx = struct.pack("<6H", 0, 2, 1, 1, 2, 3)
    return verts, idx, (x0, y0, x1, y1)


def head_art(env, r):
    """Build the head picture from textures in the body's own bundle (no art ships with
    these tools): r = {texture, mask?, crop [l,t,r,b], tilt degrees, mirror: bool}."""
    from PIL import Image, ImageDraw
    if r.get("image_path"):                              # the player's own picture (limbus-skins art)
        img = Image.open(r["image_path"]).convert("RGBA")
        return img.crop(img.getbbox())
    tex = {o.read().m_Name: o for o in env.objects if o.type.name == "Texture2D"}
    if r["texture"] not in tex:
        raise RepaintError(f"head art {r['texture']} is not in this body's bundle")
    img = tex[r["texture"]].read().image.convert("RGBA")
    if r.get("mask"):
        img.putalpha(tex[r["mask"]].read().image.convert("L").resize(img.size))
    w = img.crop(tuple(r["crop"]))
    w = w.crop(w.getbbox())
    if r.get("tilt"):
        w = w.rotate(r["tilt"], resample=Image.BICUBIC, expand=True)
        w = w.crop(w.getbbox())
    if not r.get("mirror"):
        return w
    out = Image.new("RGBA", (w.width * 2 - 6, w.height), (0, 0, 0, 0))
    out.alpha_composite(w.transpose(Image.FLIP_LEFT_RIGHT), (0, 0))
    out.alpha_composite(w, (w.width - 6, 0))
    cx = out.width // 2                                    # a thin body over the seam
    ImageDraw.Draw(out).ellipse([cx - 5, int(out.height * 0.18), cx + 5, int(out.height * 0.92)], fill=(12, 12, 12, 255))
    return out


def paint_head(frame, spec, art, width=1.9, glow=None):
    """Erase the old head inside a circle (hair, skin and the soft edge - white gloves,
    guns and collars stay) and put the art there. spec = [cx, cy, diameter, rotation]
    in frame pixels, y down."""
    from PIL import Image
    cx, cy, dia = spec[:3]
    rot = spec[3] if len(spec) > 3 else 0
    im = frame.copy()
    px = im.load()
    W, H = im.size
    r = 0.52 * dia
    ey = cy - 0.02 * dia
    for y in range(max(0, int(ey - r)), min(H, int(ey + r) + 1)):
        for x in range(max(0, int(cx - r)), min(W, int(cx + r) + 1)):
            if (x - cx) ** 2 + (y - ey) ** 2 > r * r:
                continue
            rr, g, b, al = px[x, y]
            if al and (max(rr, g, b) < 150 or (rr > 170 and rr - b > 25 and g > 120) or al < 250):
                px[x, y] = (0, 0, 0, 0)
    k = width * dia / art.width
    a = art.resize((max(1, int(art.width * k)), max(1, int(art.height * k))), Image.LANCZOS)
    if rot:
        a = a.rotate(rot, resample=Image.BICUBIC, expand=True)
    if glow:                                             # a soft halo behind the head
        a, _ = artfx.glow(a, glow["radius"] * a.width, tuple(glow["color"]),
                          (glow["low"] + glow["high"]) / 2)
    ax, ay = int(cx - a.width / 2), int(cy - a.height / 2 - 0.05 * dia)
    # grow the frame where the new head pokes out, rather than clipping it
    left, top = max(0, -ax), max(0, -ay)
    right, bottom = max(0, ax + a.width - W), max(0, ay + a.height - H)
    if left or top or right or bottom:
        big = Image.new("RGBA", (W + left + right, H + top + bottom), (0, 0, 0, 0))
        big.alpha_composite(im, (left, top))
        im = big
    im.alpha_composite(a, (ax + left, ay + top))
    return im, (left, top)


def vertex_bytes(positions, channels, name="sprite"):
    """The quad's 4 positions laid out like the sprite's own vertex data: positions in
    stream 0 and, when the sprite has one, a second stream of texture coords (left zero:
    Unity derives atlas UVs from uvTransform). A short buffer makes Unity drop the frame."""
    if any(c["dimension"] and c["stream"] == 0 and c["offset"] for c in channels):
        raise RepaintError(f"{name}: interleaved vertex layout not handled")
    extra = sum(c["dimension"] * 4 for c in channels if c["dimension"] and c["stream"] == 1)
    return positions + bytes(4 * extra)


def pack(sizes, pages):
    """Shelf-pack (w, h) boxes into pages of the given (w, h) sizes, in order.
    Returns [(page index, x, y)] (y measured from the bottom, as Unity does) or None."""
    out, pg, x, y, shelf = [], 0, 0, 0, 0
    for w, h in sizes:
        while True:
            if pg >= len(pages):
                return None
            pw, ph = pages[pg]
            if x + w > pw:
                x, y, shelf = 0, y + shelf + 2, 0
            if w <= pw and y + h <= ph:
                break
            pg, x, y, shelf = pg + 1, 0, 0, 0
        out.append((pg, x, y))
        x += w + 2
        shelf = max(shelf, h)
    return out




def _spine_pages(sp):
    pages = spine.parse_atlas(spine.text_of(sp["atlas"]))
    for p in pages:
        tex = sp["textures"].get(p["name"].rsplit(".", 1)[0])
        if tex is not None:
            return pages, p, tex
    raise spine.SpineError("no texture for its atlas")


def _spine_frames(sp, src, rendered, mirror=False):
    """Swap the idle skeleton's parts for the source's idle frames."""
    from PIL import Image
    UnityPy = unitypy()
    pages, page, tex = _spine_pages(sp)
    pma = dict(page["header"]).get("pma", "false") == "true"
    seq = src["idle"]
    imgs, atts = {}, []
    for s in seq:
        if s.path_id not in imgs:
            imgs[s.path_id] = rendered[s.path_id] if s.path_id in rendered else render(s, mirror)
    ids = list(imgs)
    scale = 1.0
    while True:
        sizes = [(max(1, int(imgs[i][0].width * scale)), max(1, int(imgs[i][0].height * scale))) for i in ids]
        placed = pack(sizes, [(2048, 2048)])
        if placed:
            break
        scale *= 0.9
    W = max(x + w for (_, x, _), (w, _) in zip(placed, sizes))
    H = max(y + h for (_, _, y), (_, h) in zip(placed, sizes))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    regions, names = [], {}
    for n, (i, (_, x, y), (w, h)) in enumerate(zip(ids, placed, sizes)):
        img, sd = imgs[i][:2]
        canvas.alpha_composite(img.resize((w, h), Image.LANCZOS) if scale != 1 else img, (x, H - y - h))
        names[i] = f"lsk{n}"
        regions.append({"name": names[i], "fields": [("bounds", f"{x},{H - y - h},{w},{h}")]})
    for s in seq:
        img, sd = imgs[s.path_id][:2]
        px, py = imgs[s.path_id][2] if len(imgs[s.path_id]) > 2 else (sd.m_Pivot.x, sd.m_Pivot.y)
        u = float(sd.m_PixelsToUnits)
        x0, y0 = -px * img.width / u, -py * img.height / u
        atts.append((names[s.path_id], spine.region_attachment(sp["to_skel"], x0, y0, x0 + img.width / u, y0 + img.height / u)))
    uniq = {}
    for n, a in atts:
        uniq.setdefault(n, a)
    frames = [(n, uniq[n]) for n, _ in atts]
    doc = json.loads(spine.text_of(sp["skeleton"]))
    t = src.get("idle_time") or 0
    new = spine.frame_swap(doc, frames, t if t > 0.05 else 0.1 * len(frames))
    page["header"] = [(k, f"{W},{H}" if k == "size" else v) for k, v in page["header"]]
    page["regions"] = regions
    spine.set_text(sp["skeleton"], spine.dumps(new))
    spine.set_text(sp["atlas"], spine.format_atlas([page]))
    d = tex.read()
    d.set_image(spine.premultiply(canvas) if pma else canvas, target_format=UnityPy.enums.TextureFormat.RGBA32)
    d.save()
    return f"idle {sp['name']}: {len(frames)} frame(s) of the new body"


def _pages_pma(rig, page):
    """Whether a rig's textures hold premultiplied alpha: its material's word, else its atlas's."""
    if rig.get("pma") is not None:
        return rig["pma"]
    return dict(page["header"]).get("pma", "false").strip() == "true"


def _shift_region(region, dy):
    fields = []
    for k, v in region["fields"]:
        if k in ("bounds", "xy"):
            nums = [int(x) for x in v.split(",")]
            nums[1] += dy
            v = ",".join(str(x) for x in nums)
        fields.append((k, v))
    return {"name": region["name"], "fields": fields}


def _borrow_pages(sp, rig):
    """The source rig's atlas pages, renamed onto the identity's own textures (pages past
    the identity's count stacked into its last one). [(page, texture reader, image)]"""
    from PIL import Image
    tpages = spine.parse_atlas(spine.text_of(sp["atlas"]))
    spages = spine.parse_atlas(spine.text_of(rig["atlas"]))
    ttex = [sp["textures"].get(p["name"].rsplit(".", 1)[0]) for p in tpages]
    if not tpages or not spages or None in ttex:
        raise spine.SpineError("no texture for its atlas")
    imgs = []
    for p in spages:
        t = rig["textures"].get(p["name"].rsplit(".", 1)[0])
        if t is None:
            raise spine.SpineError(f"no texture for {p['name']}")
        imgs.append(t.read().image.convert("RGBA"))
    want, have = _pages_pma(sp, tpages[0]), _pages_pma(rig, spages[0])
    groups = [[i] for i in range(min(len(spages), len(tpages)))]
    groups[-1] += list(range(len(groups), len(spages)))
    out = []
    for g, grp in enumerate(groups):
        if len(grp) == 1:
            img, header, regions = imgs[grp[0]], spages[grp[0]]["header"], spages[grp[0]]["regions"]
        else:
            sizes = [spine.page_size(spages[i]) for i in grp]
            W, H = max(w for w, _ in sizes), sum(h for _, h in sizes) + 2 * (len(grp) - 1)
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            regions, y = [], 0
            for i, (w, h) in zip(grp, sizes):
                img.paste(imgs[i].resize((w, h), Image.LANCZOS), (0, y))
                regions += [_shift_region(r, y) for r in spages[i]["regions"]]
                y += h + 2
            header = [(k, f"{W},{H}" if k == "size" else v) for k, v in spages[grp[0]]["header"]]
        if want != have:
            img = spine.premultiply(img) if want else spine.unpremultiply(img)
        header = [(k, v) for k, v in header if k != "pma"] + [("pma", "true" if want else "false")]
        out.append(({"name": tpages[g]["name"], "header": header, "regions": regions}, ttex[g], img))
    return out


def _spine_borrow(sp, rig, mirror=False):
    """Swap the idle skeleton's parts for the source character's own idle rig - its bones,
    parts and animation - turned to face the sinners' way and standing where its frames
    stand. Nothing is written unless the whole rig fits."""
    UnityPy = unitypy()
    flip = (-1, 0, 0, 1, 0, 0) if mirror else (1, 0, 0, 1, 0, 0)
    a, b, c, d, dx, dy = spine.mul(sp["to_skel"], spine.mul(flip, spine.inv(rig["to_skel"])))
    if max(abs(b), abs(c), abs(abs(a) - 1), abs(d - 1)) > 1e-3:
        raise spine.SpineError("the two rigs are not drawn at the same scale")
    src = json.loads(spine.text_of(rig["skeleton"]))
    anims = src.get("animations") or {}
    anim = rig["animation"] if rig.get("animation") in anims else next(iter(anims), None)
    if anim is None:
        raise spine.SpineError("the rig has no animation")
    if a < 0:
        spine.bake_ik(src, anim, rig.get("skin"))
        spine.mirror_x(src)
    pace = sp.get("time_scale", 1.0) / rig.get("time_scale", 1.0)
    if abs(pace - 1) > 1e-3:                             # as fast as in its own fights
        spine.scale_time(anims[anim], pace)
    new = spine.transplant(json.loads(spine.text_of(sp["skeleton"])), src, dx, dy, anim,
                           skin=rig.get("skin"), target_skin=sp.get("skin"), names=[sp.get("animation")])
    pages = _borrow_pages(sp, rig)
    spine.check(new, {r["name"] for page, _, _ in pages for r in page["regions"]})
    spine.set_text(sp["skeleton"], spine.dumps(new))
    spine.set_text(sp["atlas"], spine.format_atlas([page for page, _, _ in pages]))
    for _, tex, img in pages:
        t = tex.read()
        t.set_image(img, target_format=UnityPy.enums.TextureFormat.RGBA32)
        t.save()
    return f"idle {sp['name']}: the character's own idle rig ({len(src['bones'])} bones, animation {anim})"


def _append_regions(sp, pages, page, tex, parts, dens):
    """Add pictures [(region name, image, (w, h) in skeleton units)] to an idle's atlas, in
    one strip below the page's own pictures, at `dens` atlas pixels per skeleton unit."""
    import math as _m
    from PIL import Image
    UnityPy = unitypy()
    pw, ph = spine.page_size(page)
    img = tex.read().image.convert("RGBA")
    tw, th = img.size
    boxes, x = [], 1
    for name, im, (uw, uh) in parts:
        aw, ah = max(2, int(uw * dens)), max(2, int(uh * dens))
        boxes.append((name, im, x, aw, ah))
        x += aw + 2
    if x > pw:                                           # too wide for the page: shrink the strip
        k = (pw - 2 * len(parts)) / (x - 2 * len(parts))
        boxes, x = [], 1
        for name, im, (uw, uh) in parts:
            aw, ah = max(2, int(uw * dens * k)), max(2, int(uh * dens * k))
            boxes.append((name, im, x, aw, ah))
            x += aw + 2
    tall = max(ah for *_, ah in boxes)
    step = ph // _m.gcd(ph, th)
    add = -(-(tall + 2) // step) * step
    big = Image.new("RGBA", (tw, th + th * add // ph), (0, 0, 0, 0))
    big.paste(img, (0, 0))
    pma = dict(page["header"]).get("pma", "false") == "true"
    for name, im, bx, aw, ah in boxes:
        pix = im.resize((max(1, round(aw * tw / pw)), max(1, round(ah * th / ph))), Image.LANCZOS)
        big.paste(spine.premultiply(pix) if pma else pix, (round(bx * tw / pw), round((ph + 1) * th / ph)))
        page["regions"].append({"name": name, "fields": [("bounds", f"{bx},{ph + 1},{aw},{ah}")]})
    page["header"] = [(k, f"{pw},{ph + add}" if k == "size" else v) for k, v in page["header"]]
    spine.set_text(sp["atlas"], spine.format_atlas(pages))
    d = tex.read()
    d.set_image(big, target_format=UnityPy.enums.TextureFormat.RGBA32)
    d.save()


def _spine_aura(sp, sprites, spec, ref):
    """Give an idle skeleton the aura as parts of its own: a bone per sprite wandering through
    the aura's placements over each animation (flies flapping between their frames). ref =
    the body's standing height in sprite units."""
    from . import aura as auramod
    pages, page, tex = _spine_pages(sp)
    doc = json.loads(spine.text_of(sp["skeleton"]))
    a, b, c, d, _, _ = sp["to_skel"]
    lx = math.hypot(a, c)
    mirrored = a * d - b * c < 0
    steps = 8
    keys = [auramod._placements(spec, len(sprites), ref, n) for n in range(steps)]
    nominal = float(spec.get("size", 0.12)) * ref * lx             # a sprite's longest side, skeleton units
    regions = [f"{spine.PREFIX}aura{j}" for j in range(len(sprites))]
    parts = []
    for name, im in zip(regions, sprites):
        k = nominal / max(im.width, im.height)
        parts.append((name, im, (im.width * k, im.height * k)))
    root = doc["bones"][0]["name"]
    default = next((x for x in spine.skins_list(doc) if x["name"] == "default"), None)
    if default is None:
        default = {"name": "default", "attachments": {}}
        doc["skins"].insert(0, default)
    flap = spec.get("style", "swarm") == "swarm" and len(sprites) > 1
    behind = []
    for i, (j, _, _, size, deg, flip, alpha, ahead) in enumerate(keys[0]):
        bone = slot = f"{spine.PREFIX}aura_{i}"
        doc["bones"].append({"name": bone, "parent": root})
        new = {"name": slot, "bone": bone, "attachment": regions[j], "color": "ffffff%02x" % round(255 * alpha)}
        if ahead:
            doc["slots"].append(new)
        else:
            behind.append(new)
        k = size * lx / nominal
        atts = {}
        for jj in ({j} | (set(range(len(sprites))) if flap else set())):
            w, h = parts[jj][2]
            att = {"path": regions[jj], "width": round(w * k, 3), "height": round(h * k, 3)}
            if deg:
                att["rotation"] = round(-deg if mirrored else deg, 3)
            if flip != mirrored:
                att["scaleX"] = -1
            atts[regions[jj]] = att
        default["attachments"][slot] = atts
    doc["slots"][0:0] = behind
    anims = doc.setdefault("animations", {}) or {"animation": {}}
    doc["animations"] = anims
    for an in anims.values():
        L = spine.anim_length(an) or 2.0
        bones, slots = an.setdefault("bones", {}), an.setdefault("slots", {})
        for i in range(len(keys[0])):
            name = f"{spine.PREFIX}aura_{i}"
            path = []
            for n in range(steps + 1):
                x, y = keys[n % steps][i][1:3]
                X, Y = spine._apply(sp["to_skel"], x, y)
                path.append({"time": round(L * n / steps, 4), "x": round(X, 3), "y": round(Y, 3)})
            path[0].pop("time")
            bones[name] = {"translate": path}
            if flap:
                beat, t, seq = 0.07, 0.0, []
                while t < L:
                    seq.append({"time": round(t, 4), "name": regions[(len(seq) + i) % len(sprites)]})
                    t += beat
                seq[0].pop("time")
                slots[name] = {"attachment": seq}
    _append_regions(sp, pages, page, tex, parts, 128 / max(nominal, 1e-6))
    spine.set_text(sp["skeleton"], spine.dumps(doc))         # last: it names the regions just added
    return f"aura in the idle ({len(keys[0])} parts)"


def _spine_head(sp, art, spec, effects=None):
    """Swap one part of the idle skeleton (the head) for the head art; with effects, the
    head also flutters, glows and gives off a few motes."""
    import math as _m
    from PIL import Image
    UnityPy = unitypy()
    pages, _, _ = _spine_pages(sp)
    doc = json.loads(spine.text_of(sp["skeleton"]))
    slot = next((x for x in doc["slots"] if x["name"] == spec["slot"]), None)
    if slot is None or not slot.get("attachment"):
        raise spine.SpineError(f"no slot {spec['slot']}")
    skin = sp["skin"]
    att = None
    for sk in spine.skins_list(doc):
        if sk["name"] in (skin, "default") and slot["attachment"] in sk["attachments"].get(slot["name"], {}):
            att = sk["attachments"][slot["name"]][slot["attachment"]]
            if sk["name"] == skin:
                break
    if att is None or att.get("type", "region") != "region":
        raise spine.SpineError(f"{spec['slot']} is not a plain picture")
    rname = att.get("path", slot["attachment"])
    page = next(p for p in pages if any(r["name"] == rname for r in p["regions"]))
    tex = sp["textures"][page["name"].rsplit(".", 1)[0]]
    reg = next(r for r in page["regions"] if r["name"] == rname)
    dens = spine.region_geom(reg)[2] / att["width"]          # atlas px per skeleton unit
    w = att["width"] * spec.get("width", 1.3)
    h = w * art.height / art.width
    r = _m.radians(att.get("rotation", 0.0))
    dx, dy = spec.get("dx", 0) * att["width"], spec.get("dy", 0) * att["height"]
    att["x"] = round(att.get("x", 0) + dx * _m.cos(r) - dy * _m.sin(r), 3)
    att["y"] = round(att.get("y", 0) + dx * _m.sin(r) + dy * _m.cos(r), 3)
    att["width"], att["height"] = round(w, 3), round(h, 3)
    att["path"] = "lsk_head"

    # the pictures to add: (region name, image, size in skeleton units)
    parts = [("lsk_head", art, (w, h))]
    fx = dict(effects or {})
    if fx.get("glow"):
        g = fx["glow"]
        halo, pad = artfx.halo(art, g["radius"] * art.width, tuple(g["color"]), 1.0)
        k = w / art.width
        fx["glow_size"] = (halo.width * k, halo.height * k)
        parts.append((spine.GLOW, halo, fx["glow_size"]))
    if fx.get("motes"):
        fx["mote_size"] = w * fx.get("mote", 0.07)
        parts.append((spine.MOTE, artfx.mote(48, tuple((fx.get("glow") or {}).get("color", (214, 200, 255)))),
                      (fx["mote_size"], fx["mote_size"])))
    if effects:
        spine.head_effects(doc, slot["name"], (w, h), att.get("rotation", 0.0), fx,
                           skin if skin else "default")

    spine.set_text(sp["skeleton"], spine.dumps(doc))
    _append_regions(sp, pages, page, tex, parts, dens)
    return f"idle {sp['name']}: head swapped" + (" (glow, flutter, motes)" if effects else "")


def _bake_aura(spec, locate, src, rendered):
    """Draw the aura into every rendered frame of the source's body (not its effect sprites).
    Returns (aura sprites, frames done)."""
    from . import aura as auramod
    try:
        sprites = auramod.textures(spec, locate)
    except auramod.AuraError as e:
        raise RepaintError(str(e))
    order = []
    for kind, frames in src.items():
        if isinstance(frames, list) and not (kind.startswith("S") and kind.endswith("e")):
            order += [f.path_id for f in frames if f.path_id in rendered and f.path_id not in order]

    def pivot(i):
        return rendered[i][2] if len(rendered[i]) > 2 else (rendered[i][1].m_Pivot.x, rendered[i][1].m_Pivot.y)

    ref = 0.0                                            # how tall the source stands, from its idle
    for f in src.get("idle", []):
        if f.path_id in rendered:
            img, sd = rendered[f.path_id][:2]
            box = img.getchannel("A").getbbox()
            if box:
                ref = max(ref, ((img.height - box[1]) - pivot(f.path_id)[1] * img.height) / float(sd.m_PixelsToUnits))
    if not ref:
        raise RepaintError("the aura needs the source's idle to measure it by")
    for n, i in enumerate(order):
        img, sd = rendered[i][:2]
        new, pv = auramod.bake(img, pivot(i), sprites, spec, ref * float(sd.m_PixelsToUnits), n)
        rendered[i] = (new, sd, pv)
    return sprites, len(order)


def standing_height(env, ids, fallback=2.6):
    """How tall the body stands, in units: from its feet (the sprite's pivot) to the top of its
    idle frames - so a weapon hanging below the feet doesn't count."""
    best = 0.0
    for o in env.objects:
        if o.type.name != "Sprite" or o.path_id not in ids:
            continue
        s = o.read()
        if kind_of(s.m_Name)[0] != "idle":
            continue
        try:
            img = s.image
            box = img.getchannel("A").getbbox()
        except Exception:
            continue
        if box:
            above = (img.height - box[1]) - s.m_Pivot.y * img.height      # pixels from the feet up
            best = max(best, above / float(s.m_PixelsToUnits))
    return best or fallback


def _load_source(source, locate, clips, mapping, dash, notes):
    """(drawing table, special-motion table, idle rigs) of the character `source`."""
    UnityPy = unitypy()
    senv = UnityPy.load(locate(source))
    sobjs, sroots = prefab_objects(senv, source)
    rigs = spine.body_spines(senv, sroots[0])                # the character's own idle rig, if it has one
    smain = main_clips(senv, sroots[0])
    src = source_table(senv, sobjs, clips, mapping or {}, main=smain)
    special = special_table(senv, smain or sobjs, clips)
    if "idle" not in src or "S1" not in src:
        raise RepaintError(f"{source} has no idle or skill frames (found {sorted(src)}) - set clips/map")
    if "move" not in src or dash == "lunge":             # no run-up drawing, or a standing one
        pick = lunge(src)
        if pick is not None:
            src["move"] = [pick]
            notes.append(f"{source}: dash uses a lunge from its skills")
    return src, special, rigs


def repaint(target, locate, source=None, clips=None, mapping=None, head=None, mirror=True, dash=None, forms=None,
            lor=None, aura=None):
    """Repaint body `target` (from `source`'s drawings, or its own with a `head` recipe).
    forms = {another body of the identity: {"source", "clips", "map"}} draws that body
    (e.g. Wild Hunt mounted) from its own source. locate(body name) -> path of the cached
    bundle holding it. Returns (bundle bytes, notes). The bytes still need bundle.forge()
    to carry the stock CRC. aura: see aura.py."""
    notes = []
    UnityPy = unitypy()
    from PIL import Image
    if lor is not None:                                  # a Library of Ruina character: after the target
        src, special, rigs = {}, {}, []                   # is loaded, to stand as tall as it does
        source = "lor:" + lor["prefab"]
    elif source is None:
        src, special, rigs = {}, {}, []
    else:
        src, special, rigs = _load_source(source, locate, clips, mapping, dash, notes)

    tpath = locate(target)
    tenv = UnityPy.load(tpath)
    tobjs, troots = prefab_objects(tenv, target, forms=True)
    tclips = clip_frames(tenv, tobjs)
    stands = standing_height(tenv, tobjs)                # before its sprites are redrawn
    if lor is not None:
        from . import lor as lormod
        try:
            src = lormod.source_table(lor, height=stands)
        except lormod.LorError as e:
            raise RepaintError(str(e))

    # sprites only another form of the body draws take that form's source
    by_sprite, by_root = {}, {}
    if forms and source is not None:
        base = prefab_objects(tenv, target)[0]
        assets = bundle_assets(tenv)
        for app, f in forms.items():
            if app not in assets:
                raise RepaintError(f"{app}: no such body next to {target}")
            table = _load_source(f["source"], locate, f.get("clips"), f.get("map"), dash, notes)
            by_root[assets[app][0]] = table
            for i in assets[app][1] - base:
                by_sprite[i] = table

    # target sprites -> (kind, position along that kind's animation)
    order = {}
    for name, (sprites, _) in tclips.items():
        for i, s in enumerate(sprites):
            k, _ = kind_of(s.read().m_Name)
            if k and k.startswith("S"):
                seq = order.setdefault(k, [])
                if s.path_id not in seq:
                    seq.append(s.path_id)
    targets = [o for o in tenv.objects if o.type.name == "Sprite" and o.path_id in tobjs]
    plan = []                                            # (target reader, source reader)
    base_src = src
    borrowed = set()                                     # motions the source has no frames for
    for o in targets:
        k, _ = kind_of(o.read().m_Name)
        if not k:
            continue
        if source is None:
            if o.read_typetree()["m_SpriteAtlas"]["m_FileID"] == 0:
                plan.append((o, o))
            continue
        src = by_sprite.get(o.path_id, (base_src,))[0]
        if k.startswith("S") and k.endswith("e") and k not in src:
            continue                                     # keep the identity's own effect art
        pool = src.get(k)
        if pool is None and k.startswith("S") and not k.endswith("e"):
            n = int(k[1:])                               # a skill the source lacks: reuse one of its first three
            pool = src.get(f"S{(n - 1) % 3 + 1}")
        if pool is None:
            stand_in = "dmg" if k == "dead" and "dmg" in src else "idle"
            borrowed.add(f"{k}->{stand_in}")
            pool = src[stand_in]
        if k.startswith("S") and k in order and o.path_id in order[k]:
            seq = order[k]
            f = seq.index(o.path_id) / max(1, len(seq) - 1)
        else:
            f = 0.0
        plan.append((o, pool[round(f * (len(pool) - 1))]))
    if not plan:
        raise RepaintError(f"{target}: no frame drawings found (a Spine body?)")
    # frames of the body's own motions that aren't standard (a reload, a parry...):
    # the source's motion of the same name, else its first skill, by position
    if source is not None:
        tmain = set()
        for r_ in troots:
            tmain |= main_clips(tenv, r_) or set()
        done = {o.path_id for o, _ in plan}
        tsp = {o.path_id: o for o in targets}
        for name, (sprites, _) in clip_frames(tenv, tmain).items():
            pool = special.get(motion_word(name)) or src["S1"]
            n = len(sprites)
            for i, sp_ in enumerate(sprites):
                if sp_.path_id in done or sp_.path_id not in tsp or kind_of(sp_.read().m_Name)[0] \
                        or fx_like(sp_.read().m_Name):
                    continue
                if sp_.path_id in by_sprite:
                    fsrc, fspecial, _ = by_sprite[sp_.path_id]
                    pool = fspecial.get(motion_word(name)) or fsrc["S1"]
                else:
                    pool = special.get(motion_word(name)) or base_src["S1"]
                plan.append((tsp[sp_.path_id], pool[round(i / max(1, n - 1) * (len(pool) - 1))]))
                done.add(sp_.path_id)
    # every other sprite on the body's atlas keeps its own picture, repacked with the rest
    done = {o.path_id for o, _ in plan}
    for o in targets:
        if o.path_id in done:
            continue
        t = o.read_typetree()
        if t["m_SpriteAtlas"]["m_PathID"] and t["m_SpriteAtlas"]["m_FileID"] == 0:
            plan.append((o, o))

    # render each distinct source frame once
    rendered = {}
    keep = []
    for o, s in plan:
        if s.path_id not in rendered:
            try:
                rendered[s.path_id] = render(s, mirror) if s.path_id != o.path_id else render(s)
            except Exception:
                rendered[s.path_id] = None               # art lives in another bundle
        if rendered[s.path_id] is not None:
            keep.append((o, s))
    plan = keep
    rendered = {k: v for k, v in rendered.items() if v is not None}
    if head:
        recipe = head
        art = head_art(tenv, recipe["art"])
        painted = 0
        for o, s in plan:
            spec = recipe["frames"].get(o.read().m_Name)
            if spec and s.path_id == o.path_id and s.path_id in rendered:
                img, sd = rendered[s.path_id][:2]
                new, (left, top) = paint_head(img, spec, art, recipe.get("width", 1.9),
                                              (recipe.get("effects") or {}).get("glow"))
                # the canvas may have grown left/up; keep the pivot on the same body pixel
                px = (sd.m_Pivot.x * img.width + left) / new.width
                py = (sd.m_Pivot.y * img.height + (new.height - img.height - top)) / new.height
                rendered[s.path_id] = (new, sd, (px, py))
                painted += 1
        notes.append(f"head painted on {painted} frames")
    sprites = None
    if aura and source is not None:
        sprites, n = _bake_aura(aura, locate, base_src, rendered)
        notes.append(f"aura on {n} frames")

    # the body's own atlas pages get the new art
    tex = {}
    for o in targets:
        t = o.read_typetree()
        for ref in (t["m_RD"]["texture"],):
            if ref["m_PathID"]:
                tex[ref["m_PathID"]] = 1
    atlas_objs = [o for o in tenv.objects if o.type.name == "SpriteAtlas"]
    amaps = {o.path_id: o.read_typetree() for o in atlas_objs}
    for t in amaps.values():
        for _, v in t["m_RenderDataMap"]:
            if v["texture"]["m_PathID"]:
                tex[v["texture"]["m_PathID"]] = 1
    # only pages whose every sprite gets repainted can be wiped (FX and portraits stay put)
    planned = {o.path_id for o, _ in plan}
    users = {}
    for o in tenv.objects:                               # every sprite in the bundle, not just ours
        if o.type.name != "Sprite":
            continue
        t = o.read_typetree()
        refs = {t["m_RD"]["texture"]["m_PathID"]}
        key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
        for at in amaps.values():
            for k, v in at["m_RenderDataMap"]:
                if (tuple(k[0].values()), k[1]) == key:
                    refs.add(v["texture"]["m_PathID"])
        for r in refs - {0}:
            users.setdefault(r, set()).add(o.path_id)
    for o in tenv.objects:                               # a material drawing straight from a page
        if o.type.name == "Material":
            for _, v in o.read_typetree()["m_SavedProperties"]["m_TexEnvs"]:
                if v["m_Texture"]["m_FileID"] == 0 and v["m_Texture"]["m_PathID"]:
                    users.setdefault(v["m_Texture"]["m_PathID"], set()).add(-1)
    pages = [o for o in tenv.objects if o.type.name == "Texture2D" and o.path_id in users
             and users[o.path_id] <= planned and o.read().m_Name.startswith("sactx-")]
    if not pages:
        raise RepaintError(f"{target}: no atlas pages it could rewrite (a Spine body?)")
    psize = [(p.read().m_Width, p.read().m_Height) for p in pages]
    scale = 1.0
    ids = sorted(rendered, key=lambda i: -rendered[i][0].height)   # tall first packs tighter
    while True:
        sizes = [(max(1, int(rendered[i][0].width * scale)), max(1, int(rendered[i][0].height * scale))) for i in ids]
        placed = pack(sizes, psize)
        if placed:
            break
        scale *= 0.9
        if scale < 0.3:
            raise RepaintError(f"{target}: the new drawings do not fit the body's atlas pages")
    canvases = [Image.new("RGBA", sz, (0, 0, 0, 0)) for sz in psize]
    where = {}
    for i, (pg, x, y), (w, h) in zip(ids, placed, sizes):
        img = rendered[i][0].resize((w, h), Image.LANCZOS) if scale != 1.0 else rendered[i][0]
        # Unity textures are bottom-up; PIL is top-down
        canvases[pg].alpha_composite(img, (x, psize[pg][1] - y - h))
        where[i] = (pg, x, y, w, h)

    # rewrite the target sprites + atlas entries
    page_ref = {i: {"m_FileID": 0, "m_PathID": pages[i].path_id} for i in range(len(pages))}
    by_key = {}                                          # a frame can be packed in more than one atlas
    for aid, t in amaps.items():
        for n, (k, v) in enumerate(t["m_RenderDataMap"]):
            by_key.setdefault((tuple(k[0].values()), k[1]), []).append((aid, n))
    for o, s in plan:
        if s.path_id not in where:
            continue
        img, sd = rendered[s.path_id][:2]
        pg, X, Y, w, h = where[s.path_id]
        t = o.read_typetree()
        ppu = float(sd.m_PixelsToUnits) * scale
        px, py = rendered[s.path_id][2] if len(rendered[s.path_id]) > 2 else (sd.m_Pivot.x, sd.m_Pivot.y)
        verts, idx, (x0, y0, x1, y1) = quad(w, h, px, py, ppu)
        rd = {
            "textureRect": {"x": float(X), "y": float(Y), "width": float(w), "height": float(h)},
            "textureRectOffset": {"x": 0.0, "y": 0.0},
            "atlasRectOffset": {"x": float(X), "y": float(Y)},
            "uvTransform": {"x": ppu, "y": X + px * w, "z": ppu, "w": Y + py * h},
            "settingsRaw": 1 | 2 | 64,                  # packed, rect mode, full-rect mesh
            "texture": page_ref[pg],
        }
        t["m_Rect"] = {"x": 0.0, "y": 0.0, "width": float(w), "height": float(h)}
        t["m_Offset"] = {"x": 0.0, "y": 0.0}
        t["m_Pivot"] = {"x": px, "y": py}
        t["m_PixelsToUnits"] = ppu
        r = t["m_RD"]
        r.update({k: v for k, v in rd.items() if k in r})
        vd = r["m_VertexData"]
        verts = vertex_bytes(verts, vd["m_Channels"], t["m_Name"])
        vd["m_VertexCount"] = 4
        vd["m_DataSize"] = list(verts) if isinstance(vd["m_DataSize"], list) else verts
        r["m_IndexBuffer"] = list(idx)
        sm = r["m_SubMeshes"][0]
        sm.update({"firstByte": 0, "indexCount": 6, "baseVertex": 0, "firstVertex": 0, "vertexCount": 4})
        sm["localAABB"] = {"m_Center": {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2, "z": 0.0},
                           "m_Extent": {"x": (x1 - x0) / 2, "y": (y1 - y0) / 2, "z": 0.0}}
        r["m_SubMeshes"] = [sm]
        t["m_PhysicsShape"] = []
        o.save_typetree(t)
        key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
        for aid, n in by_key.get(key, []):              # every copy, or the one it reads stays on a wiped page
            v = amaps[aid]["m_RenderDataMap"][n][1]
            v.update({k: val for k, val in rd.items() if k in v})
    for o in atlas_objs:
        o.save_typetree(amaps[o.path_id])
    for i, p in enumerate(pages):
        d = p.read()
        d.set_image(canvases[i], target_format=UnityPy.enums.TextureFormat.RGBA32)
        d.save()


    # the idle most identities stand in is a Spine skeleton, not the sprites; the
    # character's own idle rig stands in for it when it has one
    done_skel = set()
    for root in troots:
        rsrc, _, rrigs = by_root.get(root, (base_src, None, rigs))
        for sp in spine.body_spines(tenv, root):
            if sp["skeleton"].path_id in done_skel:
                continue                                 # forms of one identity can share an idle
            done_skel.add(sp["skeleton"].path_id)
            src, rigs_ = rsrc, rrigs
            try:
                if rigs_:
                    try:
                        notes.append(_spine_borrow(sp, rigs_[0], mirror))
                    except (spine.SpineError, KeyError, ValueError, TypeError, IndexError) as e:
                        notes.append(f"idle {sp['name']}: the character's own rig did not fit ({e}), frames instead")
                    else:
                        if sprites:                      # frames carry the aura; a borrowed rig doesn't
                            try:
                                # the idle stands as tall as the identity, not the source's frames
                                notes.append(_spine_aura(sp, sprites, aura, stands))
                            except (spine.SpineError, KeyError, ValueError, TypeError, IndexError) as e:
                                notes.append(f"idle {sp['name']}: no aura ({e})")
                        continue
                if source is not None:
                    notes.append(_spine_frames(sp, src, rendered, mirror))
                elif head and head.get("spine"):
                    notes.append(_spine_head(sp, art, head["spine"], head.get("effects")))
                else:
                    notes.append(f"idle {sp['name']} left as it is")
            except spine.SpineError as e:
                notes.append(f"idle {sp['name']} left as it is: {e}")

    if borrowed:
        names_ = {"move": "dash", "miss": "evade", "gud": "guard", "dmg": "hit", "dead": "death", "idle": "idle"}
        notes.append("source has no " + ", ".join(sorted(f"{names_.get(x.split('->')[0], x)} (uses its {names_.get(x.split('->')[1])})"
                                                        for x in borrowed)))
    scale_note = "" if scale == 1 else f" (scaled {scale:.2f} to fit)"
    notes.insert(0, f"{len(plan)} frames from {len(rendered)} drawings{scale_note}")
    return tenv.file.save(packer="none"), notes


def preview(bundle_bytes_or_path, out_png, kinds=("idle", "S1", "S2", "S3", "gud", "dmg"), count=12, appearance=None):
    """A contact sheet of repainted frames, rendered back from the written bundle. With
    appearance, only that body's own frames (a bundle can hold several characters)."""
    UnityPy = unitypy()
    from PIL import Image
    env = UnityPy.load(bundle_bytes_or_path)
    own = prefab_objects(env, appearance, forms=True)[0] if appearance else None
    shots = []
    for o in env.objects:
        if o.type.name == "Sprite" and (own is None or o.path_id in own):
            s = o.read()
            if kind_of(s.m_Name)[0] in kinds:
                try:
                    im = s.image.convert("RGBA")
                except Exception:
                    continue
                im.thumbnail((220, 220))
                shots.append(im)
        if len(shots) >= count:
            break
    sheet = Image.new("RGBA", (220 * 6, 220 * max(1, (len(shots) + 5) // 6)), (230, 230, 230, 255))
    for n, im in enumerate(shots):
        sheet.alpha_composite(im, ((n % 6) * 220, (n // 6) * 220))
    sheet.save(out_png)
    return len(shots)


def idle_image(bundle_or_path, appearance, height=360):
    """What the body looks like standing idle in a fight: its Spine idle when it has
    one (setup pose), else its idle sprite. None when neither is found."""
    from PIL import Image
    env = unitypy().load(bundle_or_path)
    assets = bundle_assets(env)
    if appearance not in assets:
        return None
    root, ids = assets[appearance]
    img = None
    for sp in spine.body_spines(env, root):
        pages = spine.parse_atlas(spine.text_of(sp["atlas"]))
        texs = {p["name"]: sp["textures"][p["name"].rsplit(".", 1)[0]].read().image.convert("RGBA")
                for p in pages if p["name"].rsplit(".", 1)[0] in sp["textures"]}
        doc = json.loads(spine.text_of(sp["skeleton"]))
        anim = sp.get("animation") if sp.get("animation") in (doc.get("animations") or {}) \
            else next(iter(doc.get("animations") or {}), None)
        img, _ = spine.render(doc, pages, texs, sp["skin"], ppu=0.25, anim=anim)   # the idle's first moment
        break
    if img is None:
        for name, (sprites, _) in clip_frames(env, ids).items():
            if name.endswith("Default") and sprites:
                img = render(sprites[0])[0]
                break
    if img is None or not img.getbbox():
        return None
    img = img.crop(img.getbbox())
    k = height / img.height
    return img.resize((max(1, int(img.width * k)), height), Image.LANCZOS)


def idle_gif(bundle_or_path, appearance, out, frames=24, height=360):
    """An animated picture of the body's Spine idle (its first animation, one loop)."""
    from PIL import Image
    env = unitypy().load(bundle_or_path)
    assets = bundle_assets(env)
    if appearance not in assets:
        return 0
    sps = spine.body_spines(env, assets[appearance][0])
    if not sps:
        return 0
    sp = sps[0]
    pages = spine.parse_atlas(spine.text_of(sp["atlas"]))
    texs = {p["name"]: sp["textures"][p["name"].rsplit(".", 1)[0]].read().image.convert("RGBA")
            for p in pages if p["name"].rsplit(".", 1)[0] in sp["textures"]}
    doc = json.loads(spine.text_of(sp["skeleton"]))
    name = next(iter(doc.get("animations") or {"": 0}))
    L = spine.anim_length(doc.get("animations", {}).get(name, {})) or 2.0
    shots = []
    for i in range(frames):
        img, origin = spine.render(doc, pages, texs, sp["skin"], ppu=0.25, anim=name, t=L * i / frames)
        shots.append((img, origin))
    # one canvas for every frame, lined up on the skeleton's origin
    left = max(o[0] for _, o in shots)
    top = max(o[1] for _, o in shots)
    W = int(max(left - o[0] + im.width for im, o in shots)) + 2
    H = int(max(top - o[1] + im.height for im, o in shots)) + 2
    seq = []
    for im, o in shots:
        c = Image.new("RGBA", (W, H), (28, 24, 32, 255))
        c.alpha_composite(im, (int(left - o[0]), int(top - o[1])))
        k = height / H
        seq.append(c.resize((max(1, int(W * k)), height), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE))
    seq[0].save(out, save_all=True, append_images=seq[1:], duration=int(1000 * L / frames), loop=0)
    return len(seq)
