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

from . import art, bundle, game, helpers, lortext, repaint, tint

FORMAT = 17      # bump when built skins change shape: older builds are rebuilt on apply


def catalog():
    with resources.files("limbus_skins").joinpath("data/skins.json").open(encoding="utf-8") as f:
        doc = json.load(f)
    skins = {}
    for kind, one in (("helpers", "helper"), ("repaints", "repaint"), ("tints", "tint"), ("identities", "identity")):
        for key, e in doc.get(kind, {}).items():
            skins[key] = dict(e, key=key, kind=one)
    return skins


def shares_static(skin):
    """Built into the one shared static-data file (helpers, and identities of our own)."""
    return skin["kind"] in ("helper", "identity")


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
    if rel.startswith("..") or os.path.isabs(rel):
        raise OSError(f"{stock_path} is not inside the game's cache {cache}")
    dst = os.path.join(out_dir, "files", rel)
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, "__data"), "wb") as f:
        f.write(forged)
    info = os.path.join(os.path.dirname(stock_path), "__info")
    if os.path.exists(info):
        shutil.copyfile(info, os.path.join(dst, "__info"))
    return rel


def skill_edits(keys, skins, boss=()):
    """The skins among keys whose records go into the static-data bundle: repaints with boss
    skills on, and skins that recolor a skill's sin, rewrite its moves or text, swap its passives
    or change its stats."""
    return {k for k in keys if any(skins[k].get(f) for f in ("sins", "moves", "passives", "texts", "stats"))
            or (k in boss and skins[k]["kind"] == "repaint" and skins[k].get("source"))}


def build(keys, game_dir, cache, data_dir, log=print, boss=(), static=True):
    """Build the given skin keys; repaints in `boss` also take their boss's skills.
    static=False builds only the bodies and leaves the shared static-data file alone.
    Returns {key: (ok, message)}."""
    skins = catalog()
    built_root = os.path.join(data_dir, "built")
    os.makedirs(built_root, exist_ok=True)
    ids, helper_units = game.identities(game_dir, cache)
    results, boss_notes = {}, {}

    # helpers and boss skills: one combined static-data bundle
    want = [skins[k] for k in keys if static and skins[k]["kind"] == "helper"]
    want_ids = [skins[k] for k in keys if static and skins[k]["kind"] == "identity"]
    want_boss = [skins[k] for k in sorted(skill_edits(keys, skins, boss))] if static else []
    if want or want_boss or want_ids:
        swaps, skill_swaps, bad, boss_ok, kit_notes, green, passives, texts, stats = {}, {}, [], [], {}, set(), {}, {}, {}
        pers, skills, units = game.boss_data(cache)
        clones, ruina = {}, None
        if any(lortext.uses_lor(s.get("texts")) for s in want_boss):
            try:                                    # Ruina's own words, from the player's install
                ruina = lortext.Book(lortext.read_tables())
            except (lortext.LorTextError, OSError) as e:
                log(f"  Library of Ruina's text: {e}")
        for s in want_ids:
            try:
                helpers.check_new_ids(set(ids), {s["identity"]: s["base"]})
                clones[s["identity"]] = s["base"]
            except ValueError as e:
                bad.append(s["key"])
                results[s["key"]] = (False, str(e))
        for s in want:
            h = helper_units.get(s["helper"])
            if h is None or s["identity"] not in ids:
                bad.append(s["key"])
                results[s["key"]] = (False, "not in this game version")
                continue
            try:
                attrs, guard, dropped = helpers.helper_kit(pers[s["identity"]], h, skills, bool(s.get("script")))
            except (KeyError, ValueError) as e:
                bad.append(s["key"])
                results[s["key"]] = (False, str(e))
                continue
            swaps[s["identity"]] = dict(h, attributeList=attrs, defenseSkillIDList=guard, _script=bool(s.get("script")))
            if dropped:
                kit_notes[s["key"]] = "story-only skills left out: " + ", ".join(str(i) for i, _ in dropped)
        if want_boss:
            for s in want_boss:
                try:
                    said, ident = [], pers[s["identity"]]
                    if s["key"] in boss and s["kind"] == "repaint" and s.get("source"):
                        unit = helpers.pick_unit(units[s["source"]], skills, s.get("unit"))
                        skill_swaps.update(helpers.boss_skills(ident, unit, skills))
                        said.append(f"boss skills from unit {unit['id']}")
                    if s.get("moves"):                      # each edit stacks on the ones before it
                        skill_swaps.update(helpers.move_skills(ident, skills, s["moves"], skill_swaps))
                        said.append(f"own moves for S{', S'.join(sorted(s['moves']))}")
                    if s.get("passives"):
                        passives[s["identity"]] = s["passives"]
                        said.append("passives: " + ", ".join(str(i) for i in s["passives"].get("battle", [])
                                                           + s["passives"].get("support", [])))
                    if s.get("sins"):
                        recoloured = helpers.recolor_skills(ident, skills, s["sins"], skill_swaps)
                        skill_swaps.update(recoloured)
                        green.update(helpers.green_ids(recoloured))
                        said.append("sins: " + ", ".join(f"{n} {v}" for n, v in sorted(s["sins"].items())))
                    if s.get("texts") or s.get("moves"):    # shown by myosotis's SkillTextPatch
                        mine = s.get("texts") or {}
                        if lortext.uses_lor(mine):
                            try:
                                mine = lortext.resolve(mine, ruina) if ruina else None
                            except lortext.LorTextError as e:
                                said.append(str(e))
                                mine = None
                            if mine is None:                # the skill text without Ruina's words
                                mine = {n: {k: v for k, v in t.items() if not k.startswith("lor_")}
                                        for n, t in (s.get("texts") or {}).items()}
                        texts.update(helpers.skill_texts(ident, skills, mine, s.get("moves")))
                        said.append("own skill text")
                    if s.get("stats"):
                        helpers.edit_stats(dict(ident), s["stats"])         # refused here, not mid-rewrite
                        stats[s["identity"]] = s["stats"]
                        said.append("stats: " + ", ".join(f"{k} {v}" for k, v in sorted(s["stats"].items())))
                    boss_notes[s["key"]] = "; ".join(said)
                    boss_ok.append(s["key"])
                except (KeyError, StopIteration, ValueError, IndexError, TypeError, AttributeError) as e:
                    boss_notes[s["key"]] = f"skills unchanged ({e or 'no boss record'})"
        out = os.path.join(built_root, "helpers")
        shutil.rmtree(out, ignore_errors=True)
        if swaps or skill_swaps or clones or passives or texts or stats:
            static_path, _ = game.find_static(cache)
            raw, done, _ = helpers.rewrite_static(static_path, swaps, skill_swaps, clones, passives, stats)
            os.makedirs(out)
            rel = _write(out, cache, static_path, raw)
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "kind": "helpers", "skins": [s["key"] for s in want + want_ids if s["key"] not in bad],
                           "skills": boss_ok, "files": [rel], "green_skills": sorted(green),
                           "skill_texts": {str(i): t for i, t in sorted(texts.items())},
                           # the server grants these while they're built (Server/OwnIdentities.cs)
                           "identities": [dict({"id": s["identity"], "base": s["base"]},
                                               **({"unlock": s["unlock"]} if s.get("unlock") else {}))
                                          for s in want_ids if s["key"] not in bad]}, f, indent=1)
            for s in want + want_ids:
                if s["key"] not in bad:
                    ok = s["identity"] in done
                    results[s["key"]] = (ok, "; ".join(["built"] + ([kit_notes[s["key"]]] if s["key"] in kit_notes else []))
                                         if ok else "identity not found in the static data")
            log(f"  helpers: {len(done)} identities restyled, {len(boss_ok)} with new skills")

    # repaints and tints: one folder each
    want = [skins[k] for k in keys if skins[k]["kind"] in ("repaint", "tint")]
    pers_all = None                                 # identity records, for a shift on a skill
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
                if (s.get("shift") or {}).get("skill") and pers_all is None:
                    pers_all = game.boss_data(cache)[0]
                shift = shift_rule(s, ids, (pers_all or {}).get(s["identity"]))
                if s["kind"] == "tint":
                    raw, notes = tint.tint(target, locate, s["tint"], stick=s.get("stick"))
                else:
                    raw, notes = repaint.repaint(
                        target, locate, source=None if s.get("self") else s.get("source"),
                        clips=s.get("clips"), mapping=s.get("map"),
                        head=_head(s, data_dir), mirror=s.get("mirror", True), dash=s.get("dash"),
                        forms=s.get("forms"), lor=s.get("lor"), aura=s.get("aura"))
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                rels = [_write(out, cache, locate(target), raw)]
                if shift and s["kind"] == "tint":    # the body it turns into, the same colour
                    if locate(shift["to"]) == locate(target):
                        raise repaint.RepaintError(f"{shift['to']} shares {target}'s bundle")
                    more, more_notes = tint.tint(shift["to"], locate, s["tint"])
                    rels.append(_write(out, cache, locate(shift["to"]), more))
                    notes = notes + [f"{shift['to']}: " + "; ".join(more_notes)]
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump(dict({"format": FORMAT, "kind": s["kind"], "skins": [key], "files": rels, "notes": notes},
                                   **({"shift": shift} if shift else {})), f, indent=1)
                results[key] = (True, "; ".join(notes + ([boss_notes[key]] if key in boss_notes else [])))
            except (repaint.RepaintError, RuntimeError, KeyError, OSError, ValueError) as e:
                shutil.rmtree(out, ignore_errors=True)
                results[key] = (False, str(e))
            log(f"  {'ok  ' if results[key][0] else 'FAIL'} {key}: {results[key][1]}")
    return results


