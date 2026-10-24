"""Repaint an identity's own battle body with another character's drawings.

Swapping an identity onto another character's body crashes battles (those bodies lack the
per-uptie data an identity needs). So the identity keeps its own body - animations,
timelines, skills and upties exactly as the game expects - and only the pictures change:
every frame is pointed at the matching frame of the other character (idle->idle,
guard->guard, skill 2 frame 7 -> the frame at the same point of their skill 2), packed
into the body's own atlas pages. A head recipe can instead paint a new head over the
identity's own frames.
"""
import os
import re
import struct

from .game import unitypy


class RepaintError(Exception):
    pass


KINDS = ("idle", "move", "gud", "miss", "dmg", "dead")
CLIP_KIND = {"Default": "idle", "Move": "move", "Guard": "gud", "Evade": "miss",
             "Damaged": "dmg", "Dead": "dead", "Retire": "dead"}


def prefab_objects(env, appearance):
    """Path ids of every object under the appearance prefab (so a bundle that holds
    several characters only gives us this one's clips)."""
    for cp, ptr in env.container.items():
        if os.path.basename(cp)[:-7] != appearance:
            continue
        objs = ptr.assetsfile.objects
        root = objs.get(ptr.m_PathID)
        if root is None or root.type.name != "GameObject":
            continue
        return objs, root
    raise RepaintError(f"{appearance}: body not found in its bundle")


def clip_frames(env, objs, wanted=None):
    """{clip name: [sprite reader, ...]} in play order, sprites local to the bundle."""
    out = {}
    for o in env.objects:
        if o.type.name != "AnimationClip":
            continue
        t = o.read_typetree()
        name = t["m_Name"]
        if name.startswith("FX") or (wanted and not wanted(name)):
            continue
        pm = t.get("m_ClipBindingConstant", {}).get("pptrCurveMapping", [])
        sp = [o.assets_file.objects.get(p["m_PathID"]) for p in pm if p.get("m_FileID") == 0]
        sp = [s for s in sp if s is not None and s.type.name == "Sprite"]
        if sp:
            out.setdefault(name, sp)
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
    return None, 0


def source_table(env, objs, prefix=None, overrides=None):
    """Source frames by kind: {"idle": [img...], "S1": [img...], ...}."""
    clips = clip_frames(env, objs, (lambda n: n.startswith(prefix)) if prefix else None)
    table = {}
    for name, sprites in clips.items():
        tail = name[len(prefix):] if prefix else name
        tail = tail.lstrip("_")
        kind = None
        for word, k in CLIP_KIND.items():
            if tail.endswith(word):
                kind = k
        m = re.search(r"(?:^|_)S?(\d)$", tail)
        if kind is None and m:
            kind = "S" + m.group(1)
        if kind and kind not in table:
            body = [x for x in sprites if not kind_of(x.read().m_Name)[0] or not kind_of(x.read().m_Name)[0].endswith("e")]
            fx = [x for x in sprites if x not in body]
            table[kind] = body or sprites
            if kind.startswith("S") and fx:
                table[kind + "e"] = fx
    every = clip_frames(env, objs) if overrides else {}
    for k, clip in (overrides or {}).items():
        sp = every[clip]
        body = [x for x in sp if not (kind_of(x.read().m_Name)[0] or "").endswith("e")]
        table[k] = body or sp
        fx = [x for x in sp if x not in body]
        if fx:
            table[k + "e"] = fx
    return table


def render(sprite_reader):
    s = sprite_reader.read()
    img = s.image.convert("RGBA")
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


def paint_head(frame, spec, art, width=1.9):
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




