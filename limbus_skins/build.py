"""Building skins from the installed game into ready-to-swap files.

Each built skin is a folder that mirrors the cache layout
(<name hash>/<content hash>/__data + __info), already carrying the stock CRC, plus a
manifest. Helper skins all edit the one static-data bundle, so they build together
into a single "helpers" folder.
"""
import json
import re
import os
import shutil
from importlib import resources

from . import art, audit, bundle, cards, covers, game, helpers, lortext, repaint, tint

FORMS = ".forms"  # built/<skin>.forms: another body a skin turns into, swapped in apart from it
CARD = ".card"    # built/<skin>.card: the identity's card pictures, when they aren't in its body's file
COVER = ".cover"  # built/<ally>.cover: an identity of our own's own card pictures (covers.py), not swapped in
FORMAT = 28      # bump when built skins change shape: older builds are rebuilt on apply


def catalog():
    with resources.files("limbus_skins").joinpath("data/skins.json").open(encoding="utf-8") as f:
        doc = json.load(f)
    skins = {}
    for kind, one in (("helpers", "helper"), ("repaints", "repaint"), ("tints", "tint"), ("identities", "identity"),
                      ("bosses", "boss"), ("kits", "kit")):
        for key, e in doc.get(kind, {}).items():
            skins[key] = dict(e, key=key, kind=one)
    skins = with_allies(skins)
    check_copies(skins)
    return skins


def character(skins, key):
    """The skin that stands for key's character: "dupe_of" (the same character, in the same
    body) followed to the one that has none. One copy of a character is on at a time."""
    seen = set()
    while skins.get(key, {}).get("dupe_of"):
        if key in seen:
            raise ValueError(f"skins.json: {key}'s dupe_of goes round in a circle")
        seen.add(key)
        key = skins[key]["dupe_of"]
    return key


def check_copies(skins):
    """Raise ValueError for a "dupe_of" that names no skin, or one that loops."""
    for key, s in skins.items():
        if s.get("dupe_of") and s["dupe_of"] not in skins:
            raise ValueError(f"skins.json: {key} is a copy of '{s['dupe_of']}', which is no skin")
        character(skins, key)


def display_name(s, key):
    """The name an identity of ours shows in game: its own, else the short form of what it becomes."""
    return s.get("name") or short_name(s.get("becomes") or s.get("title") or key)


def short_name(text):
    """A skin's name as the game shows an identity's: its words up to a bracket, colon or dash."""
    name = re.split(r"[:;(]| - ", text)[0].strip()
    return name[:1].upper() + name[1:]


def with_allies(skins):
    """skins, plus "<key>-id" for each skin with an "ally" id: its character as an identity of its
    own (kind "identity"), equipable beside the skin's identity - a boss in its own body with its
    whole moveset, a helper with its kit - showing that identity's card. "ally_unit" and
    "ally_unlock" choose its boss record and lock apart from the skin's; its base record is
    chosen at build time (helpers.ally_base). The skin and its ally are one character in one
    body ("dupe_of"): the ally stands for it, and "ally_dupe_of" names the ally's own copy."""
    out = dict(skins)
    for key, s in skins.items():
        if not s.get("ally"):
            continue
        k = key + "-id"
        if k in skins:
            raise ValueError(f"skins.json: {key}'s ally would be '{k}', which is another skin")
        out[key] = dict(s, dupe_of=s.get("dupe_of") or k)
        a = {"key": k, "kind": "identity", "identity": s["ally"], "of": s["identity"], "folder": s["identity"],
             "becomes": f"{s.get('becomes') or key} - as an identity of its own", "tested": False}
        a.update({f: s[f] for f in ("source", "helper", "script", "cover", "cover_face", "cover_zoom") if f in s})
        unit, unlock = s.get("ally_unit", s.get("unit")), s.get("ally_unlock", s.get("unlock"))
        if unit is not None:
            a["unit"] = unit
        if unlock:
            a["unlock"] = unlock
        a["title"] = f"{s.get('title') or s.get('becomes') or key} - ally"    # how the forest's gifts name it
        a["name"] = display_name(s, key)
        if s.get("ally_dupe_of"):
            a["dupe_of"] = s["ally_dupe_of"]
        out[k] = a
    return out


