"""Ally covers: an identity of our own's card pictures, cut from its villain's own story portrait
(the game's `*_story_standing*` bundles in the player's cache - read when a skin is built, never
shipped), framed as cards.py frames a card: on the face, at the sizes the game's own card
pictures have.

skins.json "cover": "<portrait name>" on a skin with an "ally" (or on an identity of our own):
the texture's name in the story bundles, e.g. "Ahab_EGO". A portrait texture holds the body and
its face tiles; the body is its largest sprite. "cover_face": [x, y] in the body's pixels where
the top of the figure isn't its head (a spear, a parasol, wings), "cover_zoom" below 1 to show
more of it.

The pictures go to built/<ally>.cover/pics/<id>_normal<suffix>.dxt (one per card role: the full
card, info, profile and support portrait; the uptie card shows the same). limbus-skins puts them
beside the client patch for the session (client.py), and myosotis's AllyFacePatch shows them
for that identity's id alone - the identity whose card it otherwise borrows keeps its own.
"""
import glob
import os

from . import cards


class CoverError(Exception):
    pass


def file_name(ident, suffix):
    return f"{ident}_normal{suffix}.dxt"


def dxt1_file(img):
    """A picture as the client patch loads it - this Limbus build has no image decoder
    (ImageConversion.LoadImage is stripped), so Texture2D.LoadRawTextureData gets the game's own
    DXT1 blocks: "LSD1", texture width and height (padded to 4), the picture's own width and
    height (uint16 each), 4 bytes spare, then the blocks, rows bottom-up as Unity keeps them."""
    import struct
    import etcpak
    from PIL import Image
    sw, sh = img.size
    w, h = (sw + 3) // 4 * 4, (sh + 3) // 4 * 4
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    canvas.paste(img.convert("RGBA"), (0, h - sh))            # the picture at the texture's top left
    flipped = canvas.transpose(Image.FLIP_TOP_BOTTOM)
    return struct.pack("<4sHHHH4x", b"LSD1", w, h, sw, sh) + etcpak.compress_bc1(flipped.tobytes(), w, h)


def body(texture, rects):
    """The body cut from a portrait texture (PIL, top-down): the largest of its sprites' rects
    (x, y, w, h in Unity's bottom-up pixels). ValueError when it has none."""
    if not rects:
        raise ValueError("the portrait has no sprites")
    x, y, w, h = max(rects, key=lambda r: r[2] * r[3])
    top = texture.height - y - h
    return texture.convert("RGBA").crop((round(x), round(top), round(x + w), round(top + h)))


# the game's card picture sizes, for a role the borrowed identity has no picture of its own for
SIZES = {"": (1920, 1080), "_info": (537, 827), "_profile": (256, 256), "_profile_support_personality": (197, 982)}


def pictures(art, stock, face=None, zoom=1.0):
    """{suffix: picture} for every card role - the full card, info, profile (the pfp the game
    shows in lists) and support portrait: art framed as cards.render frames it, at the size of
    the identity's own picture for that role (else the game's usual size), over that picture
    blurred (else the full card's, else the art's own) - opaque, for JPEG. face: [x, y] in the
    art's pixels when the top of the figure isn't its head (a weapon or wings above it)."""
    out = {}
    for suffix, role in cards.ROLES.items():
        st = stock.get(suffix)
        size = st.size if st is not None else SIZES[suffix]
        behind = st if st is not None else stock.get("", art)      # none cached: the art itself, blurred
        out[suffix] = cards.render(role, size, art, behind, face=face, zoom=zoom).convert("RGB")
    return out


def story_bundles(game_dir, cache):
    """The cached story-portrait bundles (paths)."""
    from . import game
    names = game.catalog_names(game_dir)
    hashes = [h for h, n in names.items() if "standing" in n.lower()]
    return [p for h in hashes for p in glob.glob(os.path.join(cache, "*", h, "__data"))]


def portraits(game_dir, cache, names, keep):
    """{name: body (PIL RGBA)} for the story portraits named, from the player's cache, each
    bundle opened once; kept in keep/<name>.png and read from there next time. A name that
    isn't cached is left out (the story that has it not seen yet)."""
    from PIL import Image
    from .game import unitypy
    out, want = {}, set(names)
    for name in sorted(want):
        saved = os.path.join(keep, name + ".png")
        if os.path.isfile(saved):
            out[name] = Image.open(saved).convert("RGBA")
    want -= set(out)
    for path in story_bundles(game_dir, cache) if want else []:
        env = unitypy().load(path)
        texs = {o.path_id: o for o in env.objects if o.type.name == "Texture2D" and o.peek_name() in want}
        if not texs:
            continue
        rects = {pid: [] for pid in texs}
        for o in env.objects:
            if o.type.name != "Sprite":
                continue
            rd = o.read_typetree().get("m_RD") or {}
            pid = (rd.get("texture") or {}).get("m_PathID")
            if pid in rects:
                r = rd["textureRect"]
                rects[pid].append((r["x"], r["y"], r["width"], r["height"]))
        for pid, tex in texs.items():
            img = tex.read().image
            name = tex.peek_name()
            out[name] = body(img, rects[pid]) if rects[pid] else img.convert("RGBA")
            os.makedirs(keep, exist_ok=True)
            out[name].save(os.path.join(keep, name + ".png"))
            want.discard(name)
        if not want:
            break
    return out


def stock_pictures(game_dir, cache, folder):
    """{suffix: picture} of identity `folder`'s own normal card pictures (their sizes, and the
    backdrop behind the cover); {} when none are cached (an identity the player hasn't got)."""
    from .game import unitypy
    try:
        path = cards.find_bundle(game_dir, cache, folder)
    except cards.CardError:
        return {}
    env = unitypy().load(path)
    want = {cards.card_name(folder, "normal", s): s for s in cards.ROLES}
    out = {}
    for o in env.objects:
        if o.type.name == "Texture2D" and o.peek_name() in want:
            out[want[o.peek_name()]] = o.read().image
    return out
