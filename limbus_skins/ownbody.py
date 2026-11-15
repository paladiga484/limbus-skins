"""A body of its own: a built body's bundle under a new appearance name, so it loads beside the
game's own instead of in its place.

The game loads a unit's body by its appearance name from its own catalogue; a new name it has
no file for. The client patch (myosotis's OwnBodyPatch) hands the game a body of ours for such
a name, from a bundle of ours. That bundle is a donor body's (redrawn by repaint) with three
things made its own: the file inside it (Unity refuses a second bundle holding a file it has
loaded - the donor's own is loaded whenever the donor is in the fight), the one prefab it
offers (at asset_path(), under the new name), and its own name. What it leans on - the three
files every body shares - the game has loaded in any fight.
"""
import hashlib
import re

from .game import unitypy

NAME = re.compile(r"[A-Za-z0-9_]{1,96}")
CAB = re.compile(rb"CAB-[0-9a-f]{32}")


class OwnBodyError(Exception):
    pass


def appearance_name(identity, name):
    """A new unit's appearance name, as the game writes its own: <id>_<Name>Appearance."""
    word = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", name))
    if not word:
        raise OwnBodyError(f"{identity}: no letters in its name to make an appearance name of")
    return f"{int(identity)}_{word}Appearance"


def asset_path(appearance):
    """Where the body's prefab sits in its bundle (the patch asks for the same path)."""
    return f"assets/myosotis/{appearance.lower()}.prefab"


def cab_name(appearance):
    return "CAB-" + hashlib.md5(("limbus-skins body " + appearance).encode()).hexdigest()


def _files(env):
    bundle = env.file
    names = list(getattr(bundle, "files", {}))
    main = [n for n in names if CAB.fullmatch(n.encode())]
    if len(main) != 1:
        raise OwnBodyError(f"a body's bundle holds one file of objects; this one holds {len(main)}")
    return main[0], names


def own(raw, donor, appearance):
    """The bundle bytes of body `donor` (uncompressed, as repaint returns them) made
    `appearance`'s own. Returns the new bundle's bytes (LZ4)."""
    if not NAME.fullmatch(appearance):
        raise OwnBodyError(f"{appearance!r}: an appearance name is letters, digits and _")
    UnityPy = unitypy()
    old, _ = _files(UnityPy.load(raw))
    new = cab_name(appearance)
    # the file's name is written in the bundle's directory and in every path into its own data
    # (textures and sounds kept beside the objects); the same length, so nothing moves
    raw = bytes(raw).replace(old.encode(), new.encode()).replace(old.lower().encode(), new.lower().encode())
    env = UnityPy.load(raw)
    if _files(env)[0] != new:
        raise OwnBodyError("the bundle's own file kept its old name (a compressed directory?)")
    ab = next((o for o in env.objects if o.type.name == "AssetBundle"), None)
    if ab is None:
        raise OwnBodyError("no AssetBundle object in the body's bundle")
    t = ab.read_typetree()
    by_id = {o.path_id: o for o in env.objects}
    named = [(k, v) for k, v in t["m_Container"] if k.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == donor.lower()]
    # a prefab drawn by a skeleton lists its mesh under its own path too: the prefab is the GameObject
    mine = [(k, v) for k, v in named if getattr(by_id.get(v["asset"]["m_PathID"]), "type", None) is not None
            and by_id[v["asset"]["m_PathID"]].type.name == "GameObject"]
    if len(mine) != 1:
        raise OwnBodyError(f"{donor}: {len(mine)} prefabs of that name in its bundle")
    t["m_Container"] = [(asset_path(appearance), mine[0][1])] + [(asset_path(appearance), v) for _, v in named if v is not mine[0][1]]
    t["m_Name"] = appearance.lower() + ".bundle"
    if "m_AssetBundleName" in t:
        t["m_AssetBundleName"] = t["m_Name"]
    ab.save_typetree(t)
    root = next((o for o in env.objects if o.path_id == mine[0][1]["asset"]["m_PathID"]), None)
    if root is None or root.type.name != "GameObject":
        raise OwnBodyError(f"{donor}: its prefab isn't a GameObject in this bundle")
    g = root.read_typetree()
    g["m_Name"] = appearance
    root.save_typetree(g)
    for o, t in _char_infos(env):
        # an older body carries its identity's number ("10901"); the game then dresses it as that
        # identity from its own files - the donor's stock look, over our drawings (seen in a
        # fight: every body with a number showed its donor, every one without showed ours)
        t["charInfo"]["appearanceID"] = ""
        o.save_typetree(t)
    _own_atlases(env, appearance)
    out = env.file.save(packer="lz4")
    found = check(out, appearance)
    if found:
        raise OwnBodyError(f"{appearance}: " + "; ".join(found))
    return out


