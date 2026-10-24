"""Spine skeletons: the idle most identities play between actions.

Most identities stand idle as a Spine skeleton (bones and cut-out parts) and only switch
to their frame-by-frame sprites to move, guard or attack. Repainting the sprites alone
leaves that idle untouched, so a repaint rewrites the idle too:
- a full repaint hides every part and adds one slot that flips through the new idle
  frames, placed exactly where the sprite frames stand;
- a head recipe swaps only the head part for the new head art.
Bones, slots, skins, constraints, events and animation names all stay, so the game's code
still finds everything it looks up.
"""
import copy
import json
import math

SLOT = "lsk_frame"
BONE = "lsk_breath"
BREATH = 2.4       # seconds per breath of a repainted idle


class SpineError(Exception):
    pass


# --- atlas text -------------------------------------------------------------------

def parse_atlas(text):
    """[{"name", "header": [(key, value)], "regions": [{"name", "fields": [(key, value)]}]}]"""
    pages, page, region = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            page = region = None
            continue
        if page is None:
            page = {"name": line, "header": [], "regions": []}
            pages.append(page)
        elif ":" in line:
            k, v = (s.strip() for s in line.split(":", 1))
            (region["fields"] if region else page["header"]).append((k, v))
        else:
            region = {"name": line, "fields": []}
            page["regions"].append(region)
    return pages


def format_atlas(pages):
    out = []
    for p in pages:
        out.append(p["name"])
        out += [f"{k}:{v}" for k, v in p["header"]]
        for r in p["regions"]:
            out.append(r["name"])
            out += [f"{k}:{v}" for k, v in r["fields"]]
        out.append("")
    return "\n".join(out)


def page_size(page):
    w, h = dict(page["header"])["size"].split(",")
    return int(w), int(h)


def region_geom(region):
    """x, y (top-left, page units), w, h (unrotated), degrees, offx, offy, origw, origh."""
    f = dict(region["fields"])
    if "bounds" in f:
        x, y, w, h = (int(v) for v in f["bounds"].split(","))
    else:
        x, y = (int(v) for v in f["xy"].split(","))
        w, h = (int(v) for v in f["size"].split(","))
    rot = f.get("rotate", "false")
    deg = 90 if rot == "true" else 0 if rot == "false" else int(rot)
    ox = oy = 0
    ow, oh = w, h
    if "offsets" in f:
        ox, oy, ow, oh = (int(v) for v in f["offsets"].split(","))
    else:
        if "offset" in f:
            ox, oy = (int(v) for v in f["offset"].split(","))
        if "orig" in f:
            ow, oh = (int(v) for v in f["orig"].split(","))
    return x, y, w, h, deg, ox, oy, ow, oh


def region_image(region, page, tex):
    """The region upright, padded to its original size (whitespace restored)."""
    from PIL import Image
    x, y, w, h, deg, ox, oy, ow, oh = region_geom(region)
    pw, ph = page_size(page)
    sx, sy = tex.width / pw, tex.height / ph
    bw, bh = (h, w) if deg in (90, 270) else (w, h)
    crop = tex.crop((round(x * sx), round(y * sy), round((x + bw) * sx), round((y + bh) * sy)))
    crop = crop.resize((max(1, bw), max(1, bh)), Image.BILINEAR)
    if deg == 90:
        crop = crop.rotate(-90, expand=True)
    elif deg == 180:
        crop = crop.rotate(180)
    elif deg == 270:
        crop = crop.rotate(90, expand=True)
    if (ow, oh) != (w, h):
        full = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
        full.paste(crop, (ox, oh - oy - h))
        crop = full
    return crop


# --- pixels --------------------------------------------------------------------------

def premultiply(img):
    from PIL import Image, ImageChops
    r, g, b, a = img.convert("RGBA").split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a), ImageChops.multiply(b, a), a))


