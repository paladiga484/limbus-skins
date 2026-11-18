"""Lobotomy Corporation characters as pose sets for limbus-skins (data/lor/<prefab>/).

Lobotomy Corporation draws its abnormalities three ways, and this reads all of them from the
player's own install (LobotomyCorp_Data/resources.assets):
- skinned rigs (meshes bent by bones, e.g. Nothing There) and sprite-part rigs (e.g. WhiteNight's
  apostles), posed by their own Mecanim clips (streamed, dense and constant curves);
- Spine skeletons (e.g. WhiteNight, the Long Bird), drawn by limbus_skins.spine.

Each pose is drawn, turned to face left (as Library of Ruina's are, which is what repaint expects)
and written with its pivot (the rig's origin; feet on the drawing's bottom) into poses.json,
the same shipped format lor.ship() writes.

    python tools/lobrig.py find <regex>                 GameObjects and clips whose name matches
    python tools/lobrig.py clips <gameobject id>        the clips of its Animator
    python tools/lobrig.py rig <prefab> <gameobject id> <pose>=<clip>@<seconds> ...
    python tools/lobrig.py spine <prefab> <SkeletonDataAsset id> <pose>=<animation>@<fraction> ...

How the shipped sets were made (2026-11-18):
    rig NothingThere1 43802  Default=변신1_default_Baked@0 Standing1..4=변신1_default_Baked@0,.27,.54,.81
        Run1..4=변신1_달림_Baked@0,.18,.36,.54 AtkA1..3=변신1_공격1_Baked@.1,.27,.44
        AtkB1..3=변신1_공격2_Baked@.1,.27,.44 Dead1..3=변신1_죽음_Baked@.3,.75,1.2
    rig NothingThere2 36503  (아무것도없는_변신2_*: default, 이동, 평타2, 스킬1, 스킬2_로드호그, 데드신)
    rig Apostle 19552        (Apostle_돌격_준비/시작/유지/종료, 원거리기모으기3, 원거리기공격, 근거리_데드신)
    spine WhiteNight 106084  (0_Default_outsidde, 1_Default_outsidde_special, Dead)
A set is named lobotomy/<prefab, lower case> in poses.json; the Long and Small Birds' older sets keep
the names they were cut with (lobotomy/긴새, lobotomy/smallbird).
"""
import json
import math
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from limbus_skins import game, paths  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "limbus_skins", "data", "lor")
GIZMOS = {"IKControl", "parentControl", "ffdBone", "Transparent"}     # rig handles, never drawn in game


def assets_path():
    for root in paths.steam_roots():
        p = os.path.join(root, "steamapps", "common", "LobotomyCorp", "LobotomyCorp_Data", "resources.assets")
        if os.path.isfile(p):
            return p
    raise SystemExit("Lobotomy Corporation isn't installed through Steam here")


# ---------------------------------------------------------------- 4x4 maths

def mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def trs(p, q, s):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], p[i]] for i in range(3)] + [[0, 0, 0, 1]]


def apply(m, v):
    x, y, z = v
    return tuple(m[i][0] * x + m[i][1] * y + m[i][2] * z + m[i][3] for i in range(3))


def euler_quat(e):
    """Unity's localEulerAnglesRaw (degrees, applied Z, X, Y) as a quaternion."""
    x, y, z = (math.radians(v) / 2 for v in e)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return (sx * cy * cz + cx * sy * sz, cx * sy * cz - sx * cy * sz, cx * cy * sz - sx * sy * cz, cx * cy * cz + sx * sy * sz)


# ---------------------------------------------------------------- clips

POS, ROT, SCALE, EULER = 1, 2, 3, 4
ENABLED = zlib.crc32(b"m_Enabled")


