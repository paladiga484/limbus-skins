"""Helper-unit skins: an identity takes a story helper unit's body *and* its skills.

Some story fights give you a helper (Arayashiki Ryōshū in 9-49, A Certain Sinclair,
the Walpurgis Queen of Hatred...). Their bodies were made to be played, so giving an
identity the helper's body together with the helper's own skills works in battle.
(A body alone crashes: the identity's skills ask for motions the body lacks.)

Boss skills go the other way: a repainted identity keeps its own body (already drawn as
the boss) and its own skill records, which take the boss's numbers, coins and effects.
Each skill keeps the identity's motion, so nothing asks for a motion the body lacks.

This changes skills, so it is a gameplay change: private servers only.
"""
import copy
import json

from .game import _text, lit, unitypy

# what a boss skill brings; everything else (motion, view, uptie shell) stays the identity's
BOSS_FIELDS = ("attributeType", "atkType", "defType", "skillTargetType", "targetNum", "arearange",
               "defaultValue", "skillLevelCorrection", "coinList", "abilityScriptList",
               "canTeamKill", "canDuel", "canChangeTarget")


# Skills (helper or boss) that only work inside their own story fight: they end or stall the battle,
# retreat, or kill the user. Outside it they hang the fight (A Certain Sinclair's S6 waits for
# a "return from the living specimen" that never comes).
STORY_SCRIPTS = ("Retreat", "EndPoint", "DieOnEndSkill", "DoNotEndGame", "EndBattleForcely",
                 "GetImmortalOnlyOnUsingThisSkill")
# ...and ones that lean on their unit's own battle script, which only a helper with "script" brings
UNIT_SCRIPTS = ("CreateMang", "UseMpSelfIfCreateMang")


def _scripts(rec):
    out = set()
    for d in rec.get("skillData", []):
        for a in d.get("abilityScriptList") or []:
            out.add(a.get("scriptName") or "")
        for c in d.get("coinList") or []:
            for a in c.get("abilityScriptList") or []:
                out.add(a.get("scriptName") or "")
    return out


def story_only(rec, script=False):
    """The first ability that ties skill record rec to its own story fight (or, unless
    script, to its unit's own battle script); None when it works anywhere."""
    banned = STORY_SCRIPTS + (() if script else UNIT_SCRIPTS)
    return next((s for s in sorted(_scripts(rec)) if any(b in s for b in banned)), None)


def _attacks(unit, skills):
    """The unit's attack skills that work outside its own fight, lowest tier first."""
    out = []
    for a in lit(unit.get("attributeList")):
        s = skills.get(a["skillId"])
        if s and s.get("skillData") and s["skillData"][0].get("defType", "ATTACK") == "ATTACK" and not story_only(s):
            out.append(s)
    return sorted(out, key=lambda s: s.get("skillTier", 1))


def _power(unit, skills):
    """Enough attacks first (three fill an identity's skills), then the strongest per attack
    (base power + coins): a boss can have a weak tutorial version under the same body."""
    att = _attacks(unit, skills)
    d = [s["skillData"][0] for s in att]
    total = sum(x.get("defaultValue", 0) + sum(c.get("scale", 0) for c in x.get("coinList", [])) for x in d)
    return min(len(att), 3), total / max(1, len(att))


def pick_unit(records, skills, want=None):
    """The boss record to borrow from: the one named `want`, else the strongest."""
    if want is not None:
        return next(r for r in records if r["id"] == want)
    return max(records, key=lambda r: _power(r, skills))


def boss_skills(identity, unit, skills):
    """{skill id: new record} for the identity's own attack skills, carrying the boss's.
    When the boss has fewer attacks, the identity's last skills stay its own. Raises
    ValueError when the boss has none."""
    mine = [a["skillId"] for a in lit(identity.get("attributeList"))]
    theirs = _attacks(unit, skills)
    if not theirs:
        raise ValueError(f"unit {unit['id']} has no attack skills")
    mine = mine[:len(theirs)]
    picked, left = [], list(theirs)
    for n in range(len(mine)):                      # skill n+1 takes a boss skill of tier n+1 if any
        boss = next((s for s in left if s.get("skillTier", 1) == n + 1), left[0])
        left.remove(boss)
        picked.append(boss)
    out = {}
    for sid, boss in zip(mine, picked):
        rec = copy.deepcopy(skills[sid])
        shell = rec["skillData"][0]
        b = boss["skillData"][0]
        for k in BOSS_FIELDS:
            if k in b:
                shell[k] = copy.deepcopy(b[k])
            else:
                shell.pop(k, None)
        shell["gaksungLevel"] = 1
        rec["skillData"] = [shell]                  # no uptie steps: the boss's numbers throughout
        out[sid] = rec
    return out


