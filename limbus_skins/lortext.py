"""Library of Ruina's own words for a page or a passive: its name, and its cost, dice and
abilities as a skill's flavor text (Kali's pages, Roland's). The pages and passives the skins use
ship with limbus-skins (data/lor/book.json, written once by ship() from a Library of Ruina
install), so players don't need the game; without that file they're read from the player's own
install (resources.assets).

skins.json "texts": {"1": {"lor_card": 607005}, "defense": {"lor_passive": 250222}} - a name or
flavor written out in the skin wins over Ruina's.
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from importlib import resources

from . import paths
from .game import unitypy


class LorTextError(Exception):
    pass


TABLES = re.compile(r"^(CardInfo|EN_BattleCards|EN_BattleCardAbilities|EN_PassiveDesc)")
DETAIL = {"Slash": "Slash", "Penetrate": "Pierce", "Hit": "Blunt", "Guard": "Block", "Evasion": "Evade"}
RANGE = {"Far": "Ranged", "FarArea": "Mass (Summation)", "FarAreaEach": "Mass (Individual)", "Instance": "On Play"}


def assets_path():
    for root in paths.steam_roots():
        p = os.path.join(root, "steamapps", "common", "Library Of Ruina", "LibraryOfRuina_Data", "resources.assets")
        if os.path.isfile(p):
            return p
    raise LorTextError("Library of Ruina wasn't found - is it installed through Steam?")


def read_tables(path=None):
    """[(table name, xml bytes)] for the TextAssets this reads (several share a name)."""
    out = []
    for o in unitypy().load(path or assets_path()).objects:
        if o.type.name != "TextAsset":
            continue
        t = o.read()
        if TABLES.match(t.m_Name):
            raw = t.m_Script
            out.append((t.m_Name, raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else bytes(raw)))
    return out


class Book:
    """Ruina's pages, names, abilities and passives, from read_tables()."""

    def __init__(self, tables):
        self.cards, self.names, self.abilities, self.passives = {}, {}, {}, {}
        for name, raw in tables:
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            if name.startswith("CardInfo"):
                for c in root.iter("Card"):
                    spec = c.find("Spec")
                    self.cards[c.get("ID")] = {
                        "rarity": (c.findtext("Rarity") or "").strip(),
                        "cost": spec.get("Cost") if spec is not None else None,
                        "range": spec.get("Range") if spec is not None else None,
                        "script": (c.findtext("Script") or "").strip(),
                        "dice": [(b.get("Type"), b.get("Detail"), b.get("Min"), b.get("Dice"), b.get("Script") or "")
                                 for b in c.iter("Behaviour")]}
            elif name.startswith("EN_BattleCards"):
                for d in root.iter("BattleCardDesc"):
                    self.names[d.get("ID")] = ((d.findtext("LocalizedName") or "").strip(), (d.findtext("Ability") or "").strip())
            elif name.startswith("EN_BattleCardAbilities"):
                for a in root.iter("BattleCardAbility"):
                    self.abilities[a.get("ID")] = (a.findtext("Desc") or "").strip()
            else:
                for p in root.iter("PassiveDesc"):
                    self.passives[p.get("ID")] = ((p.findtext("Name") or "").strip(), (p.findtext("Desc") or "").strip())

    def subset(self, card_ids, passive_ids):
        """The part of the book these pages and passives need (their abilities too), as plain
        data for book.json."""
        cards = {c: self.cards[c] for c in map(str, card_ids) if c in self.cards}
        scripts = {c["script"] for c in cards.values()} | {d[4] for c in cards.values() for d in c["dice"]}
        return {"cards": cards, "names": {c: self.names[c] for c in cards if c in self.names},
                "abilities": {a: self.abilities[a] for a in sorted(scripts) if a in self.abilities},
                "passives": {p: self.passives[p] for p in map(str, passive_ids) if p in self.passives}}

    @classmethod
    def from_data(cls, d):
        book = cls([])
        book.cards = {k: dict(v, dice=[tuple(x) for x in v["dice"]]) for k, v in d["cards"].items()}
        book.names = {k: tuple(v) for k, v in d["names"].items()}
        book.abilities = dict(d["abilities"])
        book.passives = {k: tuple(v) for k, v in d["passives"].items()}
        return book

    def page(self, card_id):
        """{"name", "flavor"} for a combat page: "Combat Page · Unique · Cost 5 · Slash 20-39",
        then the page's ability and its dice's, each on a line of its own."""
        cid = str(card_id)
        card, (name, ability) = self.cards.get(cid), self.names.get(cid, ("", ""))
        if card is None or not name:
            raise LorTextError(f"Library of Ruina has no page {cid}")
        dice = ", ".join(("Counter " if t == "Standby" else "") + f"{DETAIL.get(d, d)} {lo}-{hi}"
                         for t, d, lo, hi, _ in card["dice"])
        head = " · ".join(x for x in ("Combat Page", card["rarity"], card["cost"] and f"Cost {card['cost']}",
                                      RANGE.get(card["range"]), dice) if x)
        lines = [head, ability or self.abilities.get(card["script"], "")]
        for *_, script in card["dice"]:
            say = self.abilities.get(script, "")
            if say and say not in lines:
                lines.append(say)
        return {"name": name, "flavor": "\n".join(x for x in lines if x)}

    def dice(self, card_id, kind=None):
        """[(min, max, type, detail)...] of a page's dice ("Atk", "Def", "Standby"; kind: only those)."""
        card = self.cards.get(str(card_id))
        if card is None:
            raise LorTextError(f"Library of Ruina has no page {card_id}")
        return [(int(lo), int(hi), t, d) for t, d, lo, hi, _ in card["dice"] if kind is None or t == kind]

    def passive(self, passive_id):
        """{"name", "flavor"} for a passive: its name, and "Passive · " its description."""
        name, desc = self.passives.get(str(passive_id), ("", ""))
        if not name:
            raise LorTextError(f"Library of Ruina has no passive {passive_id}")
        return {"name": name, "flavor": f"Passive · {desc}"}


