"""Spine skeletons: the idle most identities play between actions.

Most identities stand idle as a Spine skeleton (bones and cut-out parts) and only switch
to their frame-by-frame sprites to move, guard or attack. Repainting the sprites alone
leaves that idle untouched, so a repaint rewrites the idle too:
- a full repaint hides every part and brings in the other character's own idle rig
  (its bones, parts, constraints and animation), mirrored to face the sinners' way and
  standing exactly where the sprite frames stand;
- when that character has no rig, one slot flips through its idle frames instead;
- a head recipe swaps only the head part for the new head art.
Bones, slots, skins, constraints, events and animation names all stay, so the game's code
still finds everything it looks up.
"""
import copy
import json
import math
import re

SLOT = "lsk_frame"
BONE = "lsk_breath"
BREATH = 2.4       # seconds per breath of a repainted idle
PREFIX = "lsk_"    # names of a borrowed rig's bones, slots and constraints


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


# --- borrowing another character's idle rig -------------------------------------------

# a bone timeline's values, in the order its curve lists them, and whether a left-right
# mirror flips them
BONE_TIMELINES = {
    "rotate": (("value", True),), "translate": (("x", True), ("y", False)),
    "translatex": (("value", True),), "translatey": (("value", False),),
    "scale": (("x", False), ("y", False)), "scalex": (("value", False),), "scaley": (("value", False),),
    "shear": (("x", True), ("y", True)), "shearx": (("value", True),), "sheary": (("value", True),)}
SLOT_TIMELINES = {"attachment", "rgba", "rgb", "alpha", "rgba2", "rgb2"}
ANIM_PARTS = {"bones", "slots", "ik", "transform", "deform", "drawOrder", "events"}
VERTEX_TYPES = ("mesh", "boundingbox", "clipping", "path")


def _vertex_count(att):
    return len(att["uvs"]) // 2 if att.get("type") == "mesh" else int(att.get("vertexCount", 0))


def _map_vertices(v, count, x=None, bone=None):
    """Vertices with x and/or the bone index changed: plain [x, y, ...] or weighted
    [n, (bone, x, y, weight) * n, ...] per vertex."""
    if len(v) == 2 * count:
        return [x(c) if x and i % 2 == 0 else c for i, c in enumerate(v)]
    out, i = list(v), 0
    while i < len(out):
        n = int(out[i])
        i += 1
        for _ in range(n):
            if bone:
                out[i] = bone(int(out[i]))
            if x:
                out[i + 1] = x(out[i + 1])
            i += 4
    return out


def _flip_keys(keys, fields):
    for k in keys:
        cv = k.get("curve")
        for ch, (f, flip) in enumerate(fields):
            if not flip:
                continue
            if f in k:
                k[f] = -k[f]
            if isinstance(cv, list):                          # [time, value, time, value] per value
                for i in (4 * ch + 1, 4 * ch + 3):
                    if i < len(cv):
                        cv[i] = -cv[i]


def mirror_x(doc):
    """Mirror a skeleton left-right in its data: every bone, part, constraint and key, as
    if the whole picture were seen in a mirror. Worked into the data rather than a flipped
    root bone, because bones that ignore their parent's reflection would not follow one.
    IK has to be baked first (bake_ik): it aims a bone's own axis, which the mirror turns
    round. Changes doc in place."""
    if doc.get("path"):
        raise SpineError("path constraints are not handled")
    if doc.get("ik"):
        raise SpineError("bake the IK before mirroring")
    for b in doc["bones"]:
        for f in ("x", "rotation", "shearX", "shearY"):
            if f in b:
                b[f] = -b[f]
    for c in doc.get("transform", []):
        for f in ("rotation", "x", "shearY"):
            if f in c:
                c[f] = -c[f]
    for s in skins_list(doc):
        for atts in s["attachments"].values():
            for a in atts.values():
                t = a.get("type", "region")
                if t in ("region", "point"):
                    for f in ("x", "rotation"):
                        if f in a:
                            a[f] = -a[f]
                    if t == "region":
                        a["scaleX"] = -a.get("scaleX", 1.0)  # the picture itself turns round
                elif t in VERTEX_TYPES:
                    a["vertices"] = _map_vertices(a["vertices"], _vertex_count(a), x=lambda c: -c)
    for anim in (doc.get("animations") or {}).values():
        for tls in anim.get("bones", {}).values():
            for name, keys in tls.items():
                _flip_keys(keys, BONE_TIMELINES.get(name, ()))
        for slots in anim.get("deform", {}).values():
            for atts in slots.values():
                for keys in atts.values():
                    for k in keys:
                        off = k.get("offset", 0)
                        if "vertices" in k:
                            k["vertices"] = [-c if (off + i) % 2 == 0 else c for i, c in enumerate(k["vertices"])]
    sk = doc.get("skeleton") or {}
    if "x" in sk and "width" in sk:
        sk["x"] = -(sk["x"] + sk["width"])
    return doc


def scale_time(anim, k):
    """Every key of an animation (and the time half of every curve handle) k times later."""
    stack = [anim]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(x for x in v.values() if isinstance(x, (dict, list)))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for key in v:
                if "time" in key:
                    key["time"] = round(key["time"] * k, 4)
                if isinstance(key.get("curve"), list):
                    key["curve"] = [round(c * k, 4) if i % 2 == 0 else c for i, c in enumerate(key["curve"])]
    return anim