def helper_kit(identity, helper, skills, script=False):
    """(attributeList, defenseSkillIDList, [(skill id, why) dropped]) for an identity taking
    a helper's kit: the helper's skills minus the story-only ones (and those needing its
    battle script, unless that comes along). A helper without a defense skill leaves the
    identity its own, where the helper's body has the motion (Default, or one its kept skills
    use). Raises ValueError when nothing usable is left."""
    attrs, dropped = [], []
    for a in lit(helper.get("attributeList")):
        rec = skills.get(a["skillId"])
        why = "not in the skill data" if rec is None else story_only(rec, script)
        if why:
            dropped.append((a["skillId"], why))
        else:
            attrs.append(a)
    if not attrs:
        raise ValueError(f"helper {helper['id']} has no skills that work outside its story fight")
    guard = [g for g in lit(helper.get("defenseSkillIDList")) if g in skills]
    if not guard:
        motions = {"Default"} | {skills[a["skillId"]]["skillData"][0].get("skillMotion") for a in attrs}
        guard = [g for g in lit(identity.get("defenseSkillIDList"))
                 if g in skills and skills[g]["skillData"][0].get("skillMotion") in motions]
    return attrs, guard, dropped


def _record(sid, skills, base):
    """A copy of skill sid to edit, on top of edits already made to it (base)."""
    return copy.deepcopy((base or {}).get(sid) or skills[sid])


def recolor_skills(identity, skills, sins, base=None):
    """{skill id: new record}: the identity's skill n (1-based, as listed; "defense" for its
    defense skills, "all" for every skill) takes sin sins[n] ("SHAMROCK" is green) at every
    uptie that names one."""
    mine = [a["skillId"] for a in lit(identity.get("attributeList"))]
    guard = list(lit(identity.get("defenseSkillIDList")))
    out = {}
    for n, sin in sins.items():
        ids = mine + guard if n == "all" else guard if n == "defense" else [mine[int(n) - 1]]
        for sid in ids:
            rec = _record(sid, skills, {**(base or {}), **out})
            for d in rec["skillData"]:
                if "attributeType" in d or d is rec["skillData"][0]:
                    d["attributeType"] = sin
            out[rec["id"]] = rec
    return out


def green_ids(recoloured):
    """The skill ids among recolor_skills' output that are now Gluttony: their coins draw green
    too (myosotis's GreenCoinsPatch, told through the helpers manifest)."""
    return sorted(i for i, rec in recoloured.items() if rec["skillData"][0].get("attributeType") == "SHAMROCK")


SINS = ("CRIMSON", "SCARLET", "AMBER", "SHAMROCK", "AZURE", "INDIGO", "VIOLET")
ATK = ("SLASH", "PENETRATE", "HIT")


def _buff(spec, script, target):
    return {"scriptName": script,
            "buffData": {"buffKeyword": spec["buffKeyword"], "target": spec.get("target", target), "buffOwner": "",
                         "stack": int(spec.get("stack", 1)), "turn": int(spec.get("turn", 0)),
                         "activeRound": int(spec.get("activeRound", 1 if script == "GiveBuffOnUse" else 0))}}


def move_skills(identity, skills, moves, base=None):
    """{skill id: new record}: skill n of the identity rewritten from plain data - sin,
    attack type, base power, coins, and the game's own buff abilities on use and on hit:
    {"attributeType", "atkType", "defaultValue", "coins": [scale...], "onUse": {...}, "onHit": {...}}.
    n is "1", "2"... or "defense" (its first defense skill; a guard keeps its defType). Motions
    and the uptie shell stay the identity's; every uptie plays the same skill. The coin count
    stays the identity's own: each coin is a hit in its animation, and a different count stalls
    it (ValueError)."""
    mine = [a["skillId"] for a in lit(identity.get("attributeList"))]
    guard = list(lit(identity.get("defenseSkillIDList")))
    out = {}
    for n, m in moves.items():
        if n == "defense" and not guard:
            raise ValueError("the identity has no defense skill to rewrite")
        coins = m.get("coins") or []
        if not 1 <= len(coins) <= 5 or not all(isinstance(c, int) and 0 <= c <= 30 for c in coins):
            raise ValueError(f"skill {n}: coins must be 1 to 5 scales of 0-30")
        if m.get("attributeType", "CRIMSON") not in SINS or m.get("atkType", "SLASH") not in ATK:
            raise ValueError(f"skill {n}: unknown sin or attack type")
        rec = _record(guard[0] if n == "defense" else mine[int(n) - 1], skills, base)
        shell = rec["skillData"][0]
        own = len(shell.get("coinList") or [])
        if own and len(coins) != own:
            raise ValueError(f"skill {n}: {len(coins)} coins, but its animation has {own} - keep {own}")
        for k in ("attributeType", "atkType"):
            if k in m:
                shell[k] = m[k]
        shell["defaultValue"] = int(m.get("defaultValue", shell.get("defaultValue", 3)))
        shell["abilityScriptList"] = [_buff(m["onUse"], "GiveBuffOnUse", "Self")] if m.get("onUse") else []
        hit = [_buff(m["onHit"], "GiveBuffOnSucceedAttack", "Target")] if m.get("onHit") else []
        shell["coinList"] = [{"operatorType": "ADD", "scale": c, "abilityScriptList": copy.deepcopy(hit)} for c in coins]
        shell["gaksungLevel"] = 1
        rec["skillData"] = [shell]
        out[rec["id"]] = rec
    return out


