"""A private session's skins, and nothing of them in the game's own files.

The game reads its battle bodies and static data from Unity's download cache. A session links
each built bundle into modcache/ beside the private server's client patch, laid out like that
cache (<name>/<hash>/__data); the patch (myosotis's ModCachePatch) has the game read them from
there, in that session alone. The game's cache is never written: an official session only ever
sees the game's own files, however a private one ended - a crash, a closed window, a reboot.

Before limbus-skins 1.6 a session copied the built files over the cached ones (the originals kept
in stock/, the list in active.json) and only `restore` put them back - so a session that ended
badly left the skins in the official game too. `heal` puts those back (or, when an original is
lost, removes the entry so the game downloads its own again); every command runs it first.
Nothing here writes while the game is running.
"""
import json
import os
import shutil
import subprocess
import sys
import time

WINDOWS = sys.platform == "win32"
EXE = "LimbusCompany.exe"


class ModError(Exception):
    pass


def game_running():
    if WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/NH"],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return EXE.lower() in out.lower()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if EXE.encode() in f.read():
                    return True
        except OSError:
            continue
    return False


def clone(src, dst):
    """Copy src to dst; a copy-on-write clone where the filesystem has them (btrfs, xfs)."""
    tmp = dst + ".tmp-limbus-skins"
    try:
        os.remove(tmp)                   # a leftover (or a planted link): never written through
    except FileNotFoundError:
        pass
    if not WINDOWS:
        try:
            import fcntl
            with open(src, "rb") as s, open(tmp, "xb") as d:
                fcntl.ioctl(d.fileno(), 0x40049409, s.fileno())      # FICLONE
            os.replace(tmp, dst)
            return
        except (OSError, ImportError):
            try:
                os.remove(tmp)
            except OSError:
                pass
    with open(src, "rb") as s, open(tmp, "xb") as d:
        shutil.copyfileobj(s, d, 1 << 20)
    os.replace(tmp, dst)


MODCACHE = "modcache"     # beside the client patch: what ModCachePatch has the game read instead


def sync_tree(root, wanted):
    """root holds exactly wanted ({relative path: source file}): each a hard link to its source
    where the filesystem allows, else a copy-on-write clone or a copy. Anything else in it goes."""
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    want = {os.path.normpath(r): s for r, s in wanted.items()}
    for d, dirs, names in os.walk(root, topdown=False):
        for n in names:
            p = os.path.join(d, n)
            if os.path.relpath(p, root) not in want:
                os.remove(p)
        if d != root and not os.listdir(d):
            os.rmdir(d)
    for rel, src in sorted(want.items()):
        if rel.startswith("..") or os.path.isabs(rel):
            raise ModError(f"refused: {rel} isn't inside {root}")
        dst = os.path.join(root, rel)
        try:
            if os.path.samefile(src, dst):
                continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp-limbus-skins"
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass
        try:
            os.link(src, tmp)
            os.replace(tmp, dst)
        except OSError:
            clone(src, dst)


