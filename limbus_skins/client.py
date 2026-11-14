"""The client patch's settings for what limbus-skins swapped in. myosotis, the private server's
client patch, reads them from its own folder (the one with myosotis.ini, beside its DLL):

  myosotis.ini   green_coin_skills = <skill ids>   skills recoloured to Gluttony draw green coins
                 hp_shift = <from>><to>@<percent>   a body the unit turns into at low HP
                            <from>><to>#<skill id>  ...or on using that skill
                            <from>><to>!            ...or at the killing blow (a body with no death of its own)
  skilltext.txt  names and descriptions of our own for the skills a skin rewrote
  allies.txt     each identity of our own: the identity whose card it shows, and its name; and
                 each identity a skin that's on has a pfp for, as itself with no name (its card
                 and name stay the game's)
  allynames.txt  for each named one: how the game words the identity it is shown as (its title and
                 its name), and its own title (empty: that identity's) - the patch puts ours in their
                 place in whatever the game shows
  covers/        an ally's own card pictures (covers.py), and those skins' pfps:
                 <id>_normal<suffix>.dxt
  title/         the title screen's background and logo, when chosen (title.py)

A boss skin (a boss's own body on a sinner) needs the patch itself to be new enough: wears_bosses.

They come from the manifests of what was built (built/<folder>/manifest.json), and only from the
folders in this session (session.json): a build the game outdated, and so skipped, gets
nothing. `run` sets them in a private-server package's client folders for the session and
clears them after; `limbus-skins client [--off] <folder>...` does it by hand.
"""
import glob
import json
import os
import re
import shutil

