"""The audit: what a built skin would put in the game, checked before it goes in. A skin with a
finding isn't swapped in (build marks it failed); `limbus-skins audit` runs it on what is built.

body(stock, built, target) - one rewritten bundle against the stock file it replaces:
  - an object another body in the bundle loads was changed (that body would draw wrong)
  - an object of the stock file is gone, or isn't the same type any more
  - a frame of the skin's own body that drew something draws nothing now
card(stock, built, ident) - a card file changes nothing but that identity's card pictures
record(identity, skills, motions, passive_ids) - an identity record the static-data file gets:
  - a skill, a defense skill or a passive the game's data doesn't have
  - a skill whose motion the body it wears doesn't have (the fight would freeze on it)
  - nothing to fight with (an empty deck, no defense)
"""
from . import share
from .game import unitypy

ALPHA = 8                   # a frame "draws something" when a pixel is more opaque than this


def _objects(src):
    env = unitypy().load(src)
    return env, {o.path_id: o for o in env.objects}


def _frames(env, ids):
    """{sprite path_id: (page path_id, rect, turn)} for the sprites among ids (their atlas copy
    when they have one: that is what the game draws)."""
    amap = {}
    for o in env.objects:
        if o.type.name == "SpriteAtlas":
            for k, v in o.read_typetree()["m_RenderDataMap"]:
                amap[(tuple(k[0].values()), k[1])] = v
    out = {}
    for o in env.objects:
        if o.type.name != "Sprite" or o.path_id not in ids:
            continue
        t = o.read_typetree()
        key = (tuple(t["m_RenderDataKey"][0].values()), t["m_RenderDataKey"][1])
        rd = amap.get(key) or t["m_RD"]
        if not rd or not rd["texture"]["m_PathID"]:
            continue
        r = rd["textureRect"]
        rect = (round(r["x"]), round(r["y"]), round(r["width"]), round(r["height"]))
        if rect[2] > 0 and rect[3] > 0:
            out[o.path_id] = (rd["texture"]["m_PathID"], rect)
    return out


def _drawn(env, frames):
    """{sprite path_id: does it draw anything}, each page decoded once."""
    objs = {o.path_id: o for o in env.objects}
    pages, out = {}, {}
    for sid, (page, (x, y, w, h)) in frames.items():
        if page not in pages:
            o = objs.get(page)
            try:
                pages[page] = o.read().image.getchannel("A") if o is not None else None
            except Exception:
                pages[page] = None
        a = pages[page]
        if a is None:
            continue
        box = (x, a.height - y - h, x + w, a.height - y)            # Unity rects are bottom-up
        out[sid] = a.crop(box).point(lambda v: 255 if v > ALPHA else 0).getbbox() is not None
    return out


def body(stock, built, target):
    """Findings (strings) for a built bundle that rewrites body `target`; [] when it's sound."""
    from .repaint import prefab_objects
    a, sa = _objects(stock)
    b, sb = _objects(built)
    out = []
    gone = [p for p in sa if p not in sb]
    if gone:
        out.append(f"{len(gone)} object(s) of the game's file are gone")
    retyped = [p for p in sa if p in sb and sa[p].class_id != sb[p].class_id]
    if retyped:
        out.append(f"{len(retyped)} object(s) changed type")
    foreign = share.foreign(a, target)
    changed = [p for p in foreign if p in sa and p in sb and sa[p].get_raw_data() != sb[p].get_raw_data()]
    if changed:
        out.append(f"{len(changed)} object(s) another body in the file loads were changed")
    mine = prefab_objects(a, target, forms=True)[0] - foreign
    was = _drawn(a, _frames(a, mine))
    now = _drawn(b, _frames(b, prefab_objects(b, target, forms=True)[0]))
    blank = sorted(p for p, drew in was.items() if drew and now.get(p) is False)
    if blank:
        out.append(f"{len(blank)} frame(s) that drew the body draw nothing now")
    return out


def card(stock, built, ident):
    """Findings for a card file: it may change only identity ident's card pictures."""
    from .cards import ROLES, card_name
    names = {card_name(ident, v, s) for v in ("normal", "gacksung") for s in ROLES}
    _, sa = _objects(stock)
    _, sb = _objects(built)
    out = []
    if set(sa) - set(sb):
        out.append(f"{len(set(sa) - set(sb))} object(s) of the game's file are gone")
    other = [p for p in sa if p in sb and sa[p].get_raw_data() != sb[p].get_raw_data()
             and not (sa[p].type.name == "Texture2D" and sa[p].peek_name() in names)]
    if other:
        out.append(f"{len(other)} object(s) besides the card pictures were changed")
    return out


