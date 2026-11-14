#!/usr/bin/env python3
"""Write the Library of Ruina roster into skins.json: one identity per character, each with a
body and skills of its own, its kit read off its Ruina deck.

    python tools/roster.py            (needs Library of Ruina and Limbus Company installed)

One line per character in ROSTER below is the whole workflow: add a line, run this, build.
Entries it wrote before (marked "roster") are replaced; nothing else in skins.json is touched.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from limbus_skins import game, helpers, lor, lortext, paths            # noqa: E402

SKINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "limbus_skins", "data", "skins.json")

# key, shown name, its line under the name, sinner slot (1 Yi Sang .. 12 Gregor), Ruina prefab,
# the prefab whose deck it plays (None: its own; a list: these pages - the Purple Tear's come
# with her stances, not from a deck), sins for skills 1-3 and the defense,
# what it becomes below half HP (prefab, word) or None
ROSTER = [
    ("roland-ruina", "Roland", "The Black\nSilence", 1, "BlackSilence", "BlackSilenceMask", ("INDIGO", "INDIGO", "VIOLET", "INDIGO"), None),
    ("argalia-ruina", "Argalia", "The Blue\nReverberation", 6, "Argalia2", None, ("AZURE", "AZURE", "INDIGO", "AZURE"), ("TwistedArgalia", "Distorted")),
    ("iori-ruina", "Iori", "The Purple\nTear", 9, "ThePurpleTear",
     ["609002", "609003", "609006", "609008", "609001", "609010", "609011"], ("VIOLET", "VIOLET", "VIOLET", "VIOLET"), None),
    ("vermilion-cross-ruina", "Vermilion Cross", "The Vermilion\nCross", 7, "OrangeCross", None, ("CRIMSON", "SCARLET", "CRIMSON", "SCARLET"), None),
    ("xiao-ruina", "Xiao", "Iron Lotus", 8, "Shyao", None, ("CRIMSON", "CRIMSON", "SCARLET", "CRIMSON"), ("XiaoEgo", "Ego")),
    ("philip-ruina", "Philip", "Dawn Office\nFixer", 10, "Philip", None, ("CRIMSON", "CRIMSON", "AMBER", "CRIMSON"), ("PhilipEgo", "Ego")),
    ("yuna-ruina", "Yuna", "Dawn Office\nFixer", 2, "Yuna", None, ("AMBER", "AMBER", "SHAMROCK", "AMBER"), None),
    ("salvador-ruina", "Salvador", "Dawn Office\nRepresentative", 12, "Salvador", None, ("CRIMSON", "SCARLET", "SCARLET", "CRIMSON"), None),
    ("oscar-ruina", "Oscar", "Wedge Office\nRepresentative", 11, "Oscar", None, ("INDIGO", "INDIGO", "AZURE", "INDIGO"), None),
    ("pamela-ruina", "Pamela", "Wedge Office\nFixer", 3, "Pamela", None, ("INDIGO", "AZURE", "INDIGO", "AZURE"), None),
    ("pameli-ruina", "Pameli", "Wedge Office\nFixer", 4, "Pameli", None, ("AZURE", "INDIGO", "AZURE", "INDIGO"), None),
]
# The same people as Limbus Company draws them (its own boss units): key, name, line, sinner
# slot, the boss's body, its unit. Each wears the boss's own body and plays its whole moveset.
LIMBUS = [
    ("philip-limbus", "Philip", "Dawn Office\nFixer", 10, "1582_PhilipAppearance", 1582),
    ("yuna-limbus", "Yuna", "Dawn Office\nFixer", 2, "1581_YunaAppearance", 1581),
    ("salvador-limbus", "Salvador", "Dawn Office\nRepresentative", 12, "1580_SalvadorAppearance", 1580),
    ("oscar-limbus", "Oscar", "Wedge Office\nRepresentative", 11, "1583_OscarAppearance", 1583),
    ("pamela-limbus", "Pamela", "Wedge Office\nFixer", 3, "1585_PamelaAppearance", 1585),
    ("pameli-limbus", "Pameli", "Wedge Office\nFixer", 4, "1584_PamelyAppearance", 1584),
]
FIRST_ID = 30                       # identities 1SS30 and up are the roster's
# The identity a roster character is drawn on and copies its record from: the sinner's first,
# except where that body can't be redrawn (no atlas pages of its own) - then one that can.
BASE = {3: 10309, 4: 10410, 11: 11110}


def ruina():
    """({prefab: bundle}, {prefab: [card ids of its fullest deck]}, the book of pages)."""
    tables = {}
    env = game.unitypy().load(lortext.assets_path())
    for o in env.objects:
        if o.type.name != "TextAsset":
            continue
        t = o.read()
        if re.match(r"(EquipPage|EnemyUnitInfo|Deck)", t.m_Name):
            raw = t.m_Script
            tables.setdefault(t.m_Name, []).append(raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else bytes(raw))

    def roots(prefix):
        for name, raws in tables.items():
            if name.startswith(prefix):
                for raw in raws:
                    try:
                        yield ET.fromstring(raw)
                    except ET.ParseError:
                        pass
    books = {b.get("ID"): (b.findtext("CharacterSkin") or "").strip() for r in roots("EquipPage") for b in r.iter("Book")}
    decks = {d.get("ID"): [c.text.strip() for c in d.iter("Card") if c.text] for r in roots("Deck") for d in r.iter("Deck")}
    by_skin = {}
    for r in roots("EnemyUnitInfo"):
        for e in r.iter("Enemy"):
            book = (e.findtext("BookId") or "").strip()
            deck = decks.get((e.findtext("DeckId") or "").strip()) or decks.get(e.get("ID")) or decks.get(book) or []
            skin = books.get(book, "")
            if skin and len(set(deck)) > len(set(by_skin.get(skin, []))):
                by_skin[skin] = deck
    for book, skin in books.items():                   # a key page with a deck of its own number
        if skin and skin not in by_skin and decks.get(book):
            by_skin[skin] = decks[book]
    return by_skin, lortext.Book(lortext.read_tables())


def prefabs():
    """{prefab: (bundle, [motions])} for every character bundle of the Ruina install."""
    out = {}
    folder = os.path.dirname(lor.bundle_path("char/char_1.pres"))
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".pres"):
            continue
        env = game.unitypy().load(os.path.join(folder, f))
        names = [o.read_typetree()["m_Name"] for o in env.objects if o.type.name == "GameObject"]
        for n in names:
            if "_Default" in n and not n.endswith(")"):
                prefab = n[:-len("_Default")]
                out[prefab] = (f"char/{f}", sorted({m[len(prefab) + 1:] for m in names if m.startswith(prefab + "_")}))
    return out


def pose_map(motions):
    """A body's kinds of frames from a Ruina character's motions (their names vary a little)."""
    have = set(motions)

    def first(*names):
        return next((n for n in names if n in have), None)
    slash, stab, blunt = first("Slash", "J"), first("Penetrate", "Panetrate", "Z"), first("Hit", "H")
    attacks = [a for a in (slash, stab, blunt) if a] or ["Default"]
    extra = sorted((m for m in have if re.fullmatch(r"S\d+", m)), key=lambda m: int(m[1:]))
    guard = first("Guard", "GUard") or "Default"
    return {"idle": ["Default"] + (["Standing"] if "Standing" in have else []),
            "move": [first("Move") or "Default"], "gud": [guard], "miss": [first("Evade", "Evasion") or guard],
            "dmg": [first("Damaged") or "Default"], "dead": [first("Damaged") or "Default"],
            "S1": [attacks[0]], "S2": attacks[1:3] or attacks[:1],
            "S3": (attacks + extra[:3])[:5]}


def lor_spec(prefab, found):
    bundle, motions = found[prefab]
    return {"bundle": bundle, "prefab": prefab, "map": pose_map(motions), "smear": ["move", "S1", "S2", "S3"],
            "idle_time": 2.4, "stands": 4.1}


def kit(deck, book, coins, sins):
    """(moves, texts) from a Ruina deck: its cheapest, middle and dearest attack pages as skills
    1-3, its first page with a block or evade die as the defense. coins: how many coins each of
    the base identity's skills has (an animation plays one hit a coin: the count stays)."""
    seen, attacks, guard = set(), [], None
    for c in deck:
        if c in seen or c not in book.cards or not book.names.get(c, ("",))[0]:
            continue
        seen.add(c)
        if book.dice(c, "Atk"):
            attacks.append(c)
        if guard is None and book.dice(c, "Def"):
            guard = c
    if not attacks:
        return {}, {}
    attacks.sort(key=lambda c: (int(book.cards[c]["cost"] or 0), sum(d[1] for d in book.dice(c, "Atk")), c))
    pick = [attacks[0], attacks[len(attacks) // 2], attacks[-1]]
    moves, texts = {}, {}
    for n, card in enumerate(pick, 1):
        moves[str(n)] = {"lor_dice": int(card), "attributeType": sins[n - 1], "coins": [0] * coins[n - 1]}
        texts[str(n)] = {"lor_card": int(card)}
    if guard and coins[3]:
        moves["defense"] = {"lor_dice": int(guard), "attributeType": sins[3], "coins": [0] * coins[3]}
        texts["defense"] = {"lor_card": int(guard)}
    return moves, texts


def main():
    game_dir, cache = paths.find()
    pers, skills, _ = game.boss_data(cache)
    decks, book = ruina()
    found = prefabs()
    with open(SKINS, encoding="utf-8") as f:
        raw = f.read()
    doc = json.loads(raw)
    ids = doc["identities"]
    for k in [k for k, v in ids.items() if v.get("roster")]:
        del ids[k]
    taken = set(pers) | {v["identity"] for v in ids.values()}
    for key, name, line, sinner, prefab, deck_of, sins, evolves in ROSTER:
        if not set(sins) <= set(helpers.SINS):
            raise SystemExit(f"{key}: {sorted(set(sins) - set(helpers.SINS))} is no sin a skill can have ({', '.join(helpers.SINS)})")
        if prefab not in found:
            print(f"  skip {key}: Library of Ruina has no {prefab}")
            continue
        base = BASE.get(sinner, 10001 + sinner * 100)
        new = next(i for i in range(10000 + sinner * 100 + FIRST_ID, 10000 + sinner * 100 + 90) if i not in taken)
        taken.add(new)
        rec = pers[base]
        coins = [len(skills[a["skillId"]]["skillData"][0].get("coinList") or []) for a in game.lit(rec["attributeList"])][:3]
        guards = list(game.lit(rec["defenseSkillIDList"]))
        coins.append(len(skills[guards[0]]["skillData"][0].get("coinList") or []) if guards else 0)
        moves, texts = kit(deck_of if isinstance(deck_of, list) else decks.get(deck_of or prefab, []), book, coins, sins)
        entry = {"identity": new, "base": base, "roster": True, "tested": False, "name": name, "game_title": line,
                 "title": f"{name} (Ruina) - {line.replace(chr(10), ' ')}",
                 "becomes": f"{name} of Library of Ruina as an identity of their own: own body, own skills"
                            + (", their Ruina pages as the kit" if moves else ", the kit still to be written"),
                 "own_body": True, "own_skills": True, "lor": lor_spec(prefab, found)}
        if moves:
            entry["moves"], entry["texts"] = moves, texts
        if evolves and evolves[0] in found:
            entry["evolves"] = {"lor": lor_spec(evolves[0], found), "hp": 50, "word": evolves[1]}
        ids[key] = entry
        print(f"  {key}: {new} on {base}, {prefab}"
              + (f" -> {evolves[0]} below half HP" if entry.get("evolves") else "")
              + (f", pages {', '.join(str(m['lor_dice']) for m in moves.values())}" if moves else ", NO DECK FOUND: its base's skills"))
    for key, name, line, sinner, body, unit in LIMBUS:
        base = helpers.ally_base(pers, 10001 + sinner * 100)     # one with no battle script of its own
        new = next(i for i in range(10000 + sinner * 100 + FIRST_ID, 10000 + sinner * 100 + 90) if i not in taken)
        taken.add(new)
        ids[key] = {"identity": new, "base": base, "roster": True, "tested": False, "name": name, "game_title": line,
                    "source": body, "unit": unit, "title": f"{name} (Limbus) - {line.replace(chr(10), ' ')}",
                    "becomes": f"{name} as Limbus Company draws them: the boss's own body and whole moveset, as an identity of their own"}
        print(f"  {key}: {new} on {base}, the boss {body}")
    indent = len(re.search(r"\n( +)\"", raw).group(1))
    with open(SKINS, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=indent, ensure_ascii=False) + "\n")
    # what the roster draws and says goes with limbus-skins, so it builds without Library of Ruina
    data = os.path.join(os.path.dirname(SKINS), "lor")
    for key, e in ids.items():
        if not e.get("roster") or not e.get("lor"):
            continue
        for spec in [e["lor"]] + ([e["evolves"]["lor"]] if e.get("evolves") else []):
            used = sorted({m for ms in spec["map"].values() for m in ms})
            lor.ship(spec["bundle"], spec["prefab"], spec.get("head"), os.path.join(data, spec["prefab"]), used)
    from limbus_skins import build
    lortext.ship(build.catalog(), os.path.join(data, "book.json"))
    print("  shipped: the roster's poses (data/lor/<prefab>) and pages (data/lor/book.json)")


if __name__ == "__main__":
    main()