def bake_ik(doc, anim, skin=None, fps=30):
    """Work the IK constraints' effect on animation `anim` into plain keys for the bones
    they move, sampled `fps` times a second, and drop the constraints. Changes doc in place."""
    iks = doc.get("ik") or []
    if not iks:
        return doc
    if any(c.get("skin") for c in iks):
        raise SpineError("IK that only some skins switch on is not handled")
    a = (doc.get("animations") or {}).get(anim)
    if a is None:
        raise SpineError(f"the rig has no animation {anim}")
    length = anim_length(a)
    n = max(1, round(length * fps))
    times = [round(length * i / n, 4) for i in range(n + 1)] if length > 0 else [0.0]
    samples = []
    for t in times:
        rec = {}
        pose(doc, skin, anim, t, record=rec)
        samples.append(rec)
    baked = set(samples[0]).intersection(*samples[1:])
    bones = {b["name"]: b for b in doc["bones"]}
    tls = a.setdefault("bones", {})
    for name in sorted(baked):
        bd = bones[name]
        setup = (bd.get("x", 0.0), bd.get("y", 0.0), bd.get("rotation", 0.0), bd.get("scaleX", 1.0),
                 bd.get("scaleY", 1.0), bd.get("shearX", 0.0), bd.get("shearY", 0.0))
        if not setup[3] or not setup[4]:
            raise SpineError(f"IK bone {name} has a zero setup scale")
        rows, prev = [], None
        for t, rec in zip(times, samples):
            ax, ay, ar, asx, asy, ashx, ashy = rec[name]
            r = ar - setup[2]
            if prev is not None:                              # never the long way round between samples
                r += 360 * round((prev - r) / 360)
            prev = r
            rows.append((t, ax - setup[0], ay - setup[1], r, asx / setup[3], asy / setup[4],
                         ashx - setup[5], ashy - setup[6]))
        new = {}
        for tl, cols, rest in (("rotate", (3,), (0.0,)), ("translate", (1, 2), (0.0, 0.0)),
                               ("scale", (4, 5), (1.0, 1.0)), ("shear", (6, 7), (0.0, 0.0))):
            if all(abs(row[c] - v) < 1e-4 for row in rows for c, v in zip(cols, rest)):
                continue                                      # stays at the setup pose
            fields = [f for f, _ in BONE_TIMELINES[tl]]
            new[tl] = [dict({"time": row[0]}, **{f: round(row[c], 4) for f, c in zip(fields, cols)}) for row in rows]
        tls[name] = new
    doc["ik"] = []
    for an in (doc.get("animations") or {}).values():
        an.pop("ik", None)
    rest = sorted((c.get("order", 0), i, c) for i, c in enumerate(doc.get("transform", []) + doc.get("path", [])))
    for i, (_, _, c) in enumerate(rest):
        c["order"] = i
    return doc


def skin_match(name, names):
    """The source skin standing in for a target skin: the same name, its last word ("a_on"
    -> "on") or the same number ("1" -> "lv1"). None when nothing fits."""
    low = {n.lower(): n for n in names}
    n = name.lower()
    for cand in (n, n.rsplit("_", 1)[-1]):
        if cand in low:
            return low[cand]
    digits = re.sub(r"\D", "", n)
    hits = [x for x in names if digits and re.sub(r"\D", "", x) == digits]
    return hits[0] if len(hits) == 1 else None


def transplant(target, source, dx, dy, anim, skin=None, target_skin=None, names=(), prefix=PREFIX):
    """The target skeleton with the source's rig standing in for its parts.

    Every target bone, slot, constraint, skin and animation name stays (the game looks
    them up), its parts are hidden, and the source's bones, slots, constraints and parts
    join under the target's root, moved by (dx, dy) and renamed with `prefix`. Each target
    animation (and each of `names`) plays the source's animation `anim`.

    Skins: when every named target skin has a counterpart in the source (see skin_match)
    and the one the target starts in is the source's own starting `skin`, each takes its
    counterpart's parts, so the look changes when the game switches skins. Otherwise the
    starting skin is folded into the default one and the character always looks the way
    its frames are drawn."""
    if isinstance(source.get("skins"), dict) or isinstance(target.get("skins"), dict):
        raise SpineError("Spine 3.x skeletons are not handled")
    if source.get("path"):
        raise SpineError("path constraints are not handled")
    src_anim = (source.get("animations") or {}).get(anim)
    if src_anim is None:
        raise SpineError(f"the rig has no animation {anim}")
    if set(src_anim) - ANIM_PARTS:
        raise SpineError(f"animation parts not handled: {sorted(set(src_anim) - ANIM_PARTS)}")
    d = copy.deepcopy(target)
    s = copy.deepcopy(source)
    B = {b["name"]: prefix + b["name"] for b in s["bones"]}
    S = {sl["name"]: prefix + sl["name"] for sl in s["slots"]}
    C = {c["name"]: prefix + c["name"] for kind in ("ik", "transform") for c in s.get(kind, [])}
    shift = len(d["bones"])                                   # weighted vertices name bones by index

    # the target's own parts go
    for sk in d.setdefault("skins", []):
        sk["attachments"] = {}
    for sl in d["slots"]:
        sl.pop("attachment", None)

    # the source's bones, slots and constraints join it
    root = d["bones"][0]["name"]
    new_bones = {}
    for b in s["bones"]:
        nb = dict(b, name=B[b["name"]])
        if b.get("parent"):
            nb["parent"] = B[b["parent"]]
        else:
            nb["parent"] = root
            nb["x"] = round(b.get("x", 0.0) + dx, 3)
            nb["y"] = round(b.get("y", 0.0) + dy, 3)
        d["bones"].append(nb)
        new_bones[nb["name"]] = nb
    for sl in s["slots"]:
        d["slots"].append(dict(sl, name=S[sl["name"]], bone=B[sl["bone"]]))
    ranked = [(0, c.get("order", 0), c) for kind in ("ik", "transform", "path") for c in d.get(kind, [])]
    new_cons = {}
    for kind in ("ik", "transform"):
        for c in s.get(kind, []):
            nc = dict(c, name=C[c["name"]], bones=[B[x] for x in c["bones"]], target=B[c["target"]])
            d.setdefault(kind, []).append(nc)
            new_cons[nc["name"]] = nc
            ranked.append((1, c.get("order", 0), nc))
    # the runtime only updates constraints whose order is below the constraint count
    for i, (_, _, c) in enumerate(sorted(ranked, key=lambda r: (r[0], r[1]))):
        c["order"] = i

    def moved(att):
        a = copy.deepcopy(att)
        if a.get("type") in VERTEX_TYPES:
            a["vertices"] = _map_vertices(a["vertices"], _vertex_count(a), bone=lambda i: i + shift)
        if a.get("type") == "clipping" and a.get("end"):
            a["end"] = S[a["end"]]
        return a

    def parts(atts):
        return {S[slot]: {n: moved(a) for n, a in aa.items()} for slot, aa in atts.items()}

    # skins
    src_skins = {sk["name"]: sk for sk in s.get("skins", [])}
    others = [n for n in src_skins if n != "default"]
    named = [sk["name"] for sk in d["skins"] if sk["name"] != "default"]
    pick = {n: skin_match(n, others) for n in named}
    full = bool(named) and target_skin in named and all(pick.values()) and pick[target_skin] == skin
    fold = ["default"] + ([skin] if not full and skin in others else [])
    homes = {n: ["default"] for n in fold}                   # source skin -> merged skins with its parts
    dflt = next((sk for sk in d["skins"] if sk["name"] == "default"), None)
    if dflt is None:
        dflt = {"name": "default", "attachments": {}}
        d["skins"].insert(0, dflt)
    for n in fold:
        if n not in src_skins:
            continue
        for slot, aa in parts(src_skins[n]["attachments"]).items():
            dflt["attachments"].setdefault(slot, {}).update(aa)
        if n != "default":                                    # its bones and constraints are always on now
            for x in src_skins[n].get("bones", []):
                new_bones[B[x]].pop("skin", None)
            for kind in ("ik", "transform"):
                for x in src_skins[n].get(kind, []):
                    new_cons[C[x]].pop("skin", None)
    if full:
        for sk in d["skins"]:
            sn = pick.get(sk["name"])
            if not sn:
                continue
            homes.setdefault(sn, []).append(sk["name"])
            sk["attachments"] = parts(src_skins[sn]["attachments"])
            for key, ren in (("bones", B), ("ik", C), ("transform", C)):
                if src_skins[sn].get(key):
                    sk.setdefault(key, []).extend(ren[x] for x in src_skins[sn][key])

    # linked meshes find their parent mesh in a named skin (default when unnamed)
    for sk in d["skins"]:
        for slot, aa in sk["attachments"].items():
            for n, a in list(aa.items()):
                if a.get("type") != "linkedmesh":
                    continue
                ps = a.get("skin") or "default"
                where = homes.get(ps, [])
                if sk["name"] in where:
                    a["skin"] = sk["name"]
                elif where:
                    a["skin"] = where[0]
                else:                                         # its parent's skin stayed behind: carry the mesh itself
                    parent = (src_skins.get(ps) or {}).get("attachments", {}).get(slot[len(prefix):], {}).get(a["parent"])
                    if parent is None or parent.get("type") != "mesh":
                        raise SpineError(f"linked mesh {n}: parent {a['parent']} not found")
                    m = moved(parent)
                    m.update({f: a[f] for f in ("name", "path", "color", "width", "height") if f in a})
                    aa[n] = m
                    continue
                if a["skin"] == "default":
                    a.pop("skin")

    # the animation, renamed; events stay behind (nothing in the game listens for these)
    new = {}
    if src_anim.get("bones"):
        new["bones"] = {B[k]: v for k, v in src_anim["bones"].items()}
    if src_anim.get("slots"):
        new["slots"] = {S[k]: v for k, v in src_anim["slots"].items()}
    for kind in ("ik", "transform"):
        if src_anim.get(kind):
            new[kind] = {C[k]: v for k, v in src_anim[kind].items()}
    deform = {}
    for sn, slots in src_anim.get("deform", {}).items():
        for home in homes.get(sn, []):
            for slot, atts in slots.items():
                deform.setdefault(home, {}).setdefault(S[slot], {}).update(copy.deepcopy(atts))
    if deform:
        new["deform"] = deform
    if src_anim.get("drawOrder"):
        new["drawOrder"] = [dict(k, offsets=[dict(o, slot=S[o["slot"]]) for o in k["offsets"]]) if "offsets" in k
                            else dict(k) for k in src_anim["drawOrder"]]
    want = list(target.get("animations") or {}) + [n for n in names if n]
    d["animations"] = {n: copy.deepcopy(new) for n in dict.fromkeys(want or ["animation"])}
    return d