class Clip:
    """A Mecanim clip, sampled: {binding: values} at any time."""

    def __init__(self, tree):
        mc = tree["m_MuscleClip"]
        data = mc["m_Clip"]["data"]
        self.start, self.length = mc["m_StartTime"], mc["m_StopTime"] - mc["m_StartTime"]
        self.nstream = data["m_StreamedClip"]["curveCount"]
        dense = data["m_DenseClip"]
        self.ndense = dense["m_CurveCount"]
        self.dense = (dense["m_FrameCount"], dense["m_SampleRate"], dense["m_BeginTime"], dense["m_SampleArray"])
        self.const = data["m_ConstantClip"]["data"]
        self.bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        self.keys = {}                          # streamed curve -> [(frame time, cubic coefficients)]
        words = data["m_StreamedClip"]["data"]
        raw, pos = struct.pack(f"<{len(words)}I", *words), 0
        while pos + 8 <= len(raw):
            t, n = struct.unpack_from("<fI", raw, pos)
            pos += 8
            for _ in range(n):
                idx, a, b, c, d = struct.unpack_from("<i4f", raw, pos)
                pos += 20
                self.keys.setdefault(idx, []).append((t, (a, b, c, d)))

    def value(self, i, t):
        if i < self.nstream:
            keys = self.keys.get(i)
            if not keys:
                return 0.0
            k = keys[0]
            for kk in keys:
                if kk[0] > t and kk[0] != float("-inf"):
                    break
                k = kk
            dt = 0.0 if k[0] == float("-inf") else t - k[0]
            a, b, c, d = k[1]
            return ((a * dt + b) * dt + c) * dt + d
        i -= self.nstream
        if i < self.ndense:
            n, rate, begin, arr = self.dense
            f = min(n - 1, max(0, int(round((t - begin) * rate))))
            return arr[f * self.ndense + i]
        return self.const[i - self.ndense]

    def sample(self, t):
        """[(binding, values)] at t seconds into the clip."""
        t = self.start + min(max(t, 0.0), self.length)
        out, i = [], 0
        for b in self.bindings:
            n = {POS: 3, ROT: 4, SCALE: 3, EULER: 3}.get(b["attribute"], 1) if b["typeID"] == 4 else 1
            out.append((b, [self.value(i + k, t) for k in range(n)]))
            i += n
        return out


# ---------------------------------------------------------------- rigs

