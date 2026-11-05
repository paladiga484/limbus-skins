"""Reading the installed game: its static data, helper units, names and battle bodies.

Everything comes from the files the game already downloaded; nothing is fetched.
"""
import ast
import glob
import json
import os
import re
import struct
import warnings

UNITY_VERSION = "6000.3.12f1"            # bundles carry a stripped version string
SKIP = re.compile(r"video|banner|story_bg|story_cg|illust|fmod|font|announcer|portrait|"
                  r"skillicon|stagemap|battlemap")


def unitypy():
    import UnityPy
    warnings.filterwarnings("ignore")
    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_VERSION
    return UnityPy


def cache_bundles(cache):
    """{content hash: __data path} for every bundle in the Unity download cache."""
    out = {}
    for d in os.listdir(cache):
        dd = os.path.join(cache, d)
        if not os.path.isdir(dd):
            continue
        for h in os.listdir(dd):
            p = os.path.join(dd, h, "__data")
            if os.path.isfile(p):
                out[h] = p
    return out


def _cached(cache):
    """(folder, hash, __data path) of every cached bundle, in a fixed order."""
    try:
        folders = sorted(os.listdir(cache))
    except OSError:
        return
    for d in folders:
        dd = os.path.join(cache, d)
        if not os.path.isdir(dd):
            continue
        for h in sorted(os.listdir(dd)):
            p = os.path.join(dd, h, "__data")
            if os.path.isfile(p):
                yield d, h, p


def catalog_paths(cache, game=None):
    """The game's Addressables catalogs: the ones it downloaded (under LocalLow, beside the
    cache), newest first, then the one it shipped with."""
    locallow = os.path.dirname(os.path.dirname(os.path.abspath(cache)))
    found = sorted(glob.glob(os.path.join(locallow, "ProjectMoon", "LimbusCompany", "com.unity.addressables",
                                          "catalog_*.bin")), key=os.path.getmtime, reverse=True)
    if game:
        found.append(os.path.join(game, "StreamingAssets", "aa", "catalog.bin"))
    return found


def catalogs(cache, game=None):
    """The bytes of each catalog that can be read (catalog_paths)."""
    out = []
    for p in catalog_paths(cache, game):
        try:
            with open(p, "rb") as f:
                out.append(f.read())
        except OSError:
            pass
    return out


def _record(cat, p, name):
    """The size in the record at p, if its folder-name pointer names `name`."""
    if p < 0 or p + 16 > len(cat):
        return None
    q = struct.unpack_from("<I", cat, p + 4)[0]
    return struct.unpack_from("<I", cat, p + 12)[0] if cat[q:q + len(name)] == name else None


def stock_size(cat, folder, h):
    """The size catalog bytes `cat` give bundle <folder>/<h> as the game ships it, or None. Its
    record is four u32s - where its 16-byte hash is, where its folder name is, its CRC, its size -
    a little after those bytes; the folder name must be this bundle's own."""
    try:
        raw, name = bytes.fromhex(h), folder.encode()
    except ValueError:
        return None
    if len(raw) != 16:
        return None
    x = cat.find(raw)
    while x != -1:
        ptr = struct.pack("<I", x)
        size = _record(cat, cat.find(ptr, x + 16, x + 16 + 1024), name)   # the record follows its strings
        p = cat.find(ptr) if size is None else -1
        while size is None and p != -1:
            size = _record(cat, p, name)
            p = cat.find(ptr, p + 1)
        if size is not None:
            return size
        x = cat.find(raw, x + 1)
    return None


def leftovers(cache, cats):
    """[(<folder>/<hash>, its size, the size it ships with)] for each cached bundle whose size isn't
    the one the game's catalog gives it: a modded copy (an older limbus-skins' swap, or a mod's)
    forged to the stock CRC, so the game never fetched its own. None when the catalogs can't be
    read with confidence - then nothing may be removed."""
    if not cats:
        return None
    rows, known, total = [], 0, 0
    for folder, h, path in _cached(cache):
        total += 1
        sizes = {s for cat in cats for s in (stock_size(cat, folder, h),) if s is not None}
        if not sizes:
            continue                                   # an older version: the game no longer loads it
        known += 1
        real = os.path.getsize(path)
        if real not in sizes:
            rows.append((f"{folder}/{h}", real, max(sizes)))
    if not total:
        return []
    if not known or known * 2 < total or (rows and len(rows) == known) or (len(rows) > 8 and len(rows) * 4 > known):
        return None                                    # most unknown, or most "modded": the layout changed
    return rows