def check(doc, regions):
    """Raise SpineError for anything the game's Spine loader would refuse: a name that
    points at a bone, slot, constraint, skin or part that isn't there, a weighted vertex on
    a bone that doesn't exist, or a picture missing from the atlas (`regions`)."""
    bones = set()
    for i, b in enumerate(doc["bones"]):
        if b["name"] in bones:
            raise SpineError(f"bone {b['name']} twice")
        if i and b.get("parent") not in bones:
            raise SpineError(f"bone {b['name']}: parent {b.get('parent')} missing or listed after it")
        bones.add(b["name"])
    slots = set()
    for sl in doc["slots"]:
        if sl["name"] in slots or sl["bone"] not in bones:
            raise SpineError(f"slot {sl['name']}: repeated, or on a missing bone")
        slots.add(sl["name"])
    cons, orders = {}, []
    for kind in ("ik", "transform", "path"):
        for c in doc.get(kind, []):
            if c["name"] in cons:
                raise SpineError(f"constraint {c['name']} twice")
            cons[c["name"]] = kind
            orders.append(c.get("order", 0))
            if set(c["bones"]) - bones or c["target"] not in (slots if kind == "path" else bones):
                raise SpineError(f"constraint {c['name']}: a bone it names is missing")
    if sorted(orders) != list(range(len(orders))):
        raise SpineError("constraint order has gaps or repeats")
    skins = {}
    for sk in skins_list(doc):
        if sk["name"] in skins:
            raise SpineError(f"skin {sk['name']} twice")
        skins[sk["name"]] = sk
        if set(sk.get("bones", [])) - bones or {x for k in ("ik", "transform", "path") for x in sk.get(k, [])} - set(cons):
            raise SpineError(f"skin {sk['name']}: names a missing bone or constraint")
    for sk in skins.values():
        for slot, atts in sk["attachments"].items():
            if slot not in slots:
                raise SpineError(f"skin {sk['name']}: no slot {slot}")
            for n, a in atts.items():
                t = a.get("type", "region")
                if t in ("region", "mesh", "linkedmesh") and (a.get("path") or a.get("name") or n) not in regions:
                    raise SpineError(f"picture {a.get('path') or a.get('name') or n} is not in the atlas")
                if t in VERTEX_TYPES:
                    used = []
                    _map_vertices(a["vertices"], _vertex_count(a), bone=lambda i: used.append(i) or i)
                    if any(i >= len(bones) for i in used):
                        raise SpineError(f"part {n}: its vertices name a missing bone")
                if t == "clipping" and a.get("end") and a["end"] not in slots:
                    raise SpineError(f"clipping {n}: no slot {a['end']}")
                if t == "linkedmesh":
                    home = skins.get(a.get("skin") or "default")
                    parent = (home or {}).get("attachments", {}).get(slot, {}).get(a["parent"])
                    if parent is None or parent.get("type") not in ("mesh", "linkedmesh"):
                        raise SpineError(f"linked mesh {n}: parent {a['parent']} not in skin {a.get('skin') or 'default'}")
    events = set(doc.get("events") or {})
    for an, a in (doc.get("animations") or {}).items():
        if set(a) - ANIM_PARTS - {"path"}:
            raise SpineError(f"animation {an}: parts not handled")
        for bn, tls in a.get("bones", {}).items():
            if bn not in bones or set(tls) - set(BONE_TIMELINES):
                raise SpineError(f"animation {an}: bone timeline for {bn}")
        for sn, tls in a.get("slots", {}).items():
            if sn not in slots or set(tls) - SLOT_TIMELINES:
                raise SpineError(f"animation {an}: slot timeline for {sn}")
        for kind in ("ik", "transform", "path"):
            for cn in a.get(kind, {}):
                if cons.get(cn) != kind:
                    raise SpineError(f"animation {an}: no {kind} constraint {cn}")
        for skn, per_slot in a.get("deform", {}).items():
            for sn, atts in per_slot.items():
                for n in atts:
                    att = skins.get(skn, {}).get("attachments", {}).get(sn, {}).get(n)
                    if att is None or att.get("type", "region") not in VERTEX_TYPES + ("linkedmesh",):
                        raise SpineError(f"animation {an}: deform for {skn}/{sn}/{n}, which is not there")
        for key in a.get("drawOrder", []):
            if {o["slot"] for o in key.get("offsets", [])} - slots:
                raise SpineError(f"animation {an}: draw order names a missing slot")
        if {e["name"] for e in a.get("events", [])} - events:
            raise SpineError(f"animation {an}: an event that is not defined")
    return doc


