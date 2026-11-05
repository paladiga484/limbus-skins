"""Core pieces that decide what the game reads: bundles, CRCs, sessions, selection."""
import json
import math
import os
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
            with open(state, "w") as f:                                          # as an older limbus-skins left it
                json.dump({"on": ["sora", "sora-id", "ahab"]}, f)
            self.assertEqual(cli.main(["list"]), 0)
            self.assertEqual(on(), ["sora-id", "ahab"])


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

    def test_gebura_is_only_for_patchlc_and_stays_on_for_it(self):
        # friend, 2026-11-04: "make gebura used ONLY when shes enabled and only in patchlc"
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
        from limbus_skins.build import catalog
        self.assertEqual({k for k, s in catalog().items() if s.get("only") == "patchlc"}, {"kali", "kali-id"})

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
        self.assertEqual(man["identities"], [{"id": 10198, "base": 10101, "folder": 10101, "name": "A"}])

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
                                              "unlock": "black-forest"}])
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
        self.assertEqual(build.FORMAT, 27)                             # built while the cache held a leaked copy: rebuilt on apply

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
        self.assertEqual({k for k, s in sk.items() if s.get("card")},
                         {"kali", "matthias", "roach-emperor", "distorted-hohenheim", "vergilius-wildhunt", "vergilius-id"})
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
                mock.patch.object(lortext, "read_tables", return_value=self.TABLES):
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
