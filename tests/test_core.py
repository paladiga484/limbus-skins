"""Core pieces that decide what the game reads: bundles, CRCs, sessions, selection."""
import copy
import contextlib
import io
import json
import math
import os
import re
import socket
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from limbus_skins import art, bundle, cli, mods, paths, repaint, spine  # noqa: E402

HDR = {"ver": 8, "uver": "5.x.x", "urev": "6000.3.12f1", "flags": 0}


def fake_bundle(payload=b"\x01" * 300 + b"\x02" * 200):
    nodes = [[0, 300, 4, "CAB-test"], [300, 200, 0, "CAB-test.resS"]]
    return bundle.build(HDR, nodes, payload)


class Bundles(unittest.TestCase):
    def test_round_trip(self):
        raw = fake_bundle()
        hdr, blocks, nodes, data = bundle.parse(raw)
        self.assertEqual([n[3] for n in nodes], ["CAB-test", "CAB-test.resS"])
        self.assertEqual(bundle.crc(raw), zlib.crc32(data) & 0xFFFFFFFF)

    def test_lz4_blocks_are_read(self):
        import lz4.block
        payload = b"limbus" * 100
        comp = lz4.block.compress(payload, store_size=False)
        info = bytearray(16) + struct.pack(">i", 1) + struct.pack(">IIH", len(payload), len(comp), 2)
        info += struct.pack(">i", 1) + struct.pack(">qqI", 0, len(payload), 4) + b"CAB-x\0"
        head = b"UnityFS\0" + struct.pack(">I", 6) + b"5.x.x\0" + b"2019\0"
        raw = head + struct.pack(">qIII", 0, len(info), len(info), 0) + bytes(info) + comp
        self.assertEqual(bundle.parse(raw)[3], payload)

    def test_forge_hits_any_target_and_keeps_content(self):
        raw = fake_bundle()
        for target in (0, 0xDEADBEEF, bundle.crc(fake_bundle(b"\x07" * 500))):
            out = bundle.forge(raw, target)
            self.assertEqual(bundle.crc(out), target)
            _, _, nodes, data = bundle.parse(out)
            self.assertEqual(data[:300], b"\x01" * 300)         # serialized file intact, 4 bytes after it
            self.assertEqual(nodes[0][1], 304)
            self.assertEqual(data[nodes[1][0]:nodes[1][0] + 200], b"\x02" * 200)

    def test_not_a_bundle(self):
        with self.assertRaises(ValueError):
            bundle.parse(b"PNG....")


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self.tmp.name, "cache")
        self.data = os.path.join(self.tmp.name, "data")
        for rel, body in (("aa/11/__data", b"stock-a"), ("aa/11/__info", b"info-a"), ("bb/22/__data", b"stock-b")):
            os.makedirs(os.path.join(self.cache, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.cache, rel), "wb") as f:
                f.write(body)
        self.p = mock.patch.object(mods, "game_running", return_value=False)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def mod(self, name, files):
        root = os.path.join(self.tmp.name, "built", name, "files")
        for rel, body in files.items():
            os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(root, rel), "wb") as f:
                f.write(body)
        return name, root

    def read(self, rel, root=None):
        with open(os.path.join(root or self.cache, rel), "rb") as f:
            return f.read()

    def client(self, name="client"):
        d = os.path.join(self.tmp.name, "pkg", name)
        os.makedirs(d, exist_ok=True)
        return d

    def stock(self):
        return (self.read("aa/11/__data"), self.read("aa/11/__info"), self.read("bb/22/__data"))

    def test_a_session_never_writes_the_games_own_files(self):
        s, c = mods.Session(self.cache, self.data), self.client()
        done = s.apply([self.mod("one", {"aa/11/__data": b"MOD", "aa/11/__info": b"MODINFO"})], [c], log=lambda *a: None)
        self.assertEqual(done, ["one"])
        self.assertEqual(self.stock(), (b"stock-a", b"info-a", b"stock-b"))      # an official session sees these
        self.assertEqual(self.read("aa/11/__data", os.path.join(c, mods.MODCACHE)), b"MOD")   # the patch reads this
        self.assertFalse(os.path.exists(os.path.join(c, mods.MODCACHE, "aa/11/__info")))   # bundles alone
        self.assertEqual(s.current()["mods"], ["one"])
        s.restore(log=lambda *a: None)
        self.assertFalse(os.path.exists(os.path.join(c, mods.MODCACHE)))
        self.assertIsNone(s.current())
        self.assertEqual(self.stock(), (b"stock-a", b"info-a", b"stock-b"))

    def test_a_session_that_ends_badly_leaves_the_official_game_stock(self):
        # the window closed, the PC rebooted: nothing ran restore - and nothing needs to have
        s, c = mods.Session(self.cache, self.data), self.client()
        s.apply([self.mod("a", {"aa/11/__data": b"A"}), self.mod("b", {"bb/22/__data": b"B"})], [c], log=lambda *a: None)
        self.assertEqual(self.stock(), (b"stock-a", b"info-a", b"stock-b"))
        self.assertTrue(os.path.samefile(os.path.join(c, mods.MODCACHE, "aa", "11", "__data"),
                                         os.path.join(self.tmp.name, "built", "a", "files", "aa", "11", "__data")))

    def test_an_older_limbus_skins_swap_is_healed(self):
        # what 1.5 and before left after a bad end: the skin in the cache, the original in stock/
        with open(os.path.join(self.cache, "aa/11/__data"), "wb") as f:
            f.write(b"LEAKED")
        with open(os.path.join(self.cache, "bb/22/__data"), "wb") as f:
            f.write(b"LEAKED-NO-ORIGINAL")
        os.makedirs(os.path.join(self.data, "stock", "aa", "11"))
        with open(os.path.join(self.data, "stock", "aa", "11", "__data"), "wb") as f:
            f.write(b"stock-a")
        os.makedirs(os.path.join(self.cache, "cc", "33"))
        with open(os.path.join(self.cache, "cc", "33", "__extra"), "wb") as f:
            f.write(b"new")
        with open(os.path.join(self.data, "active.json"), "w") as f:
            json.dump({"files": [{"rel": "aa/11/__data", "new": False}, {"rel": "bb/22/__data", "new": False},
                                 {"rel": "cc/33/__extra", "new": True}, {"rel": "../../escape", "new": True}],
                       "mods": ["x"]}, f)
        s, logged = mods.Session(self.cache, self.data), []
        with mock.patch.object(mods, "game_running", return_value=True):
            with self.assertRaises(mods.ModError):              # not while the game has them open
                s.heal(log=logged.append)
        self.assertEqual(s.heal(log=logged.append), 3)
        self.assertEqual(self.read("aa/11/__data"), b"stock-a")             # its original back
        self.assertFalse(os.path.exists(os.path.join(self.cache, "bb", "22")))   # no original: the game re-downloads it
        self.assertFalse(os.path.exists(os.path.join(self.cache, "cc", "33", "__extra")))
        self.assertIsNone(s.active())
        self.assertFalse(os.path.exists(os.path.join(self.data, "stock")))
        self.assertTrue(any("1 the game downloads again" in line for line in logged), logged)
        self.assertEqual(s.heal(), 0)                                        # once is enough

    def test_greencliff_stays_green_when_vergilius_wildhunt_holds_wild_hunt(self):
        # a friend's `on all` (limbus-skins 1.3.2): vergilius-wildhunt and Greencliff's green Wild Hunt
        # both change Wild Hunt's file. Only that part may be skipped - Greencliff's own body goes in.
        s, c, logged = mods.Session(self.cache, self.data), self.client(), []
        done = s.apply([self.mod("vergilius-wildhunt", {"bb/22/__data": b"VERG"}),
                        self.mod("greencliff", {"aa/11/__data": b"GREEN"}),
                        self.mod("greencliff.forms", {"bb/22/__data": b"GREENHUNT"})], [c], log=logged.append)
        self.assertEqual(done, ["vergilius-wildhunt", "greencliff"])
        mc = os.path.join(c, mods.MODCACHE)
        self.assertEqual((self.read("aa/11/__data", mc), self.read("bb/22/__data", mc)), (b"GREEN", b"VERG"))
        self.assertTrue(any("skipped greencliff.forms" in line for line in logged))

    def test_skips_mods_for_bundles_the_game_replaced(self):
        s, c = mods.Session(self.cache, self.data), self.client()
        done = s.apply([self.mod("old", {"zz/99/__data": b"MOD"})], [c], log=lambda *a: None)
        self.assertEqual(done, [])
        self.assertFalse(os.path.exists(os.path.join(c, mods.MODCACHE, "zz")))

    def test_every_client_folder_gets_the_session_and_a_new_one_replaces_it(self):
        s, c, j = mods.Session(self.cache, self.data), self.client(), self.client("join")
        s.apply([self.mod("a", {"aa/11/__data": b"A"})], [c, j], log=lambda *a: None)
        s.apply([self.mod("b", {"bb/22/__data": b"B"})], [c], log=lambda *a: None)
        self.assertFalse(os.path.exists(os.path.join(j, mods.MODCACHE)))            # the last session's cleared
        self.assertFalse(os.path.exists(os.path.join(c, mods.MODCACHE, "aa")))
        self.assertEqual(self.read("bb/22/__data", os.path.join(c, mods.MODCACHE)), b"B")
        self.assertEqual(self.stock(), (b"stock-a", b"info-a", b"stock-b"))

    def test_refuses_while_the_game_runs(self):
        s, c = mods.Session(self.cache, self.data), self.client()
        s.apply([self.mod("a", {"aa/11/__data": b"A"})], [c], log=lambda *a: None)
        with mock.patch.object(mods, "game_running", return_value=True):
            with self.assertRaises(mods.ModError):
                s.restore(log=lambda *a: None)
            with self.assertRaises(mods.ModError):
                s.apply([], [c], log=lambda *a: None)
        self.assertEqual(self.read("aa/11/__data", os.path.join(c, mods.MODCACHE)), b"A")

    def test_sync_tree_keeps_only_whats_wanted_and_stays_inside(self):
        root, src = os.path.join(self.tmp.name, "tree"), os.path.join(self.tmp.name, "src")
        with open(src, "wb") as f:
            f.write(b"S")
        os.makedirs(os.path.join(root, "old", "x"))
        with open(os.path.join(root, "old", "x", "__data"), "wb") as f:
            f.write(b"O")
        mods.sync_tree(root, {"new/y/__data": src})
        self.assertEqual(sorted(os.listdir(root)), ["new"])
        with self.assertRaises(mods.ModError):
            mods.sync_tree(root, {"../outside": src})


class Selection(unittest.TestCase):
    SK = {"ahab": {"identity": 10808, "kind": "repaint", "tested": True, "becomes": "Ahab"},
          "kira": {"identity": 10814, "kind": "helper", "tested": False, "becomes": "Kira"},
          "rien": {"identity": 10115, "kind": "repaint", "tested": False, "becomes": "Rien"}}

    def test_kind_selectors(self):
        skins = {"a": {"identity": 10101, "kind": "helper"}, "b": {"identity": 10102, "kind": "repaint"}}
        self.assertEqual(cli.select(["repaints"], skins), ["b"])
        self.assertEqual(cli.select(["helpers"], skins), ["a"])

    def test_words(self):
        self.assertEqual(cli.select(["ahab"], self.SK), ["ahab"])
        self.assertEqual(cli.select(["10115"], self.SK), ["rien"])
        self.assertEqual(sorted(cli.select(["Ishmael"], self.SK)), ["ahab", "kira"])
        self.assertEqual(cli.select(["Yi-Sang"], self.SK), ["rien"])
        sk = dict(self.SK, ara={"identity": 10401, "kind": "helper", "tested": True, "becomes": "x"})
        for w in ("ryoshu", "Ryōshū", "RYŌSHŪ"):
            self.assertEqual(cli.select([w], sk), ["ara"])
        self.assertEqual(cli.select(["tested"], self.SK), ["ahab"])
        self.assertEqual(sorted(cli.select(["all"], self.SK)), ["ahab", "kira", "rien"])
        with self.assertRaises(SystemExit):
            cli.select(["nobody"], self.SK)


class OneCopy(unittest.TestCase):
    # friend, 2026-11-05: "remove dupes" - a reskin and its own identity, or two reskins in one
    # body, put the same character in the game twice; one copy of a character is on at a time
    SK = {"starbuck": {"identity": 10107, "kind": "repaint", "dupe_of": "starbuck-id"},
          "starbuck-id": {"identity": 10196, "kind": "identity"},
          "jun-gregor": {"identity": 11208, "kind": "repaint", "dupe_of": "jun-gregor-id"},
          "jun-gregor-id": {"identity": 11296, "kind": "identity", "dupe_of": "jun-ishmael-id"},
          "jun-ishmael": {"identity": 10811, "kind": "repaint", "dupe_of": "jun-ishmael-id"},
          "jun-ishmael-id": {"identity": 10893, "kind": "identity"},
          "ahab": {"identity": 10808, "kind": "repaint"}}

    def test_the_catalog_knows_each_characters_copies(self):
        from limbus_skins.build import catalog, character
        sk = catalog()
        self.assertEqual(sk["starbuck"]["dupe_of"], "starbuck-id")              # its own identity stands for it
        self.assertEqual(sk["queen-of-hatred"]["dupe_of"], "queen-of-hatred-id")
        self.assertEqual(sk["dulcinea"]["dupe_of"], "dulcinea-id")
        self.assertEqual(sk["vergilius-id"]["dupe_of"], "vergilius")            # a copy of what 10710 wears
        self.assertEqual(sk["kali-id"]["dupe_of"], "kali")
        self.assertEqual({character(sk, k) for k in ("jun-ishmael", "jun-ishmael-id", "jun-gregor", "jun-gregor-id")},
                         {"jun-ishmael-id"})                                    # four Juns, one body
        for k in ("vergilius-boss-id", "distorted-kim-id", "butterfly-yisang", "roland", "matthias"):
            self.assertEqual(character(sk, k), k)                               # a body of their own
        bodies = {}
        for k, s in sk.items():                                # no two characters left in one body
            body = s.get("source") or s.get("helper")
            if body and character(sk, k) == k:
                self.assertNotIn(body, bodies, (k, bodies.get(body)))
                bodies[body] = k

    def test_every_identity_of_our_own_has_an_id_no_other_has(self):
        from limbus_skins.build import catalog
        seen = {}
        for k, s in sorted(catalog().items()):
            if s["kind"] == "identity":
                self.assertNotIn(s["identity"], seen, f"{k} and {seen.get(s['identity'])} share {s['identity']}")
                seen[s["identity"]] = k

    def test_a_name_is_a_characters_name_and_a_title_reads_like_the_games(self):
        # friend, 2026-11-14: "fix the naming of the ids cuz most of them are broken" - the game shows
        # an identity's title (two short lines, "The Pequod\nFirst Mate") and then the name ("Yi Sang")
        from limbus_skins.build import NAME_KINDS, catalog, display_name
        for k, s in sorted(catalog().items()):
            if s["kind"] in NAME_KINDS or s["kind"] == "identity":
                n = display_name(s, k)
                self.assertTrue(0 < len(n) <= 20, f"{k}: {n!r}")
                self.assertNotRegex(n, r", the |\bin (his|her|their)\b|\bbody\b|looks only", f"{k}: {n!r}")
            t = s.get("game_title")
            if t is not None:
                lines = t.split("\n")
                self.assertTrue(1 <= len(lines) <= 3 and all(0 < len(x) <= 29 for x in lines), f"{k}: {t!r}")

    def test_a_copy_must_name_a_skin_and_never_loop(self):
        from limbus_skins.build import check_copies
        check_copies(self.SK)
        with self.assertRaises(ValueError):
            check_copies(dict(self.SK, ahab={"identity": 10808, "kind": "repaint", "dupe_of": "nobody"}))
        with self.assertRaises(ValueError):
            check_copies(dict(self.SK, **{"starbuck-id": {"identity": 10196, "kind": "identity", "dupe_of": "starbuck"}}))

    def test_all_turns_on_one_copy_of_each_character(self):
        self.assertEqual(cli.one_each(list(self.SK), self.SK), ["starbuck-id", "jun-ishmael-id", "ahab"])
        self.assertEqual(cli.one_each(["jun-gregor", "jun-ishmael"], self.SK), ["jun-ishmael"])  # the newest
        self.assertEqual(cli.one_each(["starbuck"], self.SK), ["starbuck"])

    def test_an_older_state_with_both_copies_keeps_the_characters_own_identity(self):
        on = ["starbuck", "starbuck-id", "jun-gregor", "jun-ishmael", "ahab"]
        self.assertEqual(cli.dedupe(on, self.SK), (["starbuck-id", "jun-ishmael", "ahab"], ["starbuck", "jun-gregor"]))
        self.assertEqual(cli.dedupe(["ahab", "starbuck"], self.SK), (["ahab", "starbuck"], []))

    def test_the_commands_keep_one_copy_on(self):
        from limbus_skins.build import catalog, character
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": home, "XDG_DATA_HOME": home, "APPDATA": home, "LOCALAPPDATA": home,
                             "LIMBUS_SKINS_PATCHLC": ""}), mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            state = os.path.join(home, "limbus-skins", "state.json")

            def on():
                with open(state) as f:
                    return json.load(f)["on"]
            self.assertEqual(cli.main(["on", "starbuck-id"]), 0)
            self.assertEqual(cli.main(["on", "starbuck"]), 0)
            self.assertEqual(on(), ["starbuck"])                                 # the newest choice wins
            self.assertEqual(cli.main(["on", "all"]), 0)
            sk, now = catalog(), on()
            self.assertIn("starbuck-id", now)
            self.assertNotIn("starbuck", now)
            self.assertEqual(len({character(sk, k) for k in now}), len(now))     # no character twice
            self.assertIn("binah-id", now)                                       # a whole identity of its own stands
            self.assertNotIn("binah", now)                                       # for its character: Rodion stays Rodion
            self.assertLessEqual({"twilight", "nothing-there", "whitenight"}, set(now))   # fights share no identity
            self.assertEqual(cli.main(["on", "twilight"]), 0)                    # one fight on keeps the others
            self.assertLessEqual({"twilight", "nothing-there", "whitenight"}, set(on()))
            with open(state, "w") as f:                                          # as an older limbus-skins left it
                json.dump({"on": ["sora", "sora-id", "ahab"]}, f)
            self.assertEqual(cli.main(["list"]), 0)
            self.assertEqual(on(), ["sora-id", "ahab"])

    def test_list_json_is_what_an_app_reads(self):
        import io
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": home, "XDG_DATA_HOME": home, "APPDATA": home, "LOCALAPPDATA": home,
                             "LIMBUS_SKINS_PATCHLC": ""}), mock.patch("sys.stderr"):
            with mock.patch("sys.stdout"):
                self.assertEqual(cli.main(["on", "ahab"]), 0)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(cli.main(["list", "--json"]), 0)
            rows = json.loads(out.getvalue())
            ahab = next(r for r in rows if r["key"] == "ahab")
            self.assertTrue(ahab["on"])
            self.assertEqual(sum(r["on"] for r in rows), 1)
            self.assertEqual(set(ahab), {"key", "sinner", "identity", "kind", "becomes", "on", "tested", "boss"})
            self.assertTrue(all(isinstance(r["sinner"], str) and r["sinner"] for r in rows))


def fake_catalog(entries, mislabel=()):
    """An Addressables catalog.bin as the game's lays out a bundle: its 16-byte hash, its folder name
    (u32 length 32, then the name), and the record [where the hash is][where the name is][CRC][size].
    mislabel: hashes whose record points at another folder's name."""
    blob = bytearray(64)
    for folder, h, size in entries:
        blob += f"sd_x_assets_all_{h}.bundle".encode()
        x = len(blob)
        blob += bytes.fromhex(h)
        blob += struct.pack("<I", 32)
        n = len(blob)
        blob += (("f" * 32) if h in mislabel else folder).encode() + b"\x00\x00"
        blob += struct.pack("<IIII", x, n, 0xBC5EE30D, size)
    return bytes(blob)


class CacheLeftovers(unittest.TestCase):
    # friend, 2026-11-05: "i need it to not leak" - a modded bundle swapped in with no record of it
    # stays in the game's own cache, and its stock CRC hides it from the game. Every cached bundle
    # is held against the size the game's own catalog says it ships with.
    A, B, C, D = ("a" * 32, "1" * 32), ("b" * 32, "2" * 32), ("c" * 32, "3" * 32), ("d" * 32, "4" * 32)

    def cache(self, root, sizes):
        c = os.path.join(root, "LocalLow", "Unity", "ProjectMoon_LimbusCompany")
        for (folder, h), n in sizes.items():
            os.makedirs(os.path.join(c, folder, h))
            with open(os.path.join(c, folder, h, "__data"), "wb") as f:
                f.write(b"U" * n)
        return c

    def put_catalog(self, root, blob):
        d = os.path.join(root, "LocalLow", "ProjectMoon", "LimbusCompany", "com.unity.addressables")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "catalog_S1.bin"), "wb") as f:
            f.write(blob)

    def test_the_catalog_gives_each_bundle_its_stock_size(self):
        from limbus_skins import game
        cat = fake_catalog([(*self.A, 100), (*self.B, 200)], mislabel={self.B[1]})
        self.assertEqual(game.stock_size(cat, *self.A), 100)
        self.assertIsNone(game.stock_size(cat, *self.B))       # its record names another folder: not taken
        self.assertIsNone(game.stock_size(cat, *self.C))       # not in the catalog

    def test_a_bundle_that_isnt_its_stock_size_is_a_leftover(self):
        from limbus_skins import game
        with tempfile.TemporaryDirectory() as tmp:
            c = self.cache(tmp, {self.A: 100, self.B: 200, self.C: 999, self.D: 50})
            cat = fake_catalog([(*self.A, 100), (*self.B, 200), (*self.C, 300)])    # D: an older version
            self.assertEqual(game.leftovers(c, [cat]), [("/".join(self.C), 999, 300)])

    def test_an_unreadable_catalog_never_removes_anything(self):
        from limbus_skins import game
        with tempfile.TemporaryDirectory() as tmp:
            c = self.cache(tmp, {self.A: 100, self.B: 200, self.C: 300})
            self.assertIsNone(game.leftovers(c, []))                                  # no catalog
            self.assertIsNone(game.leftovers(c, [b"\x00" * 4096]))                    # its layout changed
            wrong = fake_catalog([(*self.A, 1), (*self.B, 2), (*self.C, 3)])
            self.assertIsNone(game.leftovers(c, [wrong]))                             # everything "modded": no

    def test_the_catalogs_are_the_downloaded_one_and_the_shipped_one(self):
        from limbus_skins import game
        with tempfile.TemporaryDirectory() as tmp:
            c = self.cache(tmp, {self.A: 100})
            self.put_catalog(tmp, b"downloaded")
            g = os.path.join(tmp, "LimbusCompany_Data")
            os.makedirs(os.path.join(g, "StreamingAssets", "aa"))
            with open(os.path.join(g, "StreamingAssets", "aa", "catalog.bin"), "wb") as f:
                f.write(b"shipped")
            self.assertEqual(game.catalogs(c, g), [b"downloaded", b"shipped"])

    def test_leftovers_go_and_the_game_downloads_its_own(self):
        from limbus_skins import game
        with tempfile.TemporaryDirectory() as tmp:
            c = self.cache(tmp, {self.A: 100, self.B: 200, self.C: 999})
            self.put_catalog(tmp, fake_catalog([(*self.A, 100), (*self.B, 200), (*self.C, 300)]))
            data = os.path.join(tmp, "data")
            sess = mods.Session(c, data)
            said = []
            with mock.patch.object(mods, "game_running", return_value=True):
                with self.assertRaises(mods.ModError):                        # never under the running game
                    sess.heal_leftovers(log=said.append)
            self.assertTrue(os.path.isdir(os.path.join(c, *self.C)))
            self.assertEqual(sess.heal_leftovers(log=said.append), 1)
            self.assertFalse(os.path.exists(os.path.join(c, *self.C)))           # gone: fetched again, stock
            self.assertTrue(os.path.isfile(os.path.join(c, *self.A, "__data")))  # the rest untouched
            self.assertIn("/".join(self.C), said[-1])
            with mock.patch.object(game, "leftovers", side_effect=AssertionError("checked again")):
                self.assertEqual(sess.heal_leftovers(log=said.append), 0)     # nothing changed: not again
            with open(os.path.join(c, *self.B, "__data"), "ab") as f:
                f.write(b"modded")                                            # something wrote a game file
            self.assertEqual(sess.heal_leftovers(log=said.append), 1)
            self.assertFalse(os.path.exists(os.path.join(c, *self.B)))

    def test_status_and_restore_check_the_game_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.cache(tmp, {self.A: 100, self.B: 200, self.C: 999})
            self.put_catalog(tmp, fake_catalog([(*self.A, 100), (*self.B, 200), (*self.C, 300)]))
            g = os.path.join(tmp, "LimbusCompany_Data")
            os.makedirs(g)
            env = {"XDG_CONFIG_HOME": tmp, "XDG_DATA_HOME": tmp, "APPDATA": tmp, "LOCALAPPDATA": tmp,
                   "LIMBUS_GAME": g, "LIMBUS_CACHE": c}
            with mock.patch.dict(os.environ, env), mock.patch.object(mods, "game_running", return_value=False), \
                    mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                self.assertEqual(cli.main(["status"]), 0)
                self.assertFalse(os.path.exists(os.path.join(c, *self.C)))
                os.makedirs(os.path.join(c, *self.C))
                with open(os.path.join(c, *self.C, "__data"), "wb") as f:
                    f.write(b"x" * 5)                                         # left behind again
                self.assertEqual(cli.main(["restore"]), 0)
                self.assertFalse(os.path.exists(os.path.join(c, *self.C)))