def record(identity, skills, motions=None, passive_ids=None, passives=None):
    """Findings for an identity record about to go into the static data. skills: {id: record}
    as the file will have them; motions: the motion names of the body it wears (None: not
    checked); passive_ids: every passive the game has; passives: the identity's passive record."""
    from .helpers import lit
    out = []
    attrs = list(lit(identity.get("attributeList")))
    guard = list(lit(identity.get("defenseSkillIDList")))
    if not attrs or sum(int(a.get("number", 1)) for a in attrs) < 1:
        out.append("nothing in the deck")
    if not guard:
        out.append("no defense skill")
    for sid in [a["skillId"] for a in attrs] + guard:
        s = skills.get(sid)
        if not s or not s.get("skillData"):
            out.append(f"skill {sid} isn't in the game's data")
            continue
        if motions is not None:
            m = s["skillData"][0].get("skillMotion")
            if m and m not in motions:
                out.append(f"skill {sid}'s motion {m} isn't one its body has")
    if passive_ids is not None and passives:
        for lst in ("battlePassiveList", "supporterPassiveList"):
            for step in passives.get(lst) or []:
                for pid in step.get("passiveIDList") or []:
                    if pid not in passive_ids:
                        out.append(f"passive {pid} isn't in the game's data")
    return sorted(set(out))


def static(built_file, man, skins, game_dir, cache, data_dir):
    """Findings for the shared static-data file: every identity a skin in it changes or adds,
    checked as the file has it (skills, passives, the motions of the body it wears)."""
    import os
    from . import game, repaint
    env = unitypy().load(built_file)
    lists = game.static_lists(env, ("personality", "skill", "passive", "personality-passive"))
    pers = {r["id"]: r for r in lists["personality"]}
    skills = {r["id"]: r for r in lists["skill"]}
    passive_ids = {r["id"] for r in lists["passive"]}
    pp = {r.get("personalityID"): r for r in lists["personality-passive"]}
    bodies, have = game.body_index(game_dir, cache, os.path.join(data_dir, "bodies.json"), log=lambda *a: None)
    motions, out = {}, []
    for key in sorted(set(man.get("skins", []) + man.get("skills", []))):
        s = skins.get(key)
        if not s:
            continue
        rec = pers.get(s["identity"])
        if rec is None:
            out.append(f"{key}: identity {s['identity']} isn't in the file")
            continue
        app = rec.get("appearance")
        if app not in motions:
            h = bodies.get(app)
            motions[app] = repaint.body_motions(unitypy().load(have[h]), app) if h in have else None
        out += [f"{key}: {f}" for f in record(rec, skills, motions[app], passive_ids, pp.get(s["identity"]))]
    return out


def built(keys, game_dir, cache, data_dir, boss=()):
    """{built folder: [findings]} for every built file of the skins keys (build.built_dirs),
    against the game's own files in the cache - so no modded session may be live."""
    import json
    import os
    from . import build as b, game
    skins = b.catalog()
    ids, _ = game.identities(game_dir, cache)
    out = {}
    for label, files in b.built_dirs(data_dir, keys, boss):
        root = os.path.dirname(files)
        with open(os.path.join(root, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        found = []
        for dirpath, _, names in os.walk(files):
            if "__data" not in names:
                continue
            mine, stock = os.path.join(dirpath, "__data"), os.path.join(cache, os.path.relpath(dirpath, files), "__data")
            if not os.path.exists(stock):
                found.append("the game no longer has the file it replaces - rebuild")
            elif label.endswith(b.CARD):
                found += card(stock, mine, man["identity"])
            elif label == "helpers":
                found += static(mine, man, skins, game_dir, cache, data_dir)
            elif label.endswith(b.FORMS):
                with open(os.path.join(os.path.dirname(root), label[:-len(b.FORMS)], "manifest.json"), encoding="utf-8") as f:
                    target = (json.load(f).get("shift") or {}).get("to")
                found += body(stock, mine, target) if target else ["its skin's manifest names no body to turn into"]
            else:
                found += body(stock, mine, ids.get(skins[label]["identity"], {}).get("appearance"))
        out[label] = found
    return out
