"""Core pieces that decide what the game reads: bundles, CRCs, sessions, selection."""
import json
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

    def test_render_setup_pose(self):
        from PIL import Image
        pages = spine.parse_atlas("a.png\nsize:20,20\nhead\nbounds:0,0,20,20\n")
        img, _ = spine.render({"bones": [{"name": "root"}], "slots": [{"name": "s", "bone": "root", "attachment": "head"}],
                               "skins": [{"name": "default", "attachments": {"s": {"head": {"width": 40, "height": 40}}}}]},
                              pages, {"a.png": Image.new("RGBA", (20, 20), (255, 0, 0, 255))}, ppu=1)
        self.assertEqual(img.getpixel((img.width // 2, img.height // 2)), (255, 0, 0, 255))


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


if __name__ == "__main__":
    unittest.main()
