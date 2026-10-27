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

    def read(self, rel):
        with open(os.path.join(self.cache, rel), "rb") as f:
            return f.read()

    def test_apply_then_restore_is_byte_exact(self):
        s = mods.Session(self.cache, self.data)
        done = s.apply([self.mod("one", {"aa/11/__data": b"MOD", "aa/11/__info": b"MODINFO"})], log=lambda *a: None)
        self.assertEqual(done, ["one"])
        self.assertEqual(self.read("aa/11/__data"), b"MOD")
        s.restore(log=lambda *a: None)
        self.assertEqual(self.read("aa/11/__data"), b"stock-a")
        self.assertEqual(self.read("aa/11/__info"), b"info-a")
        self.assertIsNone(s.active())

    def test_skips_mods_for_bundles_the_game_replaced(self):
        s = mods.Session(self.cache, self.data)
        done = s.apply([self.mod("old", {"zz/99/__data": b"MOD"})], log=lambda *a: None)
        self.assertEqual(done, [])
        self.assertFalse(os.path.exists(os.path.join(self.cache, "zz")))

    def test_two_mods_on_one_file_keep_the_first(self):
        s = mods.Session(self.cache, self.data)
        done = s.apply([self.mod("a", {"aa/11/__data": b"A"}), self.mod("b", {"aa/11/__data": b"B"})], log=lambda *a: None)
        self.assertEqual(done, ["a"])
        self.assertEqual(self.read("aa/11/__data"), b"A")
        s.restore(log=lambda *a: None)
        self.assertEqual(self.read("aa/11/__data"), b"stock-a")

    def test_new_files_are_removed_on_restore(self):
        s = mods.Session(self.cache, self.data)
        s.apply([self.mod("n", {"aa/11/__data": b"X", "aa/11/__extra": b"new"})], log=lambda *a: None)
        s.restore(log=lambda *a: None)
        self.assertFalse(os.path.exists(os.path.join(self.cache, "aa/11/__extra")))

    def test_reapply_restores_first(self):
        s = mods.Session(self.cache, self.data)
        s.apply([self.mod("a", {"aa/11/__data": b"A"})], log=lambda *a: None)
        s.apply([self.mod("b", {"bb/22/__data": b"B"})], log=lambda *a: None)
        self.assertEqual(self.read("aa/11/__data"), b"stock-a")
        s.restore(log=lambda *a: None)
        self.assertEqual(self.read("bb/22/__data"), b"stock-b")

    def test_refuses_while_the_game_runs(self):
        s = mods.Session(self.cache, self.data)
        s.apply([self.mod("a", {"aa/11/__data": b"A"})], log=lambda *a: None)
        with mock.patch.object(mods, "game_running", return_value=True):
            with self.assertRaises(mods.ModError):
                s.restore(log=lambda *a: None)
            with self.assertRaises(mods.ModError):
                s.apply([], log=lambda *a: None)
        self.assertEqual(self.read("aa/11/__data"), b"A")


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
        self.assertIn("Black Forest", str(e.exception))

    def test_unlocked_it_is_an_ordinary_skin(self):
        st = {"on": ["kali"], "unlocked": ["black-forest"]}
        visible, locked = cli.unlocked(self.SK, st)
        self.assertEqual((sorted(visible), locked, st["on"]), (["ahab", "kali"], set(), ["kali"]))
        cli.refuse_locked(["kali"], locked, self.SK)                   # no complaint


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

    def test_boss_attacks_by_tier_fill_the_identitys_own_skills(self):
        out = self.h.boss_skills(self.ident, self.unit, self.skills)
        self.assertEqual(sorted(out), [1, 2, 3])
        self.assertEqual([out[i]["skillData"][0]["defaultValue"] for i in (1, 2, 3)], [92, 93, 91])
        d = out[1]["skillData"]
        self.assertEqual(len(d), 1)                                  # the boss's numbers at every uptie
        self.assertEqual(d[0]["skillMotion"], "S1")                  # the identity's own (repainted) motion
        self.assertEqual(d[0]["gaksungLevel"], 1)
        self.assertEqual(d[0]["attributeType"], "INDIGO")
        self.assertEqual(len(d[0]["coinList"]), 3)
        self.assertEqual(self.skills[1]["skillData"][0]["attributeType"], "VIOLET")   # inputs untouched

    def test_boss_coins_play_within_the_identitys_animation(self):
        # the game: "the action number went past the motion count of the current hit data" -
        # a boss coin's actionIndex is about the boss's animation, not the identity's
        self.skills[1]["skillData"][0]["coinList"] = [{"scale": 1}, {"scale": 1}]            # S1: two hits
        self.skills[92]["skillData"][0]["coinList"] = [{"scale": 4, "actionIndex": i, "cutsceneStart": True}
                                                      for i in (1, 2, 3, 4)]
        out = self.h.boss_skills(self.ident, self.unit, self.skills)
        coins = out[1]["skillData"][0]["coinList"]
        self.assertEqual([c.get("actionIndex") for c in coins], [None, None, 2, 2])        # past its hits: the last one
        self.assertFalse(any("cutsceneStart" in c for c in coins))
        self.assertEqual([c["scale"] for c in coins], [4, 4, 4, 4])

    def test_too_few_boss_attacks_keep_the_identitys_last_skills(self):
        unit = {"id": 1, "attributeList": [{"skillId": 91, "number": 0}, {"skillId": 95, "number": 0}]}
        self.assertEqual(sorted(self.h.boss_skills(self.ident, unit, self.skills)), [1])
        with self.assertRaises(ValueError):
            self.h.boss_skills(self.ident, {"id": 2, "attributeList": [{"skillId": 95}]}, self.skills)

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
        boss = self.h.boss_skills(self.ident, self.unit, self.skills)          # green on top of a boss's skill
        out = self.h.recolor_skills(self.ident, self.skills, {"all": "SHAMROCK"}, base=boss)
        self.assertEqual((out[1]["skillData"][0]["defaultValue"], out[1]["skillData"][0]["attributeType"]), (92, "SHAMROCK"))

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
        unit = {"id": 2, "attributeList": [{"skillId": 96}, {"skillId": 92}]}      # bosses skip them too
        self.assertEqual(self.h.boss_skills(self.ident, unit, self.skills)[1]["skillData"][0]["defaultValue"], 92)

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
        self.assertEqual((skins["kali-id"]["identity"], skins["kali-id"]["unlock"]), (10499, "black-forest"))


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
        for key, man in (("helpers", {"green_skills": [9], "skill_texts": self.TEXTS}),
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
        with open(os.path.join(self.data, "active.json"), "w") as f:
            json.dump({"files": [], "mods": list(mods)}, f)

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
        client.configure(self.client, self.data, on=False)
        ini = self.read("client", "myosotis.ini")
        self.assertIn("green_coin_skills=\n", ini)
        self.assertIn("hp_shift=\n", ini)
        self.assertEqual(self.read("client", "skilltext.txt"), "")

    def test_a_helpers_build_counts_only_when_it_was_swapped_in(self):
        from limbus_skins import client
        self.applied("greencliff")                     # helpers skipped (the game updated since it was built)
        client.configure(self.client, self.data)
        self.assertIn("green_coin_skills=\n", self.read("client", "myosotis.ini"))
        self.assertEqual(self.read("client", "skilltext.txt"), "")
        os.remove(os.path.join(self.data, "active.json"))       # no session at all
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
