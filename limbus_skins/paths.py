"""Where the game, its download cache and our own files live.

Linux: Steam (native or Flatpak) running the game under Proton. Windows: Steam.
Every guess can be overridden with LIMBUS_GAME / LIMBUS_CACHE (or `limbus-skins config`).
"""
import json
import os
import re
import sys

APPID = "1973530"
GAME_DIR = os.path.join("steamapps", "common", "Limbus Company", "LimbusCompany_Data")
CACHE_TAIL = os.path.join("AppData", "LocalLow", "Unity", "ProjectMoon_LimbusCompany")
WINDOWS = sys.platform == "win32"


def home_dirs():
    """(config dir, data dir) for our own state."""
    if WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "limbus-skins"), os.path.join(local, "limbus-skins")
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(cfg, "limbus-skins"), os.path.join(data, "limbus-skins")


def steam_roots():
    roots = []
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.append(os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        h = os.path.expanduser("~")
        roots += [os.path.join(h, ".local/share/Steam"), os.path.join(h, ".steam/steam"),
                  os.path.join(h, ".var/app/com.valvesoftware.Steam/.local/share/Steam")]
    seen, out = set(), []
    for r in roots:
        real = os.path.realpath(r)
        if os.path.isdir(r) and real not in seen:
            seen.add(real)
            out.append(r)
    return out


def libraries(root):
    """Steam library folders listed in libraryfolders.vdf (the root itself first)."""
    libs = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        p = m.group(1).replace("\\\\", "\\")
        if p not in libs:
            libs.append(p)
    return libs


def _saved():
    cfg, _ = home_dirs()
    try:
        with open(os.path.join(cfg, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(**kw):
    cfg, _ = home_dirs()
    os.makedirs(cfg, exist_ok=True)
    doc = _saved()
    doc.update({k: v for k, v in kw.items() if v is not None})
    with open(os.path.join(cfg, "config.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)


def find():
    """(LimbusCompany_Data dir, Unity cache dir). Raises SystemExit with a hint if unsure."""
    saved = _saved()
    game = os.environ.get("LIMBUS_GAME") or saved.get("game")
    cache = os.environ.get("LIMBUS_CACHE") or saved.get("cache")
    if not game or not cache:
        for root in steam_roots():
            for lib in libraries(root):
                if not os.path.isfile(os.path.join(lib, "steamapps", f"appmanifest_{APPID}.acf")):
                    continue
                game = game or os.path.join(lib, GAME_DIR)
                if not cache:
                    if WINDOWS:
                        cache = os.path.join(os.path.expanduser("~"), CACHE_TAIL)
                    else:
                        cache = os.path.join(lib, "steamapps", "compatdata", APPID, "pfx", "drive_c",
                                             "users", "steamuser", CACHE_TAIL)
                break
            if game and cache:
                break
    if WINDOWS and not cache:
        cache = os.path.join(os.path.expanduser("~"), CACHE_TAIL)
    problems = []
    if not game or not os.path.isdir(game):
        problems.append(f"game folder not found ({game or 'no Steam library has Limbus Company'})")
    if not cache or not os.path.isdir(cache):
        problems.append(f"download cache not found ({cache or '?'}) - launch the game once first")
    if problems:
        raise SystemExit("limbus-skins: " + "; ".join(problems) +
                         "\n  set them with: limbus-skins config --game <LimbusCompany_Data> --cache <ProjectMoon_LimbusCompany>")
    return game, cache
