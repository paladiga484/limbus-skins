"""Bodies that share a bundle: a skin changes only what its own identity draws.

The early identities of a sinner live in one bundle (LCB, Liu and G Corp. Gregor...), and the
bundle's preload lists overlap: every body there loads the shared death frame, its clip and a
page of effect sprites, and one body's list can hold another's whole frame set (G Corp. Outis
loads LCB Outis's). Anything another body loads is foreign: a skin leaves it as it is. Where the
skin's identity draws a foreign frame itself (the death frame), it gets a private copy - the
frame (and its texture, for a tint), and the clip that plays it if that is shared too - which only
its own animator points at, so the skin can redraw the copy and the others keep the original.
"""
import copy
import random

from .repaint import RepaintError, bundle_assets, clip_frames, main_clips


def _forms(assets, target):
    """The target's body and its other forms in the bundle (the names sharing its id prefix)."""
    prefix = target.split("_", 1)[0] + "_"
    return {n for n in assets if n == target or n.startswith(prefix)}


def foreign(env, target):
    """Ids of every object a body outside the target's forms loads."""
    assets = bundle_assets(env)
    mine = _forms(assets, target)
    out = set()
    for name, (_, ids) in assets.items():
        if name not in mine:
            out |= ids
    return out


def _controllers(objs, root):
    """The objects of a body's own animator chain (its AnimatorOverrideControllers and the
    AnimatorController under them)."""
    anim = None
    for c in objs[root].read_typetree()["m_Component"]:
        o = objs.get(c["component"]["m_PathID"])
        if o is None:
            continue
        if o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            if "motionList" in t and t.get("_anim", {}).get("m_PathID"):
                anim = objs.get(t["_anim"]["m_PathID"])
        elif o.type.name == "Animator" and anim is None:
            anim = o
    out = []
    ctrl = objs.get(anim.read_typetree()["m_Controller"]["m_PathID"]) if anim is not None else None
    while ctrl is not None:
        out.append(ctrl)
        t = ctrl.read_typetree()
        ctrl = objs.get(t["m_Controller"]["m_PathID"]) if ctrl.type.name == "AnimatorOverrideController" else None
    return out


def _repoint(tree, old, new):
    """Every reference to object `old` inside a typetree, pointed at `new`. Returns how many."""
    n = 0
    if isinstance(tree, dict):
        if tree.get("m_FileID") == 0 and tree.get("m_PathID") == old and len(tree) == 2:
            tree["m_PathID"] = new
            return 1
        for v in tree.values():
            n += _repoint(v, old, new)
    elif isinstance(tree, (list, tuple)):
        for v in tree:
            n += _repoint(v, old, new)
    return n


def _clone(sf, obj, tree, taken):
    """A new object in `sf`: a copy of `obj` holding `tree`, under a path id nobody uses."""
    pid = 0
    while pid == 0 or pid in taken:
        pid = random.getrandbits(63) * random.choice((1, -1))
    taken.add(pid)
    new = copy.copy(obj)
    new.path_id, new.data = pid, None
    sf.objects[pid] = new
    new.save_typetree(tree)
    return pid


def _preload(env, target_roots, new_ids):
    """Add new_ids to the preload lists of the target's bodies (each body's container entries
    share one range; later ranges move up)."""
    ab_obj = next(o for o in env.objects if o.type.name == "AssetBundle")
    ab = ab_obj.read_typetree()
    pre = ab["m_PreloadTable"]
    for root in target_roots:
        entries = [info for _, info in ab["m_Container"] if info["asset"]["m_PathID"] == root]
        if not entries:
            continue
        at = entries[0]["preloadIndex"] + entries[0]["preloadSize"]
        pre[at:at] = [{"m_FileID": 0, "m_PathID": i} for i in new_ids]
        for _, info in ab["m_Container"]:
            if info["asset"]["m_PathID"] == root:
                info["preloadSize"] += len(new_ids)
            elif info["preloadIndex"] >= at:
                info["preloadIndex"] += len(new_ids)
    ab_obj.save_typetree(ab)