class Rig:
    """A GameObject's drawing (skinned meshes, meshes and sprites under it), posed by a clip."""

    def __init__(self, env, root_id):
        self.objs = {o.path_id: o for o in env.objects}
        self.root = root_id
        self._tt = {}
        self.pose = {}                          # transform id -> {"p", "q", "e", "s"}
        self.off = set()                        # gameobjects whose renderer the clip switches off
        self.paths = {}
        self._paths(root_id, "")

    def tt(self, pid):
        if pid not in self._tt:
            self._tt[pid] = self.objs[pid].read_typetree()
        return self._tt[pid]

    def comp(self, go, kind):
        for c in go["m_Component"]:
            o = self.objs.get(c["component"]["m_PathID"])
            if o is not None and o.type.name == kind:
                return o
        return None

    def transform(self, go):
        return (self.comp(go, "Transform") or self.comp(go, "RectTransform")).path_id

    def _paths(self, gid, path):
        go = self.tt(gid)
        tid = self.transform(go)
        self.paths[zlib.crc32(path.encode("utf-8")) if path else 0] = (tid, gid)
        for ch in self.tt(tid)["m_Children"]:
            cg = self.tt(ch["m_PathID"])["m_GameObject"]["m_PathID"]
            self._paths(cg, (path + "/" if path else "") + self.tt(cg)["m_Name"])

    def set_clip(self, clip, t):
        self.pose, self.off = {}, set()
        for b, vals in clip.sample(t):
            where = self.paths.get(b["path"])
            if where is None:
                continue
            tid, gid = where
            if b["typeID"] == 4:
                key = {POS: "p", ROT: "q", SCALE: "s", EULER: "e"}.get(b["attribute"])
                if key:
                    self.pose.setdefault(tid, {})[key] = tuple(vals)
            elif b["attribute"] == ENABLED and vals[0] < 0.5:
                self.off.add(gid)

    def world(self, tid, memo):
        if tid in memo:
            return memo[tid]
        t = self.tt(tid)
        o = self.pose.get(tid, {})
        p = o.get("p") or tuple(t["m_LocalPosition"][k] for k in "xyz")
        q = o.get("q") or (euler_quat(o["e"]) if "e" in o else tuple(t["m_LocalRotation"][k] for k in "xyzw"))
        s = o.get("s") or tuple(t["m_LocalScale"][k] for k in "xyz")
        m = trs(p, q, s)
        if t["m_Father"]["m_PathID"]:
            m = mmul(self.world(t["m_Father"]["m_PathID"], memo), m)
        memo[tid] = m
        return m

    def triangles(self):
        """[(sort key, texture, three world points, three texture points)] of everything drawn."""
        from PIL import Image
        from UnityPy.helpers.MeshHelper import MeshHandler
        memo, out, order = {}, [], [0]
        textures = {}

        def texture(mat_id):
            if mat_id not in textures:
                textures[mat_id] = None
                for k, v in self.tt(mat_id)["m_SavedProperties"]["m_TexEnvs"]:
                    if k == "_MainTex" and v["m_Texture"]["m_PathID"]:
                        textures[mat_id] = self.objs[v["m_Texture"]["m_PathID"]].read().image.convert("RGBA")
            return textures[mat_id]

        def walk(gid):
            go = self.tt(gid)
            if not go.get("m_IsActive", 1):
                return
            tid = self.transform(go)
            order[0] += 1
            drawn = gid not in self.off
            rend = self.comp(go, "SkinnedMeshRenderer") or self.comp(go, "MeshRenderer")
            if drawn and rend is not None:
                r = rend.read_typetree()
                if rend.type.name == "SkinnedMeshRenderer":
                    mesh = self.objs.get(r["m_Mesh"]["m_PathID"])
                else:
                    mf = self.comp(go, "MeshFilter")
                    mesh = self.objs.get(mf.read_typetree()["m_Mesh"]["m_PathID"]) if mf else None
                if r.get("m_Enabled", 1) and mesh is not None:
                    m = mesh.read()
                    h = MeshHandler(m)
                    h.process()
                    if rend.type.name == "SkinnedMeshRenderer" and r["m_Bones"]:
                        binds = [[[getattr(b, f"e{i}{j}") for j in range(4)] for i in range(4)] for b in m.m_BindPose]
                        skin = [mmul(self.world(b["m_PathID"], memo), binds[i]) if b["m_PathID"] else None
                                for i, b in enumerate(r["m_Bones"])]
                        pts = []
                        for vi, v in enumerate(h.m_Vertices):
                            acc, tot = [0.0, 0.0, 0.0], 0.0
                            for w, i in zip(h.m_BoneWeights[vi], h.m_BoneIndices[vi]):
                                if w and i < len(skin) and skin[i] is not None:
                                    p = apply(skin[i], v)
                                    acc = [acc[k] + w * p[k] for k in range(3)]
                                    tot += w
                            pts.append(tuple(a / tot for a in acc) if tot else apply(self.world(tid, memo), v))
                    else:
                        pts = [apply(self.world(tid, memo), v) for v in h.m_Vertices]
                    mats = [x["m_PathID"] for x in r["m_Materials"]]
                    for si, sub in enumerate(h.get_triangles()):
                        tex = texture(mats[min(si, len(mats) - 1)]) if mats else None
                        if tex is None:
                            continue
                        W, H = tex.size
                        for a, b, c in sub:
                            z = (pts[a][2] + pts[b][2] + pts[c][2]) / 3
                            out.append(((r.get("m_SortingOrder", 0), -z, order[0]), tex, [pts[a][:2], pts[b][:2], pts[c][:2]],
                                        [(h.m_UV0[k][0] * W, (1 - h.m_UV0[k][1]) * H) for k in (a, b, c)]))
            sr = self.comp(go, "SpriteRenderer")
            if drawn and sr is not None:
                r = sr.read_typetree()
                so = self.objs.get(r["m_Sprite"]["m_PathID"])
                if r.get("m_Enabled", 1) and so is not None and go["m_Name"] != "Shadow" and so.peek_name() not in GIZMOS:
                    s = so.read()
                    img = s.image.convert("RGBA")
                    col = r.get("m_Color") or {}
                    if any(abs(col.get(k, 1.0) - 1.0) > 1e-3 for k in "rgba"):
                        img = Image.merge("RGBA", [b.point(lambda v, m=col.get(k, 1.0): int(v * m))
                                                   for b, k in zip(img.split(), "rgba")])
                    ppu = float(s.m_PixelsToUnits)
                    w, hh = img.width / ppu, img.height / ppu
                    px, py = s.m_Pivot.x * w, s.m_Pivot.y * hh
                    m = self.world(tid, memo)
                    wc = [apply(m, c) for c in ((-px, -py, 0), (w - px, -py, 0), (w - px, hh - py, 0), (-px, hh - py, 0))]
                    key = (r.get("m_SortingOrder", 0), -sum(c[2] for c in wc) / 4, order[0])
                    uv = [(0, img.height), (img.width, img.height), (img.width, 0), (0, 0)]
                    out.append((key, img, [wc[0][:2], wc[1][:2], wc[2][:2]], [uv[0], uv[1], uv[2]]))
                    out.append((key, img, [wc[0][:2], wc[2][:2], wc[3][:2]], [uv[0], uv[2], uv[3]]))
            for ch in self.tt(tid)["m_Children"]:
                walk(self.tt(ch["m_PathID"])["m_GameObject"]["m_PathID"])

        walk(self.root)
        return out