class Rebuilds(unittest.TestCase):
    SK = {"ahab": {"identity": 10808, "kind": "repaint", "source": "x"},
          "kira": {"identity": 10814, "kind": "helper"}}

    def _built(self, root, boss):
        from limbus_skins.build import FORMAT
        cache = os.path.join(root, "cache")
        os.makedirs(os.path.join(cache, "a", "b"))
        for folder, extra in (("ahab", {}), ("helpers", {"skins": [], "skills": boss})):
            os.makedirs(os.path.join(root, "data", "built", folder))
            with open(os.path.join(root, "data", "built", folder, "manifest.json"), "w") as f:
                json.dump(dict({"format": FORMAT, "files": ["a/b"]}, **extra), f)
        return os.path.join(root, "data"), cache

    def test_a_tints_other_body_is_its_own_folder_after_the_skin(self):
        # Greencliff's green Wild Hunt lives apart: if vergilius-wildhunt holds Wild Hunt's file,
        # only that part is skipped, not Greencliff himself (a friend's "on all" lost him whole)
        from limbus_skins import build
        with tempfile.TemporaryDirectory() as tmp:
            for folder in ("greencliff", "greencliff.forms", "vergilius-wildhunt"):
                os.makedirs(os.path.join(tmp, "built", folder, "files"))
            labels = [label for label, _ in build.built_dirs(tmp, ["vergilius-wildhunt", "greencliff"])]
            self.assertEqual(labels, ["vergilius-wildhunt", "greencliff", "greencliff.forms"])

    def test_boss_choice_changes_rebuild_and_nothing_else_does(self):
        for built, want, stale in (([], set(), []), ([], {"ahab"}, ["ahab"]),
                                   (["ahab"], {"ahab"}, []), (["ahab"], set(), [])):
            with tempfile.TemporaryDirectory() as tmp:
                data, cache = self._built(tmp, built)
                self.assertEqual(cli._need_build(["ahab"], self.SK, data, cache, want), stale, (built, want))

    def test_a_kit_that_is_built_is_not_built_again(self):
        # 2026-11-18: kits were missing from the shared file's "skins", so every launch rebuilt all
        # 94 skins (6 minutes); the build lists them now, and a launch with nothing new builds nothing
        sk = dict(self.SK, roland={"identity": 10107, "kind": "kit", "moves": {"1": {}}})
        with tempfile.TemporaryDirectory() as tmp:
            data, cache = self._built(tmp, ["roland"])
            with open(os.path.join(data, "built", "helpers", "manifest.json")) as f:
                man = json.load(f)
            man["skins"] = ["kira", "roland"]
            with open(os.path.join(data, "built", "helpers", "manifest.json"), "w") as f:
                json.dump(man, f)
            self.assertEqual(cli._need_build(["kira", "roland"], sk, data, cache, set()), [])
            man["skins"] = ["kira"]                                    # as the old build wrote it
            with open(os.path.join(data, "built", "helpers", "manifest.json"), "w") as f:
                json.dump(man, f)
            self.assertTrue(cli._need_build(["kira", "roland"], sk, data, cache, set()))

    def test_dropped_boss_skills_leave_a_helper_file_that_is_swapped_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, cache = self._built(tmp, ["ahab"])
            self.assertIn("kira", cli._need_build(["ahab", "kira"], self.SK, data, cache, set()))


class Locked(unittest.TestCase):
    SK = {"ahab": {"identity": 10808, "kind": "repaint", "becomes": "Ahab"},
          "kali": {"identity": 10410, "kind": "repaint", "becomes": "Kali", "unlock": "black-forest"}}

    def test_a_locked_skin_is_out_of_sight_and_never_active(self):
        st = {"on": ["ahab", "kali", "gebura"], "unlocked": []}           # gebura: an old name, gone
        visible, locked = cli.unlocked(self.SK, st)
        self.assertEqual((sorted(visible), locked), (["ahab"], {"kali"}))
        self.assertEqual(st["on"], ["ahab"])                           # not swapped in while locked
        self.assertEqual(sorted(cli.select(["all"], visible)), ["ahab"])
        with self.assertRaises(SystemExit) as e:
            cli.refuse_locked(["Kali"], locked, self.SK)
        self.assertIn("is locked", str(e.exception))

    def test_unlocked_it_is_an_ordinary_skin(self):
        st = {"on": ["kali"], "unlocked": ["black-forest"]}
        visible, locked = cli.unlocked(self.SK, st)
        self.assertEqual((sorted(visible), locked, st["on"]), (["ahab", "kali"], set(), ["kali"]))
        cli.refuse_locked(["kali"], locked, self.SK)                   # no complaint

    def test_the_locked_skins_are_written_down_for_the_black_forest(self):
        sk = dict(self.SK, roland={"identity": 10115, "kind": "kit", "becomes": "Roland", "unlock": "black-forest",
                                   "title": "Roland's pages"})
        with tempfile.TemporaryDirectory() as data:
            cli.write_locks(data, sk)
            path = os.path.join(data, "locks.json")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"black-forest": ["Roland's pages", "kali"]})   # by identity; key without a title
            before = os.stat(path).st_mtime_ns
            cli.write_locks(data, sk)
            self.assertEqual(os.stat(path).st_mtime_ns, before)        # unchanged: not rewritten

    def test_a_patchlc_only_skin_is_out_of_sight_elsewhere_and_stays_on_for_it(self):
        # the "only": "patchlc" mechanism (Gebura used it 2026-11-04 to 11-09)
        sk = dict(self.SK, kali={"identity": 10410, "kind": "repaint", "becomes": "Kali", "only": "patchlc"})
        with tempfile.TemporaryDirectory() as cfg, mock.patch.object(cli.paths, "home_dirs", return_value=(cfg, cfg)), \
                mock.patch.dict(os.environ, {"LIMBUS_SKINS_PATCHLC": ""}):
            st = {"on": ["ahab", "kali"]}
            visible, locked = cli.unlocked(sk, st)
            self.assertEqual((sorted(visible), locked, st["on"]), (["ahab"], {"kali"}, ["ahab"]))   # not here
            with self.assertRaises(SystemExit) as e:
                cli.refuse_locked(["kali"], locked, sk)
            self.assertIn("only for patchlc", str(e.exception))
            cli._save_state(st)                                    # e.g. `limbus-skins on x` outside patchlc
            with open(os.path.join(cfg, "state.json")) as f:
                self.assertEqual(json.load(f)["on"], ["ahab", "kali"])        # still on, for patchlc
            os.environ["LIMBUS_SKINS_PATCHLC"] = "1"
            st = {"on": ["ahab", "kali"]}
            visible, locked = cli.unlocked(sk, st)
            self.assertEqual((sorted(visible), locked, st["on"]), (["ahab", "kali"], set(), ["ahab", "kali"]))

    def test_every_skin_is_public(self):
        # friend, 2026-11-09: "make everything public skins and everything i want it out!"
        from limbus_skins.build import catalog
        skins = catalog()
        self.assertEqual({k for k, s in skins.items() if s.get("only")}, set())
        self.assertTrue({"kali", "kali-id", "roland"} <= set(skins))

    def test_nothing_waits_for_the_black_forest_any_more(self):
        # friend, 2026-11-04: "remove the black forest" - every boss, ally and kit is open from the start
        from limbus_skins.build import catalog
        skins = catalog()
        self.assertEqual({k for k, s in skins.items() if s.get("unlock") or s.get("ally_unlock")}, set())
        self.assertTrue({"kali", "kali-id", "vergilius-id", "roland", "dulcinea-id", "rien-id"} <= set(skins))


class LaunchArgs(unittest.TestCase):
    def test_flags_before_the_separator_only(self):
        self.assertEqual(cli.split_launch(["--yes", "--", "play.sh", "--yes"]), (True, ["play.sh", "--yes"]))
        self.assertEqual(cli.split_launch(["--", "steam", "-applaunch", "1973530"]), (False, ["steam", "-applaunch", "1973530"]))
        self.assertEqual(cli.split_launch(["--yes"]), (True, []))
        self.assertEqual(cli.split_launch([]), (False, []))


class Paths(unittest.TestCase):
    def test_library_folders(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "steamapps"))
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), "w") as f:
                f.write('"libraryfolders"\n{\n "0" { "path" "/a/Steam" }\n "1" { "path" "D:\\\\Games\\\\Steam" }\n}\n')
            self.assertEqual(paths.libraries(root), [root, "/a/Steam", "D:\\Games\\Steam"])

    def test_overrides_win(self):
        with tempfile.TemporaryDirectory() as g, tempfile.TemporaryDirectory() as c:
            with mock.patch.dict(os.environ, {"LIMBUS_GAME": g, "LIMBUS_CACHE": c}):
                self.assertEqual(paths.find(), (g, c))


class Repaint(unittest.TestCase):
    POS_UV = [{"stream": 0, "offset": 0, "format": 0, "dimension": 3}] + \
             [{"stream": 0, "offset": 0, "format": 0, "dimension": 0}] * 3 + \
             [{"stream": 1, "offset": 0, "format": 0, "dimension": 2}]

    def test_vertex_data_matches_the_games_layout(self):
        verts, idx, _ = repaint.quad(100, 200, 0.5, 0.0, 100.0)
        self.assertEqual(len(repaint.vertex_bytes(verts, self.POS_UV)), 4 * (12 + 8))
        self.assertEqual(len(idx), 12)

    def test_frame_names(self):
        self.assertEqual(repaint.kind_of("skill1_5_b")[0], "S1")
        self.assertEqual(repaint.kind_of("skill5_4_e")[0], "S5e")
        self.assertEqual(repaint.kind_of("gud_b")[0], "gud")
        self.assertIsNone(repaint.kind_of("pin_a_1")[0])

    def test_pack_uses_each_pages_size(self):
        placed = repaint.pack([(1000, 900), (1000, 900), (500, 500)], [(2048, 1024), (2048, 2048)])
        self.assertEqual([p[0] for p in placed], [0, 0, 1])
        self.assertIsNone(repaint.pack([(3000, 10)], [(2048, 2048)]))

    def test_head_grows_the_canvas_instead_of_clipping(self):
        from PIL import Image
        frame = Image.new("RGBA", (100, 100), (20, 20, 20, 255))
        art = Image.new("RGBA", (200, 80), (255, 255, 255, 255))
        out, (left, top) = repaint.paint_head(frame, [50, 10, 60], art, width=1.9)
        self.assertGreater(top, 0)
        self.assertEqual(out.height, 100 + top)
        self.assertEqual(out.getpixel((50 + left, 10 + top))[:3], (255, 255, 255))


class LayeredBodies(unittest.TestCase):
    def test_a_body_drawn_in_layers_is_recognised(self):
        # The Middle Nursefather Outis (11115) draws each move as parts (s1_f3_l2_p4, ..._b): a
        # repaint redrew only its whole frames and the parts stayed, his own art round Matthias
        from limbus_skins import repaint
        self.assertTrue(repaint.layered(["skill1_1", "s1_f1_l1_p1", "s1_f1_l1_b", "idle_f1_l1_p2"]))
        self.assertFalse(repaint.layered(["skill1_1", "idle", "dmg", "skill3_10_e", "1"]))


class Spine(unittest.TestCase):
    ATLAS = "a.png\nsize:100,50\nfilter:Linear,Linear\npma:true\nhead\nbounds:10,5,20,30\nrotate:90\nbody\nxy: 40, 0\nsize: 10, 10\norig: 12, 12\noffset: 1, 1\nrotate: false\n"
    DOC = {"skeleton": {"spine": "4.0.64"}, "bones": [{"name": "root"}, {"name": "b", "parent": "root", "y": 10}],
           "slots": [{"name": "head", "bone": "b", "attachment": "head"}],
           "skins": [{"name": "on", "attachments": {"head": {"head": {"width": 20, "height": 30}}}}],
           "ik": [{"name": "k", "bones": ["b"], "target": "root"}],
           "animations": {"idle": {"bones": {"b": {"rotate": [{"value": 1}]}},
                                   "deform": {"on": {"head": {"head": [{}]}}}}}}

    def test_atlas_round_trip_both_formats(self):
        pages = spine.parse_atlas(self.ATLAS)
        self.assertEqual(spine.page_size(pages[0]), (100, 50))
        self.assertEqual(spine.region_geom(pages[0]["regions"][0])[:5], (10, 5, 20, 30, 90))
        self.assertEqual(spine.region_geom(pages[0]["regions"][1]), (40, 0, 10, 10, 0, 1, 1, 12, 12))
        self.assertEqual(spine.parse_atlas(spine.format_atlas(pages)), pages)

    def test_frame_swap_keeps_names_the_game_looks_up(self):
        d = spine.frame_swap(self.DOC, [("lsk0", {"width": 5, "height": 5}), ("lsk1", {"width": 5, "height": 5})], 1.0)
        self.assertEqual([b["name"] for b in d["bones"]], ["root", "b", spine.BONE])
        self.assertEqual(d["ik"], self.DOC["ik"])
        self.assertIn("idle", d["animations"])
        self.assertNotIn("deform", d["animations"]["idle"])        # it names a part that is gone
        self.assertEqual(d["skins"][0]["name"], "default")           # new parts resolve under any skin
        self.assertEqual({s["name"] for s in d["skins"]}, {"default", "on"})
        self.assertNotIn("attachment", d["slots"][0])
        keys = d["animations"]["idle"]["slots"][spine.SLOT]["attachment"]
        self.assertEqual([k["name"] for k in keys], ["lsk0", "lsk1", "lsk0", "lsk1", "lsk0"])
        self.assertEqual(keys[-1]["time"], 2.0)                      # looped to one full breath
        self.assertIn(spine.BONE, d["animations"]["idle"]["bones"])
        self.assertEqual(self.DOC["slots"][0]["attachment"], "head")  # the original is untouched

    def test_region_attachment_placement(self):
        # sprite-renderer space -> skeleton units: shift by 1 unit, 1/0.5 units per skeleton unit
        a = spine.region_attachment((2, 0, 0, 2, 2, 0), -1, 0, 1, 4)
        self.assertEqual(a, {"x": 2.0, "y": 4.0, "width": 4.0, "height": 8.0})
        m = spine.region_attachment((-2, 0, 0, 2, 0, 0), -1, 0, 1, 4)   # mirrored body
        self.assertEqual(m["scaleX"], -1)
        self.assertNotIn("rotation", m)
        self.assertEqual(spine.mul(spine.inv((2, 0, 0, 3, 5, 7)), (2, 0, 0, 3, 5, 7)), (1, 0, 0, 1, 0, 0))

    def test_head_effects(self):
        doc = {"bones": [{"name": "root"}, {"name": "b", "parent": "root"}],
               "slots": [{"name": "coat", "bone": "b", "attachment": "coat"}, {"name": "head", "bone": "b", "attachment": "head"}],
               "skins": [{"name": "default", "attachments": {"head": {"head": {"x": 10, "y": 50, "width": 20, "height": 20},
                                                                      "blink": {"x": 12, "y": 50, "width": 20, "height": 20}}}}],
               "animations": {"animation": {"bones": {"b": {"translate": [{}, {"time": 2, "y": 3}]}},
                                            "slots": {"head": {"attachment": [{"time": 1, "name": "blink"}]}}}}}
        fx = {"glow": {"low": 0.3, "high": 0.7}, "glow_size": (30, 30), "motes": 2, "mote_size": 3, "flutter": 0.05}
        spine.head_effects(doc, "head", (20, 20), 0.0, fx)
        names = [s["name"] for s in doc["slots"]]
        self.assertEqual(names, ["coat", spine.GLOW, "head", spine.MOTE + "1", spine.MOTE + "2"])   # glow behind, motes in front
        bone = next(b for b in doc["bones"] if b["name"] == spine.HEAD_BONE)
        self.assertEqual((bone["parent"], bone["x"], bone["y"]), ("b", 10, 50))
        atts = doc["skins"][0]["attachments"]["head"]
        self.assertEqual((atts["head"]["x"], atts["head"]["y"]), (0, 0))   # same place, now on its own bone
        self.assertEqual((atts["blink"]["x"], atts["blink"]["y"]), (2, 0))
        a = doc["animations"]["animation"]
        self.assertEqual(a["slots"]["head"]["attachment"][0]["name"], "head")   # no blinking back to the old head
        self.assertEqual(a["bones"][spine.HEAD_BONE]["scale"][-1]["time"], 2.0)
        self.assertEqual(spine._color_at(a["slots"][spine.GLOW]["rgba"], 1.0), spine._hex(0.7))

    def test_motes_loop_seamlessly(self):
        for phase in (0.0, 1 / 3, 2 / 3):
            move, rgba = spine._mote_keys(2.0, phase, rise=10, drift=1)
            self.assertEqual(move[-1]["time"], 2.0)
            start = spine._lerp_keys(move, 0.0, ["x", "y"], [0, 0])
            end = spine._lerp_keys(move, 2.0, ["x", "y"], [0, 0])
            if phase:
                self.assertEqual([round(v, 2) for v in start], [round(v, 2) for v in end])
            for t in (0.0, 2.0):                       # whenever it jumps, it is invisible
                if not phase:
                    self.assertEqual(spine._color_at(rgba, t)[6:], "00")

    def test_render_setup_pose(self):
        from PIL import Image
        pages = spine.parse_atlas("a.png\nsize:20,20\nhead\nbounds:0,0,20,20\n")
        img, _ = spine.render({"bones": [{"name": "root"}], "slots": [{"name": "s", "bone": "root", "attachment": "head"}],
                               "skins": [{"name": "default", "attachments": {"s": {"head": {"width": 40, "height": 40}}}}]},
                              pages, {"a.png": Image.new("RGBA", (20, 20), (255, 0, 0, 255))}, ppu=1)
        self.assertEqual(img.getpixel((img.width // 2, img.height // 2)), (255, 0, 0, 255))


class Rigs(unittest.TestCase):
    """Borrowing a character's own idle rig: mirroring it, merging it, checking it."""

    @staticmethod
    def rig():
        """A small source rig: rotated and sheared bones, every inherit mode, a region, a
        plain mesh, a mesh weighted to two bones, a linked mesh in a second skin, one- and
        two-bone IK and a transform constraint."""
        return {
            "skeleton": {"spine": "4.0.64", "x": -10, "width": 30},
            "bones": [{"name": "root"},
                      {"name": "hip", "parent": "root", "x": 5, "y": 20, "rotation": 30, "shearX": 4},
                      {"name": "arm", "parent": "hip", "x": 12, "rotation": -50, "scaleX": 1.2, "length": 9},
                      {"name": "hand", "parent": "arm", "x": 8, "transform": "noRotationOrReflection"},
                      {"name": "aim", "parent": "root", "x": 30, "y": 12},
                      {"name": "leg", "parent": "hip", "x": -3, "y": -6, "rotation": -100, "length": 10},
                      {"name": "foot", "parent": "leg", "x": 10, "rotation": 40, "length": 8, "transform": "noScale"},
                      {"name": "heel", "parent": "foot", "x": 3, "transform": "onlyTranslation"},
                      {"name": "toe", "parent": "foot", "x": 6, "rotation": 15, "transform": "noScaleOrReflection"},
                      {"name": "step", "parent": "root", "x": 9, "y": -4}],
            "slots": [{"name": "body", "bone": "hip", "attachment": "body"},
                      {"name": "cloth", "bone": "hip", "attachment": "cloth"},
                      {"name": "sleeve", "bone": "arm", "attachment": "sleeve"},
                      {"name": "glove", "bone": "hand", "attachment": "glove"},
                      {"name": "boot", "bone": "heel", "attachment": "body"},
                      {"name": "nail", "bone": "toe", "attachment": "body"}],
            "ik": [{"name": "reach", "order": 0, "bones": ["arm"], "target": "aim"},
                   {"name": "walk", "order": 2, "bones": ["leg", "foot"], "target": "step", "bendPositive": False}],
            "transform": [{"name": "follow", "order": 1, "bones": ["hand"], "target": "hip",
                           "rotation": 20, "x": 3, "y": 4, "shearY": 5, "mixRotate": 0.5, "mixX": 0.7}],
            "skins": [
                {"name": "default", "attachments": {
                    "body": {"body": {"x": 3, "y": 1, "rotation": 15, "width": 10, "height": 20, "scaleY": 1.5}},
                    "boot": {"body": {"x": 1, "rotation": -30, "width": 4, "height": 3}},
                    "nail": {"body": {"y": 1, "width": 2, "height": 2}},
                    "cloth": {"cloth": {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1], "triangles": [0, 1, 2],
                                        "vertices": [0, 0, 6, -2, 4, 7], "hull": 3}},
                    "sleeve": {"sleeve": {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1], "triangles": [0, 1, 2], "hull": 3,
                                          "vertices": [2, 1, 1, 2, 0.5, 2, 2, 0.5, 0.5, 1, 2, 3, 4, 1, 1, 1, 5, 6, 1]}}}},
                {"name": "basic", "attachments": {
                    "glove": {"glove": {"type": "linkedmesh", "skin": "basic", "parent": "base", "path": "gloveart"},
                              "base": {"type": "mesh", "uvs": [0, 0, 1, 0, 1, 1], "triangles": [0, 1, 2],
                                       "vertices": [0, 0, 2, 0, 2, 2], "hull": 3}}}},
                {"name": "spare", "attachments": {"body": {"body": {"width": 1, "height": 1}}}}],
            "animations": {"idle": {
                "bones": {"hip": {"rotate": [{"value": 10, "curve": [0.3, 10, 0.6, 20]}, {"time": 1, "value": 20}],
                                  "translate": [{"x": 4, "y": 2, "curve": [0.3, 4, 0.6, 6, 0.3, 2, 0.6, 3]},
                                                {"time": 1, "x": 6, "y": 3}],
                                  "shear": [{"x": 2, "y": 3}]}},
                "slots": {"body": {"attachment": [{"name": "body"}]}},
                "ik": {"reach": [{"mix": 1}]},
                "deform": {"basic": {"glove": {"base": [{"offset": 1, "vertices": [5, 6, 7]}]}}},
                "drawOrder": [{"time": 0.5, "offsets": [{"slot": "body", "offset": 1}]}],
                "events": [{"name": "step"}]}},
            "events": {"step": {}}}

    def test_mirror_reflects_every_point(self):
        # the live rig against its baked, mirrored copy through the idle: every corner of
        # every part lands on its mirror image (IK, inherit modes and constraints included),
        # the picture on it unchanged
        mirrored = spine.mirror_x(spine.bake_ik(self.rig(), "idle"))
        self.assertEqual(mirrored["ik"], [])
        for anim, t in (("idle", 0.0), ("idle", 0.4), ("idle", 0.6), ("idle", 1.0)):
            before = spine.setup_triangles(self.rig(), anim=anim, t=t)
            after = spine.setup_triangles(mirrored, anim=anim, t=t)
            self.assertEqual(len(before), len(after))
            for (n0, pts0, uv0, _, _), (n1, pts1, uv1, _, _) in zip(before, after):
                self.assertEqual((n0, uv0), (n1, uv1))
                for (x0, y0), (x1, y1) in zip(pts0, pts1):
                    self.assertAlmostEqual(x1, -x0, delta=1e-3, msg=f"{n0} at {anim} {t}")
                    self.assertAlmostEqual(y1, y0, delta=1e-3, msg=f"{n0} at {anim} {t}")

    def test_ik_points_the_bone_at_its_target(self):
        doc = {"bones": [{"name": "root"}, {"name": "arm", "parent": "root", "length": 5},
                         {"name": "aim", "parent": "root", "x": 0, "y": 10}],
               "slots": [], "ik": [{"name": "k", "bones": ["arm"], "target": "aim"}]}
        a, b, c, d, x, y = spine.pose(doc)[0]["arm"]
        self.assertAlmostEqual(math.degrees(math.atan2(c, a)), 90, places=6)
        legs = {"bones": [{"name": "root"}, {"name": "thigh", "parent": "root", "length": 10},
                          {"name": "shin", "parent": "thigh", "x": 10, "length": 10},
                          {"name": "goal", "parent": "root", "x": 12, "y": 4}],
                "slots": [], "ik": [{"name": "k", "bones": ["thigh", "shin"], "target": "goal"}]}
        w = spine.pose(legs)[0]
        a, b, c, d, x, y = w["shin"]
        self.assertAlmostEqual(x + a * 10, 12, places=4)         # the shin's tip reaches the goal
        self.assertAlmostEqual(y + c * 10, 4, places=4)

    def test_mirror_keys_constraints_and_deforms(self):
        with self.assertRaises(spine.SpineError):
            spine.mirror_x(self.rig())                           # live IK would bend the wrong way
        d = spine.mirror_x(spine.bake_ik(self.rig(), "idle"))
        hip = d["animations"]["idle"]["bones"]["hip"]
        self.assertEqual(hip["rotate"][0], {"value": -10, "curve": [0.3, -10, 0.6, -20]})
        self.assertEqual(hip["translate"][0], {"x": -4, "y": 2, "curve": [0.3, -4, 0.6, -6, 0.3, 2, 0.6, 3]})
        self.assertEqual(hip["shear"][0], {"x": -2, "y": -3})
        self.assertEqual({k: d["transform"][0][k] for k in ("rotation", "x", "y", "shearY")},
                         {"rotation": -20, "x": -3, "y": 4, "shearY": -5})
        self.assertEqual(d["transform"][0]["order"], 0)         # orders close up once the IK is gone
        # offset 1: the list starts on a y, so only the 2nd value is an x
        self.assertEqual(d["animations"]["idle"]["deform"]["basic"]["glove"]["base"][0]["vertices"], [5, -6, 7])
        self.assertEqual(d["skins"][0]["attachments"]["body"]["body"]["scaleX"], -1.0)
        self.assertEqual(d["skeleton"]["x"], -20)

    def target(self):
        return {"skeleton": {"spine": "4.0.64"},
                "bones": [{"name": "root"}, {"name": "spine", "parent": "root"}],
                "slots": [{"name": "torso", "bone": "spine", "attachment": "torso"}],
                "ik": [{"name": "lean", "order": 0, "bones": ["spine"], "target": "root"}],
                "skins": [{"name": "default", "attachments": {"torso": {"torso": {"width": 5, "height": 5}}}}],
                "animations": {"animation": {"bones": {"spine": {"rotate": [{"value": 3}]}}}}}

    def regions(self):
        return {"body", "cloth", "sleeve", "base", "gloveart"}

    def test_transplant_keeps_the_target_names_and_brings_the_rig(self):
        d = spine.transplant(self.target(), self.rig(), 7, -2, "idle", skin="basic", names=["animation"])
        p = spine.PREFIX
        names = [b["name"] for b in d["bones"]]
        self.assertEqual(names[:2], ["root", "spine"])                # the game still finds its own bones
        self.assertEqual(names[2:6], [p + "root", p + "hip", p + "arm", p + "hand"])
        root = d["bones"][2]
        self.assertEqual((root["parent"], root["x"], root["y"]), ("root", 7, -2))
        self.assertEqual(d["slots"][0], {"name": "torso", "bone": "spine"})   # hidden, still there
        self.assertEqual(set(d["animations"]), {"animation"})
        self.assertNotIn("events", d["animations"]["animation"])
        self.assertEqual(set(d["animations"]["animation"]["bones"]), {p + "hip"})
        self.assertEqual([c["order"] for c in d["ik"] + d["transform"]], [0, 1, 3, 2])   # 0..n-1, source order kept
        default = d["skins"][0]["attachments"]
        self.assertNotIn("torso", default)
        # weighted vertices name bones by index: two target bones come first now
        self.assertEqual(default[p + "sleeve"]["sleeve"]["vertices"],
                         [2, 3, 1, 2, 0.5, 4, 2, 0.5, 0.5, 1, 4, 3, 4, 1, 1, 3, 5, 6, 1])
        # the starting skin is folded into the default one, its linked mesh with it
        glove = default[p + "glove"]["glove"]
        self.assertEqual(glove["type"], "linkedmesh")
        self.assertNotIn("skin", glove)
        self.assertIn("default", d["animations"]["animation"]["deform"])
        self.assertEqual([s["name"] for s in d["skins"]], ["default"])   # the spare look stays behind
        self.assertEqual(d["animations"]["animation"]["drawOrder"][0]["offsets"][0]["slot"], p + "body")
        spine.check(d, self.regions())

    def test_a_target_we_already_rewrote_is_refused(self):
        # a modded copy left in the game's cache must fail the build, not freeze the idle
        spine.refuse_modded(self.target())
        d = spine.transplant(self.target(), self.rig(), 0, 0, "idle", skin="basic", names=["animation"])
        with self.assertRaises(spine.ModdedCopy):
            spine.refuse_modded(d)
        self.assertFalse(issubclass(spine.ModdedCopy, spine.SpineError))   # never caught as "frames instead"

    def test_transplant_maps_matching_skins(self):
        t = self.target()
        t["skins"] += [{"name": "1", "attachments": {}}, {"name": "2", "attachments": {}}]
        r = self.rig()
        r["skins"][1]["name"], r["skins"][2]["name"] = "lv1", "lv2"
        r["skins"][1]["attachments"]["glove"]["glove"]["skin"] = "lv1"
        r["animations"]["idle"]["deform"] = {"lv1": r["animations"]["idle"]["deform"]["basic"]}
        d = spine.transplant(t, r, 0, 0, "idle", skin="lv1", target_skin="1")
        skins = {s["name"]: s for s in d["skins"]}
        glove = skins["1"]["attachments"][spine.PREFIX + "glove"]["glove"]
        self.assertEqual(glove["skin"], "1")                          # its parent mesh moved with it
        self.assertIn(spine.PREFIX + "body", skins["2"]["attachments"])
        self.assertNotIn(spine.PREFIX + "glove", skins["default"]["attachments"])
        self.assertEqual(set(d["animations"]["animation"]["deform"]), {"1"})
        spine.check(d, self.regions())

    def test_skins_stay_with_the_frames_look(self):
        # the identity starts "a_off", which would be the source's "off" - but the source
        # starts (and is drawn in its frames) "on": keep it "on" throughout
        t = self.target()
        t["skins"] += [{"name": "a_off", "attachments": {}}, {"name": "a_on", "attachments": {}}]
        r = self.rig()
        r["skins"][1]["name"], r["skins"][2]["name"] = "on", "off"
        r["skins"][1]["attachments"]["glove"]["glove"]["skin"] = "on"
        r["animations"]["idle"]["deform"] = {"on": r["animations"]["idle"]["deform"]["basic"]}
        d = spine.transplant(t, r, 0, 0, "idle", skin="on", target_skin="a_off")
        skins = {s["name"]: s for s in d["skins"]}
        self.assertIn(spine.PREFIX + "glove", skins["default"]["attachments"])   # "on" folded in
        self.assertEqual(skins["a_off"]["attachments"], {})
        spine.check(d, self.regions())

    def test_check_refuses_what_the_game_would(self):
        good = spine.transplant(self.target(), self.rig(), 0, 0, "idle", skin="basic")
        spine.check(good, self.regions())
        with self.assertRaisesRegex(spine.SpineError, "not in the atlas"):
            spine.check(good, self.regions() - {"gloveart"})
        for breakage in (lambda d: d["bones"].reverse(),
                         lambda d: d["ik"][0].update(order=7),
                         lambda d: d["animations"]["animation"].update(deform={"default": {"lsk_body": {"nope": [{}]}}}),
                         lambda d: d["skins"][0]["attachments"]["lsk_sleeve"]["sleeve"]["vertices"].__setitem__(1, 40)):
            d = json.loads(json.dumps(good))
            breakage(d)
            with self.assertRaises(spine.SpineError):
                spine.check(d, self.regions())

    def test_pace_and_skin_names(self):
        a = spine.scale_time({"bones": {"b": {"rotate": [{"value": 1, "curve": [0.5, 1, 1.0, 2]}, {"time": 2}]}}}, 0.5)
        self.assertEqual(a["bones"]["b"]["rotate"], [{"value": 1, "curve": [0.25, 1, 0.5, 2]}, {"time": 1.0}])
        self.assertEqual(spine.skin_match("a_on", ["idle2", "off", "on"]), "on")
        self.assertEqual(spine.skin_match("2", ["lv1", "lv2", "lv3"]), "lv2")
        self.assertIsNone(spine.skin_match("blood", ["OFF", "ON"]))


class Art(unittest.TestCase):
    def test_cut_out_drops_background_and_corner_bits(self):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (60, 60), (0, 0, 0, 255))
        d = ImageDraw.Draw(im)
        d.ellipse([15, 15, 45, 45], fill=(255, 255, 255, 255))
        d.rectangle([0, 0, 4, 4], fill=(200, 200, 255, 255))         # a rose in the corner
        out = art.cut_out(im)
        self.assertLessEqual(abs(out.width - 31), 2)
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2)), (255, 255, 255, 255))

    def test_invert_swaps_light_keeps_hue(self):
        from PIL import Image
        im = Image.new("RGBA", (3, 1))
        im.putdata([(255, 255, 255, 255), (0, 0, 0, 255), (200, 160, 240, 255)])
        a, b, c = list(art.invert(im).getdata())
        self.assertEqual(a, (0, 0, 0, 255))
        self.assertEqual(b, (255, 255, 255, 255))
        self.assertGreater(c[2], c[1])                                  # still purple, now dark
        self.assertLess(sum(c[:3]), sum((200, 160, 240)))


