"""Building skins from the installed game into ready-to-swap files.

Each built skin is a folder that mirrors the cache layout
(<name hash>/<content hash>/__data + __info), already carrying the stock CRC, plus a
manifest. Helper skins all edit the one static-data bundle, so they build together
into a single "helpers" folder.
"""
import json
import os
import shutil
from importlib import resources

from . import art, bundle, game, helpers, repaint, tint

FORMAT = 7      # bump when built skins change shape: older builds are rebuilt on apply


def catalog():
    with resources.files("limbus_skins").joinpath("data/skins.json").open(encoding="utf-8") as f:
        doc = json.load(f)
    skins = {}
    for kind in ("helpers", "repaints", "tints"):
        for key, e in doc.get(kind, {}).items():
            skins[key] = dict(e, key=key, kind=kind[:-1])
    return skins


def head_recipe(name):
    with resources.files("limbus_skins").joinpath(f"data/heads/{name}").open(encoding="utf-8") as f:
        return json.load(f)


def _head(skin, data_dir):
    """The skin's head recipe, with the player's own art (limbus-skins art) if set."""
    if not skin.get("head"):
        return None
    recipe = head_recipe(skin["head"])
    mine = art.path_for(data_dir, skin["key"])
    if os.path.exists(mine):
        recipe["art"] = {"image_path": mine}
        recipe["width"] = recipe.get("own_width", recipe.get("width", 1.9))
    return recipe


def _write(out_dir, cache, stock_path, raw):
    """Forge raw to the stock bundle's CRC and write it where the stock file sits."""
    with open(stock_path, "rb") as f:
        stock = f.read()
    forged = bundle.forge(raw, bundle.crc(stock))
    rel = os.path.relpath(os.path.dirname(stock_path), cache)
    dst = os.path.join(out_dir, "files", rel)
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "__data"), "wb") as f:
        f.write(forged)
    info = os.path.join(os.path.dirname(stock_path), "__info")
    if os.path.exists(info):
        shutil.copyfile(info, os.path.join(dst, "__info"))
    return rel


def build(keys, game_dir, cache, data_dir, log=print):
    """Build the given skin keys. Returns {key: (ok, message)}."""
    skins = catalog()
    built_root = os.path.join(data_dir, "built")
    os.makedirs(built_root, exist_ok=True)
    ids, helper_units = game.identities(game_dir, cache)
    results = {}

    # helpers: one combined static-data bundle
    want = [skins[k] for k in keys if skins[k]["kind"] == "helper"]
    if want:
        swaps, bad = {}, []
        for s in want:
            h = helper_units.get(s["helper"])
            if h is None or s["identity"] not in ids:
                bad.append(s["key"])
                results[s["key"]] = (False, "not in this game version")
                continue
            swaps[s["identity"]] = h
        out = os.path.join(built_root, "helpers")
        shutil.rmtree(out, ignore_errors=True)
        if swaps:
            static_path, _ = game.find_static(cache)
            raw, done = helpers.rewrite_static(static_path, swaps)
            os.makedirs(out)
            rel = _write(out, cache, static_path, raw)
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "kind": "helpers", "skins": [s["key"] for s in want if s["key"] not in bad],
                           "files": [rel]}, f, indent=1)
            for s in want:
                if s["key"] not in bad:
                    ok = s["identity"] in done
                    results[s["key"]] = (ok, "built" if ok else "identity not found in the static data")
            log(f"  helpers: {len(done)} identities restyled")

    # repaints and tints: one folder each
    want = [skins[k] for k in keys if skins[k]["kind"] in ("repaint", "tint")]
    if want:
        bodies, have = game.body_index(game_dir, cache, os.path.join(data_dir, "bodies.json"), log=log)

        def locate(body):
            h = bodies.get(body)
            if not h or h not in have:
                raise repaint.RepaintError(f"{body} is not in the game's cache")
            return have[h]

        for s in want:
            key = s["key"]
            out = os.path.join(built_root, key)
            target = ids.get(s["identity"], {}).get("appearance")
            if not target:
                results[key] = (False, "identity not in this game version")
                continue
            try:
                if s["kind"] == "tint":
                    raw, notes = tint.tint(target, locate, s["tint"])
                else:
                    raw, notes = repaint.repaint(
                        target, locate, source=None if s.get("self") else s["source"],
                        clips=s.get("clips"), mapping=s.get("map"),
                        head=_head(s, data_dir), mirror=s.get("mirror", True), dash=s.get("dash"))
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                rel = _write(out, cache, locate(target), raw)
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump({"format": FORMAT, "kind": s["kind"], "skins": [key], "files": [rel], "notes": notes}, f, indent=1)
                results[key] = (True, "; ".join(notes))
            except (repaint.RepaintError, RuntimeError, KeyError, OSError) as e:
                shutil.rmtree(out, ignore_errors=True)
                results[key] = (False, str(e))
            log(f"  {'ok  ' if results[key][0] else 'FAIL'} {key}: {results[key][1]}")
    return results


def built_dirs(data_dir, keys):
    """[(label, files dir)] for the built skins among keys (helpers share one folder)."""
    root = os.path.join(data_dir, "built")
    out, seen = [], set()
    skins = catalog()
    for k in keys:
        folder = "helpers" if skins.get(k, {}).get("kind") == "helper" else k
        files = os.path.join(root, folder, "files")
        if folder in seen or not os.path.isdir(files):
            continue
        if folder == "helpers":
            try:
                with open(os.path.join(root, folder, "manifest.json"), encoding="utf-8") as f:
                    if k not in json.load(f)["skins"]:
                        continue        # built without this helper - needs a rebuild
            except (OSError, ValueError, KeyError):
                continue
        seen.add(folder)
        out.append((folder, files))
    return out
