"""The client patch's settings for what limbus-skins swapped in. myosotis, the private server's
client patch, reads them from its own folder (the one with myosotis.ini, beside its DLL):

  myosotis.ini   green_coin_skills = <skill ids>   skills recoloured to Gluttony draw green coins
                 hp_shift = <from>><to>@<percent>   a body the unit turns into at low HP
                            <from>><to>#<skill id>  ...or on using that skill
  skilltext.txt  names and descriptions of our own for the skills a skin rewrote

They come from the manifests of what was built (built/<folder>/manifest.json), and only from the
folders swapped in this session (active.json): a build the game outdated, and so skipped, gets
nothing. `run` sets them in a private-server package's client folders for the session and
clears them after; `limbus-skins client [--off] <folder>...` does it by hand.
"""
import json
import os
import re

GREEN = "green_coin_skills"
SHIFT = "hp_shift"
APPEARANCE = re.compile(r"[A-Za-z0-9_]{1,80}")


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
    """The built folders swapped into the game this session (none without one)."""
    mods = _json(os.path.join(data_dir, "active.json")).get("mods")
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
    _replace(ini, set_key(text, SHIFT, ",".join(hp_shifts(data_dir, done))))
    _replace(os.path.join(folder, "skilltext.txt"), skilltext_file(skill_texts(data_dir) if "helpers" in done else {}))
    return True


def folders_beside(command):
    """The client patch folders of the private-server package whose launcher this is
    (play.sh, or the Windows app): <its folder>/client and /join, where they hold myosotis.ini."""
    if not command or not os.path.isfile(command):
        return []
    root = os.path.dirname(os.path.abspath(command))
    return [os.path.join(root, d) for d in ("client", "join") if os.path.isfile(os.path.join(root, d, "myosotis.ini"))]