class Frames(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(repaint.kind_of("Default")[0], "idle")
        self.assertEqual(repaint.kind_of("skill2_3_e")[0], "S2e")
        self.assertEqual(repaint.kind_of("move")[0], "move")
        self.assertIsNone(repaint.kind_of("3")[0])

    def test_split_body_keeps_idle_and_move(self):
        class S:
            def __init__(self, n): self.n = n
            def read(self): return type("R", (), {"m_Name": self.n})()
        body, fx = repaint.split_body([S("idle"), S("move"), S("skill1_2_e"), S("2")])
        self.assertEqual([x.n for x in body], ["idle", "move"])
        self.assertEqual([x.n for x in fx], ["skill1_2_e"])


class IdleSkeletons(unittest.TestCase):
    """spine.body_spines: the skeletons a body stands idle as."""

    class Obj:
        def __init__(self, pid, kind, tree, name=""):
            self.path_id, self.type, self.tree, self.name = pid, type("T", (), {"name": kind})(), tree, name

        def read_typetree(self):
            return self.tree

        def read(self):
            return type("R", (), {"m_Name": self.name})()

    def body(self, marked):
        """A body as the game ships one: a sprite renderer for its moves, and under a pivot a
        skeleton it stands idle as. `marked`: whether its Idle motion says isSpineAnim - newer
        identities do, the sinners' first ones don't."""
        ref = lambda i: {"m_FileID": 0, "m_PathID": i}  # noqa: E731
        still = {"m_LocalPosition": {"x": 0, "y": 0, "z": 0}, "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                 "m_LocalScale": {"x": 1, "y": 1, "z": 1}}
        O = self.Obj
        objs = [
            O(1, "GameObject", {"m_Component": [{"component": ref(2)}, {"component": ref(3)}]}),
            O(2, "Transform", dict(still, m_Children=[ref(6), ref(8)])),
            O(3, "MonoBehaviour", {"motionList": [{"_motiondetail": 18, "isSpineAnim": int(marked)}, {"_motiondetail": 7, "isSpineAnim": 0}],
                                   "_spineSkins": [ref(9), {"m_FileID": 0, "m_PathID": 0}], "sprenderer_charactermotion": ref(4)}),
            O(4, "SpriteRenderer", {"m_GameObject": ref(5), "m_FlipX": 0, "m_FlipY": 0}),
            O(5, "GameObject", {"m_Component": [{"component": ref(6)}, {"component": ref(4)}]}),
            O(6, "Transform", dict(still, m_Children=[])),
            O(7, "GameObject", {"m_Component": [{"component": ref(8)}, {"component": ref(9)}, {"component": ref(10)}]}),
            O(8, "Transform", dict(still, m_Children=[])),
            O(9, "MonoBehaviour", {"m_GameObject": ref(7), "_spine_Animation": ref(10)}),
            O(10, "MonoBehaviour", {"m_GameObject": ref(7), "skeletonDataAsset": ref(11), "initialSkinName": "", "_animationName": "idle",
                                    "timeScale": 1.0}),
            O(11, "MonoBehaviour", {"m_Name": "skeleton_SkeletonData", "atlasAssets": [ref(12)], "scale": 0.5, "skeletonJSON": ref(15)}),
            O(12, "MonoBehaviour", {"materials": [ref(13)], "atlasFile": ref(16)}),
            O(13, "Material", {"m_SavedProperties": {"m_TexEnvs": [("_MainTex", {"m_Texture": ref(14)})],
                                                     "m_Floats": [("_StraightAlphaInput", 0.0)]}}),
            O(14, "Texture2D", {}, "09_rodion"),
            O(15, "TextAsset", {}, "skeleton"),
            O(16, "TextAsset", {}, "skeleton.atlas"),
        ]
        return type("Env", (), {"objects": objs})()

    def test_a_body_stands_idle_as_its_skeleton_whether_or_not_a_motion_is_marked(self):
        from limbus_skins import spine
        for marked in (True, False):                    # the game asks only whether there is a skeleton
            rigs = spine.body_spines(self.body(marked), 1)
            self.assertEqual([(r["name"], r["animation"], sorted(r["textures"]), r["pma"]) for r in rigs],
                             [("skeleton_SkeletonData", "idle", ["09_rodion"], True)], marked)

    def test_a_sources_rig_still_goes_by_the_mark(self):
        from limbus_skins import spine
        self.assertEqual(len(spine.body_spines(self.body(True), 1, flagged=True)), 1)
        self.assertEqual(spine.body_spines(self.body(False), 1, flagged=True), [])     # what older skins borrowed stays as it was

    def test_a_body_with_no_skeleton_has_none(self):
        from limbus_skins import spine
        env = self.body(False)
        env.objects[2].tree["_spineSkins"] = []
        self.assertEqual(spine.body_spines(env, 1), [])


class NoNetwork(unittest.TestCase):
    def test_commands_that_need_no_game_touch_no_network(self):
        real = socket.socket

        def refuse(*a, **k):
            raise AssertionError("network use")
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(
                os.environ, {"XDG_CONFIG_HOME": home, "XDG_DATA_HOME": home, "APPDATA": home, "LOCALAPPDATA": home}):
            socket.socket = refuse
            try:
                with mock.patch("sys.stdout"):
                    self.assertEqual(cli.main(["list"]), 0)
                    self.assertEqual(cli.main(["on", "ahab"]), 0)
                    self.assertEqual(cli.main(["off", "all"]), 0)
                    self.assertEqual(cli.main(["help"]), 0)
                    self.assertEqual(cli.main(["--help"]), 0)
                    self.assertEqual(cli.main(["--version"]), 0)
            finally:
                socket.socket = real
            with open(os.path.join(home, "limbus-skins", "state.json")) as f:
                st = json.load(f)
            self.assertEqual(st["on"], [])


class BossSkills(unittest.TestCase):
    def setUp(self):
        from limbus_skins import helpers
        self.h = helpers
        mk = lambda i, motion, extra: {"id": i, "skillTier": 1, "skillData": [dict(  # noqa: E731
            {"gaksungLevel": 1, "skillMotion": motion, "viewType": "BATTLE", "mpUsage": 0,
             "attributeType": "VIOLET", "defaultValue": 2, "coinList": [{"scale": 1}]}, **extra),
            {"gaksungLevel": 3, "defaultValue": 3}]}
        self.skills = {1: mk(1, "S1", {}), 2: mk(2, "S2", {}), 3: mk(3, "S3", {})}
        for i, tier in ((91, 3), (92, 1), (93, 2), (94, 1)):
            self.skills[i] = {"id": i, "skillTier": tier, "skillData": [
                {"skillMotion": "S9", "attributeType": "INDIGO", "defType": "ATTACK", "defaultValue": i,
                 "coinList": [{"scale": 4}] * 3, "abilityScriptList": [{"scriptName": "EmptyBody"}]}]}
        self.skills[95] = {"id": 95, "skillTier": 1, "skillData": [{"defType": "GUARD", "skillMotion": "S4"}]}
        self.ident = {"id": 10808, "attributeList": [{"skillId": 1, "number": 3}, {"skillId": 2, "number": 2},
                                                      {"skillId": 3, "number": 1}]}
        self.unit = {"id": 81912, "attributeList": [{"skillId": i, "number": 0} for i in (95, 91, 92, 93, 94)]}

    def test_recolor_one_skill_at_every_uptie_that_names_a_sin(self):
        self.skills[3]["skillData"][1]["attributeType"] = "VIOLET"
        out = self.h.recolor_skills(self.ident, self.skills, {"3": "SHAMROCK"})
        self.assertEqual(sorted(out), [3])
        self.assertEqual([d.get("attributeType") for d in out[3]["skillData"]], ["SHAMROCK", "SHAMROCK"])
        self.assertEqual(out[3]["skillData"][0]["coinList"], [{"scale": 1}])
        self.assertEqual(self.skills[3]["skillData"][1]["attributeType"], "VIOLET")

    def test_recolor_every_skill_and_the_guard(self):
        self.ident["defenseSkillIDList"] = [95]
        out = self.h.recolor_skills(self.ident, self.skills, {"all": "SHAMROCK"})
        self.assertEqual(sorted(out), [1, 2, 3, 95])
        self.assertTrue(all(r["skillData"][0]["attributeType"] == "SHAMROCK" for r in out.values()))
        attrs, guard = self.h.boss_moveset(self.ident, self.unit, self.skills, {"S4", "S9"})   # green on a boss form
        out = self.h.recolor_skills(dict(self.ident, attributeList=attrs, defenseSkillIDList=guard),
                                    self.skills, {"all": "SHAMROCK"})
        self.assertEqual(sorted(out), [91, 92, 93, 95])                       # its own moves (a tier each), recoloured
        self.assertEqual((out[91]["skillData"][0]["attributeType"], out[91]["skillData"][0]["skillMotion"]),
                         ("SHAMROCK", "S9"))

    def test_green_ids_are_the_skills_recoloured_shamrock(self):
        self.ident["defenseSkillIDList"] = [95]
        green = self.h.recolor_skills(self.ident, self.skills, {"all": "SHAMROCK"})
        blue = self.h.recolor_skills(self.ident, self.skills, {"1": "AZURE"})
        self.assertEqual(self.h.green_ids(green), [1, 2, 3, 95])
        self.assertEqual(self.h.green_ids(blue), [])

    def test_every_skill_of_an_identity_for_green_coins_whatever_its_sin(self):
        self.ident["defenseSkillIDList"] = [95]
        self.assertEqual(self.h.all_skill_ids(self.ident), [1, 2, 3, 95])
        self.assertEqual(self.h.all_skill_ids({"attributeList": "[{'skillId': 7}]"}), [7])

    def test_skill_edits_stack_on_one_record(self):
        sins = self.h.recolor_skills(self.ident, self.skills, {"1": "SHAMROCK"})
        again = self.h.recolor_skills(self.ident, self.skills, {"2": "AZURE"}, base=sins)
        self.assertEqual(sorted(again), [2])
        self.assertEqual(sins[1]["skillData"][0]["attributeType"], "SHAMROCK")

    def test_moves_rewrite_a_skill_from_plain_data(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 3      # a three-coin skill
        moves = {"2": {"attributeType": "CRIMSON", "atkType": "PENETRATE", "defaultValue": 5, "coins": [3, 3, 3],
                       "onHit": {"buffKeyword": "Laceration", "stack": 2, "turn": 1},
                       "onUse": {"buffKeyword": "Agility", "stack": 2, "target": "Self"}}}
        out = self.h.move_skills(self.ident, self.skills, moves)
        self.assertEqual(sorted(out), [2])
        d = out[2]["skillData"]
        self.assertEqual(len(d), 1)
        d = d[0]
        self.assertEqual((d["attributeType"], d["atkType"], d["defaultValue"], d["skillMotion"]), ("CRIMSON", "PENETRATE", 5, "S2"))
        self.assertEqual([c["scale"] for c in d["coinList"]], [3, 3, 3])
        hit = d["coinList"][0]["abilityScriptList"][0]
        self.assertEqual((hit["scriptName"], hit["buffData"]["buffKeyword"], hit["buffData"]["target"],
                          hit["buffData"]["stack"], hit["buffData"]["turn"]), ("GiveBuffOnSucceedAttack", "Laceration", "Target", 2, 1))
        use = d["abilityScriptList"][0]
        self.assertEqual((use["scriptName"], use["buffData"]["buffKeyword"], use["buffData"]["target"],
                          use["buffData"]["activeRound"]), ("GiveBuffOnUse", "Agility", "Self", 1))
        self.assertEqual(self.skills[2]["skillData"][0]["attributeType"], "VIOLET")            # inputs untouched

    def test_moves_can_rewrite_the_guard(self):
        self.ident["defenseSkillIDList"] = [95]
        out = self.h.move_skills(self.ident, self.skills, {"defense": {"defaultValue": 8, "coins": [4]}})
        d = out[95]["skillData"][0]
        self.assertEqual((d["defType"], d["defaultValue"], [c["scale"] for c in d["coinList"]]), ("GUARD", 8, [4]))

    def test_moves_keep_the_identitys_coin_count(self):
        # each coin of a skill is a hit in the identity's own animation: a different count stalls it
        with self.assertRaises(ValueError) as e:
            self.h.move_skills(self.ident, self.skills, {"3": {"coins": [5, 5]}})     # skill 3 has one coin
        self.assertIn("coin", str(e.exception))
        self.assertEqual(len(self.h.move_skills(self.ident, self.skills, {"3": {"coins": [9]}})[3]["skillData"][0]["coinList"]), 1)

    def test_many_effects_on_use_and_on_hit(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 2
        moves = {"2": {"coins": [7, 7], "onUse": [{"buffKeyword": "Agility", "stack": 2, "activeRound": 1},
                                                  {"buffKeyword": "Breath", "stack": 0, "turn": 2, "activeRound": 0}],
                       "onHit": [{"buffKeyword": "Burst", "stack": 2}, {"buffKeyword": "Vibration", "stack": 1, "coins": [2]}]}}
        d = self.h.move_skills(self.ident, self.skills, moves)[2]["skillData"][0]
        self.assertEqual([tuple(a["buffData"][k] for k in ("buffKeyword", "stack", "turn", "activeRound"))
                          for a in d["abilityScriptList"]], [("Agility", 2, 0, 1), ("Breath", 0, 2, 0)])
        self.assertEqual([[a["buffData"]["buffKeyword"] for a in c["abilityScriptList"]] for c in d["coinList"]],
                         [["Burst"], ["Burst", "Vibration"]])

    def test_text_for_a_move_in_the_games_own_words(self):
        m = {"coins": [7, 7], "onUse": [{"buffKeyword": "Agility", "stack": 2, "activeRound": 1},
                                        {"buffKeyword": "Breath", "stack": 0, "turn": 2, "activeRound": 0},
                                        {"buffKeyword": "Breath", "stack": 3, "turn": 1, "target": "EveryAlly", "activeRound": 0}],
             "onHit": [{"buffKeyword": "Burst", "stack": 2}, {"buffKeyword": "Burst", "stack": 0, "turn": 1, "coins": [2]},
                       {"buffKeyword": "Laceration", "stack": 3, "activeRound": 1, "coins": [2]}]}
        desc, coins = self.h.describe_move(m)
        self.assertEqual(desc.splitlines(), ["[WhenUse] Gain 2 [Agility] next turn", "[WhenUse] Gain +2 [Breath] Count",
                                             "[WhenUse] Apply 3 [Breath] and +1 [Breath] Count to all allies"])
        self.assertEqual(coins, ["[OnSucceedAttack] Inflict 2 [Burst]",
                                 "[OnSucceedAttack] Inflict 2 [Burst]\n[OnSucceedAttack] Inflict +1 [Burst] Count\n"
                                 "[OnSucceedAttack] Inflict 3 [Laceration] next turn"])
        self.assertEqual(self.h.describe_move({"coins": [1], "onUse": {"buffKeyword": "Protection", "stack": 2}})[0],
                         "[WhenUse] Gain 2 [Protection] next turn")      # on use, "next turn" unless said otherwise

    def test_moved_skills_get_their_text_unless_the_skin_gives_it(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 2
        moves = {"2": {"coins": [7, 7], "onHit": {"buffKeyword": "Laceration", "stack": 3, "activeRound": 1}}}
        out = self.h.skill_texts(self.ident, self.skills, {"2": {"name": "Level Slash"}}, moves)
        self.assertEqual(out[2], {"name": "Level Slash", "desc": "", "flavor": "",
                                  "coins": ["[OnSucceedAttack] Inflict 3 [Laceration] next turn"] * 2})
        out = self.h.skill_texts(self.ident, self.skills, {}, moves)            # moves alone: their text, the old name
        self.assertNotIn("name", out[2])
        self.assertEqual(len(out[2]["coins"]), 2)
        out = self.h.skill_texts(self.ident, self.skills, {"2": {"coins": ["mine"]}}, moves)
        self.assertEqual(out[2]["coins"], ["mine", ""])

    def test_a_note_goes_before_the_moves_own_lines(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 2
        moves = {"2": {"coins": [7, 7], "onUse": {"buffKeyword": "Agility", "stack": 2}}}
        out = self.h.skill_texts(self.ident, self.skills, {"2": {"name": "The Greenest Hunt", "note": "[WhenUse] Become green"}}, moves)
        self.assertEqual(out[2]["desc"], "[WhenUse] Become green\n[WhenUse] Gain 2 [Agility] next turn")

    def test_huge_base_power_is_fine_but_not_absurd(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 2
        out = self.h.move_skills(self.ident, self.skills, {"2": {"defaultValue": 250, "coins": [30, 30]}})
        self.assertEqual(out[2]["skillData"][0]["defaultValue"], 250)
        with self.assertRaises(ValueError):
            self.h.move_skills(self.ident, self.skills, {"2": {"defaultValue": 5000, "coins": [30, 30]}})

    def test_stats_set_hp_and_speed_at_every_uptie_in_the_records_own_encoding(self):
        rec = {"id": 10701, "hp": {"defaultStat": 78, "incrementByLevel": 2.69},
               "minSpeedList": "[1, 1, 2, 2]", "maxSpeedList": [4, 5, 5, 5]}
        self.h.edit_stats(rec, {"hp": 130, "speed": [5, 9]})
        self.assertEqual(rec["hp"], {"defaultStat": 130, "incrementByLevel": 2.69})
        self.assertEqual(rec["minSpeedList"], "[5, 5, 5, 5]")
        self.assertEqual(rec["maxSpeedList"], [9, 9, 9, 9])
        with self.assertRaises(ValueError):
            self.h.edit_stats(rec, {"speed": [9, 5]})

    def test_stats_set_resistances_and_defense_keeping_the_rest(self):
        rec = {"id": 10701, "defCorrection": 0, "resistInfo": {"atkResistList": [
            {"type": "SLASH", "value": 2}, {"type": "PENETRATE", "value": 1}, {"type": "HIT", "value": 0.5}],
            "other": 1}}
        self.h.edit_stats(rec, {"resist": {"PENETRATE": 2.0, "HIT": 2.0}, "def": -5})
        self.assertEqual(rec["resistInfo"], {"atkResistList": [
            {"type": "SLASH", "value": 2}, {"type": "PENETRATE", "value": 2.0}, {"type": "HIT", "value": 2.0}],
            "other": 1})
        self.assertEqual(rec["defCorrection"], -5)
        enc = {"resistInfo": '{"atkResistList": [{"type": "SLASH", "value": 1}]}'}
        self.h.edit_stats(enc, {"resist": {"HIT": 1.5}})
        self.assertIsInstance(enc["resistInfo"], str)          # kept in the record's own encoding
        for bad in ({"resist": {"FIRE": 2}}, {"resist": {"HIT": 3}}, {"resist": {}}, {"def": 50}, {"def": 1.5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.h.edit_stats(dict(rec), bad)

    def test_moved_coins_keep_the_hosts_animation_cues(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1, "actionIndex": 1, "color": "x", "grade": 2},
                                                      {"scale": 1, "actionIndex": 2}, {"scale": 1}]
        self.skills[2]["skillData"][1]["coinList"] = [{"scale": 2, "actionIndex": 1}, {"scale": 2, "actionIndex": 2},
                                                      {"scale": 2, "actionIndex": 3, "cutsceneStart": True}]
        out = self.h.move_skills(self.ident, self.skills, {"2": {"coins": [5, 5, 4]}})
        coins = out[2]["skillData"][0]["coinList"]
        self.assertEqual([c.get("actionIndex") for c in coins], [1, 2, 3])     # the last uptie's, where it has more
        self.assertTrue(coins[2]["cutsceneStart"])
        self.assertNotIn("color", coins[0])                                    # not the host's coin mechanics
        self.assertNotIn("grade", coins[0])
        self.assertEqual([c["scale"] for c in coins], [5, 5, 4])

    def test_an_effect_can_go_on_chosen_coins_only(self):
        self.skills[2]["skillData"][0]["coinList"] = [{"scale": 1}] * 3
        out = self.h.move_skills(self.ident, self.skills, {"2": {"coins": [5, 5, 4],
                                 "onHit": {"buffKeyword": "Laceration", "stack": 5, "turn": 2, "coins": [1]}}})
        hits = [len(c["abilityScriptList"]) for c in out[2]["skillData"][0]["coinList"]]
        self.assertEqual(hits, [1, 0, 0])                     # one die, one Bleed - not three
        self.assertNotIn("coins", out[2]["skillData"][0]["coinList"][0]["abilityScriptList"][0]["buffData"])

    def test_texts_are_keyed_by_skill_id_with_every_old_line_blanked(self):
        self.ident["defenseSkillIDList"] = [95]
        self.skills[95]["skillData"][0]["coinList"] = [{"scale": 1}] * 2
        texts = {"1": {"name": "Level Slash", "coins": ["[OnSucceedAttack] Inflict 3 [Laceration]"]},
                 "defense": {"name": "Counter", "flavor": "Slash 4-8"}}
        out = self.h.skill_texts(self.ident, self.skills, texts)
        self.assertEqual(out[1], {"name": "Level Slash", "desc": "", "flavor": "",
                                  "coins": ["[OnSucceedAttack] Inflict 3 [Laceration]"]})
        self.assertEqual(out[95], {"name": "Counter", "desc": "", "flavor": "Slash 4-8", "coins": ["", ""]})

    def test_passives_replace_every_uptie_level(self):
        rec = {"personalityID": 10410,
               "battlePassiveList": [{"level": 1, "passiveIDList": [1041002]}, {"level": 2, "passiveIDList": [1041002, 1041001]}],
               "supporterPassiveList": [{"level": 1, "passiveIDList": [1041021]}]}
        out = self.h.swap_passives(rec, [1090101, 1090601], [1090621])
        self.assertEqual(out["battlePassiveList"], [{"level": 1, "passiveIDList": [1090101, 1090601]},
                                                    {"level": 2, "passiveIDList": [1090101, 1090601]}])
        self.assertEqual(out["supporterPassiveList"], [{"level": 1, "passiveIDList": [1090621]}])
        self.assertEqual(rec["battlePassiveList"][0]["passiveIDList"], [1041002])      # the input stays as it was

    def test_moves_refuse_odd_values(self):
        for bad in ({"atkType": "KICK"}, {"attributeType": "PLAID"}, {"coins": []}, {"coins": [99] * 9}):
            with self.assertRaises(ValueError):
                self.h.move_skills(self.ident, self.skills, {"1": dict({"coins": [4]}, **bad)})

    def test_helper_kit_drops_story_skills_and_keeps_a_guard(self):
        story = lambda i, name, motion="S5": {"id": i, "skillTier": 3, "skillData": [  # noqa: E731
            {"skillMotion": motion, "coinList": [{"scale": 1, "abilityScriptList": [{"scriptName": name}]}]}]}
        self.skills[96] = story(96, "DoNotEndGameUntilReturnFromLivingSpecimen")
        self.skills[97] = story(97, "CreateMangWithSkillPowerAdderOnUse_5_1")
        self.skills[98] = {"id": 98, "skillTier": 1, "skillData": [{"defType": "EVADE", "skillMotion": "Default"}]}
        self.ident["defenseSkillIDList"] = [95, 98]           # 95 moves as S4, which the helper's body lacks
        helper = {"id": 400013, "attributeList": [{"skillId": i, "number": 1} for i in (91, 96, 97)]}
        attrs, guard, dropped = self.h.helper_kit(self.ident, helper, self.skills)
        self.assertEqual([a["skillId"] for a in attrs], [91])
        self.assertEqual(guard, [98])
        self.assertEqual([d[0] for d in dropped], [96, 97])
        attrs, _, _ = self.h.helper_kit(self.ident, helper, self.skills, script=True)   # its battle script comes along
        self.assertEqual([a["skillId"] for a in attrs], [91, 97])
        helper["defenseSkillIDList"] = [95]
        self.assertEqual(self.h.helper_kit(self.ident, helper, self.skills)[1], [95])  # its own guard wins
        with self.assertRaises(ValueError):
            self.h.helper_kit(self.ident, {"id": 1, "attributeList": [{"skillId": 96}]}, self.skills)
        unit = {"id": 2, "attributeList": [{"skillId": 96}, {"skillId": 92}]}      # boss forms skip them too
        self.assertEqual(self.h.boss_moveset(self.ident, unit, self.skills, {"Default", "S4", "S5", "S9"})[0],
                         [{"skillId": 92, "number": 3}])

    def test_helper_kit_leaves_out_what_the_helpers_body_cant_play(self):
        # Pwie: the Little Buffe has S1-S4; its story script plays S5-S7 on another body
        self.skills[93] = {"id": 93, "skillTier": 1, "skillData": [{"skillMotion": "S7"}]}
        self.skills[98] = {"id": 98, "skillTier": 1, "skillData": [{"defType": "EVADE", "skillMotion": "Default"}]}
        self.ident["defenseSkillIDList"] = [95, 98]
        helper = {"id": 400041, "attributeList": [{"skillId": 91, "number": 1}, {"skillId": 93, "number": 0}]}
        motions = {"Default", "Guard", "S1", "S2", "S3", "S4", "S9"}
        attrs, guard, dropped = self.h.helper_kit(self.ident, helper, self.skills, motions=motions)
        self.assertEqual([a["skillId"] for a in attrs], [91])
        self.assertEqual(dropped, [(93, "its body has no S7 motion")])
        self.assertEqual(guard, [95, 98])                   # the identity's own, both playable (S4, Default)
        with self.assertRaises(ValueError):                 # nothing to defend with that it can play
            self.h.helper_kit(dict(self.ident, defenseSkillIDList=[]), helper, self.skills, motions=motions)

    def _moves(self):
        """The boss's attacks, each with a move of its own (setUp gives them all S9)."""
        for i, m in ((91, "S7"), (92, "S8"), (93, "S9"), (94, "S9")):
            self.skills[i]["skillData"][0]["skillMotion"] = m

    def test_a_boss_with_one_move_still_deals_every_tier(self):
        # Rien: every attack he has plays S1 - two coins, three, four; one per move would leave one
        unit = {"id": 1347, "attributeList": [{"skillId": i} for i in (95, 92, 94, 93, 91)]}
        attrs, _ = self.h.boss_moveset(self.ident, unit, self.skills, {"S4", "S9"})
        self.assertEqual(attrs, [{"skillId": 92, "number": 3}, {"skillId": 93, "number": 2},
                                 {"skillId": 91, "number": 1}])               # 94: tier 1 is dealt already

    def test_dice_coins(self):
        from limbus_skins.lortext import dice_coins as dc
        self.assertEqual(dc([(5, 9, "Atk", "Slash"), (5, 9, "Atk", "Slash")]), (5, [2, 2], "SLASH"))       # Durandal
        self.assertEqual(dc([(20, 39, "Atk", "Slash")]), (20, [19], "SLASH"))                             # Furioso
        self.assertEqual(dc([(3, 7, "Atk", "Penetrate")] * 3), (3, [1, 1, 2], "PENETRATE"))               # Ranga
        self.assertEqual(dc([(4, 8, "Atk", "Penetrate"), (5, 8, "Atk", "Penetrate"), (7, 12, "Atk", "Hit")]),
                         (4, [2, 3, 3], "PENETRATE"))                                                     # Atelier Logic
        self.assertEqual(dc([(20, 39, "Atk", "Slash")], 9), (20, [2] * 8 + [3], "SLASH"))  # nine coins, as written
        self.assertEqual(dc([(5, 9, "Def", "Guard")]), (5, [4], None))                     # a block keeps its type
        with self.assertRaises(ValueError):
            dc([])

    def test_a_boss_form_takes_the_bosss_whole_moveset(self):
        # the boss's own body plays its own moves: every attack it has, one per move, dealt as an
        # identity's deck is (tier 1 three times, tier 2 twice, tier 3 once), and its own guard
        self._moves()
        self.ident["defenseSkillIDList"] = [4]
        self.skills[4] = {"id": 4, "skillTier": 1, "skillData": [{"defType": "GUARD", "skillMotion": "Default"}]}
        attrs, guard = self.h.boss_moveset(self.ident, self.unit, self.skills, {"Default", "S4", "S7", "S8", "S9"})
        self.assertEqual(attrs, [{"skillId": 91, "number": 1}, {"skillId": 92, "number": 3},
                                 {"skillId": 93, "number": 2}])               # 94 is 93's move again
        self.assertEqual(guard, [95])                                          # under the boss's own ids
        self.assertEqual(self.skills[92]["skillData"][0]["defaultValue"], 92)  # its records as they are

    def test_a_boss_form_leaves_out_moves_its_body_cant_play(self):
        self._moves()
        attrs, _ = self.h.boss_moveset(self.ident, self.unit, self.skills, {"S4", "S7", "S9"})
        self.assertEqual([a["skillId"] for a in attrs], [91, 93, 94])        # 92 (S8) can't: 94 deals tier 1

    def test_a_boss_form_falls_back_to_a_counter_then_the_identitys_own_guard(self):
        self.ident["defenseSkillIDList"] = [4]
        self.skills[4] = {"id": 4, "skillTier": 1, "skillData": [{"defType": "GUARD", "skillMotion": "Default"}]}
        self.skills[96] = {"id": 96, "skillTier": 1, "skillData": [{"defType": "COUNTER", "skillMotion": "S5"}]}
        unit = {"id": 1327, "attributeList": [{"skillId": i} for i in (91, 92, 93, 96)]}
        self.assertEqual(self.h.boss_moveset(self.ident, unit, self.skills, {"Default", "S5", "S9"})[1], [96])  # Matthias
        self.assertEqual(self.h.boss_moveset(self.ident, unit, self.skills, {"Default", "S9"})[1], [4])   # own guard

    def test_a_boss_form_refuses_what_its_body_cant_play(self):
        with self.assertRaises(ValueError):                                    # no attack it has a move for
            self.h.boss_moveset(self.ident, self.unit, self.skills, {"Default"})
        self.ident["defenseSkillIDList"] = [4]
        self.skills[4] = {"id": 4, "skillTier": 1, "skillData": [{"defType": "GUARD", "skillMotion": "S7"}]}
        with self.assertRaises(ValueError):                                    # no guard it can play either
            self.h.boss_moveset(self.ident, {"id": 1, "attributeList": [{"skillId": 91}]}, self.skills, {"S9"})
        with self.assertRaises(ValueError):                                    # the identity's battle script
            self.h.boss_moveset(dict(self.ident, unitScriptID=11115), self.unit, self.skills, {"S4", "S9"})
        self.ident["attributeList"].append({"skillId": 4, "number": 0})       # a skill only a passive uses
        with self.assertRaises(ValueError):
            self.h.boss_moveset(self.ident, self.unit, self.skills, {"S4", "S9"})

    def test_body_motions_are_the_games_motion_names(self):
        self.assertEqual(repaint.motion_names([4, 0, 2, 3, 5, 7, 12, 17, 18, 1, 99]),
                         {"Damaged", "Default", "Evade", "Guard", "Move", "S1", "S6", "Parrying", "Idle", "Dead"})

    def test_pick_unit_prefers_most_attacks_or_the_named_one(self):
        recs = [{"id": 5, "attributeList": [{"skillId": 91}]}, self.unit]
        self.assertEqual(self.h.pick_unit(recs, self.skills)["id"], 81912)
        weak = {"id": 6, "attributeList": [{"skillId": i} for i in (91, 92, 93, 94)]}
        self.skills[99] = {"id": 99, "skillTier": 1, "skillData": [{"defaultValue": 1, "coinList": [{"scale": 1}]}]}
        weak2 = {"id": 7, "attributeList": [{"skillId": 99}] * 4}
        self.assertEqual(self.h.pick_unit([weak2, weak], self.skills)["id"], 6)   # same count: strongest
        self.assertEqual(self.h.pick_unit(recs, self.skills, want=5)["id"], 5)


class Identities(unittest.TestCase):
    def setUp(self):
        from limbus_skins import helpers
        self.h = helpers

    def test_new_identity_clones_base_with_new_id(self):
        base = {"id": 10710, "characterId": 7, "appearance": "10710_X", "attributeList": [{"skillId": 1071001}]}
        rec = self.h.clone_record("personality", base, 10710, 10799)
        self.assertEqual(rec, dict(base, id=10799))
        self.assertEqual(base["id"], 10710)                                  # the base stays as it was
        self.assertIsNone(self.h.clone_record("personality", {"id": 10701}, 10710, 10799))

    def test_clones_follow_every_table_keyed_by_the_identity(self):
        c = self.h.clone_record
        self.assertEqual(c("skin", {"skinId": 10710, "rank": 1}, 10710, 10799)["skinId"], 10799)
        self.assertEqual(c("personality-passive", {"personalityID": 10710}, 10710, 10799)["personalityID"], 10799)
        self.assertEqual(c("personality-voice", {"personalityId": 10710}, 10710, 10799)["personalityId"], 10799)
        self.assertEqual(c("illust-pivots-unitinfo", {"id": "10710_normal"}, 10710, 10799)["id"], "10799_normal")
        self.assertIsNone(c("illust-pivots-unitinfo", {"id": "107100_normal"}, 10710, 10799))
        thumb = c("personality-cg-thumbnail", {"personalityId": 10710, "folderName": ""}, 10710, 10799)
        self.assertEqual((thumb["personalityId"], thumb["folderName"]), (10799, "10710"))   # the base's art
        self.assertIsNone(c("gacha", {"id": 10710}, 10710, 10799))           # not an identity table

    def test_add_clones_appends_copies_of_the_records_as_they_are_now(self):
        lst = [{"id": 10710, "appearance": "400012_VergiliusAppearance"}, {"id": 10701}]
        out = self.h.add_clones("personality", lst, {10799: 10710})
        self.assertEqual([x["id"] for x in out], [10710, 10701, 10799])
        self.assertEqual(out[2]["appearance"], "400012_VergiliusAppearance")   # after a helper swap: the kit too

    def test_a_clone_shows_the_card_of_its_folder_identity(self):
        thumbs = [{"personalityId": 10911, "folderName": "", "isNormalReady": True}, {"personalityId": 10508, "folderName": ""}]
        out = self.h.add_clones("personality-cg-thumbnail", thumbs, {10999: 10917, 10599: 10508}, {10999: 10911})
        got = {r["personalityId"]: r["folderName"] for r in out}
        self.assertEqual(got, {10911: "", 10508: "", 10999: "10911", 10599: "10508"})   # 10917 has none: 10911's
        self.assertTrue(next(r for r in out if r["personalityId"] == 10999)["isNormalReady"])

    def test_an_ally_is_a_boss_form_on_a_new_id(self):
        from limbus_skins.build import boss_form, catalog
        skins = catalog()
        for key, new, base, unit in (("dulcinea-id", 10999, 10917, 8381), ("distorted-kim-id", 10599, 10508, 8153)):
            s = skins[key]
            self.assertTrue(boss_form(key, s), key)
            self.assertEqual((s["kind"], s["identity"], s["base"], s["unit"]), ("identity", new, base, unit))
        self.assertFalse(boss_form("vergilius-id", skins["vergilius-id"]))      # wears its base's body

    def test_new_identity_refuses_taken_or_missing_ids(self):
        self.h.check_new_ids({10710, 10410}, {10799: 10710, 10499: 10410})
        with self.assertRaises(ValueError):
            self.h.check_new_ids({10710, 10799}, {10799: 10710})             # the game has it already
        with self.assertRaises(ValueError):
            self.h.check_new_ids({10410}, {10799: 10710})                    # nothing to clone

    def test_an_identity_is_swapped_in_with_the_shared_static_file(self):
        from limbus_skins.build import built_dirs
        with tempfile.TemporaryDirectory() as data:
            files = os.path.join(data, "built", "helpers", "files")
            os.makedirs(os.path.join(files, "ab", "cd"))
            open(os.path.join(files, "ab", "cd", "__data"), "wb").close()
            with open(os.path.join(data, "built", "helpers", "manifest.json"), "w") as f:
                json.dump({"skins": ["vergilius-id"], "skills": []}, f)
            self.assertEqual(built_dirs(data, ["vergilius-id"]), [("helpers", files)])

    def test_catalog_has_the_identity_kind_and_kali_stays_locked(self):
        from limbus_skins.build import catalog
        skins = catalog()
        self.assertEqual((skins["vergilius-id"]["kind"], skins["vergilius-id"]["identity"], skins["vergilius-id"]["base"]),
                         ("identity", 10799, 10710))
        self.assertEqual(skins["kali-id"]["identity"], 10499)
        self.assertNotIn("unlock", skins["kali-id"])                    # the Black Forest is gone: open from the start


class FakeGame:
    """build() against a stand-in game: identity records, skills, boss units and the bodies that
    carry them - no cache, no UnityPy. Enter it, then call build.build(keys, ...)."""
    SKILLS = {1: {"id": 1, "skillTier": 1, "skillData": [{"skillMotion": "S1", "defType": "ATTACK",
                                                          "coinList": [{"scale": 1}, {"scale": 1}]}]},
              2: {"id": 2, "skillTier": 1, "skillData": [{"skillMotion": "Guard", "defType": "GUARD"}]}}

    def __init__(self, skins, pers, units=None, helpers=None, bodies=None):
        from limbus_skins import build, game, helpers as hp, repaint
        self.written = {}
        ids = {i: {"appearance": r.get("appearance", f"{i}_X")} for i, r in pers.items()}
        bodies = bodies or {}

        def rewrite(path, swaps, skill_swaps=None, clones=None, *a):
            self.written = {"swaps": swaps, "clones": dict(clones or {}), "skills": dict(skill_swaps or {})}
            return b"raw", sorted(set(swaps) | set(clones or {})), []
        self.patches = [
            mock.patch.object(build, "catalog", return_value=build.with_allies(skins)),
            mock.patch.object(game, "identities", return_value=(ids, helpers or {})),
            mock.patch.object(game, "boss_data", return_value=(pers, self.SKILLS, units or {})),
            mock.patch.object(game, "body_index", return_value=({b: b for b in bodies}, {b: b for b in bodies})),
            mock.patch.object(game, "unitypy", return_value=mock.Mock(load=lambda p: p)),
            mock.patch.object(repaint, "body_motions", side_effect=lambda env, app: bodies[env]),
            mock.patch.object(game, "find_static", return_value=("static", None)),
            mock.patch.object(hp, "rewrite_static", side_effect=rewrite),
            mock.patch.object(build, "_write", return_value="ab/cd/__data"),
        ]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class Allies(unittest.TestCase):
    """An "ally" on a skin: the same character as an identity of its own, equipable beside it."""

    def test_ally_expands(self):
        from limbus_skins.build import boss_form, with_allies
        skins = {"ahab": {"key": "ahab", "kind": "repaint", "identity": 10808, "source": "8105_AhabAppearance",
                          "unit": 8190, "ally": 10898, "title": "Ahab", "unlock": "black-forest", "card": "ahab"},
                 "qoh": {"key": "qoh", "kind": "helper", "identity": 10312, "helper": 400003, "ally": 10398},
                 "rien": {"key": "rien", "kind": "repaint", "identity": 10115, "source": "1347_X", "unit": 1380,
                          "ally": 10198, "ally_unit": 1347, "ally_unlock": "black-forest", "becomes": "Rien"}}
        out = with_allies(skins)
        a = out["ahab-id"]
        self.assertEqual((a["key"], a["kind"], a["identity"], a["of"], a["folder"], a["source"], a["unit"],
                          a["unlock"], a["title"]),
                         ("ahab-id", "identity", 10898, 10808, 10808, "8105_AhabAppearance", 8190,
                          "black-forest", "Ahab - ally"))
        self.assertNotIn("card", a)                  # its folder's card: the skin's own card step draws it
        self.assertNotIn("base", a)                  # chosen at build time (helpers.ally_base)
        self.assertTrue(boss_form("ahab-id", a))
        q = out["qoh-id"]
        self.assertEqual((q["kind"], q["identity"], q["of"], q["helper"], q.get("source")), ("identity", 10398, 10312, 400003, None))
        self.assertNotIn("unlock", q)
        r = out["rien-id"]
        self.assertEqual((r["unit"], r["unlock"]), (1347, "black-forest"))
        self.assertEqual((r["title"], q["title"]), ("Rien - ally", "qoh - ally"))   # the forest's gift list names each
        # its name in the game: the skin's "name", else its "becomes" up to a bracket or a colon
        self.assertEqual((a["name"], r["name"], q["name"]), ("Ahab", "Rien", "Qoh"))
        self.assertEqual(with_allies({"b": {"key": "b", "kind": "repaint", "identity": 11111, "ally": 11198,
                                            "becomes": "the Barber (bloodfiend)"}})["b-id"]["name"], "The Barber")
        # the skin itself stays as it was, one character with its ally: the ally stands for both
        self.assertEqual(out["ahab"], dict(skins["ahab"], dupe_of="ahab-id"))
        with self.assertRaises(ValueError):                                  # a derived key may not hide another skin
            with_allies({"x": {"key": "x", "kind": "repaint", "identity": 10101, "ally": 10199},
                         "x-id": {"key": "x-id", "kind": "identity", "identity": 10198, "base": 10101}})

    def test_ally_base(self):
        from limbus_skins.helpers import ally_base
        pers = {10101: {"id": 10101, "unitScriptID": 5},
                10102: {"id": 10102, "attributeList": "[{'skillId': 1, 'number': 0}]"},     # a hidden skill
                10103: {"id": 10103}, 10104: {"id": 10104}, 10201: {"id": 10201}}
        self.assertEqual(ally_base(pers, 10104), 10104)                      # clean: its own record
        self.assertEqual(ally_base(pers, 10101), 10103)                      # the lowest clean one of that sinner
        with self.assertRaises(ValueError):
            ally_base({10101: {"id": 10101, "unitScriptID": 5}}, 10101)

    def test_ally_missing_unit(self):
        from limbus_skins import build
        skins = {"a": {"key": "a", "kind": "repaint", "identity": 10101, "source": "A_App", "ally": 10198},
                 "b": {"key": "b", "kind": "repaint", "identity": 10102, "source": "Gone_App", "ally": 10197}}
        unit = {"id": 9, "appearance": "A_App", "attributeList": [{"skillId": 1}], "defenseSkillIDList": [2]}
        with tempfile.TemporaryDirectory() as data, \
                FakeGame(skins, {10101: {"id": 10101}, 10102: {"id": 10102}}, {"A_App": [unit]},
                         bodies={"A_App": {"S1", "Guard"}}) as g:
            res = build.build(["a-id", "b-id"], "game", "cache", data, log=lambda *a: None)
            with open(os.path.join(data, "built", "helpers", "manifest.json")) as f:
                man = json.load(f)
        self.assertEqual(res["a-id"][0], True, res)
        self.assertEqual(res["b-id"][0], False)
        self.assertIn("Gone_App", res["b-id"][1])
        self.assertEqual(g.written["clones"], {10198: 10101})               # only the ally that built
        self.assertEqual(g.written["swaps"][10198]["appearance"], "A_App")
        self.assertEqual(man["identities"], [{"id": 10198, "base": 10101, "folder": 10101, "name": "A", "color": "#ffe096"}])

    def test_helper_ally(self):
        from limbus_skins import build
        skins = {"qoh": {"key": "qoh", "kind": "helper", "identity": 10312, "helper": 400003, "ally": 10398,
                         "unlock": "black-forest"}}
        h = {"id": 400003, "appearance": "400003_QueenOfHatred", "attributeList": [{"skillId": 1, "number": 3}],
             "defenseSkillIDList": [2]}
        base = {"id": 10312, "appearance": "10312_X", "attributeList": [{"skillId": 9}]}
        with tempfile.TemporaryDirectory() as data, \
                FakeGame(skins, {10312: base}, helpers={400003: h},
                         bodies={"400003_QueenOfHatred": {"S1", "Guard"}}) as g:
            res = build.build(["qoh-id"], "game", "cache", data, log=lambda *a: None)
            with open(os.path.join(data, "built", "helpers", "manifest.json")) as f:
                man = json.load(f)
        self.assertTrue(res["qoh-id"][0], res)
        self.assertEqual(g.written["clones"], {10398: 10312})
        self.assertEqual(set(g.written["swaps"]), {10398})                  # the base identity is untouched
        swap = g.written["swaps"][10398]
        self.assertEqual((swap["appearance"], swap["attributeList"], swap["defenseSkillIDList"]),
                         ("400003_QueenOfHatred", [{"skillId": 1, "number": 3}], [2]))
        self.assertEqual(man["identities"], [{"id": 10398, "base": 10312, "folder": 10312, "name": "Qoh",
                                              "color": "#ffe096", "unlock": "black-forest"}])
        from limbus_skins import client                     # its colour reaches the game's text, not the manifest's words
        self.assertEqual(client.coloured("Qoh", man["identities"][0]), "<color=#ffe096>Qoh</color>")
        self.assertEqual(client.coloured("Qoh", {"color": "red><b"}), "Qoh")
        self.assertEqual(man["skins"], ["qoh-id"])
        self.assertEqual(base, {"id": 10312, "appearance": "10312_X", "attributeList": [{"skillId": 9}]})


class Covers(unittest.TestCase):
    """An ally's own card pictures, cut from its villain's story portrait (covers.py)."""

    def test_the_body_is_the_portraits_largest_sprite(self):
        from PIL import Image
        from limbus_skins import covers
        tex = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        tex.paste((200, 0, 0, 255), (10, 10, 60, 90))       # the body: x 10-60, y 10-90 (top-down)
        tex.paste((0, 0, 200, 255), (70, 10, 90, 30))       # a face tile
        body = covers.body(tex, [(70, 70, 20, 20), (10, 10, 50, 80)])   # Unity rects, bottom-up
        self.assertEqual(body.size, (50, 80))
        self.assertEqual(body.getpixel((25, 40)), (200, 0, 0, 255))
        with self.assertRaises(ValueError):
            covers.body(tex, [])

    def test_every_card_picture_at_the_games_own_sizes(self):
        from PIL import Image
        from limbus_skins import covers
        art = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
        art.paste((240, 240, 240, 255), (100, 50, 200, 880))
        stock = {"": Image.new("RGB", (192, 108), (40, 40, 40)), "_info": Image.new("RGB", (54, 83)),
                 "_profile": Image.new("RGB", (26, 26)), "_profile_support_personality": Image.new("RGB", (20, 98))}
        pics = covers.pictures(art, stock)
        self.assertEqual({s: im.size for s, im in pics.items()}, {s: im.size for s, im in stock.items()})
        self.assertTrue(all(im.mode == "RGB" for im in pics.values()))    # opaque: DXT1 would punch holes
        # an identity without some picture of its own (no profile portrait): the game's usual size
        pics = covers.pictures(art, {"": stock[""]})
        self.assertEqual({s: im.size for s, im in pics.items()},
                         {"": (192, 108), "_info": (537, 827), "_profile": (256, 256), "_profile_support_personality": (197, 982)})
        # an identity whose card isn't in the cache at all: the usual sizes, over the art itself
        self.assertEqual({s: im.size for s, im in covers.pictures(art, {}).items()}, covers.SIZES)
        # a spear held above the head: the face is set by hand, and the pfp centres on it
        tall = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
        tall.paste((240, 240, 240, 255), (20, 0, 40, 880))         # the spear, from the top
        tall.paste((0, 0, 255, 255), (130, 300, 230, 880))          # the body
        tall.paste((255, 0, 0, 255), (160, 220, 200, 300))          # the face
        pfp = covers.pictures(tall, {"": stock[""]}, face=(180, 260))["_profile"]
        r, g, b = pfp.getpixel((128, 118))
        self.assertTrue(r > 200 and g < 60 and b < 60, (r, g, b))
        self.assertEqual(covers.file_name(10999, "_info"), "10999_normal_info.dxt")
        self.assertEqual(covers.file_name(10999, ""), "10999_normal.dxt")

    def test_a_cutout_with_a_background_of_its_own_stands_on_it(self):
        from PIL import Image
        from limbus_skins import covers
        art = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
        art.paste((240, 240, 240, 255), (100, 50, 200, 880))
        stock = {"": Image.new("RGB", (192, 108), (0, 0, 200))}       # the borrowed identity's card: blue
        bg = Image.new("RGBA", (64, 36), (200, 0, 0, 255))             # the character's own floor: red
        for suffix, im in covers.pictures(art, stock, bg=bg).items():
            r, g, b = im.getpixel((1, 1))                              # a corner, behind the figure
            self.assertTrue(r > 100 and b < 60, (suffix, (r, g, b)))
        r, g, b = covers.pictures(art, stock)[""].getpixel((1, 1))   # none of its own: the card's, dimmed
        self.assertTrue(b > r, (r, g, b))

    def test_the_pfp_frames_on_a_point_of_its_own(self):
        # a card frames on the top of the head (chibi heads are big); a pfp on the face itself
        from PIL import Image
        from limbus_skins import covers
        art = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
        art.paste((240, 240, 240, 255), (60, 40, 240, 880))         # the body
        art.paste((255, 0, 0, 255), (110, 140, 190, 200))           # the eyes, well below the head's top
        pics = covers.pictures(art, {"": Image.new("RGB", (192, 108))}, face=(150, 45), pfp_face=(150, 170))
        r, g, b = pics["_profile"].getpixel((128, 128))
        self.assertTrue(r > 200 and g < 60, (r, g, b))
        r, g, b = covers.pictures(art, {"": Image.new("RGB", (192, 108))}, face=(150, 45))["_profile"].getpixel((128, 128))
        self.assertFalse(r > 200 and g < 60, "without a point of its own the pfp frames on the card's")

    def test_a_title_background_fills_the_screen_and_a_logo_keeps_its_alpha(self):
        import struct
        import texture2ddecoder
        from PIL import Image
        from limbus_skins import title
        with tempfile.TemporaryDirectory() as d:
            tall = os.path.join(d, "tall.png")
            Image.new("RGB", (1000, 2000), (200, 0, 0)).save(tall)
            with open(title.save(tall, d), "rb") as f:
                raw = f.read()
            self.assertEqual(struct.unpack("<4sHHHH", raw[:12]), (b"LSD1", 1920, 1080, 1920, 1080))
            px = texture2ddecoder.decode_bc1(raw[16:], 1920, 1080)      # BGRA
            mid = 540 * 1920 * 4
            self.assertEqual(px[mid + 2], 0)                            # a portrait: black at the side
            self.assertGreater(px[mid + 960 * 4 + 2], 150)              # and itself in the middle
            self.assertEqual(title.frame(Image.new("RGB", (1600, 1000)), title.SCREEN).size, title.SCREEN)
            with open(title.save(tall, d, stretch=True), "rb") as f:
                px = texture2ddecoder.decode_bc1(f.read()[16:], 1920, 1080)
            self.assertGreater(px[mid + 2], 150)                        # stretched: the picture reaches the side
            logo = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
            logo.paste((255, 255, 255, 255), (0, 0, 200, 100))          # left half solid, right clear
            src = os.path.join(d, "logo.png")
            logo.save(src)
            with open(title.save(src, d, logo=True), "rb") as f:
                raw = f.read()
            magic, w, h, sw, sh = struct.unpack("<4sHHHH", raw[:12])
            self.assertEqual((magic, sw, sh), (b"LSD5", 800, 200))    # fit to the logo's box, x2 at most
            self.assertEqual(len(raw), 16 + (w // 4) * (h // 4) * 16)
            px = texture2ddecoder.decode_bc3(raw[16:], w, h)            # BGRA, rows bottom-up
            row = (h - 1 - 100) * w * 4                                 # a row in the middle
            self.assertEqual(px[row + 3], 255)                         # left: solid
            self.assertEqual(px[row + 700 * 4 + 3], 0)                 # right: see-through
            self.assertEqual(sorted(title.files(d)), ["background.dxt", "logo.dxt"])
            title.clear(d, logo=True)
            self.assertEqual(sorted(title.files(d)), ["background.dxt"])
            title.clear(d)                                              # the game's own, not the default
            self.assertEqual(sorted(title.files(d)), ["off"])
            title.save(tall, d)
            self.assertEqual(sorted(title.files(d)), ["background.dxt"])

    def test_a_cover_is_saved_as_the_games_own_dxt1(self):
        # this Limbus build has no image decoder (ImageConversion.LoadImage is stripped): the
        # client patch hands Texture2D.LoadRawTextureData DXT1 blocks, rows bottom-up as Unity's
        import struct
        from PIL import Image
        from limbus_skins import covers
        img = Image.new("RGB", (537, 827), (0, 0, 255))
        img.paste((255, 0, 0), (0, 0, 537, 10))                     # red along the top
        raw = covers.dxt1_file(img)
        magic, w, h, sw, sh = struct.unpack("<4sHHHH", raw[:12])
        self.assertEqual((magic, w, h, sw, sh), (b"LSD1", 540, 828, 537, 827))   # padded to 4
        self.assertEqual(len(raw), 16 + (540 // 4) * (828 // 4) * 8)
        import texture2ddecoder
        px = texture2ddecoder.decode_bc1(raw[16:], 540, 828)         # BGRA
        row = lambda y: px[(y * 540) * 4:(y * 540) * 4 + 4]          # noqa: E731 - bottom-up rows
        self.assertTrue(row(826)[2] > 200 and row(826)[0] < 60, row(826))   # the picture's top: red
        self.assertTrue(row(0)[0] > 200 and row(0)[2] < 60, row(0))         # its bottom: blue (BGRA)
        self.assertTrue(max(row(827)[:3]) < 30, row(827))                  # above it: padding


class CoverBuild(unittest.TestCase):
    def build(self, idles):
        from PIL import Image
        from limbus_skins import build, covers
        skins = {"a": {"key": "a", "kind": "repaint", "identity": 10101, "source": "A_App", "ally": 10198, "cover": "A_EGO"},
                 "b": {"key": "b", "kind": "repaint", "identity": 10102, "source": "A_App", "ally": 10197, "cover": "Unseen"}}
        unit = {"id": 9, "appearance": "A_App", "attributeList": [{"skillId": 1}], "defenseSkillIDList": [2]}
        art = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
        art.paste((250, 250, 250, 255), (30, 20, 70, 290))
        stock = {"": Image.new("RGB", (96, 54)), "_info": Image.new("RGB", (27, 41)), "_profile": Image.new("RGB", (16, 16)),
                 "_profile_support_personality": Image.new("RGB", (10, 49))}
        out = {}
        with tempfile.TemporaryDirectory() as data, \
                FakeGame(skins, {10101: {"id": 10101}, 10102: {"id": 10102}}, {"A_App": [unit]}, bodies={"A_App": {"S1", "Guard"}}), \
                mock.patch.object(covers, "portraits", return_value={"A_EGO": art}), \
                mock.patch.object(covers, "idle_frames", return_value=idles) as idle, \
                mock.patch.object(covers, "stock_pictures", return_value=stock) as sp:
            res = build.build(["a-id", "b-id"], "game", "cache", data, log=lambda *a: None)
            for k in ("a-id", "b-id"):
                d = os.path.join(data, "built", k + build.COVER)
                if os.path.exists(d):
                    with open(os.path.join(d, "manifest.json")) as f:
                        out[k] = (json.load(f), sorted(os.listdir(os.path.join(d, "pics"))))
        return res, out, idle, sp

    def test_an_ally_with_a_cover_gets_its_own_card_pictures(self):
        res, out, idle, sp = self.build({})
        man, pics = out["a-id"]
        self.assertEqual((man["identity"], man["from"]), (10198, "story A_EGO"))
        self.assertEqual(pics, ["10198_normal.dxt", "10198_normal_info.dxt", "10198_normal_profile.dxt",
                                "10198_normal_profile_support_personality.dxt"])
        sp.assert_any_call("game", "cache", 10101)                  # sized by its folder's own card
        self.assertEqual(idle.call_args[0][3], {"A_App"})          # the body each ally wears
        self.assertTrue(res["a-id"][0] and "cover: story A_EGO" in res["a-id"][1], res)
        self.assertNotIn("b-id", out)                               # no portrait, no idle: its folder's card
        self.assertTrue(res["b-id"][0] and "no cover" in res["b-id"][1], res)

    def test_its_pfp_is_its_body_standing_idle_and_no_portrait_means_idle_everywhere(self):
        from PIL import Image
        stand = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
        stand.paste((255, 0, 0, 255), (40, 10, 80, 60))             # a red head
        stand.paste((0, 0, 255, 255), (30, 60, 90, 350))            # a blue body
        res, out, _, _ = self.build({"A_App": stand})
        self.assertEqual(out["a-id"][0]["from"], "story A_EGO + idle A_App")
        self.assertEqual(out["b-id"][0]["from"], "idle A_App")      # its portrait isn't cached: the idle frame for all
        self.assertEqual(len(out["b-id"][1]), 4)
        self.assertTrue(res["b-id"][0] and "cover: idle A_App" in res["b-id"][1], res)

    def test_a_name_survives_a_build_of_other_skins_and_carries_its_folders_text(self):
        from limbus_skins import build, game
        skins = {"a": {"key": "a", "kind": "kit", "identity": 10101, "name": "Ace", "game_title": "The\nAce",
                       "moves": {"1": {"coins": [1, 1]}}},
                 "b": {"key": "b", "kind": "kit", "identity": 10101, "name": "Bee", "moves": {"1": {"coins": [1, 1]}}}}
        text = {10101: {"id": 10101, "title": "LCB\nSinner", "name": "Yi Sang"}}
        with tempfile.TemporaryDirectory() as data:
            os.makedirs(os.path.join(data, "built"))
            with open(os.path.join(data, "built", "names.json"), "w") as f:
                json.dump({"names": [{"skin": "kept", "identity": 10204, "name": "Kromer"},
                                     {"skin": "a", "identity": 10101, "name": "old"}]}, f)
            with FakeGame(skins, {10101: {"id": 10101, "attributeList": [{"skillId": 1}], "defenseSkillIDList": [2]}}), \
                    mock.patch.object(game, "names", return_value=text):
                build.build(["a"], "game", "cache", data, log=lambda *a: None)
            with open(os.path.join(data, "built", "names.json")) as f:
                named = {e["skin"]: e for e in json.load(f)["names"]}
        self.assertEqual(named["kept"]["name"], "Kromer")                 # not in this build: kept
        self.assertEqual((named["a"]["name"], named["a"]["title"], named["a"]["folder_title"], named["a"]["folder_name"]),
                         ("Ace", "The\nAce", "LCB\nSinner", "Yi Sang"))

    def test_a_cover_arts_own_background_goes_behind_it(self):
        from PIL import Image
        from limbus_skins import build, covers
        skins = {"x-id": {"key": "x-id", "kind": "identity", "identity": 10193, "base": 10101, "cover_art": "x"}}
        with tempfile.TemporaryDirectory() as data:
            d = os.path.join(data, "art", "cards", "x")
            os.makedirs(d)
            fig = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
            fig.paste((250, 250, 250, 255), (30, 20, 70, 290))
            fig.save(os.path.join(d, "normal.png"))
            Image.new("RGB", (64, 36), (200, 0, 0)).save(os.path.join(d, "bg.png"))
            with FakeGame(skins, {10101: {"id": 10101}}), \
                    mock.patch.object(covers, "idle_frames", return_value={}), \
                    mock.patch.object(covers, "stock_pictures", return_value={"": Image.new("RGB", (96, 54))}), \
                    mock.patch.object(covers, "pictures", wraps=covers.pictures) as pics:
                res = build.build(["x-id"], "game", "cache", data, log=lambda *a: None)
        self.assertTrue(res["x-id"][0] and "cover: art x" in res["x-id"][1], res)
        self.assertEqual(pics.call_args.kwargs.get("bg").getpixel((1, 1))[:3], (200, 0, 0))

    def test_an_identity_with_art_of_its_own_takes_its_pfp_from_the_art_not_the_body_it_wears(self):
        # friend, 2026-11-14: "rework the already existing ids and their cards" - binah-id's pfp was
        # LCB Rodion's face (the body it wears), under Binah's card art
        from PIL import Image
        from limbus_skins import build, covers
        skins = {"x-id": {"key": "x-id", "kind": "identity", "identity": 10193, "base": 10101, "cover_art": "x"}}
        stand = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
        stand.paste((0, 0, 255, 255), (30, 10, 90, 350))           # the sinner's body: blue
        with tempfile.TemporaryDirectory() as data:
            d = os.path.join(data, "art", "cards", "x")
            os.makedirs(d)
            fig = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
            fig.paste((250, 0, 0, 255), (30, 20, 70, 290))          # the character's art: red
            fig.save(os.path.join(d, "normal.png"))
            with FakeGame(skins, {10101: {"id": 10101, "appearance": "10101_X"}}), \
                    mock.patch.object(covers, "idle_frames", return_value={"10101_X": stand}), \
                    mock.patch.object(covers, "stock_pictures", return_value={"": Image.new("RGB", (96, 54))}), \
                    mock.patch.object(covers, "pictures", wraps=covers.pictures) as pics:
                res = build.build(["x-id"], "game", "cache", data, log=lambda *a: None)
        self.assertTrue(res["x-id"][0], res)
        self.assertIsNone(pics.call_args.kwargs.get("pfp"))           # the pfp comes from the art too
        self.assertNotIn("idle", res["x-id"][1].split("cover:")[1])

    def test_the_pfp_role_alone_takes_the_idle_frame(self):
        from PIL import Image
        from limbus_skins import covers
        story = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
        story.paste((250, 250, 250, 255), (30, 20, 70, 290))        # white
        stand = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
        stand.paste((255, 0, 0, 255), (30, 10, 90, 350))            # red
        pics = covers.pictures(story, {}, pfp=stand)
        r, g, b = pics["_profile"].getpixel((128, 128))
        self.assertTrue(r > 200 and g < 60, (r, g, b))
        r, g, b = pics["_info"].getpixel((268, 413))
        self.assertTrue(r > 200 and g > 200, (r, g, b))
        # a battle sprite: a spear held up beside it and a charm floating over its head aren't its face
        sprite = Image.new("RGBA", (400, 900), (0, 0, 0, 0))
        sprite.paste((200, 200, 200, 255), (20, 0, 32, 900))        # the spear: thin, the whole height
        sprite.paste((255, 0, 255, 255), (170, 60, 230, 110))       # the charm, with a gap under it
        sprite.paste((255, 0, 0, 255), (130, 200, 270, 420))        # the head
        sprite.paste((0, 0, 255, 255), (140, 420, 260, 890))        # the body
        x, y = covers.sprite_face(sprite)
        self.assertTrue(180 <= x <= 220 and 200 < y < 420, (x, y))
        r, g, b = covers.pictures(story, {}, pfp=sprite)["_profile"].getpixel((128, 128))
        self.assertTrue(r > 200 and g < 60 and b < 60, (r, g, b))  # the pfp is the head
        self.assertIsNone(covers.sprite_face(Image.new("RGBA", (10, 10))))
        self.assertEqual(covers.idle_body({"helper": 7}, {}, {7: {"appearance": "H_App"}}), "H_App")
        self.assertEqual(covers.idle_body({"source": "B_App", "base": 1}, {1: {"appearance": "X"}}, {}), "B_App")
        self.assertEqual(covers.idle_body({"base": 1}, {1: {"appearance": "X"}}, {}), "X")


class SkinPfps(unittest.TestCase):
    """Every other skin's pfp: its body standing idle in a fight, as the skin makes it look."""

    @staticmethod
    def stand():
        from PIL import Image
        img = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
        img.paste((255, 0, 0, 255), (40, 10, 80, 60))           # a red head
        img.paste((0, 0, 255, 255), (30, 60, 90, 350))          # a blue body
        return img

    def run_pfps(self, built_idle, cached, results=None, stale=()):
        from PIL import Image
        from limbus_skins import build, covers
        skins = {"rep": {"key": "rep", "kind": "repaint", "identity": 10101},
                 "hel": {"key": "hel", "kind": "helper", "identity": 10301, "helper": 7},
                 "bos": {"key": "bos", "kind": "boss", "identity": 10501, "source": "B_App"},
                 "kit": {"key": "kit", "kind": "kit", "identity": 10115},
                 "ally-id": {"key": "ally-id", "kind": "identity", "identity": 10198, "base": 10101}}
        keys = list(skins)
        results = results or {k: (True, "built") for k in keys}
        ids = {10101: {"appearance": "10101_App"}, 10301: {"appearance": "10301_App"}, 10501: {"appearance": "10501_App"}}
        out, logged = {}, []
        with tempfile.TemporaryDirectory() as data:
            root = os.path.join(data, "built")
            for k in stale:
                os.makedirs(os.path.join(root, k + build.COVER, "pics"))
            with mock.patch.object(covers, "idle_frames", return_value=cached) as frames, \
                    mock.patch.object(covers, "built_idle", side_effect=built_idle) as own, \
                    mock.patch.object(covers, "stock_pictures", return_value={"_profile": Image.new("RGB", (16, 16))}):
                build._pfps(keys, skins, results, ids, {7: {"appearance": "H_App"}}, set(), "game", "cache", data, root,
                            logged.append)
                for k in keys:
                    d = os.path.join(root, k + build.COVER)
                    if os.path.exists(d):
                        with open(os.path.join(d, "manifest.json")) as f:
                            out[k] = (json.load(f), sorted(os.listdir(os.path.join(d, "pics"))))
            return out, results, frames, own, root, logged

    def test_a_repaint_shows_its_own_build_and_a_helper_or_boss_the_games_body(self):
        out, results, frames, own, root, _ = self.run_pfps(lambda files, app: self.stand(),
                                                           {"H_App": self.stand(), "B_App": self.stand()})
        man, pics = out["rep"]
        self.assertEqual((man["kind"], man["pfp"], man["identity"], man["skins"], man["from"]),
                         ("cover", True, 10101, ["rep"], "idle 10101_App"))
        self.assertEqual(pics, ["10101_normal_profile.dxt"])          # the pfp alone: the card stays the game's
        own.assert_called_once_with(os.path.join(root, "rep", "files"), "10101_App")   # in its own colours
        self.assertEqual(frames.call_args[0][3], {"H_App", "B_App"})  # the others as the game draws them
        self.assertEqual(out["hel"][0]["from"], "idle H_App")
        self.assertEqual(out["bos"][0]["from"], "idle B_App")
        self.assertNotIn("kit", out)                                   # moves only: it looks as it did
        self.assertNotIn("ally-id", out)                               # an ally's cover is covers.py's
        self.assertTrue(results["rep"][0] and "pfp: idle 10101_App" in results["rep"][1], results)

    def test_without_an_idle_frame_the_game_keeps_its_pfp_and_an_old_one_goes(self):
        out, results, _, _, root, logged = self.run_pfps(lambda files, app: None, {}, stale=("rep", "hel"))
        self.assertEqual(out, {})
        self.assertFalse(os.path.exists(os.path.join(root, "rep.cover")))
        self.assertTrue(results["rep"][0] and "no pfp" in results["rep"][1], results)    # the skin itself is fine
        self.assertTrue(any("pfp hel" in line for line in logged), logged)

    def test_a_skin_that_failed_to_build_gets_no_pfp(self):
        from limbus_skins import build
        res = {k: (True, "built") for k in ("rep", "hel", "bos", "kit", "ally-id")}
        res["rep"] = (False, "audit: wrong")
        out, results, _, own, _, _ = self.run_pfps(lambda files, app: self.stand(), {"H_App": self.stand()},
                                                   results=res, stale=("rep",))
        self.assertNotIn("rep", out)
        own.assert_not_called()
        self.assertEqual(results["rep"], (False, "audit: wrong"))
        self.assertIn("hel", out)
        self.assertEqual(build.FORMAT, 37)                             # 37: Nothing There speaks, no entrance cards for it or WhiteNight; 36: boss entrances, boss hits, cards in colour, map changes; 35: shorter burning pages, bosses at their bodies' size; 34: Ruina bodies move like Limbus (motion.py); 33: a fight carries its enemies' burning pages; 32: identities with skills of their own; 31: a fight carries its story and its enemies of its own; 28: the shared build records bodies with no death of their own
        # 29: identities of our own moved off ids two of them shared (10593, 10894, 10796), and the
        # builds word each named identity for the client patch (title, and the folder's own text)
        # 30: an identity with art of its own takes its pfp from that art, not the sinner's body it wears

    def test_the_pfp_is_framed_on_the_sprites_head_at_the_identitys_size(self):
        from PIL import Image
        from limbus_skins import covers
        pic = covers.profile(self.stand(), {})
        self.assertEqual(pic.size, (256, 256))
        red = [(x, y) for y in range(0, 256, 4) for x in range(0, 256, 4)
               if (lambda p: p[0] > 200 and p[1] < 60 and p[2] < 60)(pic.getpixel((x, y)))]
        self.assertTrue(red, "the head is in the pfp")
        self.assertLess(sum(y for _, y in red) / len(red), 128)            # in its upper half, the body below
        self.assertEqual(covers.profile(self.stand(), {"_profile": Image.new("RGB", (40, 40))}).size, (40, 40))


class Audit(unittest.TestCase):
    SKILLS = {1: {"id": 1, "skillData": [{"skillMotion": "S1"}]}, 2: {"id": 2, "skillData": [{"skillMotion": "S7"}]},
              9: {"id": 9, "skillData": [{"skillMotion": "Guard", "defType": "GUARD"}]}}

    def test_a_sound_record_has_no_findings(self):
        from limbus_skins import audit
        rec = {"id": 10999, "attributeList": [{"skillId": 1, "number": 3}], "defenseSkillIDList": [9]}
        passives = {"battlePassiveList": [{"level": 1, "passiveIDList": [50]}]}
        self.assertEqual(audit.record(rec, self.SKILLS, {"S1", "Guard"}, {50}, passives), [])

    def test_what_would_freeze_or_break_a_fight_is_found(self):
        from limbus_skins import audit
        rec = {"id": 10999, "attributeList": "[{'skillId': 2, 'number': 1}, {'skillId': 7}]", "defenseSkillIDList": [9]}
        found = audit.record(rec, self.SKILLS, {"S1", "Guard"}, {50},
                             {"supporterPassiveList": [{"passiveIDList": [51]}]})
        self.assertEqual(found, ["passive 51 isn't in the game's data", "skill 2's motion S7 isn't one its body has",
                                 "skill 7 isn't in the game's data"])
        self.assertEqual(audit.record({"attributeList": [{"skillId": 1, "number": 0}], "defenseSkillIDList": []},
                                      self.SKILLS), ["no defense skill", "nothing in the deck"])


class Cards(unittest.TestCase):
    """ID card pictures drawn from card art (limbus_skins/cards.py)."""

    @staticmethod
    def figure(w=200, h=600, head=(100, 60, 40)):
        """A cut-out: a red head (circle) on a grey body, on transparency."""
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([60, 100, 140, h - 10], fill=(90, 90, 90, 255))
        x, y, r = head
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 0, 0, 255))
        return im

    def test_a_cut_out_is_told_from_a_whole_picture(self):
        from PIL import Image
        from limbus_skins import cards
        self.assertTrue(cards.is_cutout(self.figure()))
        self.assertFalse(cards.is_cutout(Image.new("RGBA", (50, 50), (10, 20, 30, 255))))

    def test_every_picture_keeps_its_size_and_frames_the_face(self):
        from PIL import Image
        from limbus_skins import cards
        art, stock = self.figure(), Image.new("RGB", (64, 64), (0, 0, 200))
        for role, size in (("wide", (192, 108)), ("info", (54, 83)), ("profile", (64, 64)), ("support", (20, 98))):
            img = cards.render(role, size, art, stock, face=(100, 60))
            self.assertEqual(img.size, size, role)
            self.assertEqual(img.getpixel((0, 0))[3], 255, role)            # a backdrop under the cut-out
        prof = cards.render("profile", (64, 64), art, stock, face=(100, 60))
        r, g, b, _ = prof.getpixel((32, 27))                                # the face, near the middle
        self.assertTrue(r > 200 and g < 60 and b < 60, prof.getpixel((32, 27)))

    def test_a_whole_picture_is_cut_inside_itself_around_the_face(self):
        from PIL import Image
        from limbus_skins import cards
        art = Image.new("RGBA", (400, 400), (0, 255, 0, 255))
        x, y, w, h = cards._inside((-500, -500, 1600, 900), art, (300, 100))
        self.assertTrue(0 <= x and 0 <= y and x + w <= 400.01 and y + h <= 400.01)
        self.assertAlmostEqual(w / h, 1600 / 900, places=3)                  # the shape stays the picture's

    def test_whose_card_a_skin_draws(self):
        from limbus_skins.build import card_identity, catalog
        sk = catalog()
        self.assertEqual(card_identity(sk["dulcinea-id"]), 10911)            # its folder
        self.assertEqual(card_identity(sk["vergilius-id"]), 10710)           # its base
        self.assertEqual(card_identity(sk["matthias"]), 11109)               # its own identity
        # malkuth and hokma: their identities' cards are in files of their own (p10309, p11110); the
        # other Ruina bodies' cards share unitsprite_s1_0 with roach-emperor's, so only their -ids
        # show their art (cover_art, through the client patch)
        self.assertEqual({k for k, s in sk.items() if s.get("card")},
                         {"kali", "matthias", "roach-emperor", "distorted-hohenheim", "vergilius-wildhunt", "vergilius-id",
                          "malkuth", "hokma"})
        # an ally has a cover of its own instead: the identity whose card it borrows keeps its own
        self.assertEqual((sk["dulcinea-id"]["cover"], sk["ahab-id"]["cover"]), ("Dolcinea", "Ahab_EGO"))

    def test_two_skins_drawing_the_same_card_swap_it_in_once(self):
        from limbus_skins.build import built_dirs
        with tempfile.TemporaryDirectory() as data:
            for key in ("vergilius-wildhunt", "vergilius-id"):
                d = os.path.join(data, "built", key + ".card")
                os.makedirs(os.path.join(d, "files", "ab", "cd"))
                with open(os.path.join(d, "manifest.json"), "w") as f:
                    json.dump({"skins": [key], "files": ["ab/cd"]}, f)
            got = [label for label, _ in built_dirs(data, ["vergilius-wildhunt", "vergilius-id"])]
            self.assertEqual(got, ["vergilius-wildhunt.card"])


class LibraryOfRuina(unittest.TestCase):
    def test_afterimage_trails_behind_and_keeps_the_pivot_on_the_body(self):
        from PIL import Image
        from limbus_skins import lor
        img = Image.new("RGBA", (10, 4), (255, 0, 0, 255))
        out, (px, py) = lor.afterimage(img, (0.5, 0.25), 4, copies=2)
        self.assertEqual(out.size, (18, 4))
        self.assertEqual(out.getpixel((2, 1))[3], 255)             # the body stays at the left (its front)
        self.assertTrue(0 < out.getpixel((15, 1))[3] < 255)        # ghosts fade off to the right (behind)
        self.assertAlmostEqual(px * out.width, 5.0)                # same body pixel as before
        self.assertEqual(py, 0.25)

    def test_two_forms_each_stand_the_identitys_height(self):
        from PIL import Image
        from limbus_skins import lor
        fake = {"a.pres": {"Default": (Image.new("RGBA", (10, 250)), (0.5, 0.2)), "Slash": (Image.new("RGBA", (30, 100)), (0.5, 0.0))},
                "b.pres": {"Default": (Image.new("RGBA", (10, 400)), (0.5, 0.0)), "S3": (Image.new("RGBA", (40, 400)), (0.5, 0.0))}}
        with mock.patch.object(lor, "bundle_path", lambda n: n), mock.patch.object(lor, "poses", lambda b, p, head=None: fake[b]):
            t = lor.source_table({"bundle": "a.pres", "prefab": "A", "sources": {"ego": {"bundle": "b.pres", "prefab": "B"}},
                                  "map": {"idle": ["Default"], "S1": ["Slash"], "S3": ["ego:S3"]}}, height=5.0)
        ppu = lambda k: t[k][0].read().m_PixelsToUnits  # noqa: E731
        # 200 px above the feet (a blade below them doesn't count) / 5 u, and 400 px / 5 u
        self.assertEqual((ppu("idle"), ppu("S1"), ppu("S3")), (40.0, 40.0, 80.0))
        with self.assertRaises(lor.LorError):
            with mock.patch.object(lor, "bundle_path", lambda n: n), mock.patch.object(lor, "poses", lambda b, p, head=None: fake[b]):
                lor.source_table({"bundle": "a.pres", "prefab": "A", "map": {"S3": ["nope:S3"]}})

    def test_frames_look_like_sprites_to_repaint(self):
        from PIL import Image
        from limbus_skins import lor
        a, b = lor.Frame("S1", Image.new("RGBA", (4, 4)), (0.5, 0.0), 100), lor.Frame("S2", Image.new("RGBA", (4, 4)), (0.5, 0.0), 100)
        self.assertNotEqual(a.path_id, b.path_id)
        img, s, (px, py) = repaint.render(a, mirror=True)
        self.assertEqual((s.m_PixelsToUnits, px, py), (100, 0.5, 0.0))


class OwnBodies(unittest.TestCase):
    def test_names_and_paths_are_what_the_client_patch_asks_for(self):
        from limbus_skins import ownbody
        self.assertEqual(ownbody.appearance_name(10993, "Binah"), "10993_BinahAppearance")
        self.assertEqual(ownbody.appearance_name(10410, "the red mist"), "10410_TheRedMistAppearance")
        self.assertEqual(ownbody.asset_path("10993_BinahAppearance"), "assets/myosotis/10993_binahappearance.prefab")
        self.assertRegex(ownbody.cab_name("10993_BinahAppearance"), r"^CAB-[0-9a-f]{32}$")
        self.assertNotEqual(ownbody.cab_name("a"), ownbody.cab_name("b"))
        with self.assertRaises(ownbody.OwnBodyError):
            ownbody.appearance_name(1, "!!!")

    def test_the_session_lists_only_live_bodies_with_plain_names(self):
        from limbus_skins import client
        with tempfile.TemporaryDirectory() as d:
            for key, bodies in (("a-id", [{"appearance": "1_AAppearance", "file": "1_AAppearance.bundle", "fallback": "10101_YiSang_BaseAppearance"}]),
                                ("b-id", [{"appearance": "2_BAppearance", "file": "2_BAppearance.bundle"}]),
                                ("c-id", [{"appearance": "../x", "file": "../x.bundle"}, {"appearance": "3_C", "file": "other.bundle"}])):
                out = os.path.join(d, "built", key + ".body")
                os.makedirs(out)
                for b in bodies:
                    if "/" not in b["file"]:
                        open(os.path.join(out, b["file"]), "wb").close()
                with open(os.path.join(out, "manifest.json"), "w") as f:
                    json.dump({"kind": "body", "skins": [key], "bodies": bodies}, f)
            got = client.own_bodies(d, {"a-id", "c-id"})
            self.assertEqual([(a, f, fb) for a, f, _, fb in got], [("1_AAppearance", "1_AAppearance.bundle", "10101_YiSang_BaseAppearance")])
            self.assertEqual(client.bodies_file(got), "1_AAppearance\tbodies\\1_AAppearance.bundle\t10101_YiSang_BaseAppearance\n")

    def test_a_ruina_character_stands_as_tall_as_its_entry_says(self):
        from limbus_skins import lor
        self.assertEqual(lor.stands({}, 4.0), 4.0)
        self.assertEqual(lor.stands({"scale": 0.5}, 4.0), 2.0)
        self.assertEqual(lor.stands({"stands": 1.1}, 6.49), 1.1)

    def test_a_records_own_fields_do_not_stop_its_other_edits(self):
        from limbus_skins import helpers
        x = {"id": 5, "a": 1}
        helpers.set_fields(x, {"appearance": "5_XAppearance"})
        self.assertEqual(x["appearance"], "5_XAppearance")


class OwnBodyAndItsSkin(unittest.TestCase):
    def test_an_identity_with_a_body_and_skills_of_its_own_stands_for_its_character(self):
        from limbus_skins import cli
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d, "XDG_DATA_HOME": d, "APPDATA": d, "LOCALAPPDATA": d,
                                                                                "LIMBUS_SKINS_PATCHLC": ""}), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main(["on", "binah", "binah-id", "kali", "kali-id"])
            on = cli._state()[0]["on"]
        self.assertEqual(sorted(on), ["binah-id", "kali-id"])           # the sinners keep their own identities

    def test_such_an_identity_carries_its_characters_kit_itself(self):
        from limbus_skins import build
        sk = build.catalog()
        for key in ("binah-id", "kali-id", "chesed-id"):
            self.assertTrue(sk[key]["own_body"] and sk[key]["own_skills"])
        for key in ("binah-id", "kali-id"):                                   # no kit of its own: its character's
            self.assertEqual(sk[key]["moves"], sk[build.character(sk, key)]["moves"])
        own = sk["chesed-id"]["moves"]                                         # a kit of its own wins: its floor's pages
        self.assertTrue(all("lor_dice" in m for m in own.values()) and own != sk[build.character(sk, "chesed-id")]["moves"])
        self.assertTrue(all(m.get("onHit") or m.get("onUse") for m in own.values()))   # every skill does something
        for key in ("binah-id", "kali-id", "chesed-id"):
            self.assertEqual(sk[key]["identity"] // 100, sk[key]["base"] // 100)       # a new id in its sinner's range
        for key in ("binah-id", "kali-id", "chesed-id"):
            self.assertNotEqual(sk[key]["identity"], sk[build.character(sk, key)]["identity"])

    def test_its_skills_are_new_ids_copies_of_its_bases(self):
        from limbus_skins import helpers
        base = {"id": 10901, "attributeList": "[{'skillId': 1090101, 'number': 3}, {'skillId': 1090102, 'number': 2}, {'skillId': 1000001, 'number': 1}]",
                "defenseSkillIDList": [1090104]}
        ids = helpers.own_skill_ids(base, 10993, {1090101, 1090102, 1090104, 1000001})
        self.assertEqual(ids, {1090101: 1099301, 1090102: 1099302, 1000001: 1099391, 1090104: 1099304})
        with self.assertRaises(ValueError):
            helpers.own_skill_ids(base, 10993, {1099302})
        self.assertEqual(helpers.with_own_skills(base, ids),
                         {"attributeList": [{"skillId": 1099301, "number": 3}, {"skillId": 1099302, "number": 2}, {"skillId": 1099391, "number": 1}],
                          "defenseSkillIDList": [1099304]})
        rec = {"id": 1090101, "skillData": [{"gaksungLevel": 1, "coinList": [{}]}, {"gaksungLevel": 3}]}
        copy_ = helpers.own_skill(rec, 1099301)
        self.assertEqual((copy_["id"], copy_["textID"], copy_["skillData"][0]["iconID"]), (1099301, 1090101, 1090101))
        self.assertNotIn("textID", helpers.own_skill(rec, 1099301, own_text=True))
        self.assertNotIn("iconID", rec["skillData"][0])                               # the base's record is untouched
        self.assertEqual(helpers.own_skill(dict(rec, textID=5, skillData=[{"iconID": "x_4"}]), 7)["skillData"][0]["iconID"], "x_4")
        skin = helpers.clone_record("skin", {"skinId": 10901, "skillDataList": [{"skillId": 1090101, "skillMotion": "S1"}]}, 10901, 10993,
                                    skill_ids=ids)
        self.assertEqual(skin, {"skinId": 10993, "skillDataList": [{"skillId": 1099301, "skillMotion": "S1"}]})


class NewRecords(unittest.TestCase):
    """helpers.new_records: a fight's enemies of its own, copies of the game's."""

    RECS = [{"id": 70070, "appearance": "90070_TCorpAppearance", "hp": {"defaultStat": 67}, "passiveSet": {"passiveIdList": [1]}},
            {"id": 90088, "appearance": "90088_Fishman_CruiseAppearance", "hp": "{'defaultStat': 55}", "passiveSet": "{'passiveIdList': [2]}"}]

    def test_a_new_enemy_is_a_copy_of_its_base_with_its_edits_and_the_base_is_untouched(self):
        from limbus_skins import helpers
        recs = copy.deepcopy(self.RECS)
        out = helpers.new_records("enemy", recs, {98801: {"=clone": 70070, "hp": {"defaultStat": 300}, "+passives": [817402]},
                                                  70070: {"hp": {"defaultStat": 1}}})
        self.assertEqual(out, [{"id": 98801, "appearance": "90070_TCorpAppearance", "hp": {"defaultStat": 300},
                                "passiveSet": {"passiveIdList": [1, 817402]}}])
        self.assertEqual(recs, self.RECS)

    def test_a_rule_edits_every_record_that_has_the_field_and_mixes_with_new_ones(self):
        # all-sinners: {"*": {"participantInfo.max": 12}} next to "=clone" records in one table
        from limbus_skins import helpers
        recs = [{"id": 1, "participantInfo": {"min": 1, "max": 5}}, {"id": 2, "participantInfo": "{'min': 1, 'max': 7}"},
                {"id": 3, "waveList": []}]
        edits = {helpers.EVERY: {"participantInfo.max": 12}, 9: {"=clone": 1}}
        self.assertEqual([r["id"] for r in helpers.new_records("battle-story", recs, edits)], [9])
        rule = edits[helpers.EVERY]
        for r in recs:                                       # as rewrite_static's edit() applies it
            fields = {k: v for k, v in rule.items() if isinstance(helpers.lit(r.get(k.split(".")[0]) or None), dict)}
            helpers.set_fields(r, fields)
        self.assertEqual(recs[0]["participantInfo"], {"min": 1, "max": 12})
        self.assertEqual(recs[1]["participantInfo"], "{'min': 1, 'max': 12}")
        self.assertNotIn("participantInfo", recs[2])

    def test_a_field_kept_as_text_stays_text(self):
        from limbus_skins import helpers
        out = helpers.new_records("enemy", copy.deepcopy(self.RECS), {98802: {"=clone": 90088, "+passives": [817402]}})
        self.assertEqual(out[0]["passiveSet"], "{'passiveIdList': [2, 817402]}")
        self.assertEqual(out[0]["id"], 98802)

    def test_a_field_inside_a_field_is_set_and_the_rest_of_it_kept(self):
        from limbus_skins import helpers
        rec = {"id": 18, "daysofweek": ["FRI", "SAT", "SUN"], "uiConfig": {"tabLabel": "FRI\nSAT", "title": "mainui_exp_dungeon_18_title"}}
        helpers.set_fields(rec, {"daysofweek": ["MON", "SUN"], "uiConfig.tabLabel": "TWI\nLIGHT"})
        self.assertEqual(rec, {"id": 18, "daysofweek": ["MON", "SUN"],
                               "uiConfig": {"tabLabel": "TWI\nLIGHT", "title": "mainui_exp_dungeon_18_title"}})
        text = {"id": 1, "uiConfig": "{'tabLabel': 'FRI', 'title': 't'}"}            # kept as text: stays text
        helpers.set_fields(text, {"uiConfig.tabLabel": "X"})
        self.assertEqual(text["uiConfig"], "{'tabLabel': 'X', 'title': 't'}")
        for bad in ({"nothing.tabLabel": "X"}, {"id.x": 1}, {"uiConfig.a.b": 1}):
            with self.assertRaises(ValueError, msg=str(bad)):
                helpers.set_fields({"id": 1, "uiConfig": {"tabLabel": "FRI"}}, bad)

    def test_twilight_is_open_every_day(self):
        from limbus_skins import build
        t = build.catalog()["twilight"]
        stage = next(iter(t["records"]["battle-exp-dungeon"]))
        self.assertEqual(set(t["records"]["exp-dungeon"]), {stage})                  # the stage it rewrites, no other
        self.assertEqual(sorted(t["records"]["exp-dungeon"][stage]["daysofweek"]),
                         sorted(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]))  # the game opens it FRI, SAT, SUN only
        self.assertEqual(t["music"]["dungeon"], int(stage))

    def test_a_base_kept_in_another_file_is_that_files_and_a_taken_id_is_refused(self):
        from limbus_skins import helpers
        self.assertEqual(helpers.new_records("enemy", copy.deepcopy(self.RECS), {98803: {"=clone": 71007}}), [])
        with self.assertRaises(ValueError):
            helpers.new_records("enemy", copy.deepcopy(self.RECS), {90088: {"=clone": 70070}})

    def test_twilights_birds_are_enemies_of_their_own(self):
        from limbus_skins import build
        t = build.catalog()["twilight"]
        new = {int(i): f for i, f in t["records"]["enemy"].items()}
        self.assertTrue(all("=clone" in f for f in new.values()))       # no enemy of the game's is changed
        staged = {u["unitID"] for w in t["records"]["battle-exp-dungeon"]["18"]["waveList"] for u in w["unitList"]}
        self.assertEqual(staged, set(new))
        cast = {l["unit"] for l in t["story"] if l["when"] in ("half", "one")}
        self.assertEqual(cast, {i for i, f in new.items() if 817402 in f.get("+passives", [])})   # who can't die: who the story waits for
        self.assertEqual(set(map(int, t["enemy_names"])), set(new))

    def test_every_fight_has_a_stage_of_its_own_and_enemies_of_its_own(self):
        # friend, 2026-11-18: "build a nothing there and whitenight boss and also improve the apocalypse bird boss"
        from limbus_skins import build
        sk = build.catalog()
        fights = {k: s for k, s in sk.items() if s["kind"] == "fight"}
        self.assertLessEqual({"twilight", "nothing-there", "whitenight"}, set(fights))
        stages, enemies = {}, {}
        for k, s in fights.items():
            for st in s["records"]["battle-exp-dungeon"]:
                self.assertNotIn(st, stages, f"{k} and {stages.get(st)} rewrite one stage")
                stages[st] = k
                self.assertEqual(set(s["records"]["exp-dungeon"]), {st})
            for e in s["records"]["enemy"]:
                self.assertNotIn(e, enemies, f"{k} and {enemies.get(e)} make one enemy")
                enemies[e] = k
            for e, kit in (s.get("kits") or {}).items():
                self.assertIn(e, s["records"]["enemy"], f"{k}: a kit for an enemy it doesn't make")
                self.assertLessEqual(set(kit.get("pages") or {}), set(kit.get("moves") or {}), k)
            for app, body in (s.get("bodies") or {}).items():          # one body redrawn by one fight
                others = [x for x, o in fights.items() if x != k and app in (o.get("bodies") or {})]
                self.assertEqual(others, [], f"{k}: {app}")

    def test_a_fights_enemies_get_their_own_skills_and_pages(self):
        # the kits of the fights, built against a stand-in game: own skill ids, the moves' coins, words and pages
        from limbus_skins import build, helpers
        sk = build.catalog()
        for key in ("nothing-there", "whitenight", "twilight"):
            s = sk[key]
            for e, kit in s["kits"].items():
                base = s["records"]["enemy"][e]["=clone"]
                for n, page in (kit.get("pages") or {}).items():
                    self.assertTrue(re.fullmatch(r"[A-Za-z0-9_]{1,60}", page), page)
                    self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(build.__file__), "data", "art", "pages", page + ".png"))
                                    or not os.path.isdir(os.path.join(os.path.dirname(build.__file__), "data", "art", "pages")), page)
                for n, m in kit["moves"].items():
                    self.assertIn(m["attributeType"], helpers.SINS, (key, e, n))
                    self.assertIn(m["atkType"], helpers.ATK, (key, e, n))
                self.assertTrue(base, (key, e))

    def test_a_page_burns_away_to_nothing(self):
        from PIL import Image
        from limbus_skins import pages
        art = Image.new("RGB", (410, 310), (200, 30, 30))
        face = pages.face(art, "SCARLET")
        frames = pages.burn(face)
        self.assertEqual(len(frames), pages.FRAMES)
        self.assertTrue(all(f.size == frames[0].size for f in frames))
        alpha = [sum(f.getchannel("A").getdata()) for f in frames]
        self.assertEqual(alpha[0], max(alpha))                               # whole first
        self.assertLess(alpha[-1], alpha[0] * 0.03)                          # then nearly nothing but embers
        self.assertTrue(all(b <= a * 1.02 for a, b in zip(alpha[1:], alpha[2:])), alpha)   # less of it each frame
        with tempfile.TemporaryDirectory() as tmp:                           # written as the patch reads it
            from limbus_skins import build
            lines = build.page_frames({"_pages": {9880701: ("no_such_art", "VIOLET")}}, tmp)
            self.assertEqual(lines, [])                                      # art not shipped: no page, no failure
            with self.assertRaises(ValueError):
                build.page_frames({"_pages": {9880701: ("../x", "VIOLET")}}, tmp)

    def test_a_story_line_that_isnt_one_is_refused(self):
        from limbus_skins import build
        for line in ({"when": "later", "picture": "a"}, {"when": "half", "picture": "a"}, {"when": "start", "unit": 5, "picture": "a"},
                     {"when": "start", "picture": "../a"}, {"when": "start", "picture": "no-such-picture"}):
            with self.assertRaises(ValueError, msg=str(line)), tempfile.TemporaryDirectory() as tmp:
                build.story_pictures({"key": "twilight", "story": [line]}, tmp)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(build.story_pictures({"key": "twilight"}, tmp), [])
            self.assertEqual(os.listdir(tmp), [])


class ClientPatch(unittest.TestCase):
    """limbus_skins.client: the client patch's settings for what was swapped in."""
    INI = "[myosotis]\ntoken=my-save\nserver=http://127.0.0.1:3000/\nlog_level=1\n"
    TEXTS = {"1041001": {"name": "Level Slash", "desc": "", "flavor": "line one\nline two",
                         "coins": ["[OnSucceedAttack] Inflict 3 [Laceration]", ""]},
             "1041005": {"desc": "a\ttab and a back\\slash", "flavor": "", "coins": []}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        for key, man in (("helpers", {"green_skills": [9], "skill_texts": self.TEXTS, "identities": [
                              {"id": 10999, "base": 10917, "folder": 10911, "name": "Dulcinea"},
                              {"id": 10895, "base": 10808, "folder": 10808, "name": "Captain\tAhab"},
                              {"id": "x", "folder": 1}, {"id": 10799, "base": 10710}]}),
                         ("greencliff", {"shift": {"from": "10701_", "to": "10710_Heathclif_WHuntAppearance", "hp": 50, "skill": 1070103}}),
                         ("other", {"shift": {"from": "A_Appearance", "to": "B_Appearance", "hp": 30}}),
                         ("broken", {"shift": {"from": "x y", "to": "B", "hp": 300}})):
            os.makedirs(os.path.join(self.data, "built", key))
            with open(os.path.join(self.data, "built", key, "manifest.json"), "w") as f:
                json.dump(man, f)
        self.pkg = os.path.join(self.tmp.name, "LimbusPrivateServer")
        for d in ("client", "join"):
            os.makedirs(os.path.join(self.pkg, d))
            with open(os.path.join(self.pkg, d, "myosotis.ini"), "w", newline="") as f:
                f.write(self.INI if d == "client" else self.INI.replace("\n", "\r\n"))
        self.client = os.path.join(self.pkg, "client")

    def tearDown(self):
        self.tmp.cleanup()

    def applied(self, *mods):
        with open(os.path.join(self.data, "session.json"), "w") as f:
            json.dump({"files": [], "folders": [], "mods": list(mods)}, f)

    def read(self, *parts):
        with open(os.path.join(self.pkg, *parts), encoding="utf-8", newline="") as f:
            return f.read()

    def test_skilltext_has_a_line_per_field_in_the_patchs_format(self):
        from limbus_skins import client
        self.assertEqual(client.skilltext_file(self.TEXTS).splitlines(), [
            "1041001\tname\tLevel Slash", "1041001\tdesc\t", "1041001\tflavor\tline one\\nline two",
            "1041001\tcoin1\t[OnSucceedAttack] Inflict 3 [Laceration]", "1041001\tcoin2\t",
            "1041005\tdesc\ta\\ttab and a back\\\\slash", "1041005\tflavor\t"])
        self.assertEqual(client.skilltext_file({}), "")

    def test_a_key_is_replaced_in_place_or_added_to_the_section(self):
        from limbus_skins import client
        once = client.set_key(self.INI, "green_coin_skills", "1")
        twice = client.set_key(once, "green_coin_skills", "2,3")
        self.assertEqual(twice.count("green_coin_skills="), 1)
        self.assertIn("green_coin_skills=2,3\n", twice)
        self.assertTrue(twice.startswith("[myosotis]\ntoken=my-save\n"))
        self.assertIn("hp_shift=x\r\n", client.set_key(self.INI.replace("\n", "\r\n"), "hp_shift", "x"))

    def test_configure_sets_what_was_applied_and_off_clears_it(self):
        from limbus_skins import client
        self.applied("helpers", "greencliff", "broken")
        client.configure(self.client, self.data)
        ini = self.read("client", "myosotis.ini")
        self.assertIn("green_coin_skills=9\n", ini)
        self.assertIn("hp_shift=10701_>10710_Heathclif_WHuntAppearance@50,10701_>10710_Heathclif_WHuntAppearance#1070103\n", ini)
        self.assertIn("1041001\tname\tLevel Slash\n", self.read("client", "skilltext.txt"))
        self.assertEqual(self.read("client", "allies.txt"), "10799\t10710\t\n10895\t10808\tCaptain\\tAhab\n10999\t10911\tDulcinea\n")
        client.configure(self.client, self.data, on=False)
        ini = self.read("client", "myosotis.ini")
        self.assertIn("green_coin_skills=\n", ini)
        self.assertIn("hp_shift=\n", ini)
        self.assertEqual(self.read("client", "skilltext.txt"), "")
        self.assertEqual(self.read("client", "allies.txt"), "")          # no ally borrows a card once it's over
        self.assertEqual(os.listdir(os.path.join(self.client, "covers")), [])

    def test_the_patch_gets_each_identitys_title_and_its_folders_own_text(self):
        from limbus_skins import client
        with open(os.path.join(self.data, "built", "helpers", "manifest.json")) as f:
            man = json.load(f)
        man["identities"][0].update(title="The Princess of\nLa Manchaland", folder_title="Thumb\nCapo", folder_name="Rodion")
        with open(os.path.join(self.data, "built", "helpers", "manifest.json"), "w") as f:
            json.dump(man, f)
        with open(os.path.join(self.data, "built", "names.json"), "w") as f:
            json.dump({"names": [{"skin": "greencliff", "identity": 10701, "name": "Greencliff", "title": "",
                                  "folder_title": "LCB\nSinner", "folder_name": "Heathcliff"}]}, f)
        self.applied("helpers", "greencliff")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "allynames.txt"),
                         "10701\tLCB\\nSinner\tHeathcliff\t\n"
                         "10999\tThumb\\nCapo\tRodion\tThe Princess of\\nLa Manchaland\n")
        self.assertIn("10999\t10911\tDulcinea\n", self.read("client", "allies.txt"))   # unchanged for an older patch
        client.configure(self.client, self.data, on=False)
        self.assertEqual(self.read("client", "allynames.txt"), "")

    def test_a_fights_music_goes_beside_the_patch_only_from_this_machine(self):
        from limbus_skins import client
        os.makedirs(os.path.join(self.data, "built", "twilight"))
        with open(os.path.join(self.data, "built", "twilight", "manifest.json"), "w") as f:
            json.dump({"kind": "fight", "skins": ["twilight"], "files": [],
                       "music": {"request": "EnterExpDungeon", "dungeon": 18, "file": "twilight.ogg"}}, f)
        os.makedirs(os.path.join(self.data, "built", "quiet"))
        with open(os.path.join(self.data, "built", "quiet", "manifest.json"), "w") as f:
            json.dump({"kind": "fight", "skins": ["quiet"], "files": [],
                       "music": {"request": "EnterExpDungeon", "dungeon": 17, "file": "nobody-has-it.ogg"}}, f)
        os.makedirs(os.path.join(self.data, "music"))
        with open(os.path.join(self.data, "music", "twilight.ogg"), "wb") as f:
            f.write(b"OggS song")
        self.applied("twilight", "quiet")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "fights.txt"), "EnterExpDungeon\t18\tmusic\\twilight.ogg\n")
        self.assertEqual(os.listdir(os.path.join(self.client, "music")), ["twilight.ogg"])
        client.configure(self.client, self.data, on=False)
        self.assertEqual(self.read("client", "fights.txt"), "")
        self.assertEqual(os.listdir(os.path.join(self.client, "music")), [])

    def test_a_fight_with_a_second_phase_names_its_second_track(self):
        from limbus_skins import client
        os.makedirs(os.path.join(self.data, "built", "twilight"))
        os.makedirs(os.path.join(self.data, "music"))
        for name in ("twilight-1.ogg", "twilight-2.ogg"):
            with open(os.path.join(self.data, "music", name), "wb") as f:
                f.write(b"OggS")

        def built(second):
            with open(os.path.join(self.data, "built", "twilight", "manifest.json"), "w") as f:
                json.dump({"kind": "fight", "skins": ["twilight"], "files": [],
                           "music": {"request": "EnterExpDungeon", "dungeon": 18, "file": "twilight-1.ogg", "second": second}}, f)
            self.applied("twilight")
            client.configure(self.client, self.data)
            return self.read("client", "fights.txt"), sorted(os.listdir(os.path.join(self.client, "music")))
        self.assertEqual(built({"file": "twilight-2.ogg", "at": 182000}),
                         ("EnterExpDungeon\t18\tmusic\\twilight-1.ogg\tmusic\\twilight-2.ogg\t182000\n", ["twilight-1.ogg", "twilight-2.ogg"]))
        for bad in ({"file": "not-here.ogg", "at": 5}, {"file": "../x.ogg"}, {"file": "twilight-2.ogg", "at": "soon"}, "junk"):
            self.assertEqual(built(bad), ("EnterExpDungeon\t18\tmusic\\twilight-1.ogg\n", ["twilight-1.ogg"]), bad)

    def test_a_fights_story_and_its_enemies_names_go_beside_the_patch(self):
        from limbus_skins import client
        story = os.path.join(self.data, "built", "twilight", "story")
        os.makedirs(story)
        for name in ("twilight-forest.pic", "twilight-big.pic"):
            with open(os.path.join(story, name), "wb") as f:
                f.write(b"LSR4")
        with open(os.path.join(self.data, "built", "twilight", "manifest.json"), "w") as f:
            json.dump({"kind": "fight", "skins": ["twilight"], "files": [],
                       "enemy_names": {"98801": "Big  Bird", "x": "Nobody", "98802": "", "98803": 5},
                       "story": [{"when": "start", "unit": 0, "file": "twilight-forest.pic"},
                                 {"when": "half", "unit": 98801, "file": "twilight-big.pic"},
                                 {"when": "one", "unit": 98801, "file": "not-built.pic"},
                                 {"when": "one", "unit": 0, "file": "twilight-big.pic"},
                                 {"when": "fuse", "unit": 7, "file": "twilight-big.pic"},
                                 {"when": "later", "unit": 0, "file": "twilight-big.pic"},
                                 {"when": "half", "unit": 98801, "file": "..\\myosotis.ini"}, "junk"]}, f)
        self.applied("twilight")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "story.txt"),
                         "start\t0\tstory\\twilight-forest.pic\nhalf\t98801\tstory\\twilight-big.pic\n")
        self.assertEqual(sorted(os.listdir(os.path.join(self.client, "story"))), ["twilight-big.pic", "twilight-forest.pic"])
        self.assertEqual(self.read("client", "enemies.txt"), "98801\tBig Bird\n")
        self.applied()                                   # the fight off: nothing of it stays
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "story.txt"), "")
        self.assertEqual(self.read("client", "enemies.txt"), "")
        self.assertEqual(os.listdir(os.path.join(self.client, "story")), [])

    def test_a_fights_burning_pages_go_beside_the_patch(self):
        from limbus_skins import client
        story = os.path.join(self.data, "built", "whitenight", "story")
        os.makedirs(story)
        for k in range(3):
            with open(os.path.join(story, f"page-9880701-{k}.pic"), "wb") as f:
                f.write(b"LSD5")
        with open(os.path.join(story, "page-9880702-0.pic"), "wb") as f:
            f.write(b"LSD5")
        with open(os.path.join(self.data, "built", "whitenight", "manifest.json"), "w") as f:
            json.dump({"kind": "fight", "skins": ["whitenight"], "files": [],
                       "pages": [{"skill": 9880701, "frames": 3, "stem": "page-9880701"},
                                 {"skill": 9880702, "frames": 2, "stem": "page-9880702"},     # a frame missing
                                 {"skill": 9880703, "frames": 1, "stem": "..\\myosotis"},
                                 {"skill": "x", "frames": 1, "stem": "page-1"}, "junk"]}, f)
        self.applied("whitenight")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "cards.txt"), "9880701\t3\tstory\\page-9880701\n")
        self.assertEqual(sorted(os.listdir(os.path.join(self.client, "story"))),
                         ["page-9880701-0.pic", "page-9880701-1.pic", "page-9880701-2.pic"])
        self.applied()
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "cards.txt"), "")
        self.assertEqual(os.listdir(os.path.join(self.client, "story")), [])

    def test_a_fights_boss_entrances_go_beside_the_patch(self):
        from limbus_skins import client
        story = os.path.join(self.data, "built", "whitenight", "story")
        os.makedirs(story)
        with open(os.path.join(story, "intro-98807.pic"), "wb") as f:
            f.write(b"LSR4")
        with open(os.path.join(self.data, "built", "whitenight", "manifest.json"), "w") as f:
            json.dump({"kind": "fight", "skins": ["whitenight"], "files": [],
                       "intros": [{"unit": 98807, "file": "intro-98807.pic"}, {"unit": 5, "file": "intro-5.pic"},
                                  {"unit": 6, "file": "..\\myosotis.ini"}, "junk"]}, f)
        self.applied("whitenight")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "intros.txt"), "98807\tstory\\intro-98807.pic\n")
        self.assertEqual(os.listdir(os.path.join(self.client, "story")), ["intro-98807.pic"])
        self.applied()
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "intros.txt"), "")

    def test_a_body_with_no_death_dies_as_its_sinner_while_its_skin_is_in_the_session(self):
        from limbus_skins import client
        with open(os.path.join(self.data, "built", "helpers", "manifest.json")) as f:
            man = json.load(f)
        man["skins"] = ["kim-id", "whole-id"]
        man["deaths"] = {"kim-id": {"from": "400040_LCD_KimSatGatAppearance", "to": "10501_Meursault_BaseAppearance"},
                         "gone-id": {"from": "X_Appearance", "to": "10101_Yisang_BaseAppearance"},   # not this session
                         "odd-id": {"from": "x y", "to": "B"}, "same-id": {"from": "A", "to": "A"}}
        with open(os.path.join(self.data, "built", "helpers", "manifest.json"), "w") as f:
            json.dump(man, f)
        self.applied("helpers", "greencliff")
        client.configure(self.client, self.data)
        ini = self.read("client", "myosotis.ini")
        self.assertIn("hp_shift=10701_>10710_Heathclif_WHuntAppearance@50,10701_>10710_Heathclif_WHuntAppearance#1070103,"
                      "400040_LCD_KimSatGatAppearance>10501_Meursault_BaseAppearance!\n", ini)
        self.applied("greencliff")                                       # helpers not swapped in: no deaths
        client.configure(self.client, self.data)
        self.assertNotIn("!", self.read("client", "myosotis.ini").split("hp_shift=")[1].splitlines()[0])

    def test_a_skin_that_makes_an_identity_someone_else_shows_that_characters_name(self):
        from limbus_skins import client
        with open(os.path.join(self.data, "built", "names.json"), "w") as f:
            json.dump({"names": [{"skin": "greencliff", "identity": 10701, "name": "Greencliff"},
                                 {"skin": "matthias", "identity": 11109, "name": "The Middle Nursefather Matthias"},
                                 {"skin": "greencliff", "identity": "x", "name": "bad"}, {"skin": "greencliff", "identity": 5, "name": " "}]}, f)
        self.applied("helpers", "greencliff")                            # matthias isn't in this session
        client.configure(self.client, self.data)
        allies = self.read("client", "allies.txt")
        self.assertIn("10701\t10701\tGreencliff\n", allies)
        self.assertNotIn("11109", allies)
        self.assertIn("10999\t10911\tDulcinea\n", allies)                # an ally keeps its own name

    def test_an_allys_cover_goes_beside_the_patch_for_the_session(self):
        from limbus_skins import client
        pics = os.path.join(self.data, "built", "ahab-id.cover", "pics")
        os.makedirs(pics)
        with open(os.path.join(self.data, "built", "ahab-id.cover", "manifest.json"), "w") as f:
            json.dump({"kind": "cover", "identity": 10895, "files": ["10895_normal.dxt"]}, f)
        with open(os.path.join(pics, "10895_normal.dxt"), "wb") as f:
            f.write(b"jpg")
        self.applied("helpers")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "covers", "10895_normal.dxt"), "jpg")
        self.applied("greencliff")                                       # helpers not swapped in: no allies
        client.configure(self.client, self.data)
        self.assertEqual(os.listdir(os.path.join(self.client, "covers")), [])

    def test_the_title_pictures_go_beside_the_patch_and_leave_with_the_session(self):
        from PIL import Image
        from limbus_skins import client, title
        pic = os.path.join(self.tmp.name, "wide.png")
        Image.new("RGB", (3000, 1000), (40, 90, 40)).save(pic)
        title.save(pic, self.data)
        client.configure(self.client, self.data)
        self.assertEqual(os.listdir(os.path.join(self.client, "title")), ["background.dxt"])
        client.configure(self.client, self.data, on=False)
        self.assertEqual(os.listdir(os.path.join(self.client, "title")), [])

    def pfp(self, key, identity, body):
        d = os.path.join(self.data, "built", key + ".cover")
        os.makedirs(os.path.join(d, "pics"))
        name = f"{identity}_normal_profile.dxt"
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump({"kind": "cover", "pfp": True, "skins": [key], "identity": identity, "files": [name]}, f)
        with open(os.path.join(d, "pics", name), "wb") as f:
            f.write(body)

    def test_a_skins_pfp_is_shown_for_its_identity_only_while_that_skin_is_on(self):
        from limbus_skins import client
        self.pfp("red", 10101, b"red")
        self.pfp("blue", 10101, b"blue")                 # another skin of the same identity, off
        self.pfp("queen", 10312, b"queen")               # a helper skin: in the helpers build
        with open(os.path.join(self.data, "built", "helpers", "manifest.json")) as f:
            man = json.load(f)
        man["skins"] = ["queen"]
        with open(os.path.join(self.data, "built", "helpers", "manifest.json"), "w") as f:
            json.dump(man, f)
        self.applied("red")
        client.configure(self.client, self.data)
        self.assertEqual(self.read("client", "allies.txt"), "10101\t10101\t\n")   # no name: the game's stays
        self.assertEqual(sorted(os.listdir(os.path.join(self.client, "covers"))), ["10101_normal_profile.dxt"])
        self.assertEqual(self.read("client", "covers", "10101_normal_profile.dxt"), "red")
        self.applied("blue", "helpers")
        client.configure(self.client, self.data)
        allies = self.read("client", "allies.txt").splitlines()
        self.assertIn("10101\t10101\t", allies)
        self.assertIn("10312\t10312\t", allies)
        self.assertIn("10999\t10911\tDulcinea", allies)                          # the allies, as before
        self.assertEqual(self.read("client", "covers", "10101_normal_profile.dxt"), "blue")
        self.assertEqual(self.read("client", "covers", "10312_normal_profile.dxt"), "queen")
        client.configure(self.client, self.data, on=False)
        self.assertEqual(self.read("client", "allies.txt"), "")
        self.assertEqual(os.listdir(os.path.join(self.client, "covers")), [])

    def test_a_helpers_build_counts_only_when_it_was_swapped_in(self):
        from limbus_skins import client
        self.applied("greencliff")                     # helpers skipped (the game updated since it was built)
        client.configure(self.client, self.data)
        self.assertIn("green_coin_skills=\n", self.read("client", "myosotis.ini"))
        self.assertEqual(self.read("client", "skilltext.txt"), "")
        os.remove(os.path.join(self.data, "session.json"))      # no session at all
        client.configure(self.client, self.data)
        self.assertIn("hp_shift=\n", self.read("client", "myosotis.ini"))

    def test_the_client_folders_of_a_package_are_found_beside_its_launcher(self):
        from limbus_skins import client
        play = os.path.join(self.pkg, "play.sh")
        open(play, "w").close()
        self.assertEqual(client.folders_beside(play), [os.path.join(self.pkg, "client"), os.path.join(self.pkg, "join")])
        self.assertEqual(client.folders_beside(os.path.join(self.pkg, "not-a-file")), [])   # a command on PATH, say
        os.remove(os.path.join(self.pkg, "join", "myosotis.ini"))
        self.assertEqual(client.folders_beside(play), [os.path.join(self.pkg, "client")])

    def test_configure_leaves_a_folder_without_the_patch_alone(self):
        from limbus_skins import client
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertFalse(client.configure(empty, self.data))
        self.assertEqual(os.listdir(empty), [])


    def test_a_boss_body_needs_a_client_patch_with_bossbodypatch(self):
        from limbus_skins import client
        self.assertFalse(client.wears_bosses(self.client))                    # no DLL
        with open(os.path.join(self.client, "myosotis.dll"), "wb") as f:
            f.write(b"MZ...skilltext...hpshift...")                           # build 5
        self.assertFalse(client.wears_bosses(self.client))
        with open(os.path.join(self.client, "myosotis.dll"), "wb") as f:
            f.write(b"MZ...hpshift...bossbody...")
        self.assertTrue(client.wears_bosses(self.client))

