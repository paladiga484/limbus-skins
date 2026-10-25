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


def skill_edits(keys, skins, boss=()):
    """The skins among keys whose skill records go into the static-data bundle: repaints
    with boss skills on, skins that recolor a skill's sin (Greencliff's S3) and skins whose
    death does something (Greencliff's Black Forest)."""
    return {k for k in keys if skins[k].get("sins") or skins[k].get("death")
            or (k in boss and skins[k]["kind"] == "repaint" and skins[k].get("source"))}


def build(keys, game_dir, cache, data_dir, log=print, boss=()):
    """Build the given skin keys; repaints in `boss` also take their boss's skills.
    Returns {key: (ok, message)}."""
    skins = catalog()
    built_root = os.path.join(data_dir, "built")
    os.makedirs(built_root, exist_ok=True)
    ids, helper_units = game.identities(game_dir, cache)
    results, boss_notes = {}, {}

    # helpers and boss skills: one combined static-data bundle
    want = [skins[k] for k in keys if skins[k]["kind"] == "helper"]
    want_boss = [skins[k] for k in sorted(skill_edits(keys, skins, boss))]
    if want or want_boss:
        swaps, skill_swaps, buffs, bad, boss_ok = {}, {}, [], [], []
        for s in want:
            h = helper_units.get(s["helper"])
            if h is None or s["identity"] not in ids:
                bad.append(s["key"])
                results[s["key"]] = (False, "not in this game version")
                continue
            swaps[s["identity"]] = h
        if want_boss:
            pers, skills, units = game.boss_data(cache)
            for s in want_boss:
                try:
                    if s.get("sins") or s.get("death"):
                        said = []
                        if s.get("sins"):
                            skill_swaps.update(helpers.recolor_skills(pers[s["identity"]], skills, s["sins"], skill_swaps))
                            said.append("sins: " + ", ".join(f"S{n} {v}" for n, v in sorted(s["sins"].items())))
                        if s.get("death") == "black-forest":
                            edits, new = helpers.black_forest(pers[s["identity"]], skills, skill_swaps)
                            skill_swaps.update(edits)
                            buffs += new
                            said.append("carries the Black Forest")
                        boss_notes[s["key"]] = "; ".join(said)
                    else:
                        unit = helpers.pick_unit(units[s["source"]], skills, s.get("unit"))
                        skill_swaps.update(helpers.boss_skills(pers[s["identity"]], unit, skills))
                        boss_notes[s["key"]] = f"boss skills from unit {unit['id']}"
                    boss_ok.append(s["key"])
                except (KeyError, StopIteration, ValueError) as e:
                    boss_notes[s["key"]] = f"skills unchanged ({e or 'no boss record'})"
        out = os.path.join(built_root, "helpers")
        shutil.rmtree(out, ignore_errors=True)
        if swaps or skill_swaps:
            static_path, _ = game.find_static(cache)
            raw, done, _ = helpers.rewrite_static(static_path, swaps, skill_swaps, buffs)
            os.makedirs(out)
            rel = _write(out, cache, static_path, raw)
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "kind": "helpers", "skins": [s["key"] for s in want if s["key"] not in bad],
                           "skills": boss_ok, "files": [rel]}, f, indent=1)
            for s in want:
                if s["key"] not in bad:
                    ok = s["identity"] in done
                    results[s["key"]] = (ok, "built" if ok else "identity not found in the static data")
            log(f"  helpers: {len(done)} identities restyled, {len(boss_ok)} with new skills")

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
                        head=_head(s, data_dir), mirror=s.get("mirror", True), dash=s.get("dash"),
                        forms=s.get("forms"))
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                rel = _write(out, cache, locate(target), raw)
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump({"format": FORMAT, "kind": s["kind"], "skins": [key], "files": [rel], "notes": notes}, f, indent=1)
                results[key] = (True, "; ".join(notes + ([boss_notes[key]] if key in boss_notes else [])))
            except (repaint.RepaintError, RuntimeError, KeyError, OSError) as e:
                shutil.rmtree(out, ignore_errors=True)
                results[key] = (False, str(e))
            log(f"  {'ok  ' if results[key][0] else 'FAIL'} {key}: {results[key][1]}")
    return results


def built_dirs(data_dir, keys, boss=()):
    """[(label, files dir)] for the built skins among keys (helpers and boss skills share
    one folder)."""
    root = os.path.join(data_dir, "built")
    out, seen = [], set()
    skins = catalog()
    edits = skill_edits([k for k in keys if k in skins], skins, boss)
    try:
        with open(os.path.join(root, "helpers", "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        man = {}
    for k in keys:
        folders = ["helpers"] if skins.get(k, {}).get("kind") == "helper" else [k]
        if k in edits and k in man.get("skills", []):
            folders.append("helpers")
        for folder in folders:
            files = os.path.join(root, folder, "files")
            if folder in seen or not os.path.isdir(files):
                continue
            if folder == "helpers" and k not in man.get("skins", []) + man.get("skills", []):
                continue                    # built without this skin - needs a rebuild
            seen.add(folder)
            out.append((folder, files))
    return out