def privatize(env, target, texture=False):
    """Give the target's forms private copies of the foreign frames they draw (see the module's
    note). texture: copy each frame's texture too (a tint recolours textures, not frames).
    Returns (bundle bytes with the copies, notes), or (None, notes) when nothing is shared.
    Raises RepaintError when a shared frame is played through an animator another body uses
    too - no copy could be the target's alone."""
    assets = bundle_assets(env)
    mine = _forms(assets, target)
    roots = [assets[n][0] for n in mine]
    away = foreign(env, target)
    objs = {o.path_id: o for o in env.objects}
    drawn = {}                                          # my clip id -> the foreign frames it plays
    for root in roots:
        clips = main_clips(env, root) or set()
        for cid in clips:
            if cid not in objs:
                continue
            for sprites, _ in clip_frames(env, {cid}).values():
                shared = [s.path_id for s in sprites if s.path_id in away]
                if shared:
                    drawn.setdefault(cid, set()).update(shared)
    if not drawn:
        return None, []
    sf = objs[roots[0]].assets_file
    taken = set(sf.objects)
    copies, new_ids, notes = {}, [], []
    for sid in sorted({s for v in drawn.values() for s in v}):
        t = objs[sid].read_typetree()
        atlas = objs.get(t["m_SpriteAtlas"]["m_PathID"]) if t["m_SpriteAtlas"]["m_PathID"] else None
        if atlas is not None:                           # the copy leaves the atlas: its render data become its own
            key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
            rd = next((v for k, v in atlas.read_typetree()["m_RenderDataMap"]
                       if (tuple(k[0].values()), k[1]) == key), None)
            if rd is None:
                raise RepaintError(f"{target}: the shared frame {t['m_Name']} isn't in its atlas")
            t["m_RD"].update({k: v for k, v in rd.items() if k in t["m_RD"]})
            t["m_SpriteAtlas"] = {"m_FileID": 0, "m_PathID": 0}
            t["m_AtlasTags"] = []
        if texture and t["m_RD"]["texture"]["m_PathID"]:
            tex = objs[t["m_RD"]["texture"]["m_PathID"]]
            t["m_RD"]["texture"]["m_PathID"] = _clone(sf, tex, tex.read_typetree(), taken)
            new_ids.append(t["m_RD"]["texture"]["m_PathID"])
        key = t["m_RenderDataKey"]                       # a key of its own: the copy is a sprite of its own
        t["m_RenderDataKey"] = ({k: random.getrandbits(32) for k in key[0]}, key[1])
        copies[sid] = _clone(sf, objs[sid], t, taken)
        new_ids.append(copies[sid])
        notes.append(f"own copy of the shared frame {t['m_Name']}")
    ctrls = [c for root in roots for c in _controllers(objs, root)]
    if any(c.path_id in away for c in ctrls):
        raise RepaintError(f"{target}: its animator is shared with another body; its frames can't be its own")
    # read once: an object reads its original bytes even after a save, so edits pile up here
    ctrl_trees, dirty = {c.path_id: c.read_typetree() for c in ctrls}, set()
    for cid in sorted(drawn):
        tree = objs[cid].read_typetree()
        for old, new in copies.items():
            _repoint(tree.get("m_ClipBindingConstant", {}), old, new)
        if cid not in away:
            objs[cid].save_typetree(tree)               # the clip is ours alone: edit it
            continue
        mine_clip = _clone(sf, objs[cid], tree, taken)  # shared: a copy, and our animator plays it
        new_ids.append(mine_clip)
        hit = 0
        for pid, ct in ctrl_trees.items():
            n = _repoint(ct, cid, mine_clip)
            if n:
                dirty.add(pid)
                hit += n
        if not hit:
            raise RepaintError(f"{target}: the shared clip {tree['m_Name']} isn't played through its own animator")
        notes.append(f"own copy of the shared clip {tree['m_Name']}")
    for pid in dirty:
        objs[pid].save_typetree(ctrl_trees[pid])
    _preload(env, roots, new_ids)
    return env.file.save(packer="none"), notes