def draw(tris, scale=100.0):
    """(picture, (origin x, origin y) as fractions, bottom-up) of the triangles, orthographic."""
    from PIL import Image, ImageDraw
    xs = [p[0] for t in tris for p in t[2]]
    ys = [p[1] for t in tris for p in t[2]]
    minx, maxy = min(xs), max(ys)
    W, H = int((max(xs) - minx) * scale) + 8, int((maxy - min(ys)) * scale) + 8
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    def to(p):
        return (p[0] - minx) * scale + 4, (maxy - p[1]) * scale + 4

    for _, tex, wp, uv in sorted(tris, key=lambda t: t[0]):
        dst = [to(p) for p in wp]
        x0, y0 = int(min(p[0] for p in dst)), int(min(p[1] for p in dst))
        x1, y1 = int(math.ceil(max(p[0] for p in dst))) + 1, int(math.ceil(max(p[1] for p in dst))) + 1
        local = [(x - x0, y - y0) for x, y in dst]
        (a0, b0), (a1, b1), (a2, b2) = local
        det = (a1 - a0) * (b2 - b0) - (a2 - a0) * (b1 - b0)
        if x1 - x0 < 1 or y1 - y0 < 1 or abs(det) < 1e-9:
            continue
        co = []
        for k in (0, 1):                        # each output pixel -> its point on the texture
            s0, s1, s2 = uv[0][k], uv[1][k], uv[2][k]
            a = ((s1 - s0) * (b2 - b0) - (s2 - s0) * (b1 - b0)) / det
            b = ((s2 - s0) * (a1 - a0) - (s1 - s0) * (a2 - a0)) / det
            co += [a, b, s0 - a * a0 - b * b0]
        patch = tex.transform((x1 - x0, y1 - y0), Image.AFFINE, co, resample=Image.BILINEAR)
        mask = Image.new("L", patch.size, 0)
        ImageDraw.Draw(mask).polygon(local, fill=255)
        canvas.alpha_composite(Image.composite(patch, Image.new("RGBA", patch.size), mask), (x0, y0))
    ox, oy = to((0.0, 0.0))
    return canvas, (ox / W, 1 - oy / H)


# ---------------------------------------------------------------- writing a pose set