# --- posing a skeleton the way the runtime does (previews) ------------------------------
# A port of the parts of the Spine 4.0 runtime a preview needs: bone inheritance modes,
# one- and two-bone IK, transform constraints, and the order the runtime updates them in.

def _cos(deg):
    return math.cos(math.radians(deg))


def _sin(deg):
    return math.sin(math.radians(deg))


def _wrap(r, half):
    return r - 2 * half if r > half else r + 2 * half if r < -half else r


class _Bone:
    def __init__(self, data, parent):
        self.data, self.parent, self.children = data, parent, []
        self.mode = data.get("transform", "normal")
        self.length = data.get("length", 0.0)
        self.x, self.y = data.get("x", 0.0), data.get("y", 0.0)
        self.rotation = data.get("rotation", 0.0)
        self.scaleX, self.scaleY = data.get("scaleX", 1.0), data.get("scaleY", 1.0)
        self.shearX, self.shearY = data.get("shearX", 0.0), data.get("shearY", 0.0)
        self.active, self.sorted = True, False
        self.reset()
        self.a, self.b, self.c, self.d, self.wx, self.wy = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0

    def reset(self):
        self.ax, self.ay, self.arotation = self.x, self.y, self.rotation
        self.ascaleX, self.ascaleY, self.ashearX, self.ashearY = self.scaleX, self.scaleY, self.shearX, self.shearY

    def update(self):
        self.world(self.ax, self.ay, self.arotation, self.ascaleX, self.ascaleY, self.ashearX, self.ashearY)

    def world(self, x, y, rotation, scaleX, scaleY, shearX, shearY):
        self.ax, self.ay, self.arotation = x, y, rotation
        self.ascaleX, self.ascaleY, self.ashearX, self.ashearY = scaleX, scaleY, shearX, shearY
        p, m = self.parent, self.mode
        if p is None or m == "onlyTranslation":
            self.a, self.b = _cos(rotation + shearX) * scaleX, _cos(rotation + 90 + shearY) * scaleY
            self.c, self.d = _sin(rotation + shearX) * scaleX, _sin(rotation + 90 + shearY) * scaleY
            if p is None:
                self.wx, self.wy = x, y
            else:
                self.wx, self.wy = p.a * x + p.b * y + p.wx, p.c * x + p.d * y + p.wy
            return
        pa, pb, pc, pd = p.a, p.b, p.c, p.d
        self.wx, self.wy = pa * x + pb * y + p.wx, pc * x + pd * y + p.wy
        if m == "normal":
            la, lb = _cos(rotation + shearX) * scaleX, _cos(rotation + 90 + shearY) * scaleY
            lc, ld = _sin(rotation + shearX) * scaleX, _sin(rotation + 90 + shearY) * scaleY
            self.a, self.b, self.c, self.d = pa * la + pb * lc, pa * lb + pb * ld, pc * la + pd * lc, pc * lb + pd * ld
        elif m == "noRotationOrReflection":
            s = pa * pa + pc * pc
            if s > 0.0001:
                s = abs(pa * pd - pb * pc) / s
                pb, pd = pc * s, pa * s
                prx = math.degrees(math.atan2(pc, pa))
            else:
                pa = pc = 0.0
                prx = 90 - math.degrees(math.atan2(pd, pb))
            rx, ry = rotation + shearX - prx, rotation + shearY - prx + 90
            la, lb, lc, ld = _cos(rx) * scaleX, _cos(ry) * scaleY, _sin(rx) * scaleX, _sin(ry) * scaleY
            self.a, self.b, self.c, self.d = pa * la - pb * lc, pa * lb - pb * ld, pc * la + pd * lc, pc * lb + pd * ld
        else:                                                    # noScale, noScaleOrReflection
            za, zc = pa * _cos(rotation) + pb * _sin(rotation), pc * _cos(rotation) + pd * _sin(rotation)
            s = math.hypot(za, zc)
            if s > 0.00001:
                za, zc = za / s, zc / s
            s = math.hypot(za, zc)
            if m == "noScale" and pa * pd - pb * pc < 0:
                s = -s
            r = math.pi / 2 + math.atan2(zc, za)
            zb, zd = math.cos(r) * s, math.sin(r) * s
            la, lb = _cos(shearX) * scaleX, _cos(90 + shearY) * scaleY
            lc, ld = _sin(shearX) * scaleX, _sin(90 + shearY) * scaleY
            self.a, self.b, self.c, self.d = za * la + zb * lc, za * lb + zb * ld, zc * la + zd * lc, zc * lb + zd * ld

    def applied(self):
        """Local values back from the world transform, after a constraint moved it."""
        p = self.parent
        if p is None:
            self.ax, self.ay, self.arotation = self.wx, self.wy, math.degrees(math.atan2(self.c, self.a))
            self.ascaleX, self.ascaleY, self.ashearX = math.hypot(self.a, self.c), math.hypot(self.b, self.d), 0.0
            self.ashearY = math.degrees(math.atan2(self.a * self.b + self.c * self.d, self.a * self.d - self.b * self.c))
            return
        pid = 1 / (p.a * p.d - p.b * p.c)
        dx, dy = self.wx - p.wx, self.wy - p.wy
        self.ax, self.ay = (dx * p.d - dy * p.b) * pid, (dy * p.a - dx * p.c) * pid
        ia, id_, ib, ic = pid * p.d, pid * p.a, pid * p.b, pid * p.c
        ra, rb = ia * self.a - ib * self.c, ia * self.b - ib * self.d
        rc, rd = id_ * self.c - ic * self.a, id_ * self.d - ic * self.b
        self.ashearX, self.ascaleX = 0.0, math.hypot(ra, rc)
        if self.ascaleX > 0.0001:
            det = ra * rd - rb * rc
            self.ascaleY = det / self.ascaleX
            self.ashearY = math.degrees(math.atan2(ra * rb + rc * rd, det))
            self.arotation = math.degrees(math.atan2(rc, ra))
        else:
            self.ascaleX, self.ascaleY, self.ashearY = 0.0, math.hypot(rb, rd), 0.0
            self.arotation = 90 - math.degrees(math.atan2(rd, rb))


