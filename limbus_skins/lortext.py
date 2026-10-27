"""Library of Ruina's own words for a page or a passive: its name, and its cost, dice and
abilities as a skill's flavor text (Kali's pages). Read from the player's own Library of Ruina
install when a skin is built (resources.assets), never shipped.

skins.json "texts": {"1": {"lor_card": 607005}, "defense": {"lor_passive": 250222}} - a name or
flavor written out in the skin wins over Ruina's.
"""
import os
import re
import xml.etree.ElementTree as ET

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


def uses_lor(texts):
    return any("lor_card" in t or "lor_passive" in t for t in (texts or {}).values())
