"""Swapping built skins into the game's download cache, and putting the originals back.

The game reads its battle bodies and static data from Unity's download cache. A session
copies each built file over the cached one (keeping the original aside), and `restore`
puts every original back. Nothing here runs while the game is running.
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


class Session:
    """The applied state: which cache files are ours, and the originals we set aside."""

    def __init__(self, cache, data_dir):
        self.cache = cache
        self.stock = os.path.join(data_dir, "stock")
        self.path = os.path.join(data_dir, "active.json")

    def active(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, doc):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(self.path + ".tmp", self.path)

    def apply(self, mods, log=print):
        """mods: [(key, files dir)]. Each files dir mirrors the cache layout. Returns the
        keys applied; mods whose bundles are no longer in the cache are skipped."""
        if game_running():
            raise ModError("Limbus is running - close it first")
        self.restore(log=log)
        claimed, applied, entries = {}, [], []
        self._save({"files": entries, "mods": applied})       # written first: a crash mid-way can still restore
        for key, files in mods:
            rels = []
            for root, _, names in os.walk(files):
                for n in names:
                    rels.append(os.path.relpath(os.path.join(root, n), files))
            data = [r for r in rels if os.path.basename(r) == "__data"]
            if not data or not all(os.path.isdir(os.path.join(self.cache, os.path.dirname(r))) for r in data):
                log(f"  skipped {key}: the game updated since it was built - run build again")
                continue
            clash = next((claimed[r] for r in rels if r in claimed), None)
            if clash:
                log(f"  skipped {key}: it changes the same game file as {clash}")
                continue
            for r in sorted(rels):
                dst = os.path.join(self.cache, r)
                had = os.path.exists(dst)
                if had:
                    keep = os.path.join(self.stock, r)
                    os.makedirs(os.path.dirname(keep), exist_ok=True)
                    clone(dst, keep)
                entries.append({"rel": r, "new": not had})
                self._save({"files": entries, "mods": applied})
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                clone(os.path.join(files, r), dst)
                claimed[r] = key
            applied.append(key)
            self._save({"files": entries, "mods": applied})
        return applied

    def restore(self, log=print):
        doc = self.active()
        if doc is None:
            return 0
        if game_running():
            raise ModError("Limbus is running - the originals go back once it exits")
        n = 0
        for e in doc["files"]:
            dst = os.path.join(self.cache, e["rel"])
            if e["new"]:
                try:
                    os.remove(dst)
                except FileNotFoundError:
                    pass
            else:
                keep = os.path.join(self.stock, e["rel"])
                if not os.path.exists(keep):
                    raise ModError(f"original of {e['rel']} is missing - verify the game files in Steam")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                clone(keep, dst)
            n += 1
        shutil.rmtree(self.stock, ignore_errors=True)
        os.remove(self.path)
        if n:
            log(f"put back {n} original file(s)")
        return n

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