def shift_rule(skin, ids, identity_rec=None):
    """A skin's "shift" ({"to": identity id, "hp": percent and/or "skill": its slot "1", "2"...})
    as the client patch takes it: {"from": "<identity>_" (any body of the skin's identity - LCB
    Heathcliff has a Canto VI one too), "to": that identity's body, "hp": percent, "skill": skill id}
    - the unit turns into the other body the first time its HP falls below that percent of its max,
    or when it starts that skill (myosotis's HpShiftPatch). identity_rec: the skin identity's
    record, to find the skill's id. None without a shift; ValueError for one that can't work."""
    sh = skin.get("shift")
    if not sh:
        return None
    to = ids.get(sh.get("to"), {}).get("appearance")
    if not to or skin["identity"] not in ids:
        raise ValueError(f"shift: identity {sh.get('to')} isn't in this game version")
    out = {"from": f"{skin['identity']}_", "to": to}
    if "hp" in sh:
        if not isinstance(sh["hp"], int) or not 1 <= sh["hp"] <= 99:
            raise ValueError("shift: hp must be a percent, 1-99")
        out["hp"] = sh["hp"]
    if "skill" in sh:
        mine = [a["skillId"] for a in helpers.lit((identity_rec or {}).get("attributeList"))]
        n = str(sh["skill"])
        if not n.isdigit() or not 1 <= int(n) <= len(mine):
            raise ValueError(f"shift: the identity has no skill {n}")
        out["skill"] = mine[int(n) - 1]
    if "hp" not in out and "skill" not in out:
        raise ValueError("shift: needs an hp percent or a skill")
    return out


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
        folders = ["helpers"] if k in skins and shares_static(skins[k]) else [k]
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
