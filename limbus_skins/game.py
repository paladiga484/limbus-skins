"""Reading the installed game: its static data, helper units, names and battle bodies.

Everything comes from the files the game already downloaded; nothing is fetched.
"""
import ast
import glob
import json
import os
import re
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