def _ik1(bone, tx, ty, compress, stretch, uniform, alpha):
    p = bone.parent
    pa, pb, pc, pd = p.a, p.b, p.c, p.d
    rot = -bone.ashearX - bone.arotation
    if bone.mode == "onlyTranslation":
        x, y = tx - bone.wx, ty - bone.wy
    else:
        if bone.mode == "noRotationOrReflection":
            s = abs(pa * pd - pb * pc) / (pa * pa + pc * pc)
            pb, pd = -pc * s, pa * s
            rot += math.degrees(math.atan2(pc, pa))
        x0, y0 = tx - p.wx, ty - p.wy
        d = pa * pd - pb * pc
        x, y = (x0 * pd - y0 * pb) / d - bone.ax, (y0 * pa - x0 * pc) / d - bone.ay
    rot += math.degrees(math.atan2(y, x))
    if bone.ascaleX < 0:
        rot += 180
    rot = _wrap(rot, 180)
    sx, sy = bone.ascaleX, bone.ascaleY
    if compress or stretch:
        if bone.mode in ("noScale", "noScaleOrReflection"):
            x, y = tx - bone.wx, ty - bone.wy
        b, dd = bone.length * sx, math.hypot(x, y)
        if (compress and dd < b) or (stretch and dd > b and b > 0.0001):
            s = (dd / b - 1) * alpha + 1
            sx *= s
            if uniform:
                sy *= s
    bone.world(bone.ax, bone.ay, bone.arotation + rot * alpha, sx, sy, bone.ashearX, bone.ashearY)


def _ik2(parent, child, tx, ty, bend, stretch, uniform, softness, alpha):
    px, py, psx, psy, csx = parent.ax, parent.ay, parent.ascaleX, parent.ascaleY, child.ascaleX
    sx, sy = psx, psy
    os1, s2 = (180, -1) if psx < 0 else (0, 1)
    psx = abs(psx)
    if psy < 0:
        psy, s2 = -psy, -s2
    os2 = 180 if csx < 0 else 0
    csx = abs(csx)
    cx, a, b, c, d = child.ax, parent.a, parent.b, parent.c, parent.d
    u = abs(psx - psy) <= 0.0001
    if not u or stretch:
        cy, cwx, cwy = 0.0, a * cx + parent.wx, c * cx + parent.wy
    else:
        cy = child.ay
        cwx, cwy = a * cx + b * cy + parent.wx, c * cx + d * cy + parent.wy
    pp = parent.parent
    a, b, c, d = pp.a, pp.b, pp.c, pp.d
    iden = 1 / (a * d - b * c)
    x, y = cwx - pp.wx, cwy - pp.wy
    dx, dy = (x * d - y * b) * iden - px, (y * a - x * c) * iden - py
    l1, l2 = math.hypot(dx, dy), child.length * csx
    if l1 < 0.0001:
        _ik1(parent, tx, ty, False, stretch, False, alpha)
        child.world(cx, cy, 0, child.ascaleX, child.ascaleY, child.ashearX, child.ashearY)
        return
    x, y = tx - pp.wx, ty - pp.wy
    tx, ty = (x * d - y * b) * iden - px, (y * a - x * c) * iden - py
    dd = tx * tx + ty * ty
    if softness:
        softness *= psx * (csx + 1) * 0.5
        td = math.sqrt(dd)
        sd = td - l1 - l2 * psy + softness
        if sd > 0:
            p = min(1.0, sd / (softness * 2)) - 1
            p = (sd - softness * (1 - p * p)) / td
            tx, ty = tx - p * tx, ty - p * ty
            dd = tx * tx + ty * ty
    a1 = a2 = 0.0
    if u:
        l2 *= psx
        cos = (dd - l1 * l1 - l2 * l2) / (2 * l1 * l2)
        if cos < -1:
            cos, a2 = -1.0, math.pi * bend
        elif cos > 1:
            cos, a2 = 1.0, 0.0
            if stretch:
                a = (math.sqrt(dd) / (l1 + l2) - 1) * alpha + 1
                sx *= a
                if uniform:
                    sy *= a
        else:
            a2 = math.acos(cos) * bend
        a, b = l1 + l2 * cos, l2 * math.sin(a2)
        a1 = math.atan2(ty * a - tx * b, tx * a + ty * b)
    else:
        a, b = psx * l2, psy * l2
        aa, bb, ta = a * a, b * b, math.atan2(ty, tx)
        c = bb * l1 * l1 + aa * dd - aa * bb
        c1, c2 = -2 * bb * l1, bb - aa
        d = c1 * c1 - 4 * c2 * c
        done = False
        if d >= 0 and c2:
            q = math.sqrt(d)
            if c1 < 0:
                q = -q
            q = -(c1 + q) * 0.5
            r0, r1 = q / c2, (c / q if q else float("inf"))
            r = r0 if abs(r0) < abs(r1) else r1
            if r * r <= dd:
                y = math.sqrt(dd - r * r) * bend
                a1 = ta - math.atan2(y, r)
                a2 = math.atan2(y / psy, (r - l1) / psx)
                done = True
        if not done:
            min_a, min_x, min_d, min_y = math.pi, l1 - a, (l1 - a) ** 2, 0.0
            max_a, max_x, max_d, max_y = 0.0, l1 + a, (l1 + a) ** 2, 0.0
            c = -a * l1 / (aa - bb) if aa != bb else 2.0
            if -1 <= c <= 1:
                c = math.acos(c)
                x, y = a * math.cos(c) + l1, b * math.sin(c)
                d = x * x + y * y
                if d < min_d:
                    min_a, min_d, min_x, min_y = c, d, x, y
                if d > max_d:
                    max_a, max_d, max_x, max_y = c, d, x, y
            if dd <= (min_d + max_d) * 0.5:
                a1, a2 = ta - math.atan2(min_y * bend, min_x), min_a * bend
            else:
                a1, a2 = ta - math.atan2(max_y * bend, max_x), max_a * bend
    os_ = math.atan2(cy, cx) * s2
    rot = parent.arotation
    a1 = _wrap(math.degrees(a1 - os_) + os1 - rot, 180)
    parent.world(px, py, rot + a1 * alpha, sx, sy, 0, 0)
    rot = child.arotation
    a2 = _wrap((math.degrees(a2 + os_) - child.ashearX) * s2 + os2 - rot, 180)
    child.world(cx, cy, rot + a2 * alpha, child.ascaleX, child.ascaleY, child.ashearX, child.ashearY)