def save_poses(prefab, bundle, source, drawn):
    """Write drawn {pose: (picture, origin)} as data/lor/<prefab>/, turned to face left."""
    from PIL import Image
    out = os.path.join(OUT, prefab)
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".png"):
            os.remove(os.path.join(out, f))
    index = {"bundle": bundle, "prefab": prefab, "head": None, "poses": {}, "source": source}
    for pose, (img, (px, _)) in drawn.items():
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
        ox = (1 - px) * img.width
        bb = img.getbbox()
        img = img.crop(bb)
        img.save(os.path.join(out, pose + ".png"), optimize=True)
        index["poses"][pose] = {"file": pose + ".png", "pivot": [round((ox - bb[0]) / img.width, 6), 0.0]}
    with open(os.path.join(out, "poses.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print(f"{len(drawn)} poses -> {out}")


def plan(args):
    """<pose>=<clip>@<t> ... -> {pose: (clip, t)}"""
    out = {}
    for a in args:
        m = re.fullmatch(r"([A-Za-z0-9_]+)=(.+)@([0-9.]+)", a)
        if not m:
            raise SystemExit(f"{a}: write <pose>=<clip>@<seconds>")
        out[m.group(1)] = (m.group(2), float(m.group(3)))
    return out


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    env = game.unitypy().load(assets_path())
    cmd = argv[0]
    if cmd == "find":
        pat = re.compile(argv[1], re.I)
        for o in env.objects:
            if o.type.name in ("GameObject", "AnimationClip", "TextAsset"):
                try:
                    n = o.peek_name()
                except Exception:
                    continue
                if n and pat.search(n):
                    print(f"{o.type.name:13} {o.path_id:8} {n}")
        return 0
    if cmd == "clips":
        rig = Rig(env, int(argv[1]))
        anim = rig.comp(rig.tt(rig.root), "Animator")
        if anim is None:
            raise SystemExit("no Animator on that GameObject")
        ctrl = rig.objs[anim.read_typetree()["m_Controller"]["m_PathID"]].read_typetree()
        for ref in ctrl.get("m_AnimationClips", []):
            c = rig.objs[ref["m_PathID"]].read_typetree()
            print(f"{c['m_Name']}  {c['m_MuscleClip']['m_StopTime'] - c['m_MuscleClip']['m_StartTime']:.2f} s")
        return 0
    if cmd == "rig":
        prefab, root, wanted = argv[1], int(argv[2]), plan(argv[3:])
        rig = Rig(env, root)
        clips = {}
        for o in env.objects:
            if o.type.name == "AnimationClip" and o.peek_name() in {c for c, _ in wanted.values()}:
                clips.setdefault(o.peek_name(), Clip(o.read_typetree()))
        drawn = {}
        for pose, (clip, t) in wanted.items():
            if clip not in clips:
                raise SystemExit(f"no clip {clip}")
            rig.set_clip(clips[clip], t)
            drawn[pose] = draw(rig.triangles())
        save_poses(prefab, f"lobotomy/{prefab.lower()}", f"Lobotomy Corporation resources.assets: rig {root}, posed by its own clips", drawn)
        return 0
    if cmd == "spine":
        from limbus_skins import spine
        prefab, data_id, wanted = argv[1], int(argv[2]), plan(argv[3:])
        objs = {o.path_id: o for o in env.objects}

        def refs(o):                            # what a MonoBehaviour points at (its PPtrs, read raw)
            raw = o.get_raw_data()
            return [pid for pid in (struct.unpack_from("<iq", raw, i) for i in range(0, len(raw) - 12, 4))
                    if pid[0] == 0 and pid[1] in objs and pid[1] != o.path_id]

        def text(o):
            s = o.read().m_Script
            return (s.encode("utf-8", "surrogateescape") if isinstance(s, str) else bytes(s)).decode("utf-8-sig")
        skel = atlas = None
        tex = {}
        for _, pid in refs(objs[data_id]):                     # SkeletonDataAsset -> skeleton json, AtlasAsset
            o = objs[pid]
            if o.type.name == "TextAsset":
                skel = o
            elif o.type.name == "MonoBehaviour":
                for _, q in refs(o):                           # AtlasAsset -> atlas text, its page materials
                    a = objs[q]
                    if a.type.name == "TextAsset":
                        atlas = a
                    elif a.type.name == "Material":
                        for k, v in a.read_typetree()["m_SavedProperties"]["m_TexEnvs"]:
                            if k == "_MainTex" and v["m_Texture"]["m_PathID"]:
                                t = objs[v["m_Texture"]["m_PathID"]].read()
                                tex[t.m_Name + ".png"] = t.image.convert("RGBA")
        if skel is None or atlas is None:
            raise SystemExit("that isn't a SkeletonDataAsset (no skeleton or atlas found)")
        doc, pages = json.loads(text(skel)), spine.parse_atlas(text(atlas))
        drawn = {}
        for pose, (anim, f) in wanted.items():
            img, (ox, oy) = spine.render(doc, pages, tex, anim=anim, t=spine.anim_length(doc["animations"][anim]) * f)
            drawn[pose] = (img, (ox / img.width, 1 - oy / img.height))
        save_poses(prefab, f"lobotomy/{prefab.lower()}", f"Lobotomy Corporation resources.assets: Spine skeleton data {data_id}", drawn)
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
