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
    mine = [(k, v) for k, v in t["m_Container"] if k.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == donor.lower()]
    if len(mine) != 1:
        raise OwnBodyError(f"{donor}: {len(mine)} prefabs of that name in its bundle")
    t["m_Container"] = [(asset_path(appearance), mine[0][1])]
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
    out = env.file.save(packer="lz4")
    found = check(out, appearance)
    if found:
        raise OwnBodyError(f"{appearance}: " + "; ".join(found))
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
    if keys != [asset_path(appearance)]:
        found.append(f"it offers {keys or 'nothing'}, not just {asset_path(appearance)}")
    else:
        root = next((o for o in env.objects if o.path_id == t["m_Container"][0][1]["asset"]["m_PathID"]), None)
        if root is None or root.read_typetree().get("m_Name") != appearance:
            found.append("its prefab isn't named for it")
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
