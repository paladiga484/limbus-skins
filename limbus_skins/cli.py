"""limbus-skins: dress Limbus Company identities as the characters they mirror.

  limbus-skins                         what is on, and whether the game is ready
  limbus-skins list [all|on|<sinner>]  every skin you can turn on
  limbus-skins on <skin|id|sinner|helpers|repaints|tested|all>...   turn skins on (saved)
  limbus-skins off <skin|id|sinner|helpers|repaints|all>...         turn skins off
  limbus-skins boss on|off <skin|all>   a repaint also fights with its boss's skills
  limbus-skins run -- <launch command>  swap the skins in, start the game with your
                                        command, put the originals back when it exits
  limbus-skins apply / restore          the two halves of `run`, by hand
  limbus-skins watch                    restore as soon as the game exits
  limbus-skins build [<skin>...]        rebuild (apply does this when needed)
  limbus-skins preview <skin> [file.png]  see a repaint's frames before playing it
  limbus-skins sheet [file.png]         every skin that is on: its idle before -> after
  limbus-skins art <skin> <picture> [--invert]   your own head art (art <skin> --reset)
  limbus-skins doctor                   check paths, Python packages and the cache
  limbus-skins config --game DIR --cache DIR   set paths by hand

Built only from your own installed game; no game art is downloaded or shipped.
Private servers only: helper skins change skills, and any mod on official servers
breaks the game's terms of service and can get an account banned.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

from . import __version__, paths

NOTICE = """\
limbus-skins is for private servers only. Helper skins change identities' skills, and
any modded file on the official servers breaks the terms of service and can get your
account banned. Run `limbus-skins apply --yes` (or `run --yes`) once to confirm you
understand; it is remembered."""


def _state():
    cfg, data = paths.home_dirs()
    try:
        with open(os.path.join(cfg, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("on", [])
    st.setdefault("boss", [])
    return st, cfg, data


def _save_state(st):
    cfg, _ = paths.home_dirs()
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "state.json") + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(os.path.join(cfg, "state.json") + ".tmp", os.path.join(cfg, "state.json"))


def _norm(s):
    """'Ryōshū', 'ryoshu' and 'RYŌSHŪ' all become 'ryoshu'."""
    plain = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


SINNERS = {1: "Yi Sang", 2: "Faust", 3: "Don Quixote", 4: "Ryōshū", 5: "Meursault", 6: "Hong Lu",
           7: "Heathcliff", 8: "Ishmael", 9: "Rodion", 10: "Sinclair", 11: "Outis", 12: "Gregor"}
SINNER_IDS = {_norm(n): k for k, n in SINNERS.items()}
SINNER_IDS.update({"don": 3, "heath": 7})


def select(words, skins):
    keys = []
    for w in words:
        n = _norm(w)
        hit = [k for k, s in skins.items() if
               n in ("all",) or (n == "tested" and s.get("tested")) or _norm(k) == n
               or (n in ("repaints", "helpers", "tints") and s["kind"] == n[:-1])
               or str(s["identity"]) == w or (n in SINNER_IDS and s["identity"] // 100 % 100 == SINNER_IDS[n])]
        if not hit:
            raise SystemExit(f"limbus-skins: no skin, identity or sinner called '{w}' (see `limbus-skins list`)")
        keys += [k for k in hit if k not in keys]
    return keys


UNLOCK_HINT = {"black-forest": "waits in the Black Forest: let Greencliff die in a fight first"}


def unlocked(skins, st):
    """(the skins that can be used, the locked ones). A skin with "unlock" stays out of sight
    and is never swapped in until state["unlocked"] holds that word (the private server's
    Black Forest writes it when Greencliff dies). Locked ones are dropped from state["on"]
    in memory, so nothing locked is built or applied."""
    have = set(st.get("unlocked", []))
    locked = {k for k, s in skins.items() if s.get("unlock") and s["unlock"] not in have}
    # names this version doesn't know (a skin renamed since, e.g. gebura -> kali) drop out too
    st["on"] = [k for k in st["on"] if k in skins and k not in locked]
    return {k: v for k, v in skins.items() if k not in locked}, locked


def refuse_locked(words, locked, skins):
    """Asking for a locked skin by name says where it is instead of 'no such skin'."""
    for w in words:
        hit = next((k for k in locked if _norm(k) == _norm(w)), None)
        if hit:
            why = UNLOCK_HINT.get(skins[hit]["unlock"], "is locked")
            raise SystemExit(f"limbus-skins: {hit} {why}")


def _table(skins, on, keys, boss=()):
    width = max((len(k) for k in keys), default=10)
    last = None
    for k in sorted(keys, key=lambda k: (skins[k]["identity"], k)):
        s = skins[k]
        sid = s["identity"] // 100 % 100
        if sid != last:
            last = sid
            print(f"\n{SINNERS.get(sid, '?').upper()}")
        mark = "ON " if k in on else "   "
        kind = f"{s['kind']:7}"
        tested = "" if s.get("tested") else "  (not yet seen in a fight)"
        fights = "  +boss skills" if k in boss else ""
        print(f"  {mark} {k:{width}}  {s['identity']}  {kind}  -> {s['becomes']}{fights}{tested}")


def split_launch(args):
    """(yes, launch command). Our own flags come before "--"; everything after it is the
    launch command, passed on untouched (without "--", the words after our flags are)."""
    if "--" in args:
        cut = args.index("--")
        return "--yes" in args[:cut], args[cut + 1:]
    return "--yes" in args, [x for x in args if x != "--yes"]


def _need_build(keys, skins, data_dir, cache, boss=()):
    """Enabled skins with no build, or built for an older game version."""
    from .build import FORMAT
    root = os.path.join(data_dir, "built")
    out = []
    try:
        with open(os.path.join(root, "helpers", "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        helper_built, boss_built = set(man["skins"]), set(man.get("skills", []))
    except (OSError, ValueError, KeyError):
        helper_built, boss_built = set(), set()
    want_helpers = {k for k in keys if skins[k]["kind"] == "helper"}
    from .build import skill_edits
    want_boss = skill_edits(keys, skins, boss)
    for k in keys:
        # a skin with skill edits also needs the shared static-data file to be current
        folders = ["helpers"] if skins[k]["kind"] == "helper" else [k]
        if k in want_boss:
            folders.append("helpers")
        for folder in folders:
            try:
                with open(os.path.join(root, folder, "manifest.json"), encoding="utf-8") as f:
                    m = json.load(f)
                files = m["files"]
            except (OSError, ValueError, KeyError):
                out.append(k)
                break
            if m.get("format") != FORMAT:
                out.append(k)                               # built by an older limbus-skins
                break
            if not all(os.path.isdir(os.path.join(cache, r)) for r in files):
                out.append(k)                               # game updated: bundle replaced
                break
    # helpers and skill edits share one file, swapped in whenever any of them is on: rebuild it
    # unless it holds exactly what is on now (a dropped helper or boss choice must leave it too)
    if (want_helpers or want_boss) and (helper_built != want_helpers or boss_built != want_boss):
        out += [k for k in want_boss | want_helpers if k not in out]
    if any(skins[k]["kind"] == "helper" or k in want_boss for k in out):   # the static bundle rebuilds as one set
        out += [k for k in want_helpers | want_boss if k not in out]
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, args = (argv[0], argv[1:]) if argv else ("status", [])

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd in ("-V", "--version", "version"):
        print(f"limbus-skins {__version__}")
        return 0
    if cmd == "config":
        p = argparse.ArgumentParser(prog="limbus-skins config")
        p.add_argument("--game"); p.add_argument("--cache")
        o = p.parse_args(args)
        paths.save_config(game=o.game, cache=o.cache)
        g, c = paths.find()
        print(f"game:  {g}\ncache: {c}")
        return 0

    from .build import build, built_dirs, catalog
    from .mods import ModError, Session, game_running
    st, _, data = _state()
    allskins = catalog()
    skins, locked = unlocked(allskins, st)

    if cmd in ("status",):
        print(f"limbus-skins {__version__} - {len(st['on'])} skin(s) on" + (": " + ", ".join(st["on"]) if st["on"] else ""))
        try:
            g, c = paths.find()
            sess = Session(c, data).active()
            print("swapped in right now: " + (", ".join(sess["mods"]) if sess else "nothing (the game is stock)"))
        except SystemExit as e:
            print(e)
        print("`limbus-skins help` for commands")
        return 0
    if cmd == "list":
        keys = list(skins)
        if args and args[0] == "on":
            keys = [k for k in keys if k in st["on"]]
        elif args and args[0] != "all":
            keys = select(args, skins)
        _table(skins, set(st["on"]), keys, set(st["boss"]))
        print(f"\n{len(st['on'])} on. `limbus-skins on <name>` to add one.")
        return 0
    if cmd in ("on", "off"):
        if not args:
            raise SystemExit(f"usage: limbus-skins {cmd} <skin|id|sinner|tested|all>...")
        if cmd == "on":
            refuse_locked(args, locked, allskins)
        keys = select(args, skins)
        if cmd == "on":
            # one skin per identity: the newest choice wins
            for k in keys:
                st["on"] = [x for x in st["on"] if skins[x]["identity"] != skins[k]["identity"]] + [k]
        else:
            st["on"] = [x for x in st["on"] if x not in keys]
        _save_state(st)
        print(f"{len(st['on'])} skin(s) on. They are swapped in the next time you `run` or `apply`.")
        return 0
    if cmd == "boss":
        if len(args) < 2 or args[0] not in ("on", "off"):
            raise SystemExit("usage: limbus-skins boss on|off <skin|sinner|all>...")
        keys = [k for k in select(args[1:], skins) if skins[k]["kind"] == "repaint" and skins[k].get("source")]
        if not keys:
            raise SystemExit("boss skills are for repaints of a boss (e.g. ahab, kromer)")
        if args[0] == "on":
            st["boss"] = sorted(set(st["boss"]) | set(keys))
            print("boss skills on: " + ", ".join(keys))
            print("these change skills and have not been seen in a fight yet - try them on a spare account")
        else:
            st["boss"] = [k for k in st["boss"] if k not in keys]
            print("boss skills off: " + ", ".join(keys))
        _save_state(st)
        return 0
    if cmd == "doctor":
        return doctor(skins, st, data)
    if cmd == "art":
        from . import art
        if not args:
            raise SystemExit("usage: limbus-skins art <skin> <picture> [--invert] | art <skin> --reset")
        key = select(args[:1], skins)[0]
        if not skins[key].get("head"):
            raise SystemExit(f"{key} has no head recipe - art is for head skins (e.g. butterfly-yisang)")
        if "--reset" in args:
            try:
                os.remove(art.path_for(data, key))
            except FileNotFoundError:
                pass
            print(f"{key} uses the in-game art again (rebuilt on the next apply)")
        else:
            pics = [a for a in args[1:] if not a.startswith("--")]
            if not pics:
                mine = art.path_for(data, key)
                print(f"{key}: " + (f"your art, {mine}" if os.path.exists(mine) else "the in-game art"))
                return 0
            p, size = art.save(pics[0], data, key, "--invert" in args)
            print(f"saved {size[0]}x{size[1]} art to {p}; rebuilt on the next apply")
        shutil.rmtree(os.path.join(data, "built", key), ignore_errors=True)
        return 0

    g, c = paths.find()
    sess = Session(c, data)

    if cmd == "build":
        refuse_locked(args, locked, allskins)
        keys = select(args, skins) if args else st["on"]
        if not keys:
            raise SystemExit("nothing to build - turn some skins on first")
        if sess.active():
            raise SystemExit("a modded session is live - `limbus-skins restore` first (with the game closed)")
        res = build(keys, g, c, data, boss=set(st["boss"]))
        bad = [k for k, (ok, _) in res.items() if not ok]
        print(f"built {len(res) - len(bad)} of {len(res)}" + (f"; failed: {', '.join(bad)}" if bad else ""))
        return 1 if bad else 0
    if cmd == "preview":
        if not args:
            raise SystemExit("usage: limbus-skins preview <skin> [file.png]")
        refuse_locked(args[:1], locked, allskins)
        key = select(args[:1], skins)[0]
        if skins[key]["kind"] == "helper":
            raise SystemExit("previews are for repaints and tints (helper skins use the helper's own art)")
        own = lambda: [d for label, d in built_dirs(data, [key]) if label == key]  # noqa: E731 - not the shared helpers file
        if not own():
            if sess.active():
                raise SystemExit("restore first (a modded session is live)")
            build([key], g, c, data, static=False)      # the body only: the shared file keeps everyone's edits
        from .repaint import preview
        files = own()[0]
        data_file = next(os.path.join(r, n) for r, _, ns in os.walk(files) for n in ns if n == "__data")
        out = args[1] if len(args) > 1 else f"{key}.png"
        from .game import identities
        from .repaint import idle_gif
        app = identities(g, c)[0].get(skins[key]["identity"], {}).get("appearance")
        n = preview(data_file, out, appearance=app)
        print(f"{n} frames -> {out}")
        gif = os.path.splitext(out)[0] + "-idle.gif"
        if app and idle_gif(data_file, app, gif):
            print(f"standing idle, animated -> {gif}")
        return 0
    if cmd == "sheet":
        from .game import identities
        from .repaint import idle_image
        from PIL import Image, ImageDraw
        keys = [k for k in st["on"] if k in skins] or list(skins)
        if not keys:
            raise SystemExit("no skins")
        if sess.active():
            raise SystemExit("restore first (a modded session is live)")
        stale = _need_build(keys, skins, data, c, set(st["boss"]))
        if stale:
            print(f"building {len(stale)} skin(s)...")
            build(stale, g, c, data, boss=set(st["boss"]))
        from .game import body_index
        ids, helper_units = identities(g, c)
        bodies, have = body_index(g, c, os.path.join(data, "bodies.json"))
        cells = []
        for k in sorted(keys, key=lambda k: (skins[k]["identity"], k)):
            s = skins[k]
            app = ids.get(s["identity"], {}).get("appearance")
            before = idle_image(have[bodies[app]], app, 300) if app in bodies else None
            after = None
            if s["kind"] == "helper":
                ha = helper_units.get(s["helper"], {}).get("appearance")
                after = idle_image(have[bodies[ha]], ha, 300) if ha in bodies else None
            else:
                b = built_dirs(data, [k])
                f = next((os.path.join(r, n) for r, _, ns in os.walk(b[0][1]) for n in ns if n == "__data"), None) if b else None
                after = idle_image(f, app, 300) if f else None
            cells.append((k, s, before, after))
            print(f"  {k}")
        W, H, cols = 560, 350, 3
        sheet = Image.new("RGB", (W * cols, H * ((len(cells) + cols - 1) // cols)), (24, 22, 26))
        dr = ImageDraw.Draw(sheet)
        for n, (k, s, b, a) in enumerate(cells):
            x, y = (n % cols) * W, (n // cols) * H
            dr.rectangle([x + 2, y + 2, x + W - 3, y + H - 3], outline=(70, 60, 80))
            dr.text((x + 10, y + 8), f"{s['identity']} {k} ({s['kind']}) -> {s['becomes']}", fill=(235, 225, 210))
            for i, im in enumerate((b, a)):
                if im is None:
                    dr.text((x + 20 + i * 270, y + 160), "no picture", fill=(200, 80, 80))
                    continue
                im = im.copy()
                im.thumbnail((260, 300))
                sheet.paste(im, (x + 10 + i * 270 + (260 - im.width) // 2, y + 30 + 300 - im.height), im)
        out = args[0] if args else "limbus-skins-idles.png"
        sheet.save(out)
        print(f"{len(cells)} skins -> {out} (left: as the game ships it, right: with the skin)")
        return 0
    if cmd in ("apply", "run"):
        yes, launch = split_launch(args)
        if launch and cmd == "apply":
            raise SystemExit(f"limbus-skins apply: unknown option {launch[0]}")
        args = launch
        if not st.get("understood"):
            if not yes:
                print(NOTICE)
                return 2
            st["understood"] = True
            _save_state(st)
        if not st["on"]:
            raise SystemExit("no skins are on - `limbus-skins list`, then `limbus-skins on <name>`")
        if game_running():
            raise SystemExit("Limbus is running - close it first")
        sess.restore()
        stale = _need_build(st["on"], skins, data, c, set(st["boss"]))
        if stale:
            print(f"building {len(stale)} skin(s) for this game version...")
            build(stale, g, c, data, boss=set(st["boss"]))
        try:
            done = sess.apply(built_dirs(data, st["on"], set(st["boss"])))
        except ModError as e:
            raise SystemExit(str(e))
        print(f"swapped in: {', '.join(done) if done else 'nothing'}")
        if cmd == "apply":
            print("start the game now; `limbus-skins restore` (or `watch`) puts the originals back")
            return 0
        if not args:
            print("no launch command given - waiting for you to start the game...")
        else:
            try:
                subprocess.Popen(args)
            except OSError as e:
                sess.restore()
                raise SystemExit(f"could not start {args[0]}: {e}")
        sess.watch()
        return 0
    if cmd == "restore":
        try:
            sess.restore()
        except ModError as e:
            raise SystemExit(str(e))
        print("the game's files are stock")
        return 0
    if cmd == "watch":
        sess.watch()
        return 0
    raise SystemExit(f"limbus-skins: unknown command '{cmd}' - `limbus-skins help`")


def doctor(skins, st, data):
    ok = True
    print(f"limbus-skins {__version__}, Python {sys.version.split()[0]} on {sys.platform}")
    for mod in ("UnityPy", "PIL", "lz4"):
        try:
            __import__(mod)
            print(f"  ok   {mod}")
        except ImportError:
            ok = False
            print(f"  MISSING {mod} - pip install UnityPy==1.25.4 'Pillow>=13' 'brotli>=1.2.1' lz4")
    try:
        g, c = paths.find()
        print(f"  ok   game:  {g}\n  ok   cache: {c}")
        from .game import find_static
        p, _ = find_static(c)
        print(f"  ok   static data: {os.path.relpath(p, c)}")
    except SystemExit as e:
        ok = False
        print(f"  FAIL {e}")
    from .mods import game_running
    print(f"  game running: {'yes' if game_running() else 'no'}")
    print(f"  skins on: {len(st['on'])} of {len(skins)}; data folder: {data}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
