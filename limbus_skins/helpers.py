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


def _attacks(unit, skills):
    """The unit's attack skills, lowest tier first."""
    out = []
    for a in lit(unit.get("attributeList")):
        s = skills.get(a["skillId"])
        if s and s.get("skillData") and s["skillData"][0].get("defType", "ATTACK") == "ATTACK":
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


def _record(sid, skills, base):
    """A copy of skill sid to edit, on top of edits already made to it (base)."""
    return copy.deepcopy((base or {}).get(sid) or skills[sid])


def recolor_skills(identity, skills, sins, base=None):
    """{skill id: new record}: the identity's skill n (1-based, as listed) takes sin
    sins[n] ("SHAMROCK" is green) at every uptie that names one."""
    mine = [a["skillId"] for a in lit(identity.get("attributeList"))]
    out = {}
    for n, sin in sins.items():
        rec = _record(mine[int(n) - 1], skills, base)
        for d in rec["skillData"]:
            if "attributeType" in d or d is rec["skillData"][0]:
                d["attributeType"] = sin
        out[rec["id"]] = rec
    return out


# The Black Forest: whoever carries it takes everyone with them. Built from the game's own
# abilities: the mark is given at battle start by the identity's first skill, and on death
# it gives every unit on both sides a buff that kills at the end of the round.
BLACK_FOREST = [
    {"id": "BlackForest", "buffClass": "NonvolatileBuff", "buffType": "Neutral", "canBeDespelled": False,
     "maxStack": 1, "list": [
         {"ability": "GiveBuffOnDie", "buffData": {"buffKeyword": "Apocalypse", "target": side, "stack": 1,
                                                   "turn": 1, "activeRound": 0}}
         for side in ("EveryEnemy", "EveryAlly")]},
    {"id": "Apocalypse", "buffClass": "NonvolatileBuff", "buffType": "Negative", "canBeDespelled": False,
     "maxStack": 1, "list": [{"ability": "InstantDeathOnRoundEnd", "value": 1}]},
]


def black_forest(identity, skills, base=None):
    """({skill id: new record}, [buff records]): the identity carries the Black Forest."""
    sid = lit(identity.get("attributeList"))[0]["skillId"]
    rec = _record(sid, skills, base)
    shell = rec["skillData"][0]
    shell["abilityScriptList"] = list(shell.get("abilityScriptList") or []) + [
        {"scriptName": "GiveBuffOnBattleStart",
         "buffData": {"buffKeyword": "BlackForest", "target": "Self", "buffOwner": "", "stack": 1,
                      "turn": 0, "activeRound": 0}}]
    return {sid: rec}, copy.deepcopy(BLACK_FOREST)


def add_records(doc, records):
    """Put records into a static list, replacing any with the same id. Returns the ids."""
    new = {r["id"]: r for r in records}
    keep = [x for x in doc.get("list", []) if x.get("id") not in new]
    doc["list"] = keep + list(new.values())
    return set(new)


def rewrite_static(static_path, swaps, skill_swaps=None, buffs=()):
    """Rewrite identity records (and skill records) in the static-data bundle.
    swaps = {identity id: helper record}; skill_swaps = {skill id: new record}.
    buffs = new buff records, added to the first buff list. Returns (bundle bytes,
    [identity ids changed], [skill ids changed])."""
    UnityPy = unitypy()
    env = UnityPy.load(static_path)
    skill_swaps = skill_swaps or {}
    done, done_skills = set(), set()
    buffs_left = list(buffs)
    for path, obj in sorted(env.container.items()):
        if obj.type.name != "TextAsset":
            continue
        is_pers = "/static-data/personality/" in path
        is_buff = buffs_left and "/static-data/buff/" in path
        if not (is_pers or is_buff or (skill_swaps and "/static-data/skill/" in path)):
            continue
        doc = _text(obj)
        hit = False
        if is_buff:
            add_records(doc, buffs_left)
            buffs_left = []
            hit = True
        for i, x in enumerate([] if is_buff else doc.get("list", [])):
            if is_pers:
                h = swaps.get(x.get("id"))
                if not h:
                    continue
                new = {"appearance": h["appearance"],
                       "attributeList": [{"skillId": a["skillId"], "number": 1} for a in lit(h.get("attributeList"))],
                       "defenseSkillIDList": list(lit(h.get("defenseSkillIDList")))}
                for k, v in new.items():
                    # keep the record's own encoding: some fields are stored as Python-literal strings
                    x[k] = str(v) if isinstance(x.get(k), str) and not isinstance(v, str) else v
                done.add(x["id"])
            else:
                if x.get("id") not in skill_swaps:
                    continue
                doc["list"][i] = skill_swaps[x["id"]]
                done_skills.add(x["id"])
            hit = True
        if hit:
            ta = obj.read()
            ta.m_Script = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
            ta.save()
    if buffs_left:
        raise ValueError("no buff list in the static data to add the new buffs to")
    return env.file.save(packer="none"), sorted(done), sorted(done_skills)