def shares_static(skin):
    """Built into the one shared static-data file (helpers, boss skins, identities of our own, and
    kits: an identity's moves and text, its body left as it is - for a body mod's character)."""
    return skin["kind"] in ("helper", "identity", "boss", "kit")


def boss_form(key, skin, boss=()):
    """Does the skin wear its boss's own body and skills (helpers.boss_moveset)? Boss skins always do,
    and so does an identity of our own with a "source" (an ally: the boss as a new identity); a
    repaint of a boss does with boss on - and then its repainted body isn't built or swapped in."""
    return (skin["kind"] == "boss" or (skin["kind"] == "identity" and bool(skin.get("source")))
            or (skin["kind"] == "repaint" and key in boss and bool(skin.get("source"))))


def card_identity(skin):
    """Whose card pictures a skin's "card" art redraws: an identity of our own shows its art
    folder's (or its base's), any other skin its own identity's."""
    if skin["kind"] == "identity":
        return skin.get("folder") or skin["base"]
    return skin["identity"]


def _card_art(skin, data_dir):
    return cards.load_art(cards.art_dir(data_dir, skin["card"]))


def head_recipe(name):
    with resources.files("limbus_skins").joinpath(f"data/heads/{name}").open(encoding="utf-8") as f:
        return json.load(f)


def _head(skin, data_dir):
    """The skin's head recipe, with its art: the player's own (limbus-skins art), else the one
    shipped with limbus-skins."""
    if not skin.get("head"):
        return None
    recipe = head_recipe(skin["head"])
    mine = art.head_art(data_dir, skin["key"])
    if mine:
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
    """The skins among keys whose records go into the static-data bundle: boss forms (boss skins,
    and repaints of a boss with boss on), and skins that recolor a skill's sin, rewrite its moves
    or text, swap its passives or change its stats."""
    return {k for k in keys if any(skins[k].get(f) for f in ("sins", "moves", "passives", "texts", "stats", "green_coins"))
            or boss_form(k, skins[k], boss)}