GREEN = "green_coin_skills"
SHIFT = "hp_shift"
APPEARANCE = re.compile(r"[A-Za-z0-9_]{1,80}")
COVER = re.compile(r"\d{1,9}_normal[a-z_]{0,40}\.dxt")


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        return out if isinstance(out, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest(data_dir, folder):
    return _json(os.path.join(data_dir, "built", folder, "manifest.json"))


def applied(data_dir):
    """The built folders in this session (session.json; none without one)."""
    mods = _json(os.path.join(data_dir, "session.json")).get("mods")
    return {m for m in mods if isinstance(m, str)} if isinstance(mods, list) else set()


def green_skills(data_dir):
    """The skill ids the helpers build recoloured to Gluttony ([] if none)."""
    try:
        return sorted(int(i) for i in _manifest(data_dir, "helpers").get("green_skills", []))
    except (ValueError, TypeError):
        return []


def skill_texts(data_dir):
    """{skill id (str): {"name", "desc", "flavor", "coins"}} from the helpers build ({} if none);
    entries that aren't a skill id and a dict are left out."""
    texts = _manifest(data_dir, "helpers").get("skill_texts")
    if not isinstance(texts, dict):
        return {}
    return {k: v for k, v in texts.items() if k.isdigit() and len(k) <= 9 and isinstance(v, dict)}


def new_skills(data_dir):
    """{new skill id: the skill it is a copy of} from the helpers build: identities' own skills."""
    got = _manifest(data_dir, "helpers").get("new_skills")
    out = {}
    for k, v in (got.items() if isinstance(got, dict) else []):
        if str(k).isdigit() and len(str(k)) <= 9 and isinstance(v, int) and 0 < v < 10 ** 9 and int(k) != v and int(k) > 0:
            out[int(k)] = v
    return out


def allies(data_dir):
    """[(id, folder, name)] for the identities of our own the helpers build adds: the identity
    whose card each shows (its base's when none is named), and its name in the game."""
    out = []
    for e in _manifest(data_dir, "helpers").get("identities") or []:
        try:
            i, folder = int(e["id"]), int(e.get("folder") or e["base"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((i, folder, str(e.get("name") or "")))
    return sorted(out)


TEXT = ("folder_title", "folder_name", "title")


def _wording(e):
    """(folder title, folder name, title) from a manifest or names.json entry, or None without any."""
    got = tuple(e.get(k) if isinstance(e.get(k), str) else "" for k in TEXT)
    return got if any(got) else None


def wordings(data_dir, live):
    """{identity: (folder title, folder name, title)} for the named identities this session: the
    helpers build's own ones and the skins in live that rename theirs (allynames.txt)."""
    out = {}
    for e in _json(os.path.join(data_dir, "built", "names.json")).get("names") or []:
        if isinstance(e, dict) and e.get("skin") in live and isinstance(e.get("identity"), int) and _wording(e):
            out[e["identity"]] = _wording(e)
    for e in _manifest(data_dir, "helpers").get("identities") or []:
        if isinstance(e, dict) and isinstance(e.get("id"), int) and _wording(e):
            out[e["id"]] = _wording(e)
    return out


MUSIC = re.compile(r"[A-Za-z0-9_-]{1,60}\.(ogg|mp3|wav|flac)")


def fight_music(data_dir, live):
    """[(request, dungeon id, file name, path, second)] for the fights in live whose music is on
    this machine (<data dir>/music/<file>: friend's own tracks, never built, shipped or
    downloaded). second: (file name, path, start in ms) of the track its second phase moves to,
    or None when it has none (or that file isn't here)."""
    out = []
    for man in sorted(glob.glob(os.path.join(data_dir, "built", "*", "manifest.json"))):
        m = _json(man)
        mus = m.get("music")
        if m.get("kind") != "fight" or not isinstance(mus, dict) or not set(m.get("skins") or []) & live:
            continue
        req, dungeon, name = mus.get("request"), mus.get("dungeon"), mus.get("file")
        if not (isinstance(req, str) and req.isalnum() and isinstance(dungeon, int) and 0 < dungeon < 10 ** 9
                and isinstance(name, str) and MUSIC.fullmatch(name)):
            continue
        path = os.path.join(data_dir, "music", name)
        if not os.path.isfile(path):
            continue
        second, sec = None, mus.get("second")
        if isinstance(sec, dict) and isinstance(sec.get("file"), str) and MUSIC.fullmatch(sec["file"]) \
                and isinstance(sec.get("at", 0), int) and 0 <= sec.get("at", 0) < 10 ** 8:
            p2 = os.path.join(data_dir, "music", sec["file"])
            if os.path.isfile(p2):
                second = (sec["file"], p2, sec.get("at", 0))
        out.append((req, dungeon, name, path, second))
    return out


STORY_FILE = re.compile(r"[a-z0-9-]{1,80}\.pic")


def _fights(data_dir, live):
    for man in sorted(glob.glob(os.path.join(data_dir, "built", "*", "manifest.json"))):
        m = _json(man)
        if m.get("kind") == "fight" and set(m.get("skins") or []) & live:
            yield os.path.dirname(man), m


def fight_stories(data_dir, live):
    """[(when, enemy id, file name, path)] for the story pictures of the fights in live (built
    into built/<fight>/story/), in the order they were written."""
    out = []
    for folder, m in _fights(data_dir, live):
        for line in m.get("story") if isinstance(m.get("story"), list) else []:
            when, unit, name = (line.get("when"), line.get("unit"), line.get("file")) if isinstance(line, dict) else (None,) * 3
            if when not in ("start", "half", "one", "fuse") or not isinstance(unit, int) or not 0 <= unit < 10 ** 9 \
                    or (unit > 0) != (when in ("half", "one")) or not isinstance(name, str) or not STORY_FILE.fullmatch(name):
                continue
            path = os.path.join(folder, "story", name)
            if os.path.isfile(path):
                out.append((when, unit, name, path))
    return out


def enemy_names(data_dir, live):
    """{enemy id: name} for the enemies of their own the fights in live add."""
    out = {}
    for _, m in _fights(data_dir, live):
        names_ = m.get("enemy_names")
        for i, name in (names_.items() if isinstance(names_, dict) else []):
            if str(i).isdigit() and 0 < int(i) < 10 ** 9 and isinstance(name, str) and name.strip():
                out[int(i)] = " ".join(name.split())[:80]
    return out


BODY_NAME = re.compile(r"[A-Za-z0-9_]{1,96}")


def own_bodies(data_dir, live):
    """[(appearance, file name, path, fallback)] for the bodies of their own (built/<skin>.body)
    of the skins in live: the client patch loads each from <its folder>/bodies/."""
    out = []
    for man in sorted(glob.glob(os.path.join(data_dir, "built", "*.body", "manifest.json"))):
        m = _json(man)
        if m.get("kind") != "body" or not set(m.get("skins") or []) & live:
            continue
        for b in m.get("bodies") or []:
            app, name, fb = (b.get("appearance"), b.get("file"), b.get("fallback") or "") if isinstance(b, dict) else (None,) * 3
            if not (isinstance(app, str) and BODY_NAME.fullmatch(app) and name == app + ".bundle"
                    and isinstance(fb, str) and (not fb or BODY_NAME.fullmatch(fb))):
                continue
            path = os.path.join(os.path.dirname(man), name)
            if os.path.isfile(path):
                out.append((app, name, path, fb))
    return out


def bodies_file(entries):
    """bodies.txt: <appearance> TAB bodies\\<file> TAB <stock appearance to fall back on>."""
    return "".join(f"{a}\tbodies\\{f}\t{fb}\n" for a, f, _, fb in entries)


def allynames_file(entries):
    """allynames.txt: <id> TAB <folder's title> TAB <folder's name> TAB <its own title> (\\n for a line
    break) - AllyFacePatch swaps each of the game's words for the identity's own."""
    return "".join(f"{i}\t" + "\t".join(_escape(x) for x in w) + "\n" for i, w in sorted(entries.items()))


def session_skins(data_dir, done):
    """The skins swapped in this session: the folders applied, and the skins in the helpers build
    when it was (helpers and bosses share it)."""
    helpers = _manifest(data_dir, "helpers").get("skins") if "helpers" in done else None
    return set(done) | {k for k in helpers or [] if isinstance(k, str)}


def pfps(data_dir, live):
    """[(identity, identity, "")] for each skin in live with a pfp of its own (built/<skin>.cover
    with "pfp"): listed as itself with no name, the client patch shows that pfp alone."""
    out = set()
    for man in glob.glob(os.path.join(data_dir, "built", "*.cover", "manifest.json")):
        m = _json(man)
        ident = m.get("identity")
        if m.get("pfp") and isinstance(ident, int) and 0 < ident < 10 ** 9 and set(m.get("skins") or []) & live:
            out.add((ident, ident, ""))
    return sorted(out)


def covers(data_dir, ids, live=frozenset()):
    """{file name: path} of the cover pictures built for the identities ids (built/*.cover); a
    skin's pfp only while that skin is in live (two skins of one identity: the one that's on)."""
    out = {}
    for man in sorted(glob.glob(os.path.join(data_dir, "built", "*.cover", "manifest.json"))):
        m = _json(man)
        if m.get("identity") not in ids:
            continue
        if m.get("pfp") and not set(m.get("skins") or []) & live:
            continue
        pics = os.path.join(os.path.dirname(man), "pics")
        for f in m.get("files") or []:
            if isinstance(f, str) and COVER.fullmatch(f) and os.path.isfile(os.path.join(pics, f)):
                out[f] = os.path.join(pics, f)
    return out


def _sync(folder, wanted):
    """folder holds exactly the files wanted ({name: source}): linked where it can be, else
    copied; a file already there and as new as its source is left alone."""
    os.makedirs(folder, exist_ok=True)
    for f in os.listdir(folder):
        if f not in wanted:
            os.remove(os.path.join(folder, f))
    for f, src in wanted.items():
        dst = os.path.join(folder, f)
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        tmp = dst + ".tmp"
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        os.replace(tmp, dst)


def names(data_dir, live):
    """{identity: name} for the skins in live that make their identity someone else (built/names.json):
    the client patch shows that name instead of the identity's own."""
    out = {}
    for e in _json(os.path.join(data_dir, "built", "names.json")).get("names") or []:
        if not isinstance(e, dict) or e.get("skin") not in live:
            continue
        i, n = e.get("identity"), e.get("name")
        if isinstance(i, int) and 0 < i < 10 ** 9 and isinstance(n, str) and n.strip():
            out[i] = n.strip()[:80]
    return out


def allies_file(entries):
    """allies.txt: <id> TAB <folder id> TAB <name> per identity of our own - the client patch
    shows each with its folder's card pictures and that name (AllyFacePatch)."""
    return "".join(f"{i}\t{folder}\t{_escape(name)}\n" for i, folder, name in entries)


def hp_shifts(data_dir, folders):
    """["<from>><to>@<percent>", "<from>><to>#<skill id>"] from the "shift" of each built folder given."""
    out = []
    for folder in sorted(folders):
        if folder == "helpers" or os.sep in folder or "/" in folder or folder.startswith("."):
            continue
        sh = _manifest(data_dir, folder).get("shift")
        if not isinstance(sh, dict):
            continue
        frm, to, hp, skill = sh.get("from"), sh.get("to"), sh.get("hp"), sh.get("skill")
        if not (isinstance(frm, str) and isinstance(to, str) and APPEARANCE.fullmatch(frm) and APPEARANCE.fullmatch(to)):
            continue
        if isinstance(hp, int) and 1 <= hp <= 99:
            out.append(f"{frm}>{to}@{hp}")
        if isinstance(skill, int) and 1 <= skill <= 999999999:
            out.append(f"{frm}>{to}#{skill}")              # on using that skill
    return out


def death_shifts(data_dir, skins):
    """["<from>><to>!"] for the skins given whose borrowed body has no death of its own (the shared
    build's "deaths"): at the killing blow the unit turns into its sinner's body, whose death plays."""
    out = []
    for key, d in sorted((_manifest(data_dir, "helpers").get("deaths") or {}).items()):
        frm, to = (d or {}).get("from"), (d or {}).get("to")
        if key in skins and isinstance(frm, str) and isinstance(to, str) and frm != to \
                and APPEARANCE.fullmatch(frm) and APPEARANCE.fullmatch(to):
            out.append(f"{frm}>{to}!")
    return out


def _escape(text):
    return (str(text).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "")
            .replace("\n", "\\n").replace("\t", "\\t"))


def skilltext_file(texts):
    """skilltext.txt: one line per field, <skill id> TAB <name|desc|flavor|coin1..coin5> TAB <text>,
    with \\n, \\t and \\\\ for line breaks, tabs and backslashes."""
    lines = []
    for sid, t in sorted(texts.items(), key=lambda kv: int(kv[0])):
        fields = [(k, t[k]) for k in ("name", "desc", "flavor") if k in t]
        fields += [(f"coin{n}", line) for n, line in enumerate((t.get("coins") or [])[:5], 1)]
        lines += [f"{sid}\t{k}\t{_escape(v)}" for k, v in fields]
    return "".join(line + "\n" for line in lines)


def set_key(ini, key, value):
    """The ini text with key=value (replaced in place, else added to the end of the [myosotis]
    section), everything else and the file's line ends kept."""
    nl = "\r\n" if "\r\n" in ini else "\n"
    line = f"{key}={value}"
    lines = ini.splitlines()
    for n, text in enumerate(lines):
        if re.match(rf"\s*{re.escape(key)}\s*=", text):
            lines[n] = line
            break
    else:
        at = len(lines)
        for n, text in enumerate(lines):
            if text.strip().lower() == "[myosotis]":
                at = next((m for m in range(n + 1, len(lines)) if lines[m].lstrip().startswith("[")), len(lines))
                break
        lines.insert(at, line)
    return nl.join(lines) + nl


def _replace(path, text):
    """Write text to path unless it is there already (UTF-8, line ends as given)."""
    try:
        with open(path, encoding="utf-8", newline="") as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def configure(folder, data_dir, on=True):
    """Set the client patch's settings in folder (it holds myosotis.ini) for what was swapped
    in this session; on=False clears them. False if the folder has no client patch."""
    ini = os.path.join(folder, "myosotis.ini")
    if not os.path.isfile(ini):
        return False
    done = applied(data_dir) if on else set()
    with open(ini, encoding="utf-8", newline="") as f:
        text = f.read()
    text = set_key(text, GREEN, ",".join(str(i) for i in (green_skills(data_dir) if "helpers" in done else [])))
    live_now = session_skins(data_dir, done)
    _replace(ini, set_key(text, SHIFT, ",".join(hp_shifts(data_dir, done)
                                               + (death_shifts(data_dir, live_now) if "helpers" in done else []))))
    _replace(os.path.join(folder, "skilltext.txt"), skilltext_file(skill_texts(data_dir) if "helpers" in done else {}))
    _replace(os.path.join(folder, "newskills.txt"),
             "".join(f"{n}\t{o}\n" for n, o in sorted((new_skills(data_dir) if "helpers" in done else {}).items())))
    ours = allies(data_dir) if "helpers" in done else []
    live = session_skins(data_dir, done)
    ours += [p for p in pfps(data_dir, live) if p[0] not in {i for i, _, _ in ours}]
    # the character's own name on an identity a skin turned into someone else
    named = names(data_dir, live)
    ours = [(i, folder, name or named.get(i, "")) for i, folder, name in ours]
    ours += [(i, i, n) for i, n in sorted(named.items()) if i not in {j for j, _, _ in ours}]
    _replace(os.path.join(folder, "allies.txt"), allies_file(sorted(ours)))
    named_ids = {i for i, _, name in ours if name}
    words = wordings(data_dir, live) if on else {}
    _replace(os.path.join(folder, "allynames.txt"), allynames_file({i: w for i, w in words.items() if i in named_ids}))
    _sync(os.path.join(folder, "covers"), covers(data_dir, {i for i, _, _ in ours}, live))
    music = fight_music(data_dir, live) if on else []
    _replace(os.path.join(folder, "fights.txt"),
             "".join(f"{r}\t{d}\tmusic\\{n}" + (f"\tmusic\\{s2[0]}\t{s2[2]}" if s2 else "") + "\n" for r, d, n, _, s2 in music))
    tracks = {n: p for _, _, n, p, _ in music}
    tracks.update({s2[0]: s2[1] for *_, s2 in music if s2})
    _sync(os.path.join(folder, "music"), tracks)
    story = fight_stories(data_dir, live) if on else []
    _replace(os.path.join(folder, "story.txt"), "".join(f"{w}\t{u}\tstory\\{n}\n" for w, u, n, _ in story))
    _sync(os.path.join(folder, "story"), {n: p for _, _, n, p in story})
    _replace(os.path.join(folder, "enemies.txt"),
             "".join(f"{i}\t{n}\n" for i, n in sorted((enemy_names(data_dir, live) if on else {}).items())))
    bodies = own_bodies(data_dir, live) if on else []
    _replace(os.path.join(folder, "bodies.txt"), bodies_file(bodies))
    _sync(os.path.join(folder, "bodies"), {f: p for _, f, p, _ in bodies})
    from . import title
    _sync(os.path.join(folder, "title"), title.files(data_dir) if on else {})
    return True


def wears_bosses(folder):
    """Can the client patch in folder put a boss's own body on a sinner (myosotis's BossBodyPatch,
    from the Limbus Private Server's build 6)? Without it a boss skin's sinner has no body at all."""
    try:
        with open(os.path.join(folder, "myosotis.dll"), "rb") as f:
            return b"bossbody" in f.read()
    except OSError:
        return False


def reads_modcache(folder):
    """Does the client patch in folder read a session's skins from its modcache/ (myosotis's
    ModCachePatch)? An older one only sees skins copied into the game's own files, which
    limbus-skins no longer does: such a package gets no skins at all, and is told so."""
    try:
        with open(os.path.join(folder, "myosotis.dll"), "rb") as f:
            return b"modcache" in f.read()
    except OSError:
        return False


def folders_beside(command):
    """The client patch folders of the private-server package whose launcher this is
    (play.sh, or the Windows app): <its folder>/client and /join, where they hold myosotis.ini."""
    if not command or not os.path.isfile(command):
        return []
    root = os.path.dirname(os.path.abspath(command))
    return [os.path.join(root, d) for d in ("client", "join") if os.path.isfile(os.path.join(root, d, "myosotis.ini"))]