def _salt(appearance):
    return int(hashlib.md5(("limbus-skins atlas " + appearance).encode()).hexdigest()[:8], 16)


def _own_atlases(env, appearance):
    """Every sprite atlas in the bundle, and every sprite packed in one, under tags and keys of
    their own. Unity binds a packed sprite to a loaded atlas by the atlas's tag and the sprite's
    key; the donor's own bundle - in memory whenever its sinner's first identities are, which is
    always - holds atlases with the same tags and keys, and the sprites took their pictures from
    those: the donor's look on our body (seen in a fight: every body drawn on a sinner's first
    bundle showed the sinner, every other showed ours)."""
    salt = _salt(appearance)

    def key(k):
        guid = dict(k[0])
        guid["data[0]"] = (guid["data[0]"] ^ salt) & 0xFFFFFFFF
        return (guid, k[1])

    def tag(t):
        return f"{t}@{appearance}"
    for o in env.objects:
        if o.type.name == "SpriteAtlas":
            t = o.read_typetree()
            t["m_Tag"], t["m_Name"] = tag(t["m_Tag"]), tag(t["m_Name"])
            t["m_RenderDataMap"] = [(key(k), v) for k, v in t["m_RenderDataMap"]]
            o.save_typetree(t)
        elif o.type.name == "Sprite":
            t = o.read_typetree()
            if not t.get("m_AtlasTags") and not (t.get("m_SpriteAtlas") or {}).get("m_PathID"):
                continue
            t["m_AtlasTags"] = [tag(x) for x in t.get("m_AtlasTags") or []]
            t["m_RenderDataKey"] = key(t["m_RenderDataKey"])
            o.save_typetree(t)


def _foreign_atlases(env, appearance):
    """How many atlases and packed sprites in the bundle still go by the donor's tags."""
    n = 0
    for o in env.objects:
        if o.type.name == "SpriteAtlas":
            n += not o.read_typetree()["m_Tag"].endswith("@" + appearance)
        elif o.type.name == "Sprite":
            t = o.read_typetree()
            n += any(not x.endswith("@" + appearance) for x in t.get("m_AtlasTags") or [])
    return n


def _char_infos(env):
    """[(object, typetree)] of the bundle's CharacterAppearance components that name an identity."""
    out = []
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            t = o.read_typetree()
        except Exception:
            continue
        info = t.get("charInfo") if isinstance(t, dict) else None
        if isinstance(info, dict) and info.get("appearanceID"):
            out.append((o, t))
    return out


def check(bundle, appearance, donor_cab=None):
    """What is wrong with an own-body bundle ([] when nothing): its file and prefab under its
    own names, every path into its data its own, and none of the donor's left."""
    UnityPy = unitypy()
    found = []
    env = UnityPy.load(bundle)
    try:
        main, names = _files(env)
    except OwnBodyError as e:
        return [str(e)]
    if main != cab_name(appearance):
        found.append(f"its file is {main}, not its own {cab_name(appearance)}")
    ab = next((o for o in env.objects if o.type.name == "AssetBundle"), None)
    t = ab.read_typetree() if ab is not None else {"m_Container": []}
    keys = [k for k, _ in t["m_Container"]]
    by_id = {o.path_id: o for o in env.objects}
    roots = [by_id[v["asset"]["m_PathID"]] for _, v in t["m_Container"]
             if v["asset"]["m_PathID"] in by_id and by_id[v["asset"]["m_PathID"]].type.name == "GameObject"]
    if not keys or set(keys) != {asset_path(appearance)}:
        found.append(f"it offers {sorted(set(keys)) or 'nothing'}, not just {asset_path(appearance)}")
    elif len(roots) != 1:
        found.append(f"it offers {len(roots)} prefabs, not one")
    elif roots[0].read_typetree().get("m_Name") != appearance:
        found.append("its prefab isn't named for it")
    if _char_infos(env):
        found.append("a body in it still names the identity it was drawn on")
    left = _foreign_atlases(env, appearance)
    if left:
        found.append(f"{left} sprite atlases or packed sprites still go by the donor's tags")
    held = {n.rsplit("/", 1)[-1] for n in names}
    for o in env.objects:
        if o.type.name not in ("Texture2D", "AudioClip", "Mesh", "Texture2DArray", "Cubemap", "VideoClip"):
            continue
        try:
            d = o.read_typetree()
        except Exception:
            continue
        path = (d.get("m_StreamData") or d.get("m_Resource") or {}).get("path") or (d.get("m_Resource") or {}).get("m_Source") or ""
        if path and path.rsplit("/", 1)[-1] not in held:
            found.append(f"{o.type.name} {d.get('m_Name', '')} reads {path}, which the bundle doesn't hold")
            break
    if donor_cab and (donor_cab.encode() in bytes(bundle) or donor_cab.lower().encode() in bytes(bundle)):
        found.append("the donor's file name is still in it")
    return found