def build(keys, game_dir, cache, data_dir, log=print, boss=(), static=True):
    """Build the given skin keys; repaints in `boss` become their boss (boss_form).
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
    forms = [s for s in want_boss if s["kind"] == "boss"]     # boss skins: nothing but the static file
    if want or want_boss or want_ids:
        swaps, skill_swaps, bad, boss_ok, kit_notes, green, passives, texts, stats = {}, {}, [], [], {}, set(), {}, {}, {}
        pers, skills, units = game.boss_data(cache)
        clones, folders, ruina = {}, {}, None
        known_passives = []

        def passive_ids():                          # every passive the game has, read once if needed
            if not known_passives:
                known_passives.append({r["id"] for r in game.static_lists(game.find_static(cache)[1], ("passive",))["passive"]})
            return known_passives[0]

        dressed = want + [s for s in want_ids if s.get("helper")]   # helper skins, and helpers' allies
        located = lambda b: None  # noqa: E731 - set below when a helper or a boss form is wanted
        if dressed or any(boss_form(s["key"], s, boss) for s in want_boss):  # the motions the body worn has
            bodies_, have_ = game.body_index(game_dir, cache, os.path.join(data_dir, "bodies.json"), log=log)
            located = lambda b: have_.get(bodies_.get(b, ""))  # noqa: E731
        if any(lortext.uses_lor(s.get("texts")) or lortext.uses_dice(s.get("moves")) for s in want_boss):
            try:                                    # Ruina's own words: shipped, else the player's install
                ruina = lortext.shipped_book() or lortext.Book(lortext.read_tables())
            except (lortext.LorTextError, OSError) as e:
                log(f"  Library of Ruina's text: {e}")
        for s in want_ids:
            try:
                if not s.get("base"):               # an ally: the record it copies is chosen now
                    s["base"] = helpers.ally_base(pers, s["of"])
                helpers.check_new_ids(set(ids), {s["identity"]: s["base"]})
                clones[s["identity"]] = s["base"]
                if s.get("folder"):                 # the card art it shows: that identity's
                    folders[s["identity"]] = s["folder"]
            except ValueError as e:
                bad.append(s["key"])
                results[s["key"]] = (False, str(e))
        # a borrowed body with no death of its own dies as its sinner (myosotis's hp_shift "from>to!"):
        # the sinner's own body, whose death the game then plays
        deaths = {}

        def no_death(s, body, motions):
            base = pers.get(s["identity"] // 100 * 100 + 1)
            if "Dead" not in motions and base and base.get("appearance") and base["appearance"] != body:
                deaths[s["key"]] = {"from": body, "to": base["appearance"]}

        for s in dressed:
            ally = s["kind"] == "identity"          # a helper as an identity of its own: dresses its clone
            if ally and s["identity"] not in clones:
                continue                            # refused above
            h = helper_units.get(s["helper"])

            def refuse(why):
                bad.append(s["key"])
                results[s["key"]] = (False, why)
                if ally:                            # no bare copy of its base either
                    clones.pop(s["identity"], None)
                    folders.pop(s["identity"], None)
            if h is None or (not ally and s["identity"] not in ids):
                refuse("not in this game version")
                continue
            try:
                ident = dict(pers[s["base"]], id=s["identity"]) if ally else pers[s["identity"]]
                where = located(h["appearance"])
                if not where:
                    raise ValueError(f"{h['appearance']} is not in the game's cache")
                worn = repaint.body_motions(game.unitypy().load(where), h["appearance"])
                attrs, guard, dropped = helpers.helper_kit(ident, h, skills, bool(s.get("script")), worn)
            except (KeyError, ValueError, OSError, repaint.RepaintError) as e:
                refuse(str(e))
                continue
            findings = audit.record(dict(h, attributeList=[{"skillId": a["skillId"], "number": 1} for a in attrs],
                                         defenseSkillIDList=guard), skills, worn)   # as written: each skill once
            if findings:
                refuse("audit: " + "; ".join(findings))
                continue
            swaps[s["identity"]] = dict(h, attributeList=attrs, defenseSkillIDList=guard, _script=bool(s.get("script")))
            no_death(s, h["appearance"], worn)
            if dropped:
                kit_notes[s["key"]] = "left out: " + ", ".join(f"{i} ({why})" for i, why in dropped)
        for s in want_boss:
            # every edit of one skin lands together or not at all: half a boss (its skills on a
            # body that can't play them) would freeze the fight
            mine_skills, mine_texts, mine_green, said = {}, {}, set(), []
            mine_swap = mine_passives = mine_stats = worn = None
            try:
                if s["kind"] == "identity":              # a new id: its base's record, under its own id
                    if s["identity"] not in clones:
                        continue                            # refused above (an id the game has, no base)
                    ident = dict(pers[s["base"]], id=s["identity"])
                else:
                    ident = pers[s["identity"]]
                base = lambda: {**skill_swaps, **mine_skills}  # noqa: E731 - edits stack on earlier ones
                if boss_form(s["key"], s, boss):                      # the boss's own body and whole moveset
                    unit = helpers.pick_unit(units[s["source"]], skills, s.get("unit"))
                    where = located(unit["appearance"])
                    if not where:
                        raise ValueError(f"{unit['appearance']} is not in the game's cache")
                    worn = repaint.body_motions(game.unitypy().load(where), unit["appearance"])
                    no_death(s, unit["appearance"], worn)
                    attrs, guard = helpers.boss_moveset(ident, unit, skills, worn)
                    mine_swap = {"appearance": unit["appearance"], "attributeList": attrs,
                                 "defenseSkillIDList": guard, "_numbers": True}
                    ident = dict(ident, attributeList=attrs, defenseSkillIDList=guard)   # what's below acts on them
                    said.append(f"the boss's own body {unit['appearance']} and moveset (unit {unit['id']}: "
                                + ", ".join(f"{a['skillId']}x{a['number']}" for a in attrs) + f"; defense {guard[0]})")
                if s.get("moves"):                          # each edit stacks on the ones before it
                    moves = lortext.resolve_moves(s["moves"], ruina)    # Ruina's dice, read from its install
                    mine_skills.update(helpers.move_skills(ident, skills, moves, base()))
                    said.append(f"own moves for S{', S'.join(sorted(s['moves']))}")
                if s.get("passives"):
                    mine_passives = s["passives"]
                    said.append("passives: " + ", ".join(str(i) for i in s["passives"].get("battle", [])
                                                       + s["passives"].get("support", [])))
                if s.get("sins"):
                    recoloured = helpers.recolor_skills(ident, skills, s["sins"], base())
                    mine_skills.update(recoloured)
                    mine_green.update(helpers.green_ids(recoloured))
                    said.append("sins: " + ", ".join(f"{n} {v}" for n, v in sorted(s["sins"].items())))
                if s.get("green_coins"):                    # green coins, keeping the skills' own sins
                    mine_green.update(helpers.all_skill_ids(ident))
                    said.append("green coins")
                if s.get("texts") or s.get("moves"):        # shown by myosotis's SkillTextPatch
                    mine = s.get("texts") or {}
                    if lortext.uses_lor(mine):
                        try:
                            mine = lortext.resolve(mine, ruina) if ruina else None
                        except lortext.LorTextError as e:
                            said.append(str(e))
                            mine = None
                        if mine is None:                    # the skill text without Ruina's words
                            mine = {n: {k: v for k, v in t.items() if not k.startswith("lor_")}
                                    for n, t in (s.get("texts") or {}).items()}
                    mine_texts.update(helpers.skill_texts(ident, skills, mine, s.get("moves")))
                    said.append("own skill text")
                if s.get("stats"):
                    helpers.edit_stats(dict(ident), s["stats"])             # refused here, not mid-rewrite
                    mine_stats = s["stats"]
                    said.append("stats: " + ", ".join(f"{k} {v}" for k, v in sorted(s["stats"].items())))
                # the audit: the record as the file will have it (a boss form: on the boss's body)
                own = mine_passives and {"battlePassiveList": [{"passiveIDList": mine_passives.get("battle", [])}],
                                         "supporterPassiveList": [{"passiveIDList": mine_passives.get("support", [])}]}
                findings = audit.record(ident, {**skills, **skill_swaps, **mine_skills}, worn,
                                        passive_ids() if own else None, own or None)
                if findings:
                    raise ValueError("audit: " + "; ".join(findings))
            except (KeyError, StopIteration, ValueError, IndexError, TypeError, AttributeError,
                    repaint.RepaintError, lortext.LorTextError, OSError) as e:
                if boss_form(s["key"], s, boss):                      # a boss form that can't be built isn't swapped in
                    bad.append(s["key"])
                    results[s["key"]] = (False, f"no boss form: {e or 'no boss record'}")
                    if s["kind"] == "identity":         # nor is the ally's id: no bare copy of its base
                        clones.pop(s["identity"], None)
                        folders.pop(s["identity"], None)
                boss_notes[s["key"]] = f"skills unchanged ({e or 'no boss record'})"
                continue
            skill_swaps.update(mine_skills)
            texts.update(mine_texts)
            green.update(mine_green)
            if mine_swap:
                swaps[s["identity"]] = mine_swap
            if mine_passives:
                passives[s["identity"]] = mine_passives
            if mine_stats:
                stats[s["identity"]] = mine_stats
            boss_notes[s["key"]] = "; ".join(said)
            boss_ok.append(s["key"])
        out = os.path.join(built_root, "helpers")
        shutil.rmtree(out, ignore_errors=True)
        if swaps or skill_swaps or clones or passives or texts or stats:
            static_path, _ = game.find_static(cache)
            raw, done, _ = helpers.rewrite_static(static_path, swaps, skill_swaps, clones, passives, stats, folders)
            os.makedirs(out)
            rel = _write(out, cache, static_path, raw)
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "kind": "helpers",
                           "skins": [s["key"] for s in want + want_ids + forms if s["key"] not in bad],
                           "skills": boss_ok, "files": [rel], "green_skills": sorted(green),
                           "skill_texts": {str(i): t for i, t in sorted(texts.items())},
                           # the server grants these while they're built (Server/OwnIdentities.cs)
                           "identities": [dict({"id": s["identity"], "base": s["base"], "folder": s.get("folder") or s["base"],
                                                "name": display_name(s, s["key"])},
                                               **({"unlock": s["unlock"]} if s.get("unlock") else {}))
                                          for s in want_ids if s["key"] not in bad],
                           "deaths": {k: v for k, v in sorted(deaths.items()) if k not in bad}}, f, indent=1)
            for s in want + want_ids:
                if s["key"] not in bad:
                    ok = s["identity"] in done
                    results[s["key"]] = (ok, "; ".join(["built"] + ([kit_notes[s["key"]]] if s["key"] in kit_notes else []))
                                         if ok else "identity not found in the static data")
            for s in want_boss:
                if boss_form(s["key"], s, boss) and s["key"] not in bad:
                    ok = s["identity"] in done
                    results[s["key"]] = (ok, boss_notes[s["key"]] if ok else "identity not found in the static data")
                    if ok:                              # a body built before (as a repaint) is never used again
                        for folder in (s["key"], s["key"] + FORMS):
                            shutil.rmtree(os.path.join(built_root, folder), ignore_errors=True)
            log(f"  helpers: {len(done)} identities restyled, {len(boss_ok)} with new skills")
        for s in want_boss:
            if s["kind"] == "kit":                      # nothing but the static file: its edits are its result
                results[s["key"]] = (s["key"] in boss_ok, boss_notes[s["key"]])

    # repaints and tints: one folder each
    want = [skins[k] for k in keys if skins[k]["kind"] in ("repaint", "tint") and not boss_form(k, skins[k], boss)]
    pers_all = None                                 # identity records, for a shift on a skill
    merged = set()                                  # skins whose card went into their body's file
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
                if s.get("card"):                   # its card, where the card pictures share the body's file
                    try:
                        env = game.unitypy().load(raw)
                        if cards.has_card(env, card_identity(s)):
                            n = cards.apply(env, card_identity(s), _card_art(s, data_dir))
                            raw = env.file.save(packer="none")
                            merged.add(key)
                            notes = notes + [f"card art: {n} pictures"]
                    except cards.CardError as e:
                        merged.add(key)             # nothing to put in a card file either
                        notes = notes + [str(e)]
                findings = audit.body(locate(target), raw, target)
                if findings:                        # never swapped in: it would draw wrong in a fight
                    raise repaint.RepaintError("audit: " + "; ".join(findings))
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                rels = [_write(out, cache, locate(target), raw)]
                extra = out + FORMS
                shutil.rmtree(extra, ignore_errors=True)
                if shift and s["kind"] == "tint":    # the body it turns into, the same colour - in a
                    if locate(shift["to"]) == locate(target):   # folder of its own, so a clash over it
                        raise repaint.RepaintError(f"{shift['to']} shares {target}'s bundle")  # skips only it
                    more, more_notes = tint.tint(shift["to"], locate, s["tint"])
                    findings = audit.body(locate(shift["to"]), more, shift["to"])
                    if findings:
                        raise repaint.RepaintError(f"audit of {shift['to']}: " + "; ".join(findings))
                    os.makedirs(extra)
                    rel = _write(extra, cache, locate(shift["to"]), more)
                    with open(os.path.join(extra, "manifest.json"), "w", encoding="utf-8") as f:
                        json.dump({"format": FORMAT, "kind": s["kind"], "skins": [key], "files": [rel]}, f, indent=1)
                    notes = notes + [f"{shift['to']}: " + "; ".join(more_notes)]
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump(dict({"format": FORMAT, "kind": s["kind"], "skins": [key], "files": rels, "notes": notes},
                                   **({"shift": shift} if shift else {})), f, indent=1)
                results[key] = (True, "; ".join(notes + ([boss_notes[key]] if key in boss_notes else [])))
            except (repaint.RepaintError, RuntimeError, KeyError, OSError, ValueError) as e:
                shutil.rmtree(out, ignore_errors=True)
                shutil.rmtree(out + FORMS, ignore_errors=True)
                results[key] = (False, str(e))
            log(f"  {'ok  ' if results[key][0] else 'FAIL'} {key}: {results[key][1]}")

    # card pictures in a file of their own: built/<skin>.card
    if static:
        for key in keys:
            s, out = skins[key], os.path.join(built_root, key + CARD)
            if not s.get("card") or key in merged or not results.get(key, (False,))[0]:
                shutil.rmtree(out, ignore_errors=True)
                continue
            try:
                art_ = _card_art(s, data_dir)
                path = cards.find_bundle(game_dir, cache, card_identity(s))
                env = game.unitypy().load(path)
                n = cards.apply(env, card_identity(s), art_)
                raw = env.file.save(packer="none")
                findings = audit.card(path, raw, card_identity(s))
                if findings:
                    raise cards.CardError("audit: " + "; ".join(findings))
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(out)
                rel = _write(out, cache, path, raw)
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump({"format": FORMAT, "kind": "card", "skins": [key], "files": [rel],
                               "identity": card_identity(s)}, f, indent=1)
                note = f"card art: {n} pictures of {card_identity(s)}"
            except (cards.CardError, OSError) as e:
                shutil.rmtree(out, ignore_errors=True)
                note = f"no card art ({e})"
            ok, msg = results[key]
            results[key] = (ok, f"{msg}; {note}")
            log(f"  card {key}: {note}")

    # covers: an identity of our own's own card pictures - its villain's story portrait, and its pfp
    # from the body it wears standing idle in a fight (that frame for everything when it has no portrait)
    if static:
        wanted = [k for k in keys if skins[k]["kind"] == "identity" and (skins[k].get("cover") or skins[k].get("cover_art")
                  or skins[k].get("source") or skins[k].get("helper")) and results.get(k, (False,))[0]]
        for k in keys:
            if k not in wanted:
                shutil.rmtree(os.path.join(built_root, k + COVER), ignore_errors=True)
        found, idles = {}, {}
        body_of = {k: covers.idle_body(skins[k], ids, helper_units) for k in wanted}
        if wanted:
            try:
                found = covers.portraits(game_dir, cache, {skins[k]["cover"] for k in wanted if skins[k].get("cover")},
                                         os.path.join(data_dir, "art", "covers"))
            except (OSError, ValueError) as e:
                log(f"  covers: the story portraits couldn't be read ({e})")
            try:
                idles = covers.idle_frames(game_dir, cache, data_dir, set(body_of.values()),
                                           os.path.join(data_dir, "art", "covers", "idle"), log=log)
            except (OSError, ValueError) as e:
                log(f"  covers: the idle frames couldn't be read ({e})")
        for k in wanted:
            s, out = skins[k], os.path.join(built_root, k + COVER)
            try:
                portrait, idle = found.get(s.get("cover")), idles.get(body_of[k])
                face, zoom = s.get("cover_face"), s.get("cover_zoom")
                if s.get("cover_art"):                  # <data dir>/art/cards/<name>/, else the shipped one
                    mine = cards.load_art(cards.art_dir(data_dir, s["cover_art"]))
                    portrait = mine["normal"]
                    face = face or mine["spec"].get("face")
                    zoom = zoom if zoom is not None else mine["spec"].get("zoom")
                if portrait is None and idle is None:
                    raise covers.CoverError(f"no story portrait {s.get('cover')} nor idle frame of {body_of[k]} in the game's cache")
                stock = covers.stock_pictures(game_dir, cache, s.get("folder") or s["base"])
                shutil.rmtree(out, ignore_errors=True)
                os.makedirs(os.path.join(out, "pics"))
                files = []
                art, face = (portrait, face) if portrait is not None else (idle, covers.sprite_face(idle))
                for suffix, im in covers.pictures(art, stock, face, float(zoom if zoom is not None else 1.0) if portrait is not None else 1.0,
                                                  pfp=idle).items():
                    files.append(covers.file_name(s["identity"], suffix))
                    with open(os.path.join(out, "pics", files[-1]), "wb") as f:
                        f.write(covers.dxt1_file(im))
                made = ([f"art {s['cover_art']}"] if s.get("cover_art") else [f"story {s['cover']}"]) if portrait is not None else []
                made += [f"idle {body_of[k]}"] if idle is not None else []
                with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                    json.dump({"format": FORMAT, "kind": "cover", "skins": [k], "identity": s["identity"],
                               "from": " + ".join(made), "files": files}, f, indent=1)
                note = "cover: " + " + ".join(made)
            except (covers.CoverError, cards.CardError, OSError, ValueError) as e:
                shutil.rmtree(out, ignore_errors=True)
                note = f"no cover ({e}): it shows its folder's card"
            ok, msg = results[k]
            results[k] = (ok, f"{msg}; {note}")
            log(f"  cover {k}: {note}")

    # every other skin's pfp: its body standing idle in a fight
    if static:
        _pfps(keys, skins, results, ids, helper_units, set(boss), game_dir, cache, data_dir, built_root, log)
    # the name each skin's identity shows in game: the character it became, not the identity it
    # borrowed (Matthias, not "Ring Pointillist Student") - names.json, read by client.names
    named = [{"skin": k, "identity": skins[k]["identity"], "name": display_name(skins[k], k)}
             for k in keys if skins[k]["kind"] in NAME_KINDS and results.get(k, (False,))[0]]
    try:
        with open(os.path.join(built_root, "names.json"), "w", encoding="utf-8") as f:
            json.dump({"format": FORMAT, "names": named}, f, indent=1, ensure_ascii=False)
    except OSError as e:
        log(f"  names: {e}")
    return results


NAME_KINDS = ("repaint", "tint", "helper", "boss", "kit")    # skins that make an identity someone else


PFP_KINDS = ("repaint", "tint", "helper", "boss")    # a kit changes moves only: it looks as it did


def pfp_body(key, skin, ids, helper_units, boss=()):
    """(appearance, built) of the body a skin's pfp shows: built when it's the skin's own build (a
    repaint or a tint, in its own colours), else the body as the game draws it (a helper's, a boss's)."""
    if skin["kind"] == "helper":
        return (helper_units.get(skin.get("helper")) or {}).get("appearance"), False
    if boss_form(key, skin, boss):
        return skin.get("source"), False
    return (ids.get(skin["identity"]) or {}).get("appearance"), True


def _pfps(keys, skins, results, ids, helper_units, boss, game_dir, cache, data_dir, built_root, log):
    """built/<skin>.cover holding one picture, the identity's pfp: the skin's body standing idle in a
    fight, framed on its face. The client patch shows it for that identity's id while the skin is on
    (client.py lists the identity with no name of its own: its card and name stay the game's)."""
    want = [k for k in keys if skins[k]["kind"] in PFP_KINDS and results.get(k, (False,))[0]]
    for k in keys:
        if skins[k]["kind"] != "identity" and k not in want:
            shutil.rmtree(os.path.join(built_root, k + COVER), ignore_errors=True)
    if not want:
        return
    body = {k: pfp_body(k, skins[k], ids, helper_units, boss) for k in want}
    cached = {}
    try:
        cached = covers.idle_frames(game_dir, cache, data_dir, {a for a, own in body.values() if a and not own},
                                    os.path.join(data_dir, "art", "covers", "idle"), log=log)
    except (OSError, ValueError) as e:
        log(f"  pfps: the idle frames couldn't be read ({e})")
    for k in want:
        s, out = skins[k], os.path.join(built_root, k + COVER)
        app, own = body[k]
        try:
            if not app:
                raise covers.CoverError("its body isn't known")
            if own:
                try:
                    idle = covers.built_idle(os.path.join(built_root, k, "files"), app)
                except Exception as e:              # a body that won't render keeps the game's pfp
                    raise covers.CoverError(f"its idle didn't render: {e}")
            else:
                idle = cached.get(app)
            if idle is None:
                raise covers.CoverError(f"no idle frame of {app}")
            pic = covers.profile(idle, covers.stock_pictures(game_dir, cache, s["identity"]))
            name = covers.file_name(s["identity"], "_profile")
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(os.path.join(out, "pics"))
            with open(os.path.join(out, "pics", name), "wb") as f:
                f.write(covers.dxt1_file(pic))
            with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"format": FORMAT, "kind": "cover", "pfp": True, "skins": [k], "identity": s["identity"],
                           "from": f"idle {app}", "files": [name]}, f, indent=1)
            note = f"pfp: idle {app}"
        except (covers.CoverError, cards.CardError, OSError, ValueError) as e:
            shutil.rmtree(out, ignore_errors=True)
            note = f"no pfp ({e}): the game's own"
        ok, msg = results[k]
        results[k] = (ok, f"{msg}; {note}")
        log(f"  pfp {k}: {note}")


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
    cards_seen = set()                      # card files: one per game file (two skins may draw the same card)
    for k in keys:
        folders = ["helpers"] if k in skins and (shares_static(skins[k]) or boss_form(k, skins[k], boss)) else [k, k + FORMS]
        if k in edits and k in man.get("skills", []):
            folders.append("helpers")
        folders.append(k + CARD)
        for folder in folders:
            files = os.path.join(root, folder, "files")
            if folder in seen or not os.path.isdir(files):
                continue
            if folder == "helpers" and k not in man.get("skins", []) + man.get("skills", []):
                continue                    # built without this skin - needs a rebuild
            if folder.endswith(CARD):
                try:
                    with open(os.path.join(root, folder, "manifest.json"), encoding="utf-8") as f:
                        rels = tuple(json.load(f).get("files", []))
                except (OSError, ValueError):
                    continue
                if rels in cards_seen:
                    continue
                cards_seen.add(rels)
            seen.add(folder)
            out.append((folder, files))
    return out