# Identities of our own: a new id cloned from an existing identity, in every table the client
# keys by identity (the record, its skin, passives, voice, card thumbnail, art offsets).
IDENTITY_KEYS = {"personality": "id", "skin": "skinId", "personality-passive": "personalityID",
                 "personality-voice": "personalityId", "personality-cg-thumbnail": "personalityId"}


def clone_record(kind, rec, base, new):
    """A copy of `rec` as identity `new`'s, if it is identity `base`'s in table `kind`; else None.
    The card thumbnail points at the base's art folder, which the new id has none of its own."""
    key = IDENTITY_KEYS.get(kind)
    if key is not None:
        if rec.get(key) != base:
            return None
        out = dict(copy.deepcopy(rec), **{key: new})
        if kind == "personality-cg-thumbnail" and not out.get("folderName"):
            out["folderName"] = str(base)
        return out
    if kind.startswith("illust-pivots-") and str(rec.get("id", "")).startswith(f"{base}_"):
        return dict(copy.deepcopy(rec), id=f"{new}_{rec['id'][len(str(base)) + 1:]}")
    return None


def add_clones(kind, records, clones):
    """records plus a clone of each base record for every {new id: base id} in `clones`."""
    out = list(records)
    for new, base in sorted(clones.items()):
        out += [c for c in (clone_record(kind, r, base, new) for r in records) if c is not None]
    return out


def check_new_ids(have, clones):
    """Refuse a new id the game already uses, or a base it doesn't have (ValueError)."""
    for new, base in clones.items():
        if new in have:
            raise ValueError(f"identity {new} already exists in this game version")
        if base not in have:
            raise ValueError(f"identity {base} (to clone {new} from) isn't in this game version")


def swap_passives(rec, battle, support):
    """A copy of a personality-passive record with these passive ids at every uptie level
    (passives are coded per id in the client: borrowed ones bring their own effect)."""
    out = copy.deepcopy(rec)
    for key, ids in (("battlePassiveList", battle), ("supporterPassiveList", support)):
        out[key] = [dict(lv, passiveIDList=list(ids)) for lv in rec.get(key, [])]
    return out


def rewrite_static(static_path, swaps, skill_swaps=None, clones=None, passive_swaps=None):
    """Rewrite identity records (and skill records) in the static-data bundle.
    swaps = {identity id: helper record}; skill_swaps = {skill id: new record};
    clones = {new identity id: base id} (cloned after the swaps, so a clone wears them too);
    passive_swaps = {identity id: {"battle": [passive ids], "support": [passive ids]}}.
    Returns (bundle bytes, [identity ids changed], [skill ids changed])."""
    UnityPy = unitypy()
    env = UnityPy.load(static_path)
    skill_swaps = skill_swaps or {}
    clones = clones or {}
    passive_swaps = passive_swaps or {}
    done, done_skills = set(), set()
    for path, obj in env.container.items():
        if obj.type.name != "TextAsset":
            continue
        kind = path.split("/static-data/")[1].split("/")[0] if "/static-data/" in path else ""
        is_pers = kind == "personality"
        cloned = bool(clones) and (kind in IDENTITY_KEYS or kind.startswith("illust-pivots-"))
        if not (is_pers or cloned or (skill_swaps and kind == "skill")
                or (passive_swaps and kind == "personality-passive")):
            continue
        doc = _text(obj)
        hit = False
        for i, x in enumerate(doc.get("list", [])):
            if is_pers:
                h = swaps.get(x.get("id"))
                if not h:
                    continue
                new = {"appearance": h["appearance"],
                       "attributeList": [{"skillId": a["skillId"], "number": 1} for a in lit(h.get("attributeList"))],
                       "defenseSkillIDList": list(lit(h.get("defenseSkillIDList")))}
                if h.get("_script") and h.get("unitScriptID"):
                    # the helper's own battle script too: the identity's would drive a body it no
                    # longer has (Wild Hunt's mount)
                    new["unitScriptID"] = h["unitScriptID"]
                for k, v in new.items():
                    # keep the record's own encoding: some fields are stored as Python-literal strings
                    x[k] = str(v) if isinstance(x.get(k), str) and not isinstance(v, str) else v
                done.add(x["id"])
            elif kind == "skill":
                if x.get("id") not in skill_swaps:
                    continue
                doc["list"][i] = skill_swaps[x["id"]]
                done_skills.add(x["id"])
            elif kind == "personality-passive":
                sw = passive_swaps.get(x.get("personalityID"))
                if not sw:
                    continue
                doc["list"][i] = swap_passives(x, sw.get("battle", []), sw.get("support", []))
            else:
                continue                            # a table here only for its clones
            hit = True
        if cloned:
            grown = add_clones(kind, doc.get("list", []), clones)
            if len(grown) != len(doc.get("list", [])):
                doc["list"] = grown
                hit = True
                done.update(n for n in clones if is_pers and any(r.get("id") == n for r in grown))
        if hit:
            ta = obj.read()
            ta.m_Script = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
            ta.save()
    return env.file.save(packer="none"), sorted(done), sorted(done_skills)