def resolve(texts, book):
    """A skin's texts with every "lor_card"/"lor_passive" filled in from the book (fields the
    skin writes out itself win)."""
    out = {}
    for n, t in texts.items():
        t = dict(t)
        ref = (book.page(t.pop("lor_card")) if "lor_card" in t else
               book.passive(t.pop("lor_passive")) if "lor_passive" in t else {})
        out[n] = {**ref, **t}
    return out


RUINA_ATK = {"Slash": "SLASH", "Penetrate": "PENETRATE", "Hit": "HIT"}


def dice_coins(dice, n=None):
    """(base power, [coin powers], attack type) for Library of Ruina dice [(min, max, type,
    detail)...]: each hit as hard as Ruina's die - all tails the lowest die's min, all heads the
    highest die's max - over n coins (one per die unless given; the rest of the split on the last
    coins). The attack type is the first die's (None for a block or an evade)."""
    if not dice:
        raise ValueError("no dice to read")
    n = n or len(dice)
    base = min(d[0] for d in dice)
    q, r = divmod(max(d[1] for d in dice) - base, n)
    return base, [q] * (n - r) + [q + 1] * r, RUINA_ATK.get(dice[0][3])
def resolve_moves(moves, book):
    """A skin's moves with every "lor_dice": <page> turned into that page's dice
    (dice_coins): its attack dice for a skill, its block or evade die for "defense". The
    coin count stays the one written (the animation's); an attack type written out wins. Without
    Ruina (book None) a move that needs its dice raises LorTextError rather than guess."""
    out = {}
    for n, m in moves.items():
        if "lor_dice" not in m:
            out[n] = m
            continue
        if book is None:
            raise LorTextError("Library of Ruina isn't installed: its pages' dice can't be read")
        m = dict(m)
        kind = "Def" if n == "defense" else "Atk"
        dice = book.dice(m.pop("lor_dice"), kind)
        if not dice:
            raise LorTextError(f"move {n}: that page has no {kind} die")
        m["defaultValue"], m["coins"], atk = dice_coins(dice, len(m.get("coins") or []) or None)
        if atk:
            m.setdefault("atkType", atk)
        out[n] = m
    return out


def referenced(skins):
    """(page ids, passive ids) the skins' moves and texts name."""
    cards, passives = set(), set()
    for s in skins.values():
        for m in (s.get("moves") or {}).values():
            if "lor_dice" in m:
                cards.add(str(m["lor_dice"]))
        for t in (s.get("texts") or {}).values():
            if "lor_card" in t:
                cards.add(str(t["lor_card"]))
            if "lor_passive" in t:
                passives.add(str(t["lor_passive"]))
    return sorted(cards), sorted(passives)


def ship(skins, path, tables=None):
    """Write book.json for the skins: the pages and passives they name, from Library of Ruina."""
    book = Book(tables if tables is not None else read_tables())
    cards, passives = referenced(skins)
    data = book.subset(cards, passives)
    missing = [c for c in cards if c not in data["cards"]] + [p for p in passives if p not in data["passives"]]
    if missing:
        raise LorTextError("Library of Ruina has no " + ", ".join(missing))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return len(data["cards"]), len(data["passives"])


def shipped_book():
    """The book shipped with limbus-skins, or None."""
    try:
        raw = resources.files("limbus_skins").joinpath("data", "lor", "book.json").read_text(encoding="utf-8")
        return Book.from_data(json.loads(raw))
    except (OSError, ValueError, KeyError):
        return None


def uses_dice(moves):
    return any("lor_dice" in m for m in (moves or {}).values())


def uses_lor(texts):
    return any("lor_card" in t or "lor_passive" in t for t in (texts or {}).values())