def _transform(c, bones, mix):
    """A transform constraint: c is its data, mix its six mixes (rotate, x, y, scaleX,
    scaleY, shearY)."""
    target = bones[c["target"]]
    mr, mx, my, msx, msy, msh = mix
    orot, ox, oy = c.get("rotation", 0.0), c.get("x", 0.0), c.get("y", 0.0)
    osx, osy, osh = c.get("scaleX", 0.0), c.get("scaleY", 0.0), c.get("shearY", 0.0)
    local, relative = c.get("local", False), c.get("relative", False)
    ta, tb, tc, td = target.a, target.b, target.c, target.d
    refl = 1 if ta * td - tb * tc > 0 else -1
    for name in c["bones"]:
        bone = bones[name]
        if local:
            if relative:
                bone.world(bone.ax + (target.ax + ox) * mx, bone.ay + (target.ay + oy) * my,
                           bone.arotation + (target.arotation + orot) * mr,
                           bone.ascaleX * ((target.ascaleX - 1 + osx) * msx + 1),
                           bone.ascaleY * ((target.ascaleY - 1 + osy) * msy + 1),
                           bone.ashearX, bone.ashearY + (target.ashearY + osh) * msh)
            else:
                rot = bone.arotation + _wrap(target.arotation - bone.arotation + orot, 180) * mr
                sx, sy = bone.ascaleX, bone.ascaleY
                if msx and sx:
                    sx = sx + (target.ascaleX - sx + osx) * msx
                if msy and sy:
                    sy = sy + (target.ascaleY - sy + osy) * msy
                bone.world(bone.ax + (target.ax - bone.ax + ox) * mx, bone.ay + (target.ay - bone.ay + oy) * my,
                           rot, sx, sy, bone.ashearX,
                           bone.ashearY + _wrap(target.ashearY - bone.ashearY + osh, 180) * msh)
            continue
        if mr:
            r = math.atan2(tc, ta) + math.radians(orot) * refl
            if not relative:
                r -= math.atan2(bone.c, bone.a)
            r = _wrap(r, math.pi) * mr
            cs, sn = math.cos(r), math.sin(r)
            bone.a, bone.b, bone.c, bone.d = (cs * bone.a - sn * bone.c, cs * bone.b - sn * bone.d,
                                              sn * bone.a + cs * bone.c, sn * bone.b + cs * bone.d)
        if mx or my:
            wx, wy = ta * ox + tb * oy + target.wx, tc * ox + td * oy + target.wy
            if relative:
                bone.wx, bone.wy = bone.wx + wx * mx, bone.wy + wy * my
            else:
                bone.wx, bone.wy = bone.wx + (wx - bone.wx) * mx, bone.wy + (wy - bone.wy) * my
        if msx:
            s = math.hypot(bone.a, bone.c)
            if relative:
                s = (math.hypot(ta, tc) - 1 + osx) * msx + 1
            elif s:
                s = (s + (math.hypot(ta, tc) - s + osx) * msx) / s
            bone.a, bone.c = bone.a * s, bone.c * s
        if msy:
            s = math.hypot(bone.b, bone.d)
            if relative:
                s = (math.hypot(tb, td) - 1 + osy) * msy + 1
            elif s:
                s = (s + (math.hypot(tb, td) - s + osy) * msy) / s
            bone.b, bone.d = bone.b * s, bone.d * s
        if msh > 0:
            by = math.atan2(bone.d, bone.b)
            if relative:
                r = _wrap(math.atan2(td, tb) - math.atan2(tc, ta), math.pi)
                r = by + (r - math.pi / 2 + math.radians(osh) * refl) * msh
            else:
                r = _wrap(math.atan2(td, tb) - math.atan2(tc, ta) - (by - math.atan2(bone.c, bone.a)), math.pi)
                r = by + (r + math.radians(osh) * refl) * msh
            s = math.hypot(bone.b, bone.d)
            bone.b, bone.d = math.cos(r) * s, math.sin(r) * s
        bone.applied()


def _draw_order(n, index, offsets):
    """Spine's draw-order key: slots moved by offsets, the rest keep their order."""
    order, unchanged, orig = [-1] * n, [], 0
    for o in offsets:
        i = index[o["slot"]]
        while orig != i:
            unchanged.append(orig)
            orig += 1
        order[orig + o["offset"]] = orig
        orig += 1
    unchanged += range(orig, n)
    for i in range(n - 1, -1, -1):
        if order[i] == -1:
            order[i] = unchanged.pop()
    return order