def fingerprint(cache, game=None):
    """Size and time of every cached bundle and catalog: while it's the same, nothing changed."""
    import hashlib
    h = hashlib.sha256()
    for p in catalog_paths(cache, game):
        try:
            st = os.stat(p)
        except OSError:
            continue
        h.update(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    for folder, hh, path in _cached(cache):
        try:
            st = os.stat(path)
        except OSError:
            continue
        h.update(f"{folder}/{hh}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def catalog_names(game):
    """{content hash: bundle name} from the game's Addressables catalog."""
    try:
        with open(os.path.join(game, "StreamingAssets", "aa", "catalog.bin"), "rb") as f:
            blob = f.read()
    except OSError:
        return {}
    return {m.group(2).decode(): m.group(1).decode()
            for m in re.finditer(rb"([A-Za-z0-9_\-.]+?)_([0-9a-f]{32})\.bundle", blob)}


def find_static(cache):
    """(path, UnityPy env) of the newest cached static-data bundle."""
    UnityPy = unitypy()
    found = [p for p in glob.glob(os.path.join(cache, "*", "*", "__data")) if os.path.getsize(p) < 16 << 20]
    for p in sorted(found, key=os.path.getmtime, reverse=True):
        try:
            env = UnityPy.load(p)
        except Exception:
            continue
        if any("/static-data/personality/" in k for k in env.container):
            return p, env
    raise SystemExit("limbus-skins: no static-data bundle in the cache - launch the game once (to the title screen)")


def _text(obj):
    raw = obj.read().m_Script
    raw = raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else bytes(raw)
    return json.loads(raw.decode("utf-8-sig"))


def lit(x):
    return ast.literal_eval(x) if isinstance(x, str) else (x or [])


def static_lists(env, kinds=("personality", "skin", "skill", "assistant")):
    """{kind: [records]} from the static bundle's TextAssets."""
    out = {k: [] for k in kinds}
    for path, obj in env.container.items():
        if obj.type.name != "TextAsset":
            continue
        kind = next((k for k in kinds if f"/static-data/{k}/" in path), None)
        if kind:
            try:
                out[kind] += _text(obj).get("list", [])
            except ValueError:
                pass
    return out


def names(game, prefix):
    """English names: {id: record} from Localize/en/<prefix>*.json."""
    loc = os.path.join(game, "Assets", "Resources_moved", "Localize", "en")
    out = {}
    for f in glob.glob(os.path.join(loc, prefix + "*.json")):
        try:
            with open(f, encoding="utf-8-sig") as fh:
                out.update({r["id"]: r for r in json.load(fh).get("dataList", [])})
        except (ValueError, KeyError, OSError):
            pass
    return out


SINNERS = {1: "Yi Sang", 2: "Faust", 3: "Don Quixote", 4: "Ryōshū", 5: "Meursault", 6: "Hong Lu",
           7: "Heathcliff", 8: "Ishmael", 9: "Rodion", 10: "Sinclair", 11: "Outis", 12: "Gregor"}


def identities(game, cache):
    """{id: {sinner, appearance, title}} and {helper id: record} from the installed build."""
    _, env = find_static(cache)
    lists = static_lists(env)
    pn = names(game, "EN_Personalities")
    ids = {}
    for r in lists["personality"]:
        n = pn.get(r["id"], {})
        ids[r["id"]] = {"sinner": SINNERS.get(r.get("characterId"), "?"), "appearance": r["appearance"],
                        "title": (n.get("title") or "").replace("\n", " ")}
    helpers = {r["id"]: r for r in lists["assistant"]}
    return ids, helpers


def boss_data(cache):
    """({identity id: record}, {skill id: record}, {appearance: [unit records]}) - the
    bosses (enemy, abnormality-unit and helper units) that a repaint's source can lend skills."""
    _, env = find_static(cache)
    lists = static_lists(env, ("personality", "skill", "enemy", "abnormality-unit", "assistant"))
    units = {}
    for kind in ("abnormality-unit", "enemy", "assistant"):
        for r in lists[kind]:
            if r.get("appearance"):
                units.setdefault(r["appearance"], []).append(r)
    return ({r["id"]: r for r in lists["personality"]}, {r["id"]: r for r in lists["skill"]}, units)


# ---------------------------------------------------------------- battle bodies

def scan_bundle(path):
    """{prefab name: True} for every battle body (SD prefab) in one cached bundle."""
    UnityPy = unitypy()
    try:
        env = UnityPy.load(path)
    except Exception:
        return path, []
    found = []
    for cpath, ptr in env.container.items():
        if cpath.endswith(".prefab") and "/SD/" in cpath:
            root = ptr.assetsfile.objects.get(ptr.m_PathID)
            if root is not None and root.type.name == "GameObject":
                found.append(os.path.basename(cpath)[:-7])
    return path, found


def body_index(game, cache, state_path, workers=4, log=print):
    """{body name: bundle hash}. Only bundles not read before are opened (the first run
    reads them all, a few minutes); the result is kept in state_path."""
    from concurrent.futures import ProcessPoolExecutor
    try:
        with open(state_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    have = cache_bundles(cache)
    names_ = catalog_names(game)
    bodies = {k: h for k, h in prev.get("bodies", {}).items() if h in have}
    scanned = set(prev.get("scanned", [])) & set(have)
    todo = [h for h in have if h not in scanned and not SKIP.search(names_.get(h, ""))
            and os.path.getsize(have[h]) < 200 << 20]
    if todo:
        log(f"reading {len(todo)} game bundles to find the battle bodies (first run takes a few minutes)...")
        with ProcessPoolExecutor(workers) as ex:
            for path, found in ex.map(scan_bundle, [have[h] for h in todo], chunksize=4):
                h = os.path.basename(os.path.dirname(path))
                for k in found:
                    bodies[k] = h
        scanned |= set(todo)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"bodies": bodies, "scanned": sorted(scanned)}, f)
        os.replace(state_path + ".tmp", state_path)
    return bodies, have