class Session:
    """A private session's skins (session.json: the skins, and the client folders whose
    modcache/ holds them), and the cache swap an older limbus-skins may have left (active.json)."""

    def __init__(self, cache, data_dir, game=None):
        self.cache = cache
        self.game = game                                        # LimbusCompany_Data: its shipped catalog
        self.data = data_dir
        self.stock = os.path.join(data_dir, "stock")
        self.path = os.path.join(data_dir, "active.json")      # an older limbus-skins' swap into the cache
        self.live = os.path.join(data_dir, "session.json")
        self.clean = os.path.join(data_dir, "clean.json")      # the cache as it was when last found clean

    def heal_leftovers(self, log=print):
        """Remove every cached bundle that isn't as the game ships it (game.leftovers) - whatever
        left it, whether or not anything wrote it down - so the official game downloads its own
        again. Skipped while nothing in the cache changed since it was last found clean. Returns
        how many went."""
        from . import game
        doc = self._load(self.clean)
        if doc and doc.get("clean") == game.fingerprint(self.cache, self.game):
            return 0
        if game_running():
            raise ModError("Limbus is running - its files are checked once it exits")
        rows = game.leftovers(self.cache, game.catalogs(self.cache, self.game))
        if rows is None:
            log("warning: the game's catalog couldn't be read, so its files weren't checked for leftovers")
            return 0
        for rel, real, stock in rows:
            shutil.rmtree(os.path.join(self.cache, *rel.split("/")), ignore_errors=True)
            log(f"removed {rel} from the game's own files: {real} bytes where the game ships {stock} - a "
                "modded copy something older left there; the game downloads its own again")
        os.makedirs(self.data, exist_ok=True)
        with open(self.clean + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"clean": game.fingerprint(self.cache, self.game)}, f)
        os.replace(self.clean + ".tmp", self.clean)
        return len(rows)

    @staticmethod
    def _load(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else None
        except (OSError, ValueError):
            return None

    def active(self):
        """What an older limbus-skins swapped into the game's own cache and didn't put back."""
        return self._load(self.path)

    def current(self):
        return self._load(self.live)

    def _save(self, doc):
        os.makedirs(os.path.dirname(self.live), exist_ok=True)
        with open(self.live + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(self.live + ".tmp", self.live)

    def plan(self, mods, log=print):
        """mods: [(key, files dir)], each mirroring the cache layout. (keys taken, {relative path:
        built file}); a skin whose bundles the game no longer has (updated since the build) is
        skipped, and so is one that changes a file an earlier one does."""
        claimed, applied, files = {}, [], {}
        for key, root in mods:
            rels = [os.path.relpath(os.path.join(d, n), root) for d, _, ns in os.walk(root) for n in ns]
            data = [r for r in rels if os.path.basename(r) == "__data"]
            if not data or not all(os.path.isdir(os.path.join(self.cache, os.path.dirname(r))) for r in data):
                log(f"  skipped {key}: the game updated since it was built - run build again")
                continue
            clash = next((claimed[r] for r in data if r in claimed), None)
            if clash:
                log(f"  skipped {key}: it changes the same game file as {clash}")
                continue
            for r in data:
                claimed[r] = key
                files[r] = os.path.join(root, r)
            applied.append(key)
        return applied, files

    def apply(self, mods, folders, log=print):
        """The skins in mods for this session: in each client folder's modcache/. Returns the keys
        taken. The game's own cache isn't touched."""
        if game_running():
            raise ModError("Limbus is running - close it first")
        self.restore(log=log)
        applied, files = self.plan(mods, log)
        folders = [os.path.abspath(f) for f in folders]
        self._save({"mods": applied, "folders": folders, "files": sorted(files)})   # first: a crash can still clear
        for f in folders:
            sync_tree(os.path.join(f, MODCACHE), files)
        return applied

    def heal(self, log=print):
        """Put back what an older limbus-skins swapped into the game's cache (active.json): each
        original from stock/, or - when it's lost - the cache entry removed, so the game downloads
        its own again. Returns how many files were healed."""
        doc = self.active()
        if doc is None:
            return 0
        if game_running():
            raise ModError("Limbus is running - the game's own files go back once it exits")
        n, gone = 0, 0
        for e in doc.get("files") or []:
            rel = e.get("rel") if isinstance(e, dict) else None
            if not isinstance(rel, str) or rel.startswith("..") or os.path.isabs(rel):
                continue
            dst = os.path.join(self.cache, rel)
            keep = os.path.join(self.stock, rel)
            if e.get("new"):
                try:
                    os.remove(dst)
                except FileNotFoundError:
                    pass
            elif os.path.exists(keep):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                clone(keep, dst)
            else:                                   # the original is lost: the game fetches its own
                shutil.rmtree(os.path.dirname(dst), ignore_errors=True)
                gone += 1
            n += 1
        shutil.rmtree(self.stock, ignore_errors=True)
        os.remove(self.path)
        if n:
            log(f"put back {n} of the game's own file(s) an older limbus-skins had left modded"
                + (f" ({gone} the game downloads again)" if gone else ""))
        return n

    def restore(self, log=print):
        """The game as it ships, and no skins for the client patch: heal, then empty every
        modcache/ of the session. Returns how many files that took."""
        n = self.heal(log)
        doc = self.current()
        if doc is not None:
            if game_running():
                raise ModError("Limbus is running - the session's skins go once it exits")
            for f in doc.get("folders") or []:
                if isinstance(f, str):
                    shutil.rmtree(os.path.join(f, MODCACHE), ignore_errors=True)
            os.remove(self.live)
            n += len(doc.get("files") or [])
        return n + self.heal_leftovers(log)               # and nothing modded in the game's own files

    def watch(self, appear_timeout=600, log=print):
        """Wait for the game to start, then to exit, then restore."""
        t = time.time()
        while not game_running():
            if time.time() - t > appear_timeout:
                log("the game never started - restoring now")
                return self.restore(log=log)
            time.sleep(3)
        while game_running():
            time.sleep(3)
        time.sleep(3)
        return self.restore(log=log)