def unpremultiply(img):
    px = img.load()
    out = img.copy()
    po = out.load()
    for yy in range(img.height):
        for xx in range(img.width):
            rr, gg, bb, aa = px[xx, yy]
            if 0 < aa < 255:
                po[xx, yy] = (min(255, rr * 255 // aa), min(255, gg * 255 // aa), min(255, bb * 255 // aa), aa)
    return out


# --- skeleton ------------------------------------------------------------------------

def skins_list(doc):
    s = doc.get("skins", [])
    if isinstance(s, dict):                                   # Spine 3.x layout
        return [{"name": k, "attachments": v} for k, v in s.items()]
    return s


def find_attachment(doc, slot, name, skin=None):
    for s in skins_list(doc):
        if s["name"] == skin and name in s["attachments"].get(slot, {}):
            return s["attachments"][slot][name]
    for s in skins_list(doc):
        if s["name"] == "default" and name in s["attachments"].get(slot, {}):
            return s["attachments"][slot][name]
    return None


def frame_swap(doc, frames, duration):
    """A copy of the skeleton whose only visible part is a new slot cycling `frames`
    ([(region name, attachment dict)]) over `duration` seconds in every animation."""
    if doc.get("path"):
        raise SpineError("path constraints are not handled")
    d = copy.deepcopy(doc)
    if isinstance(d.get("skins"), dict):
        raise SpineError("Spine 3.x skeletons are not handled")
    for s in d["skins"]:
        s["attachments"] = {}
    for sl in d["slots"]:
        sl.pop("attachment", None)
    d["bones"].append({"name": BONE, "parent": d["bones"][0]["name"]})
    d["slots"].append({"name": SLOT, "bone": BONE, "attachment": frames[0][0]})
    default = next((s for s in d["skins"] if s["name"] == "default"), None)
    if default is None:
        default = {"name": "default", "attachments": {}}
        d["skins"].insert(0, default)
    default["attachments"][SLOT] = {name: att for name, att in frames}
    # loop the frames for at least one breath, so a single still frame still breathes
    reps = max(1, round(BREATH / duration))
    length = duration * reps
    step = duration / len(frames)
    keys = [{"name": frames[0][0]}]
    for r in range(reps):
        keys += [{"time": round(r * duration + i * step, 4), "name": n} for i, (n, _) in enumerate(frames) if i or r]
    keys.append({"time": round(length, 4), "name": frames[0][0]})
    breath = {"scale": [{},
                        {"time": round(length / 2, 4), "x": 0.996, "y": 1.018},
                        {"time": round(length, 4)}]}
    anims = d.setdefault("animations", {}) or {"animation": {}}
    d["animations"] = anims
    if not anims:
        anims["animation"] = {}
    for a in anims.values():
        a.pop("deform", None)                                 # names attachments that are gone
        a.pop("attachments", None)
        a.setdefault("slots", {})[SLOT] = {"attachment": keys}
        a.setdefault("bones", {})[BONE] = breath
    return d


# --- setup pose + render (previews) ---------------------------------------------------

def bone_world(doc):
    world = {}
    for b in doc["bones"]:
        x, y = b.get("x", 0.0), b.get("y", 0.0)
        r = math.radians(b.get("rotation", 0.0))
        sx, sy = b.get("scaleX", 1.0), b.get("scaleY", 1.0)
        shx, shy = math.radians(b.get("shearX", 0.0)), math.radians(b.get("shearY", 0.0))
        la, lb = math.cos(r + shx) * sx, math.cos(r + math.pi / 2 + shy) * sy
        lc, ld = math.sin(r + shx) * sx, math.sin(r + math.pi / 2 + shy) * sy
        p = world.get(b.get("parent"))
        if p is None:
            world[b["name"]] = (la, lb, lc, ld, x, y)
        else:
            pa, pb, pc, pd, px, py = p
            world[b["name"]] = (pa * la + pb * lc, pa * lb + pb * ld, pc * la + pd * lc, pc * lb + pd * ld,
                                pa * x + pb * y + px, pc * x + pd * y + py)
    return world


def _apply(m, x, y):
    a, b, c, d, tx, ty = m
    return a * x + b * y + tx, c * x + d * y + ty


def setup_triangles(doc, skin=None):
    """[(region name, [(world x, y)], [(u, v) in the upright region, 0..1, v down], triangles, alpha)]."""
    world = bone_world(doc)
    bones = [b["name"] for b in doc["bones"]]
    out = []
    for sl in doc["slots"]:
        an = sl.get("attachment")
        if not an:
            continue
        att = find_attachment(doc, sl["name"], an, skin)
        if att is None:
            continue
        typ = att.get("type", "region")
        alpha = int(sl.get("color", "ffffffff")[6:8], 16) / 255 if len(sl.get("color", "")) == 8 else 1.0
        m = world[sl["bone"]]
        if typ == "region":
            w, h = att["width"], att["height"]
            r = math.radians(att.get("rotation", 0.0))
            sx, sy = att.get("scaleX", 1.0), att.get("scaleY", 1.0)
            ox, oy = att.get("x", 0.0), att.get("y", 0.0)
            pts = []
            for lx, ly in ((-w / 2, -h / 2), (-w / 2, h / 2), (w / 2, h / 2), (w / 2, -h / 2)):
                lx, ly = lx * sx, ly * sy
                pts.append(_apply(m, ox + lx * math.cos(r) - ly * math.sin(r), oy + lx * math.sin(r) + ly * math.cos(r)))
            out.append((att.get("path", an), pts, [(0, 1), (0, 0), (1, 0), (1, 1)], [(0, 1, 2), (0, 2, 3)], alpha))
        elif typ in ("mesh", "linkedmesh"):
            src = att
            if typ == "linkedmesh":
                src = find_attachment(doc, sl["name"], att["parent"], att.get("skin"))
                if src is None:
                    continue
            uvs = src["uvs"]
            n = len(uvs) // 2
            v = src["vertices"]
            pts = []
            if len(v) == len(uvs):
                pts = [_apply(m, v[2 * i], v[2 * i + 1]) for i in range(n)]
            else:
                i = 0
                while i < len(v):
                    cnt = int(v[i]); i += 1
                    wx = wy = 0.0
                    for _ in range(cnt):
                        bi, bx, by, wt = int(v[i]), v[i + 1], v[i + 2], v[i + 3]; i += 4
                        px, py = _apply(world[bones[bi]], bx, by)
                        wx += px * wt; wy += py * wt
                    pts.append((wx, wy))
            tris = [tuple(src["triangles"][k:k + 3]) for k in range(0, len(src["triangles"]), 3)]
            out.append((att.get("path", an), pts, [(uvs[2 * i], uvs[2 * i + 1]) for i in range(n)], tris, alpha))
    return out


def render(doc, pages, textures, skin=None, ppu=0.45, pma=None):
    """Setup pose -> (RGBA image, (origin x, origin y) in image pixels). textures:
    {page name: PIL image}. ppu: image pixels per skeleton unit."""
    from PIL import Image, ImageDraw
    regions = {}
    for p in pages:
        tex = textures.get(p["name"])
        if tex is None:
            continue
        pm = dict(p["header"]).get("pma", "false") == "true" if pma is None else pma
        for r in p["regions"]:
            regions[r["name"]] = (r, p, tex, pm)
    tris = setup_triangles(doc, skin)
    allp = [pt for t in tris for pt in t[1]]
    if not allp:
        return Image.new("RGBA", (8, 8)), (4, 4)
    x0, x1 = min(p[0] for p in allp), max(p[0] for p in allp)
    y0, y1 = min(p[1] for p in allp), max(p[1] for p in allp)
    W, H = int((x1 - x0) * ppu) + 4, int((y1 - y0) * ppu) + 4
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    to = lambda p: ((p[0] - x0) * ppu + 2, (y1 - p[1]) * ppu + 2)  # noqa: E731
    cache = {}
    for name, pts, uvs, tri, alpha in tris:
        if name not in regions:
            continue
        if name not in cache:
            r, p, tex, pm = regions[name]
            im = region_image(r, p, tex).convert("RGBA")
            cache[name] = unpremultiply(im) if pm else im
        img = cache[name]
        for t in tri:
            d = [to(pts[i]) for i in t]
            s = [(uvs[i][0] * img.width, uvs[i][1] * img.height) for i in t]
            bx0, by0 = int(max(0, min(p[0] for p in d))), int(max(0, min(p[1] for p in d)))
            bx1, by1 = int(min(W, max(p[0] for p in d) + 2)), int(min(H, max(p[1] for p in d) + 2))
            if bx1 <= bx0 or by1 <= by0:
                continue
            coeff = _affine(d, s, bx0, by0)
            if coeff is None:
                continue
            patch = img.transform((bx1 - bx0, by1 - by0), Image.AFFINE, coeff, resample=Image.BILINEAR)
            mask = Image.new("L", patch.size, 0)
            ImageDraw.Draw(mask).polygon([(p[0] - bx0, p[1] - by0) for p in d], fill=int(255 * alpha))
            pa = patch.getchannel("A")
            from PIL import ImageChops
            patch.putalpha(ImageChops.multiply(pa, mask))
            canvas.alpha_composite(patch, (bx0, by0))
    return canvas, (to((0, 0)))


def _affine(d, s, ox, oy):
    """Coefficients mapping destination pixels (offset by ox, oy) to source pixels."""
    (x1, y1), (x2, y2), (x3, y3) = [(p[0] - ox, p[1] - oy) for p in d]
    det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    if abs(det) < 1e-9:
        return None
    out = []
    for k in (0, 1):
        u1, u2, u3 = s[0][k], s[1][k], s[2][k]
        a = ((u2 - u1) * (y3 - y1) - (u3 - u1) * (y2 - y1)) / det
        b = ((u3 - u1) * (x2 - x1) - (u2 - u1) * (x3 - x1)) / det
        out += [a, b, u1 - a * x1 - b * y1]
    return tuple(out)


def dumps(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


# --- the Unity side: which skeleton a body idles with ------------------------------

def _trs(t):
    q = t["m_LocalRotation"]
    if abs(q["x"]) > 1e-3 or abs(q["y"]) > 1e-3:
        raise SpineError("a transform rotates out of the screen plane")
    th = 2 * math.atan2(q["z"], q["w"])
    p, s = t["m_LocalPosition"], t["m_LocalScale"]
    return (math.cos(th) * s["x"], -math.sin(th) * s["y"], math.sin(th) * s["x"], math.cos(th) * s["y"], p["x"], p["y"])


def mul(m, n):
    a, b, c, d, tx, ty = m
    e, f, g, h, ux, uy = n
    return (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h, a * ux + b * uy + tx, c * ux + d * uy + ty)


def inv(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ib * ty), -(ic * tx + id_ * ty))


def body_spines(env, root_id):
    """For the body prefab rooted at root_id: [{skeleton, atlas, pages: {page name:
    Texture2D reader}, scale, skin, to_skel (sprite-renderer space -> skeleton units)}]."""
    objs = {o.path_id: o for o in env.objects}
    tt = lambda pid: objs[pid].read_typetree()  # noqa: E731
    parent = {}
    for o in env.objects:
        if o.type.name in ("Transform", "RectTransform"):
            for ch in o.read_typetree()["m_Children"]:
                parent[ch["m_PathID"]] = o.path_id

    def go_matrix(go):
        tr = next(c["component"]["m_PathID"] for c in tt(go)["m_Component"]
                  if objs[c["component"]["m_PathID"]].type.name in ("Transform", "RectTransform"))
        m = (1, 0, 0, 1, 0, 0)
        while tr is not None:
            m = mul(_trs(tt(tr)), m)
            tr = parent.get(tr)
        return m

    app = None
    for c in tt(root_id)["m_Component"]:
        o = objs[c["component"]["m_PathID"]]
        if o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            if "motionList" in t:
                app = t
    if app is None or not any(m.get("isSpineAnim") for m in app["motionList"]):
        return []
    sr = tt(app["sprenderer_charactermotion"]["m_PathID"])
    m_sr = go_matrix(sr["m_GameObject"]["m_PathID"])
    if sr.get("m_FlipX"):
        m_sr = mul(m_sr, (-1, 0, 0, 1, 0, 0))
    if sr.get("m_FlipY"):
        m_sr = mul(m_sr, (1, 0, 0, -1, 0, 0))
    out, seen = [], set()
    for ref in app["_spineSkins"]:
        if not ref["m_PathID"] or ref["m_FileID"]:
            continue
        ctl = tt(ref["m_PathID"])
        aid = ctl.get("_spine_Animation", {}).get("m_PathID", 0)
        if not aid:
            aid = next((c["component"]["m_PathID"] for c in tt(ctl["m_GameObject"]["m_PathID"])["m_Component"]
                        if objs[c["component"]["m_PathID"]].type.name == "MonoBehaviour"
                        and "skeletonDataAsset" in objs[c["component"]["m_PathID"]].read_typetree()), 0)
        if not aid:
            continue
        anim = tt(aid)
        sd_id = anim["skeletonDataAsset"]["m_PathID"]
        if sd_id in seen:
            continue
        seen.add(sd_id)
        sd = tt(sd_id)
        at = tt(sd["atlasAssets"][0]["m_PathID"])
        texs = {}
        for mref in at["materials"]:
            mt = tt(mref["m_PathID"])
            for k, v in mt["m_SavedProperties"]["m_TexEnvs"]:
                if k == "_MainTex" and v["m_Texture"]["m_PathID"] and not v["m_Texture"]["m_FileID"]:
                    tex = objs[v["m_Texture"]["m_PathID"]]
                    texs[tex.read().m_Name] = tex
        m_go = go_matrix(anim["m_GameObject"]["m_PathID"])
        s = sd["scale"]
        fx = -1 if anim.get("initialFlipX") else 1
        fy = -1 if anim.get("initialFlipY") else 1
        to_skel = mul((fx / s, 0, 0, fy / s, 0, 0), mul(inv(m_go), m_sr))
        out.append({"skeleton": objs[sd["skeletonJSON"]["m_PathID"]], "atlas": objs[at["atlasFile"]["m_PathID"]],
                    "textures": texs, "scale": s, "skin": anim.get("initialSkinName") or None,
                    "to_skel": to_skel, "name": sd["m_Name"]})
    return out


def text_of(reader):
    t = reader.read()
    s = t.m_Script
    return s if isinstance(s, str) else s.decode("utf-8", "surrogateescape")


def set_text(reader, text):
    t = reader.read_typetree()
    t["m_Script"] = text
    reader.save_typetree(t)


def region_attachment(to_skel, x0, y0, x1, y1):
    """Attachment dict for a quad given in sprite-renderer units."""
    a, b, c, d, _, _ = to_skel
    cx, cy = _apply(to_skel, (x0 + x1) / 2, (y0 + y1) / 2)
    lx, ly = math.hypot(a, c), math.hypot(b, d)
    rot = math.degrees(math.atan2(c, a))
    att = {"x": round(cx, 3), "y": round(cy, 3), "width": round((x1 - x0) * lx, 3), "height": round((y1 - y0) * ly, 3)}
    if a * d - b * c < 0:
        att["scaleX"] = -1
        rot -= 180
    if abs(rot) > 1e-3 and abs(abs(rot) - 360) > 1e-3:
        att["rotation"] = round(rot, 3)
    return att


# --- head effects ---------------------------------------------------------------------

HEAD_BONE, GLOW, MOTE = "lsk_head", "lsk_glow", "lsk_mote"


def anim_length(anim):
    """The latest key time in an animation (0 when it has no keys)."""
    best = 0.0
    stack = [anim]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            if isinstance(v.get("time"), (int, float)):
                best = max(best, float(v["time"]))
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return best


def _hex(alpha):
    return "ffffff%02x" % max(0, min(255, round(alpha * 255)))


def head_effects(doc, slot_name, size, rotation, fx, skin_name="default"):
    """Give the head slot its own bone (so it can flutter), a pulsing glow behind it and
    a few motes drifting up in front. size = (w, h) of the head art in skeleton units;
    fx = {"glow": {...}, "glow_size": (w, h), "motes": n, "mote_size": units, "flutter": f}.
    Changes doc in place."""
    slots = doc["slots"]
    i = next(k for k, s in enumerate(slots) if s["name"] == slot_name)
    slot = slots[i]
    att_name = slot["attachment"]
    owner = next(s for s in skins_list(doc) if att_name in s["attachments"].get(slot_name, {})
                 and s["name"] in (skin_name, "default"))
    att = owner["attachments"][slot_name][att_name]
    bx, by = att.get("x", 0.0), att.get("y", 0.0)
    doc["bones"].append({"name": HEAD_BONE, "parent": slot["bone"], "x": round(bx, 3), "y": round(by, 3)})
    parent = slot["bone"]
    # every attachment in the slot moves to the new bone (a pure shift, nothing else changes)
    for s in skins_list(doc):
        for a in s["attachments"].get(slot_name, {}).values():
            if a.get("type", "region") == "region":
                a["x"] = round(a.get("x", 0.0) - bx, 3)
                a["y"] = round(a.get("y", 0.0) - by, 3)
            elif a.get("type") == "mesh" and len(a["vertices"]) == len(a["uvs"]):
                a["vertices"] = [round(v - (bx if k % 2 == 0 else by), 3) for k, v in enumerate(a["vertices"])]
    slot["bone"] = HEAD_BONE
    w, h = size
    extra = {}
    if fx.get("glow"):
        gw, gh = fx["glow_size"]
        slots.insert(i, {"name": GLOW, "bone": HEAD_BONE, "attachment": GLOW, "color": _hex(fx["glow"]["low"])})
        extra[GLOW] = {GLOW: {"width": round(gw, 3), "height": round(gh, 3), "rotation": rotation} if rotation
                       else {"width": round(gw, 3), "height": round(gh, 3)}}
    n = int(fx.get("motes", 0))
    spots = [(-0.36, 0.05), (0.38, 0.12), (0.04, 0.42), (-0.2, 0.3), (0.22, -0.1)][:n]
    after = next(k for k, s in enumerate(slots) if s["name"] == slot_name) + 1
    for k, (ux, uy) in enumerate(spots, 1):
        doc["bones"].append({"name": f"{MOTE}{k}", "parent": parent,
                             "x": round(bx + ux * w, 3), "y": round(by + uy * h, 3)})
        slots.insert(after, {"name": f"{MOTE}{k}", "bone": f"{MOTE}{k}", "attachment": MOTE, "color": _hex(0)})
        after += 1
        m = fx["mote_size"]
        extra[f"{MOTE}{k}"] = {MOTE: {"width": round(m, 3), "height": round(m, 3)}}
    default = next((s for s in skins_list(doc) if s["name"] == "default"), None)
    if default is None:
        default = {"name": "default", "attachments": {}}
        doc["skins"].insert(0, default)
    default["attachments"].update(extra)

    anims = doc.setdefault("animations", {})
    if not anims:
        anims["animation"] = {}
    for a in anims.values():
        L = anim_length(a) or 2.0
        # the head slot keeps showing the head: blinks and swaps would bring the old one back
        for key in a.get("slots", {}).get(slot_name, {}).get("attachment", []):
            if key.get("name"):
                key["name"] = att_name
        bones = a.setdefault("bones", {})
        f = fx.get("flutter", 0)
        if f:
            bones[HEAD_BONE] = {"scale": [{}, {"time": round(L / 4, 4), "x": round(1 - f, 4), "y": round(1 + f / 4, 4)},
                                          {"time": round(L / 2, 4)},
                                          {"time": round(3 * L / 4, 4), "x": round(1 - f, 4), "y": round(1 + f / 4, 4)},
                                          {"time": round(L, 4)}]}
        sl = a.setdefault("slots", {})
        if fx.get("glow"):
            lo, hi = fx["glow"]["low"], fx["glow"]["high"]
            sl[GLOW] = {"rgba": [{"color": _hex(lo)}, {"time": round(L / 2, 4), "color": _hex(hi)},
                                 {"time": round(L, 4), "color": _hex(lo)}]}
        for k in range(1, n + 1):
            phase = (k - 1) / max(1, n)
            move, rgba = _mote_keys(L, phase, rise=0.38 * h, drift=0.05 * w * (1 if k % 2 else -1))
            bones[f"{MOTE}{k}"] = {"translate": move}
            sl[f"{MOTE}{k}"] = {"rgba": rgba}
    return doc


def _mote_keys(L, phase, rise, drift, steps=8):
    """Keys for one mote looping over L seconds, started `phase` of the way through its
    climb: it rises and drifts while it fades in and out, then jumps back unseen."""
    def at(p):
        return (round(drift * math.sin(2 * math.pi * p), 3), round(rise * p, 3),
                _hex(0.85 * math.sin(math.pi * p) ** 1.5))
    wrap = (1 - phase) * L                       # when the climb restarts
    times = sorted({round(L * j / steps, 4) for j in range(steps + 1)} | {round(wrap, 4)})
    move, rgba = [], []
    for t in times:
        if t > L:
            continue
        p = t / L + phase
        if p > 1 + 1e-9:
            p -= 1                                # (p == 1 at the very end stays the top)
        top = abs(t - wrap) < 1e-3 and 0 < wrap < L
        if top:                                   # end of this climb, then a stepped jump
            x, y, c = at(1.0)
            move.append({"time": t, "x": x, "y": y, "curve": "stepped"})
            rgba.append({"time": t, "color": c, "curve": "stepped"})
            t2 = round(t + 0.001, 4)
            x, y, c = at(0.0)
            move.append({"time": t2, "x": x, "y": y})
            rgba.append({"time": t2, "color": c})
            continue
        x, y, c = at(p)
        move.append({"time": t, "x": x, "y": y})
        rgba.append({"time": t, "color": c})
    for keys in (move, rgba):
        if keys and keys[0]["time"] == 0:
            del keys[0]["time"]
    return move, rgba


# --- animation sampling (previews) ----------------------------------------------------

def _lerp_keys(keys, t, fields, defaults):
    """Value of a timeline at t (linear between keys, stepped where marked)."""
    if not keys:
        return None
    prev = keys[0]
    if t <= prev.get("time", 0):
        return [prev.get(f, d) for f, d in zip(fields, defaults)]
    for k in keys[1:]:
        if t < k.get("time", 0):
            t0, t1 = prev.get("time", 0), k.get("time", 0)
            if prev.get("curve") == "stepped" or t1 <= t0:
                return [prev.get(f, d) for f, d in zip(fields, defaults)]
            u = (t - t0) / (t1 - t0)
            out = []
            for f, d in zip(fields, defaults):
                a, b = prev.get(f, d), k.get(f, d)
                out.append(a + (b - a) * u if isinstance(a, (int, float)) else a)
            return out
        prev = k
    return [prev.get(f, d) for f, d in zip(fields, defaults)]


def _color_at(keys, t):
    if not keys:
        return None
    prev = keys[0]
    if t <= prev.get("time", 0):
        return prev["color"]
    for k in keys[1:]:
        if t < k.get("time", 0):
            if prev.get("curve") == "stepped":
                return prev["color"]
            t0, t1 = prev.get("time", 0), k["time"]
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0
            a, b = int(prev["color"], 16), int(k["color"], 16)
            ch = [round(((a >> s) & 255) + (((b >> s) & 255) - ((a >> s) & 255)) * u) for s in (24, 16, 8, 0)]
            return "%02x%02x%02x%02x" % tuple(ch)
        prev = k
    return prev["color"]


def sample(doc, anim_name, t):
    """A copy of the skeleton posed at time t of an animation (bones and slot colours and
    attachments; curves as straight lines - good enough for a preview)."""
    d = copy.deepcopy(doc)
    a = d.get("animations", {}).get(anim_name, {})
    bones = {b["name"]: b for b in d["bones"]}
    for name, tl in a.get("bones", {}).items():
        b = bones.get(name)
        if b is None:
            continue
        v = _lerp_keys(tl.get("rotate", []), t, ["value"], [0.0])
        if v:
            b["rotation"] = b.get("rotation", 0.0) + v[0]
        v = _lerp_keys(tl.get("translate", []), t, ["x", "y"], [0.0, 0.0])
        if v:
            b["x"], b["y"] = b.get("x", 0.0) + v[0], b.get("y", 0.0) + v[1]
        v = _lerp_keys(tl.get("scale", []), t, ["x", "y"], [1.0, 1.0])
        if v:
            b["scaleX"], b["scaleY"] = b.get("scaleX", 1.0) * v[0], b.get("scaleY", 1.0) * v[1]
    slots = {s["name"]: s for s in d["slots"]}
    for name, tl in a.get("slots", {}).items():
        s = slots.get(name)
        if s is None:
            continue
        c = _color_at(tl.get("rgba", []), t)
        if c:
            s["color"] = c
        keys = tl.get("attachment", [])
        if keys:
            cur = keys[0].get("name")
            for k in keys:
                if k.get("time", 0) <= t:
                    cur = k.get("name")
            s["attachment"] = cur
    return d