def repaint(target, locate, source=None, clips=None, mapping=None, head=None):
    """Repaint body `target` (from `source`'s drawings, or its own with a `head` recipe).
    locate(body name) -> path of the cached bundle holding it. Returns (bundle bytes,
    notes). The bytes still need bundle.forge() to carry the stock CRC."""
    notes = []
    UnityPy = unitypy()
    from PIL import Image
    if source is None:
        src = {}
    else:
        senv = UnityPy.load(locate(source))
        sobjs, _ = prefab_objects(senv, source)
        src = source_table(senv, sobjs, clips, mapping or {})
        missing_kinds = [k for k in ("idle", "S1", "S2", "S3") if k not in src]
        if missing_kinds:
            raise RepaintError(f"{source} has no frames for {missing_kinds} (found {sorted(src)}) - set clips/map")

    tpath = locate(target)
    tenv = UnityPy.load(tpath)
    tobjs, _ = prefab_objects(tenv, target)
    tclips = clip_frames(tenv, tobjs)

    # target sprites -> (kind, position along that kind's animation)
    order = {}
    for name, sprites in tclips.items():
        for i, s in enumerate(sprites):
            k, _ = kind_of(s.read().m_Name)
            if k and k.startswith("S"):
                seq = order.setdefault(k, [])
                if s.path_id not in seq:
                    seq.append(s.path_id)
    targets = [o for o in tenv.objects if o.type.name == "Sprite"]
    plan = []                                            # (target reader, source reader)
    for o in targets:
        k, _ = kind_of(o.read().m_Name)
        if not k:
            continue
        if source is None:
            if o.read_typetree()["m_SpriteAtlas"]["m_FileID"] == 0:
                plan.append((o, o))
            continue
        if k.startswith("S") and k.endswith("e") and k not in src:
            continue                                     # keep the identity's own effect art
        pool = src.get(k)
        if pool is None and k.startswith("S") and not k.endswith("e"):
            n = int(k[1:])                               # a skill the source lacks: reuse one of its first three
            pool = src.get(f"S{(n - 1) % 3 + 1}")
        pool = pool or src.get("dmg" if k == "dead" else "idle")
        if k.startswith("S") and k in order and o.path_id in order[k]:
            seq = order[k]
            f = seq.index(o.path_id) / max(1, len(seq) - 1)
        else:
            f = 0.0
        plan.append((o, pool[round(f * (len(pool) - 1))]))
    if not plan:
        raise RepaintError(f"{target}: no frame drawings found (a Spine body?)")
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
                rendered[s.path_id] = render(s)
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
                new, (left, top) = paint_head(img, spec, art, recipe.get("width", 1.9))
                # the canvas may have grown left/up; keep the pivot on the same body pixel
                px = (sd.m_Pivot.x * img.width + left) / new.width
                py = (sd.m_Pivot.y * img.height + (new.height - img.height - top)) / new.height
                rendered[s.path_id] = (new, sd, (px, py))
                painted += 1
        notes.append(f"head painted on {painted} frames")

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
    for o in targets:
        t = o.read_typetree()
        refs = {t["m_RD"]["texture"]["m_PathID"]}
        key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
        for at in amaps.values():
            for k, v in at["m_RenderDataMap"]:
                if (tuple(k[0].values()), k[1]) == key:
                    refs.add(v["texture"]["m_PathID"])
        for r in refs - {0}:
            users.setdefault(r, set()).add(o.path_id)
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
    by_key = {}
    for aid, t in amaps.items():
        for n, (k, v) in enumerate(t["m_RenderDataMap"]):
            by_key[(tuple(k[0].values()), k[1])] = (aid, n)
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
        if key in by_key:
            aid, n = by_key[key]
            v = amaps[aid]["m_RenderDataMap"][n][1]
            v.update({k: val for k, val in rd.items() if k in v})
    for o in atlas_objs:
        o.save_typetree(amaps[o.path_id])
    for i, p in enumerate(pages):
        d = p.read()
        d.set_image(canvases[i], target_format=UnityPy.enums.TextureFormat.RGBA32)
        d.save()


    scale_note = "" if scale == 1 else f" (scaled {scale:.2f} to fit)"
    notes.insert(0, f"{len(plan)} frames from {len(rendered)} drawings{scale_note}")
    return tenv.file.save(packer="none"), notes


def preview(bundle_bytes_or_path, out_png, kinds=("idle", "S1", "S2", "S3", "gud", "dmg"), count=12):
    """A contact sheet of repainted frames, rendered back from the written bundle."""
    UnityPy = unitypy()
    from PIL import Image
    env = UnityPy.load(bundle_bytes_or_path)
    shots = []
    for o in env.objects:
        if o.type.name == "Sprite":
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
