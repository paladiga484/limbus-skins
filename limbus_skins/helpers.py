"""Helper-unit skins: an identity takes a story helper unit's body *and* its skills.

Some story fights give you a helper (Arayashiki Ryōshū in 9-49, A Certain Sinclair,
the Walpurgis Queen of Hatred...). Their bodies were made to be played, so giving an
identity the helper's body together with the helper's own skills works in battle.
(A body alone crashes: the identity's skills ask for motions the body lacks.)

This changes skills, so it is a gameplay change: private servers only.
"""
import json

from .game import _text, lit, unitypy


def rewrite_static(static_path, swaps):
    """Rewrite identity records in the static-data bundle. swaps = {identity id: helper
    record}. Returns (bundle bytes, [ids changed])."""
    UnityPy = unitypy()
    env = UnityPy.load(static_path)
    done = set()
    for path, obj in env.container.items():
        if obj.type.name != "TextAsset" or "/static-data/personality/" not in path:
            continue
        doc = _text(obj)
        hit = False
        for x in doc.get("list", []):
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
            hit = True
        if hit:
            ta = obj.read()
            ta.m_Script = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
            ta.save()
    return env.file.save(packer="none"), sorted(done)