def pose(doc, skin=None, anim=None, t=0.0, record=None):
    """The skeleton posed the way the Spine runtime poses it: animation `anim` at time t
    over the setup pose, then bones and constraints updated in the runtime's order.
    -> (world {bone: (a, b, c, d, x, y)}, [(slot, attachment name, colour)] in draw order,
    {slot name: deformed vertices}). record: a dict that gets each IK bone's local values
    just after its IK, (x, y, rotation, scaleX, scaleY, shearX, shearY)."""
    bones, ordered = {}, []
    for bd in doc["bones"]:
        b = _Bone(bd, bones.get(bd.get("parent")))
        if b.parent is not None:
            b.parent.children.append(b)
        bones[bd["name"]] = b
        ordered.append(b)
    a = (doc.get("animations") or {}).get(anim) or {}
    for name, tls in a.get("bones", {}).items():
        b = bones.get(name)
        if b is None:
            continue
        for tl, keys in tls.items():
            fields = BONE_TIMELINES.get(tl)
            if not fields:
                continue
            base = 1.0 if tl.startswith("scale") else 0.0
            v = _lerp_keys(keys, t, [f for f, _ in fields], [base] * len(fields))
            d = b.data
            if tl == "rotate":
                b.rotation = d.get("rotation", 0.0) + v[0]
            elif tl in ("translate", "translatex", "translatey"):
                if tl != "translatey":
                    b.x = d.get("x", 0.0) + v[0]
                if tl != "translatex":
                    b.y = d.get("y", 0.0) + v[-1]
            elif tl in ("scale", "scalex", "scaley"):
                if tl != "scaley":
                    b.scaleX = d.get("scaleX", 1.0) * v[0]
                if tl != "scalex":
                    b.scaleY = d.get("scaleY", 1.0) * v[-1]
            else:
                if tl != "sheary":
                    b.shearX = d.get("shearX", 0.0) + v[0]
                if tl != "shearx":
                    b.shearY = d.get("shearY", 0.0) + v[-1]
    # which bones and constraints this skin switches on
    sk = next((s for s in skins_list(doc) if s["name"] == skin), None) if skin else None
    for b in ordered:
        b.active = not b.data.get("skin")
    for name in (sk or {}).get("bones", []):
        b = bones.get(name)
        while b is not None:
            b.active, b = True, b.parent
    on_skin = set((sk or {}).get("ik", [])) | set((sk or {}).get("transform", []))
    cons = []
    for kind in ("ik", "transform"):
        for c in doc.get(kind, []):
            if bones[c["target"]].active and (not c.get("skin") or c["name"] in on_skin):
                cons.append((c.get("order", 0), kind, c))
    # the runtime's update order: a constraint after its target and its bones' parents,
    # and the children of the bones it moves after it
    cache = []

    def sort_bone(b):
        if b.sorted:
            return
        if b.parent is not None:
            sort_bone(b.parent)
        b.sorted = True
        cache.append(b)

    def sort_reset(children):
        for b in children:
            if b.active:
                if b.sorted:
                    sort_reset(b.children)
                b.sorted = False

    for b in ordered:
        b.sorted = not b.active
    for _, kind, c in sorted(cons, key=lambda x: x[0]):
        moved = [bones[n] for n in c["bones"]]
        sort_bone(bones[c["target"]])
        if kind == "ik":
            sort_bone(moved[0])
            if len(moved) > 1:
                sort_bone(moved[-1])
            cache.append((kind, c))
            sort_reset(moved[0].children)
            if len(moved) > 1:
                moved[-1].sorted = True
        else:
            for m in moved:
                if c.get("local"):
                    sort_bone(m.parent)
                sort_bone(m)
            cache.append((kind, c))
            for m in moved:
                sort_reset(m.children)
            for m in moved:
                m.sorted = True
    for b in ordered:
        sort_bone(b)
    for b in ordered:
        b.reset()
    for item in cache:
        if isinstance(item, _Bone):
            item.update()
            continue
        kind, c = item
        keys = a.get(kind, {}).get(c["name"])
        if kind == "ik":
            mix, soft = c.get("mix", 1.0), c.get("softness", 0.0)
            bend, comp, stretch = c.get("bendPositive", True), c.get("compress", False), c.get("stretch", False)
            if keys:
                mix, soft = _lerp_keys(keys, t, ["mix", "softness"], [1.0, 0.0])
                k = max((k for k in keys if k.get("time", 0) <= t), key=lambda k: k.get("time", 0), default=keys[0])
                bend, comp, stretch = k.get("bendPositive", True), k.get("compress", False), k.get("stretch", False)
            moved, target = [bones[n] for n in c["bones"]], bones[c["target"]]
            if mix and len(moved) == 1:
                _ik1(moved[0], target.wx, target.wy, comp, stretch, c.get("uniform", False), mix)
            elif mix:
                _ik2(moved[0], moved[1], target.wx, target.wy, 1 if bend else -1, stretch, c.get("uniform", False),
                     soft, mix)
            if record is not None:
                for m in moved:
                    record[m.data["name"]] = (m.ax, m.ay, m.arotation, m.ascaleX, m.ascaleY, m.ashearX, m.ashearY)
        else:
            mx = c.get("mixX", 1.0)
            msx = c.get("mixScaleX", 1.0)
            mix = [c.get("mixRotate", 1.0), mx, c.get("mixY", mx), msx, c.get("mixScaleY", msx), c.get("mixShearY", 1.0)]
            if keys:
                first = keys[0]
                kx, ksx = first.get("mixX", 1.0), first.get("mixScaleX", 1.0)
                mix = _lerp_keys(keys, t, ["mixRotate", "mixX", "mixY", "mixScaleX", "mixScaleY", "mixShearY"],
                                 [1.0, 1.0, kx, 1.0, ksx, 1.0])
            _transform(c, bones, mix)
    world = {n: (b.a, b.b, b.c, b.d, b.wx, b.wy) for n, b in bones.items() if b.active}
    # slots: attachment, colour and draw order at time t
    names = [s["name"] for s in doc["slots"]]
    state = {s["name"]: [s.get("attachment"), s.get("color", "ffffffff")] for s in doc["slots"]}
    for name, tls in a.get("slots", {}).items():
        if name not in state:
            continue
        keys = tls.get("attachment", [])
        if keys:
            cur = keys[0].get("name") if keys[0].get("time", 0) <= t else state[name][0]
            for k in keys:
                if k.get("time", 0) <= t:
                    cur = k.get("name")
            state[name][0] = cur
        c = _color_at(tls.get("rgba", []), t)
        if c:
            state[name][1] = c
        if tls.get("alpha"):
            al = _lerp_keys(tls["alpha"], t, ["value"], [1.0])[0]
            state[name][1] = state[name][1][:6] + "%02x" % max(0, min(255, round(al * 255)))
    order = list(range(len(names)))
    keys = [k for k in a.get("drawOrder", []) if k.get("time", 0) <= t]
    if keys and keys[-1].get("offsets"):
        order = _draw_order(len(names), {n: i for i, n in enumerate(names)}, keys[-1]["offsets"])
    deform = {}
    for skn, per_slot in a.get("deform", {}).items():
        for sn, atts in per_slot.items():
            att = state.get(sn, [None])[0]
            if skn not in (skin, "default") or att not in atts:
                continue
            base = find_attachment(doc, sn, att, skin)
            if base is None or base.get("type") != "mesh":
                continue
            deform[sn] = _deformed(base, atts[att], t)
    slots = [(names[i], state[names[i]][0], state[names[i]][1]) for i in order]
    return world, slots, deform


def _deformed(att, keys, t):
    """A mesh's vertices with a deform timeline's offsets at time t added: to plain
    vertices one to one, to weighted ones per bone influence."""
    v = att["vertices"]
    plain = len(v) == len(att["uvs"])
    size = len(v) if plain else 2 * len(_influences(v))

    def offsets(k):
        out = [0.0] * size
        for i, x in enumerate(k.get("vertices", []), k.get("offset", 0)):
            if i < size:
                out[i] = x
        return out
    prev, off = keys[0], None
    if t > prev.get("time", 0):
        for k in keys[1:]:
            if t < k.get("time", 0):
                t0, t1, cv = prev.get("time", 0), k["time"], prev.get("curve")
                if cv != "stepped" and t1 > t0:
                    u = (t - t0) / (t1 - t0)
                    if isinstance(cv, list) and len(cv) >= 4:
                        u = _bezier(t, t0, 0.0, cv[0], cv[1], cv[2], cv[3], t1, 1.0)
                    off = [a + (b - a) * u for a, b in zip(offsets(prev), offsets(k))]
                break
            prev = k
    if off is None:
        off = offsets(prev)
    if plain:
        return [a + b for a, b in zip(v, off)]
    out = list(v)
    for f, i in enumerate(_influences(v)):
        out[i + 1] += off[2 * f]
        out[i + 2] += off[2 * f + 1]
    return out


def _influences(v):
    """Where each bone influence starts in a weighted vertex list."""
    out, i = [], 0
    while i < len(v):
        n = int(v[i])
        out += [i + 1 + 4 * k for k in range(n)]
        i += 1 + 4 * n
    return out


def _apply(m, x, y):
    a, b, c, d, tx, ty = m
    return a * x + b * y + tx, c * x + d * y + ty