class Stickman(unittest.TestCase):
    def _figure(self):
        """A 60x100 figure: a 30x30 head on a 40-wide body block with two 10-wide legs."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (80, 120), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([25, 10, 54, 39], fill=(200, 160, 120, 255))      # head
        d.rectangle([20, 40, 59, 79], fill=(40, 60, 200, 255))        # torso
        d.rectangle([22, 80, 31, 109], fill=(40, 40, 40, 255))        # legs
        d.rectangle([48, 80, 57, 109], fill=(40, 40, 40, 255))
        return img

    def test_the_head_stays_and_the_body_becomes_thin_lines(self):
        from limbus_skins import stick
        img = self._figure()
        out = stick.stickify(img, head_px=30, width_px=4)
        self.assertEqual(out.size, img.size)
        self.assertEqual(out.getpixel((40, 25)), img.getpixel((40, 25)))   # the head, untouched
        a = out.getchannel("A")
        torso_row = [x for x in range(80) if a.getpixel((x, 60)) > 0]
        self.assertTrue(torso_row and len(torso_row) <= 10)                  # a 40-wide torso is a stick now
        self.assertEqual(a.getpixel((21, 60)), 0)                            # its old edge is gone
        self.assertTrue(any(a.getpixel((x, 100)) for x in range(18, 36)))    # the left leg is still there...
        self.assertTrue(any(a.getpixel((x, 100)) for x in range(44, 62)))    # ...and the right
        self.assertTrue(len([x for x in range(80) if a.getpixel((x, 100))]) <= 16)

    def test_a_bat_held_over_the_head_isnt_taken_for_it(self):
        from PIL import ImageDraw
        from limbus_skins import stick
        img = self._figure()
        ImageDraw.Draw(img).line([(40, 0), (75, 0), (75, 14)], fill=(0, 0, 0, 255), width=3)   # thin, above the head
        out = stick.stickify(img, head_px=30, width_px=4, skip_px=8)
        self.assertEqual(out.getpixel((40, 25)), img.getpixel((40, 25)))   # the head is still drawn
        self.assertEqual(out.getpixel((40, 35)), img.getpixel((40, 35)))   # down to its own chin

    def test_a_wobbly_limb_is_drawn_straight(self):
        from limbus_skins import stick
        wobble = [(x, 10 + (x % 2)) for x in range(30)]                      # a line that jitters by a pixel
        self.assertEqual(stick.straighten(wobble), [(0, 10), (29, 11)])
        bent = [(x, 0) for x in range(10)] + [(9, y) for y in range(1, 10)]   # an elbow stays an elbow
        self.assertEqual(stick.straighten(bent), [(0, 0), (9, 0), (9, 9)])

    def test_spurs_and_lone_streaks_are_dropped(self):
        from limbus_skins import stick
        g = [[0] * 40 for _ in range(12)]
        for x in range(2, 38):
            g[5][x] = 1                                                      # a long limb
        g[4][20] = g[3][20] = 1                                              # a 2-pixel spur off it
        g[10][2] = g[10][3] = g[10][4] = 1                                   # a short streak on its own
        cells = {p for path in stick.limbs(g, 6) for p in path}
        self.assertTrue({(x, 5) for x in range(2, 38)} <= cells)
        self.assertFalse({(20, 3), (2, 10), (3, 10), (4, 10)} & cells)

    def test_an_arm_that_touches_the_head_becomes_a_stick(self):
        from PIL import ImageDraw
        from limbus_skins import stick
        img = self._figure()
        ImageDraw.Draw(img).rectangle([55, 20, 78, 22], fill=(250, 0, 250, 255))   # thin, off the head's side
        out = stick.stickify(img, head_px=30, width_px=2)
        self.assertEqual(out.getpixel((40, 25)), img.getpixel((40, 25)))   # the head is kept
        self.assertNotEqual(out.getpixel((74, 21))[:3], (250, 0, 250))      # the arm isn't, as drawn

    def test_an_empty_picture_comes_back_as_it_was(self):
        from PIL import Image
        from limbus_skins import stick
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        self.assertEqual(stick.stickify(img, 5, 2).tobytes(), img.tobytes())

    def test_thinning_keeps_a_line_one_pixel_wide(self):
        from limbus_skins import stick
        grid = [[1 if 2 <= x <= 6 and 1 <= y <= 18 else 0 for x in range(9)] for y in range(20)]
        thin = stick.thin(grid)
        rows = [sum(r) for r in thin]
        self.assertTrue(all(c <= 2 for c in rows))
        self.assertGreater(sum(rows), 8)                                     # still a long line


class LorText(unittest.TestCase):
    TABLES = [
        ("CardInfo_test", b"""<?xml version="1.0" encoding="utf-8"?><DiceCardXmlRoot>
          <Card ID="1"><Name>x</Name><Rarity>Rare</Rarity><Spec Range="Near" Cost="2" /><Script>pageScript</Script>
            <BehaviourList><Behaviour Min="5" Dice="9" Type="Atk" Detail="Slash" Script="dieScript" />
              <Behaviour Min="5" Dice="8" Type="Atk" Detail="Hit" Script="dieScript" /></BehaviourList></Card>
          <Card ID="2"><Rarity>Unique</Rarity><Spec Range="FarArea" Cost="6" /><Script></Script>
            <BehaviourList><Behaviour Min="28" Dice="42" Type="Atk" Detail="Penetrate" Script="other" /></BehaviourList></Card>
          <Card ID="3"><Rarity>Rare</Rarity><Spec Range="Near" Cost="1" />
            <BehaviourList><Behaviour Min="4" Dice="8" Type="Standby" Detail="Slash" /></BehaviourList></Card>
          </DiceCardXmlRoot>"""),
        ("EN_BattleCards", b"""<r><cardDescList><BattleCardDesc ID="1"><LocalizedName>Page One</LocalizedName><Ability /></BattleCardDesc>
          <BattleCardDesc ID="2"><LocalizedName>Page Two</LocalizedName></BattleCardDesc></cardDescList></r>"""),
        ("EN_BattleCardAbilities", b"""<r><BattleCardAbility ID="pageScript"><Desc>page ability</Desc></BattleCardAbility>
          <BattleCardAbility ID="dieScript"><Desc>die ability</Desc></BattleCardAbility>
          <BattleCardAbility ID="other"><Desc>other die ability</Desc></BattleCardAbility></r>"""),
        ("EN_PassiveDesc", b"""<r><PassiveDesc ID="9"><Name>Retort</Name><Desc>passive text</Desc></PassiveDesc></r>"""),
        ("EN_PassiveDesc", b"<r><broken"),
    ]

    def test_a_page_in_ruinas_own_words(self):
        from limbus_skins import lortext
        book = lortext.Book(self.TABLES)
        self.assertEqual(book.page(1), {"name": "Page One", "flavor": "Combat Page · Rare · Cost 2 · Slash 5-9, Blunt 5-8\n"
                                        "page ability\ndie ability"})
        self.assertEqual(book.page("2")["flavor"], "Combat Page · Unique · Cost 6 · Mass (Summation) · Pierce 28-42\n"
                                                   "other die ability")
        with self.assertRaises(lortext.LorTextError):
            book.page(3)                                    # a page with no English name
        self.assertEqual(book.passive(9), {"name": "Retort", "flavor": "Passive · passive text"})

    def test_texts_take_ruinas_words_and_keep_their_own(self):
        from limbus_skins import lortext
        book = lortext.Book(self.TABLES)
        out = lortext.resolve({"1": {"lor_card": 1}, "2": {"lor_card": 2, "name": "Mine"},
                               "defense": {"lor_passive": 9}, "3": {"name": "Plain"}}, book)
        self.assertEqual(out["1"]["name"], "Page One")
        self.assertEqual(out["2"]["name"], "Mine")
        self.assertTrue(out["2"]["flavor"].startswith("Combat Page · Unique"))
        self.assertEqual(out["defense"]["name"], "Retort")
        self.assertEqual(out["3"], {"name": "Plain"})

    def test_a_pages_dice(self):
        from limbus_skins import lortext
        book = lortext.Book(self.TABLES)
        self.assertEqual(book.dice(1), [(5, 9, "Atk", "Slash"), (5, 8, "Atk", "Hit")])
        self.assertEqual(book.dice(3, "Atk"), [])                      # a counter die isn't an attack
        with self.assertRaises(lortext.LorTextError):
            book.dice(77)

    def test_a_kit_is_built_with_ruinas_dice(self):
        from limbus_skins import build, lortext
        skins = {"k": {"key": "k", "kind": "kit", "identity": 10101, "moves": {"1": {"lor_dice": 1, "coins": [0, 0]}}}}
        pers = {10101: {"id": 10101, "attributeList": [{"skillId": 1, "number": 3}], "defenseSkillIDList": [2]}}
        with tempfile.TemporaryDirectory() as data, FakeGame(skins, pers) as g, \
                mock.patch.object(lortext, "shipped_book", return_value=None), \
                mock.patch.object(lortext, "read_tables", return_value=self.TABLES):   # nothing shipped: the install
            res = build.build(["k"], "game", "cache", data, log=lambda *a: None)
        self.assertTrue(res["k"][0], res)
        shell = g.written["skills"][1]["skillData"][0]
        self.assertEqual((shell["defaultValue"], [c["scale"] for c in shell["coinList"]], shell["atkType"]), (5, [2, 2], "SLASH"))
        with tempfile.TemporaryDirectory() as data, FakeGame(skins, pers), \
                mock.patch.object(lortext, "read_tables", side_effect=lortext.LorTextError("not installed")):
            res = build.build(["k"], "game", "cache", data, log=lambda *a: None)
        self.assertEqual(res["k"][0], False)                         # no Ruina: the kit isn't guessed
        self.assertIn("Library of Ruina", res["k"][1])

    def test_moves_take_ruinas_dice(self):
        # each hit as hard as Ruina's die: all tails its lowest, all heads its highest; the coin
        # count stays the one written (the animation's), its sin and effects stay the skin's own
        from limbus_skins import lortext
        tables = self.TABLES + [("CardInfo_def", b"""<r><Card ID="4"><Spec Cost="1" /><BehaviourList>
            <Behaviour Min="5" Dice="9" Type="Def" Detail="Guard" /><Behaviour Min="4" Dice="8" Type="Atk" Detail="Hit" />
            </BehaviourList></Card></r>""")]
        book = lortext.Book(tables)
        moves = {"1": {"lor_dice": 1, "coins": [20, 20], "attributeType": "CRIMSON", "onHit": {"buffKeyword": "X"}},
                 "4": {"lor_dice": 2, "coins": [15] * 9},
                 "defense": {"lor_dice": 4, "coins": [10]},
                 "2": {"atkType": "HIT", "defaultValue": 7, "coins": [3]}}
        out = lortext.resolve_moves(moves, book)
        self.assertEqual(out["1"], {"defaultValue": 5, "coins": [2, 2], "atkType": "SLASH", "attributeType": "CRIMSON",
                                    "onHit": {"buffKeyword": "X"}})
        self.assertEqual((out["4"]["defaultValue"], out["4"]["coins"], out["4"]["atkType"]),
                         (28, [1, 1, 1, 1, 2, 2, 2, 2, 2], "PENETRATE"))
        self.assertEqual((out["defense"]["defaultValue"], out["defense"]["coins"]), (5, [4]))   # its block die
        self.assertNotIn("atkType", out["defense"])
        self.assertEqual(out["2"], moves["2"])                        # written out: as it is
        self.assertTrue(lortext.uses_dice(moves))
        with self.assertRaises(lortext.LorTextError):
            lortext.resolve_moves(moves, None)                       # no Ruina installed: no guessing
        self.assertNotIn("lor_card", out["1"])
        self.assertTrue(lortext.uses_lor({"1": {"lor_card": 1}}))
        self.assertFalse(lortext.uses_lor({"1": {"name": "x"}}))


class Auras(unittest.TestCase):
    def _frames(self):
        """An idle frame and a skill frame of a 100px-tall body standing on its pivot."""
        from PIL import Image
        from limbus_skins import lor
        src, rendered = {}, {}
        for kind in ("idle", "S1"):
            f = lor.Frame(kind, Image.new("RGBA", (20, 100), (0, 0, 255, 255)), (0.5, 0.0), 100)
            src[kind] = [f]
            rendered[f.path_id] = (f.read().image, f.read(), (0.5, 0.0))
        return src, rendered

    def _bake(self, specs, sprites, src, rendered):
        from limbus_skins import repaint
        notes = []
        with mock.patch("limbus_skins.aura.textures", side_effect=lambda spec, locate: sprites[spec["style"]]):
            done = repaint._bake_aura(specs, None, src, rendered, notes)
        return done, notes

    STRIKE = {"style": "strike", "kinds": ["S1", "S2", "S3"], "size": 0.3}
    MIST = {"style": "mist", "count": 2, "size": 0.2}

    def test_strike_only_on_skill_frames(self):
        from PIL import Image
        src, rendered = self._frames()
        idle_before = rendered[src["idle"][0].path_id][0].tobytes()
        arc = Image.new("RGBA", (30, 10), (255, 0, 0, 255))
        done, _ = self._bake([self.STRIKE], {"strike": [arc]}, src, rendered)
        self.assertEqual(rendered[src["idle"][0].path_id][0].tobytes(), idle_before)
        self.assertNotEqual(rendered[src["S1"][0].path_id][0].size, (20, 100))
        self.assertEqual([d[0]["style"] for d in done], ["strike"])

    def test_strike_in_front_on_the_facing_side(self):
        from PIL import Image
        src, rendered = self._frames()
        arc = Image.new("RGBA", (30, 10), (255, 0, 0, 255))
        self._bake([self.STRIKE], {"strike": [arc]}, src, rendered)
        img, _, (px, py) = rendered[src["S1"][0].path_id]
        fx = px * img.width
        reds = [x for x in range(img.width) for y in range(img.height) if img.getpixel((x, y))[:3] == (255, 0, 0)]
        self.assertTrue(reds and min(reds) > fx - 10 and sum(reds) / len(reds) > fx)   # ahead of her, to the right

    def test_auras_compose_mist_then_strike(self):
        from PIL import Image
        from limbus_skins import aura
        src, rendered = self._frames()
        sprites = {"mist": [Image.new("RGBA", (40, 40), (90, 0, 0, 200))], "strike": [Image.new("RGBA", (30, 10), (255, 0, 0, 255))]}
        i = src["S1"][0].path_id
        img, sd, pv = rendered[i]
        a, pa = aura.bake(img, pv, sprites["mist"], self.MIST, 100, 1)          # S1 is the second frame: seed 1
        want, _ = aura.bake(a, pa, sprites["strike"], self.STRIKE, 100, 0)       # first frame the strike draws on
        self._bake([self.MIST, self.STRIKE], sprites, src, rendered)
        self.assertEqual(rendered[i][0].tobytes(), want.tobytes())

    def test_an_idle_rig_can_carry_a_thicker_swarm(self):
        from limbus_skins import aura
        spec = {"style": "swarm", "count": 8, "size": 0.14, "idle": {"count": 18, "size": 0.2}}
        idle = aura.idle_spec(spec)
        self.assertEqual((idle["count"], idle["size"], idle["style"]), (18, 0.2, "swarm"))
        self.assertEqual(len(aura._placements(idle, 2, 100.0, 0)), 18)
        self.assertEqual(len(aura._placements(spec, 2, 100.0, 0)), 8)          # the drawn frames keep theirs
        self.assertEqual(aura.idle_spec({"count": 3}), {"count": 3})

    def test_missing_donor_is_a_note(self):
        from limbus_skins import aura, repaint
        src, rendered = self._frames()
        before = {k: v[0].tobytes() for k, v in rendered.items()}
        notes = []
        with mock.patch("limbus_skins.aura.textures", side_effect=aura.AuraError("1079_Sancho has no Fx_X")):
            done = repaint._bake_aura([self.STRIKE], None, src, rendered, notes)
        self.assertEqual(done, [])
        self.assertTrue(any("no aura" in n and "Fx_X" in n for n in notes))
        self.assertEqual({k: v[0].tobytes() for k, v in rendered.items()}, before)

    def test_tint_makes_the_grey_background_clear(self):
        from PIL import Image
        from limbus_skins import aura
        img = Image.new("RGBA", (8, 8), (80, 80, 80, 255))
        img.putpixel((4, 4), (230, 230, 230, 255))
        out = aura.tint(img, (150, 0, 0))
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        self.assertEqual(out.getpixel((4, 4)), (150, 0, 0, 255))

    def test_bake_keeps_the_feet_on_the_pivot(self):
        from PIL import Image
        from limbus_skins import aura
        body = Image.new("RGBA", (20, 100), (0, 0, 255, 255))
        fly = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
        spec = {"style": "swarm", "count": 6, "size": 0.2}
        out, (px, py) = aura.bake(body, (0.5, 0.0), [fly], spec, 100, seed=3)
        self.assertGreater(out.width, body.width)                    # flies outside the body grow the canvas
        fx, fy = round(px * out.width), round((1 - py) * out.height)
        self.assertEqual(out.getpixel((fx, fy - 1)), (0, 0, 255, 255))   # just above the feet: still the body
        again, _ = aura.bake(body, (0.5, 0.0), [fly], spec, 100, seed=3)
        self.assertEqual(out.tobytes(), again.tobytes())             # the same frame bakes the same way


class Tints(unittest.TestCase):
    def test_colorize_goes_green_and_keeps_alpha_and_light(self):
        from PIL import Image
        from limbus_skins import tint
        img = Image.new("RGBA", (3, 1))
        img.putdata([(20, 20, 20, 255), (200, 40, 40, 128), (240, 240, 240, 0)])
        res = tint.colorize(img, {"hue": 120})
        out = [res.getpixel((x, 0)) for x in range(3)]
        self.assertEqual([p[3] for p in out], [255, 128, 0])
        dark, red = out[0], out[1]
        self.assertTrue(red[1] > red[0] and red[1] > red[2])        # green dominates
        lum = lambda p: 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]  # noqa: E731
        self.assertLess(lum(dark), lum(red))                        # light order kept

    def test_premultiplied_pages_stay_premultiplied(self):
        from PIL import Image
        from limbus_skins import tint
        img = Image.new("RGBA", (1, 1), (100, 0, 0, 100))          # premultiplied: rgb <= a
        p = tint.colorize(img, {"hue": 120}, pma=True).getpixel((0, 0))
        self.assertEqual(p[3], 100)
        self.assertTrue(all(c <= 100 for c in p[:3]))

    def test_a_shift_names_both_bodies_for_the_client_patch(self):
        from limbus_skins.build import shift_rule
        ids = {10701: {"appearance": "10701_Heathclif_BaseAppearance"}, 10710: {"appearance": "10710_Heathclif_WHuntAppearance"}}
        skin = {"identity": 10701, "shift": {"to": 10710, "hp": 50}}
        self.assertEqual(shift_rule(skin, ids), {"from": "10701_",          # every body of his (a Canto VI one too)
                                                 "to": "10710_Heathclif_WHuntAppearance", "hp": 50})
        self.assertIsNone(shift_rule({"identity": 10701}, ids))
        rec = {"attributeList": [{"skillId": 1070101}, {"skillId": 1070102}, {"skillId": 1070103}]}
        both = shift_rule({"identity": 10701, "shift": {"to": 10710, "hp": 50, "skill": "3"}}, ids, rec)
        self.assertEqual((both["hp"], both["skill"]), (50, 1070103))
        only = shift_rule({"identity": 10701, "shift": {"to": 10710, "skill": "1"}}, ids, rec)
        self.assertEqual((only.get("hp"), only["skill"]), (None, 1070101))
        with self.assertRaises(ValueError):
            shift_rule({"identity": 10701, "shift": {"to": 10710, "skill": "9"}}, ids, rec)
        with self.assertRaises(ValueError):
            shift_rule({"identity": 10701, "shift": {"to": 10710}}, ids, rec)       # neither hp nor skill
        for bad in ({"to": 99999, "hp": 50}, {"to": 10710, "hp": 0}, {"to": 10710, "hp": "50"}):
            with self.assertRaises(ValueError):
                shift_rule({"identity": 10701, "shift": bad}, ids)


if __name__ == "__main__":
    unittest.main()


class Motion(unittest.TestCase):
    """motion.py: Limbus-style motion from Ruina's still poses (friend, 2026-11-18: "high quality
    animations for the ids we added that make it feel like limbus and not library of ruina")."""

    def figure(self, w=60, h=120):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((w // 2 - 12, 0, w // 2 + 12, 24), fill=(20, 20, 20, 255))         # head
        d.rectangle((w // 2 - 8, 24, w // 2 + 8, h - 1), fill=(30, 30, 30, 255))     # body, feet at the bottom
        d.line((w // 2, 50, 0, 70), fill=(200, 200, 210, 255), width=4)               # a blade held forward (left)
        return img

    def test_every_motion_comes_out_with_its_tag_and_its_feet_on_the_ground(self):
        from limbus_skins import motion
        img = self.figure()
        still = [("Slash", img, (0.5, 0.0), 77.0), ("Hit", img, (0.5, 0.0), 77.0)]
        for kind in ("idle", "move", "gud", "miss", "dmg", "dead", "S1", "S2", "S3"):
            out = motion.animate(kind, still, sin="CRIMSON")
            self.assertTrue(out, kind)
            for name, pic, (px, py), tag in out:
                self.assertEqual(tag, 77.0, kind)
                self.assertTrue(-0.5 < px < 1.5 and -0.2 < py < 0.6, (kind, name, px, py))
                self.assertIsNotNone(pic.getbbox(), (kind, name))

    def test_a_skill_winds_up_strikes_lands_and_recovers(self):
        from limbus_skins import motion
        img = self.figure()
        out = motion.animate("S1", [("Slash", img, (0.5, 0.0), 1.0), ("Penetrate", img, (0.5, 0.0), 1.0)], sin="AZURE")
        names = [n for n, *_ in out]
        self.assertEqual(names, ["Slash_windup", "Slash_strike0", "Slash_impact0", "Penetrate_strike1",
                                 "Penetrate_impact1", "Penetrate_recover"])
        impact = dict((n, p) for n, p, *_ in out)["Slash_impact0"]
        blue = sum(1 for r, g, b, a in impact.getdata() if a > 100 and b > r + 60)
        self.assertGreater(blue, 20)                                 # its hit glows the skill's sin (Gloom: blue)
        self.assertGreater(impact.width, img.width)                 # drawn in front of the body

    def test_idle_breathes_and_a_motion_drawn_in_many_poses_keeps_them(self):
        from limbus_skins import motion
        img = self.figure()
        idle = motion.animate("idle", [("Default", img, (0.5, 0.0), 1.0)])
        self.assertEqual(len(idle), motion.IDLE_FRAMES)
        self.assertGreater(len({p.size for _, p, *_ in idle}), 1)     # it rises and settles
        many = [(f"Swing{i}", img, (0.5, 0.0), 1.0) for i in range(6)]
        out = motion.animate("S1", many)
        self.assertEqual(len(out), 6)                                # its own frames, one of them the impact
        self.assertEqual(sum(1 for n, *_ in out if n == "Swing3_m3"), 1)

    def test_what_a_pose_draws_names_its_hit(self):
        from limbus_skins import motion
        self.assertEqual(motion.attack_of("GreatSword_S10", motion.SLASH), motion.BLUNT)
        self.assertEqual(motion.attack_of("Penetrate", motion.SLASH), motion.PIERCE)
        self.assertEqual(motion.attack_of("Axe_S8", motion.BLUNT), motion.SLASH)
        self.assertEqual(motion.attack_of("Default", motion.PIERCE), motion.PIERCE)

    def test_a_ruina_source_can_keep_its_poses_as_they_are(self):
        from limbus_skins import lor
        spec = {"bundle": "char/char_421.pres", "prefab": "BlackSilence", "map": {"S1": ["Mace_S9"]}, "motion": False}
        if lor.shipped_poses(spec["bundle"], spec["prefab"], None) is None:
            self.skipTest("Roland's poses aren't in this copy")
        self.assertEqual([f.read().m_Name for f in lor.source_table(spec)["S1"]], ["Mace_S9"])
        spec["motion"] = True
        self.assertEqual(len(lor.source_table(spec)["S1"]), 4)       # wind-up, strike, impact, recovery


class LobotomyRigs(unittest.TestCase):
    """tools/lobrig.py: Lobotomy Corporation rigs posed by their own clips (no install needed here)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        import lobrig
        cls.L = lobrig

    def clip(self, words, nstream, const=(), dense=(0, 60.0, 0.0, []), ndense=0, bindings=()):
        return self.L.Clip({"m_MuscleClip": {"m_StartTime": 0.0, "m_StopTime": 1.0, "m_Clip": {"data": {
            "m_StreamedClip": {"curveCount": nstream, "data": words},
            "m_DenseClip": {"m_CurveCount": ndense, "m_FrameCount": dense[0], "m_SampleRate": dense[1],
                            "m_BeginTime": dense[2], "m_SampleArray": dense[3]},
            "m_ConstantClip": {"data": list(const)}}}},
            "m_ClipBindingConstant": {"genericBindings": list(bindings)}})

    def test_a_streamed_curve_is_its_cubic_from_its_last_key(self):
        def key(t, idx, a, b, c, d):                                # a frame of one key, as the clip's words
            return list(struct.unpack("<7I", struct.pack("<fIi4f", t, 1, idx, a, b, c, d)))
        words = key(0.0, 0, 0.0, 0.0, 1.0, 2.0) + key(0.5, 0, 0.0, 0.0, 0.0, 9.0)
        c = self.clip(words, 1, const=[7.0])
        self.assertAlmostEqual(c.value(0, 0.25), 2.25)              # 2 + 1 * 0.25
        self.assertAlmostEqual(c.value(0, 0.75), 9.0)               # the next key from its own time
        self.assertEqual(c.value(1, 0.5), 7.0)                      # past the streamed curves: the constant ones

    def test_bindings_take_their_curves_in_order(self):
        rot = {"path": 1, "attribute": 2, "typeID": 4}
        pos = {"path": 2, "attribute": 1, "typeID": 4}
        on = {"path": 3, "attribute": self.L.ENABLED, "typeID": 212}
        c = self.clip([], 0, const=[0, 0, 0, 1, 5, 6, 7, 0], bindings=[rot, pos, on])
        got = c.sample(0.0)
        self.assertEqual([v for _, v in got], [[0, 0, 0, 1], [5, 6, 7], [0]])

    def test_euler_and_rotation(self):
        q = self.L.euler_quat((0.0, 0.0, 90.0))
        self.assertAlmostEqual(q[2], math.sin(math.pi / 4))
        self.assertAlmostEqual(q[3], math.cos(math.pi / 4))
        m = self.L.trs((1.0, 2.0, 0.0), q, (1.0, 1.0, 1.0))
        x, y, _ = self.L.apply(m, (1.0, 0.0, 0.0))
        self.assertAlmostEqual(x, 1.0)                              # (1,0) turned a quarter, moved by (1,2)
        self.assertAlmostEqual(y, 3.0)

    def test_a_triangle_is_drawn_from_its_texture_and_the_origin_is_kept(self):
        from PIL import Image
        tex = Image.new("RGBA", (8, 8), (200, 10, 10, 255))
        img, (ox, oy) = self.L.draw([((0, 0, 0), tex, [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], [(0, 8), (8, 8), (0, 0)])])
        self.assertIsNotNone(img.getbbox())
        self.assertEqual(img.getpixel((10, img.height - 10))[:3], (200, 10, 10))
        self.assertAlmostEqual(oy, 1 - (img.height - 4) / img.height)   # the origin: the triangle's corner

    def test_every_shipped_lobotomy_set_is_named_as_skins_json_asks(self):
        from limbus_skins import build, lor
        for key, s in build.catalog().items():
            for app, body in (s.get("bodies") or {}).items():
                spec = body.get("lor") or {}
                if spec.get("bundle", "").startswith("lobotomy/"):
                    got = lor.shipped_poses(spec["bundle"], spec["prefab"], spec.get("head"))
                    if got is None and not os.path.isdir(os.path.join(os.path.dirname(build.__file__), "data", "lor", spec["prefab"])):
                        continue                                    # a copy without the poses (git): nothing to check
                    self.assertIsNotNone(got, f"{key}: {spec['prefab']} isn't shipped as {spec['bundle']}")
                    wanted = {m.rpartition(":")[2] for ms in spec["map"].values() for m in ms if ":" not in m}
                    self.assertLessEqual(wanted, set(got), f"{key}: {spec['prefab']}")


class Shipped(unittest.TestCase):
    """friend, 2026-11-09: "keep my gebura sprite as global and push thats it and any other external
    id" - what a skin draws from outside the Limbus install ships with limbus-skins."""

    def setUp(self):
        from limbus_skins.build import catalog
        self.skins = catalog()

    def test_every_ruina_body_builds_without_library_of_ruina(self):
        from limbus_skins import lor
        bodies = {k: s["lor"] for k, s in self.skins.items() if s.get("lor")}
        self.assertTrue({"kali", "binah", "baral", "malkuth", "hokma", "apocalypse-bird"} <= set(bodies))
        for key, spec in bodies.items():
            with mock.patch.object(lor.paths, "steam_roots", return_value=[]), \
                    mock.patch.object(lor, "poses", side_effect=AssertionError("read Library of Ruina")):
                table = lor.source_table(dict(spec, motion=False))      # the poses, not their motion
            self.assertEqual(set(table) - {"idle_time"}, set(spec["map"]), key)
            self.assertTrue(all(table[k] for k in spec["map"]), key)

    def test_every_outside_input_ships(self):
        from limbus_skins import art, cards, lor
        with tempfile.TemporaryDirectory() as nobody:            # a player with no art of their own
            for key, s in self.skins.items():
                if s.get("lor"):
                    self.assertIsNotNone(lor.shipped_poses(s["lor"]["bundle"], s["lor"]["prefab"], s["lor"].get("head")), key)
                for name in filter(None, (s.get("card"), s.get("cover_art"))):
                    folder = cards.art_dir(nobody, name)
                    self.assertTrue(os.path.isfile(os.path.join(folder, "normal.png")), f"{key}: {name}")
                    cards.load_art(folder)
            self.assertIsNotNone(art.head_art(nobody, "butterfly-yisang"))

    def test_the_players_own_art_comes_first(self):
        from PIL import Image
        from limbus_skins import art, cards
        with tempfile.TemporaryDirectory() as data:
            mine = os.path.join(data, "art", "cards", "kali")
            os.makedirs(mine)
            Image.new("RGB", (8, 8)).save(os.path.join(mine, "normal.png"))
            self.assertEqual(cards.art_dir(data, "kali"), mine)
            Image.new("RGBA", (8, 8)).save(art.path_for(data, "butterfly-yisang"))
            self.assertEqual(art.head_art(data, "butterfly-yisang"), art.path_for(data, "butterfly-yisang"))

    def test_ruinas_pages_ship_for_the_skins_that_use_them(self):
        from limbus_skins import lortext
        book = lortext.shipped_book()
        self.assertIsNotNone(book)
        with mock.patch.object(lortext, "read_tables", side_effect=AssertionError("read Library of Ruina")):
            for key in [k for k, s in self.skins.items()
                        if lortext.uses_dice(s.get("moves")) or lortext.uses_lor(s.get("texts"))]:
                s = self.skins[key]
                moves = lortext.resolve_moves(s["moves"], book)
                self.assertFalse(any("lor_dice" in m for m in moves.values()), key)
                texts = lortext.resolve(s.get("texts") or {}, book)
                self.assertTrue(all(t.get("name") for t in texts.values() if t), key)