def setup_triangles(doc, skin=None, anim=None, t=0.0):
    """The skeleton posed at time t of `anim` (setup pose without one), as triangles:
    [(region name, [(world x, y)], [(u, v) in the upright region, 0..1, v down], triangles, alpha)]."""
    world, slots, deform = pose(doc, skin, anim, t)
    bones = [b["name"] for b in doc["bones"]]
    bone_of = {s["name"]: s["bone"] for s in doc["slots"]}
    out = []
    for slot, an, color in slots:
        if not an or bone_of[slot] not in world:
            continue
        att = find_attachment(doc, slot, an, skin)
        if att is None:
            continue
        typ = att.get("type", "region")
        alpha = int(color[6:8], 16) / 255 if len(color) == 8 else 1.0
        m = world[bone_of[slot]]
        region = att.get("path") or att.get("name") or an
        if typ == "region":
            w, h = att["width"], att["height"]
            r = math.radians(att.get("rotation", 0.0))
            sx, sy = att.get("scaleX", 1.0), att.get("scaleY", 1.0)
            ox, oy = att.get("x", 0.0), att.get("y", 0.0)
            pts = []
            for lx, ly in ((-w / 2, -h / 2), (-w / 2, h / 2), (w / 2, h / 2), (w / 2, -h / 2)):
                lx, ly = lx * sx, ly * sy
                pts.append(_apply(m, ox + lx * math.cos(r) - ly * math.sin(r), oy + lx * math.sin(r) + ly * math.cos(r)))
            out.append((region, pts, [(0, 1), (0, 0), (1, 0), (1, 1)], [(0, 1, 2), (0, 2, 3)], alpha))
        elif typ in ("mesh", "linkedmesh"):
            src = att
            if typ == "linkedmesh":
                src = find_attachment(doc, slot, att["parent"], att.get("skin"))
                if src is None:
                    continue
            uvs = src["uvs"]
            n = len(uvs) // 2
            v = deform.get(slot, src["vertices"])
            pts = []
            if len(v) == len(uvs):
                pts = [_apply(m, v[2 * i], v[2 * i + 1]) for i in range(n)]
            else:
                i = 0
                while i < len(v):
                    cnt = int(v[i])
                    i += 1
                    wx = wy = 0.0
                    for _ in range(cnt):
                        bi, bx, by, wt = int(v[i]), v[i + 1], v[i + 2], v[i + 3]
                        i += 4
                        if bones[bi] in world:
                            px, py = _apply(world[bones[bi]], bx, by)
                            wx += px * wt
                            wy += py * wt
                    pts.append((wx, wy))
            tris = [tuple(src["triangles"][k:k + 3]) for k in range(0, len(src["triangles"]), 3)]
            out.append((region, pts, [(uvs[2 * i], uvs[2 * i + 1]) for i in range(n)], tris, alpha))
    return out


def render(doc, pages, textures, skin=None, ppu=0.45, pma=None, anim=None, t=0.0):
    """The skeleton at time t of `anim` (setup pose without one) -> (RGBA image, (origin x,
    origin y) in image pixels). textures: {page name: PIL image}. ppu: image pixels per
    skeleton unit."""
    from PIL import Image, ImageDraw
    regions = {}
    for p in pages:
        tex = textures.get(p["name"])
        if tex is None:
            continue
        pm = dict(p["header"]).get("pma", "false") == "true" if pma is None else pma
        for r in p["regions"]:
            regions[r["name"]] = (r, p, tex, pm)
    tris = setup_triangles(doc, skin, anim, t)
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
        texs, pma = {}, None
        for mref in at["materials"]:
            mt = tt(mref["m_PathID"])
            for k, v in mt["m_SavedProperties"]["m_TexEnvs"]:
                if k == "_MainTex" and v["m_Texture"]["m_PathID"] and not v["m_Texture"]["m_FileID"]:
                    tex = objs[v["m_Texture"]["m_PathID"]]
                    texs[tex.read().m_Name] = tex
            floats = dict(mt["m_SavedProperties"].get("m_Floats", []))
            if "_StraightAlphaInput" in floats:               # the shader's word on premultiplied alpha
                pma = floats["_StraightAlphaInput"] == 0
        m_go = go_matrix(anim["m_GameObject"]["m_PathID"])
        s = sd["scale"]
        fx = -1 if anim.get("initialFlipX") else 1
        fy = -1 if anim.get("initialFlipY") else 1
        to_skel = mul((fx / s, 0, 0, fy / s, 0, 0), mul(inv(m_go), m_sr))
        out.append({"skeleton": objs[sd["skeletonJSON"]["m_PathID"]], "atlas": objs[at["atlasFile"]["m_PathID"]],
                    "textures": texs, "scale": s, "skin": anim.get("initialSkinName") or None,
                    "to_skel": to_skel, "name": sd["m_Name"], "pma": pma,
                    "animation": anim.get("_animationName") or None, "time_scale": anim.get("timeScale") or 1.0})
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

def _bezier(t, t0, v0, cx1, cy1, cx2, cy2, t1, v1):
    """A Spine 4 curve: value at time t on the Bezier through (t0, v0), the two handles
    (absolute time, value) and (t1, v1)."""
    lo, hi = 0.0, 1.0
    for _ in range(30):
        s = (lo + hi) / 2
        x = (1 - s) ** 3 * t0 + 3 * (1 - s) ** 2 * s * cx1 + 3 * (1 - s) * s * s * cx2 + s ** 3 * t1
        lo, hi = (s, hi) if x < t else (lo, s)
    s = (lo + hi) / 2
    return (1 - s) ** 3 * v0 + 3 * (1 - s) ** 2 * s * cy1 + 3 * (1 - s) * s * s * cy2 + s ** 3 * v1


def _lerp_keys(keys, t, fields, defaults):
    """Value of a timeline at t: along each key's curve, stepped where marked, straight
    when it has none. A field a key leaves out takes its default."""
    if not keys:
        return list(defaults)
    prev = keys[0]
    if t <= prev.get("time", 0):
        return [prev.get(f, d) for f, d in zip(fields, defaults)]
    for k in keys[1:]:
        if t < k.get("time", 0):
            t0, t1, cv = prev.get("time", 0), k.get("time", 0), prev.get("curve")
            if cv == "stepped" or t1 <= t0:
                return [prev.get(f, d) for f, d in zip(fields, defaults)]
            u = (t - t0) / (t1 - t0)
            out = []
            for ch, (f, d) in enumerate(zip(fields, defaults)):
                a, b = prev.get(f, d), k.get(f, d)
                if not isinstance(a, (int, float)):
                    out.append(a)
                elif isinstance(cv, list) and len(cv) >= 4 * ch + 4:
                    out.append(_bezier(t, t0, a, *cv[4 * ch:4 * ch + 4], t1, b))
                else:
                    out.append(a + (b - a) * u)
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
