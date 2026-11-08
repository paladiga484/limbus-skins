"""Ally covers: an identity of our own's card pictures, cut from its villain's own story portrait
(the game's `*_story_standing*` bundles in the player's cache - read when a skin is built, never
shipped), framed as cards.py frames a card: on the face, at the sizes the game's own card
pictures have.

skins.json "cover": "<portrait name>" on a skin with an "ally" (or on an identity of our own):
the texture's name in the story bundles, e.g. "Ahab_EGO". A portrait texture holds the body and
its face tiles; the body is its largest sprite. "cover_face": [x, y] in the body's pixels where
the top of the figure isn't its head (a spear, a parasol, wings), "cover_zoom" below 1 to show
more of it.

"cover_art": "<name>" instead: the player's own picture, <data dir>/art/cards/<name>/normal.png
(card.json's "face" and "zoom" apply), for an identity of our own the story has no portrait of.

The pictures go to built/<ally>.cover/pics/<id>_normal<suffix>.dxt (one per card role: the full
card, info, profile and support portrait; the uptie card shows the same). limbus-skins puts them
beside the client patch for the session (client.py), and myosotis's AllyFacePatch shows them
for that identity's id alone - the identity whose card it otherwise borrows keeps its own.
"""
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


def dxt5_file(img):
    """The same with alpha kept ("LSD5", DXT5 blocks): for a picture with see-through parts, a logo."""
    import struct
    import etcpak
    from PIL import Image
    sw, sh = img.size
    w, h = (sw + 3) // 4 * 4, (sh + 3) // 4 * 4
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img.convert("RGBA"), (0, h - sh))
    flipped = canvas.transpose(Image.FLIP_TOP_BOTTOM)
    return struct.pack("<4sHHHH4x", b"LSD5", w, h, sw, sh) + etcpak.compress_bc3(flipped.tobytes(), w, h)


def body(texture, rects):
    """The body cut from a portrait texture (PIL, top-down): the largest of its sprites' rects
    (x, y, w, h in Unity's bottom-up pixels). ValueError when it has none."""
    if not rects:
        raise ValueError("the portrait has no sprites")
    x, y, w, h = max(rects, key=lambda r: r[2] * r[3])
    top = texture.height - y - h
    return texture.crop((round(x), round(top), round(x + w), round(top + h))).convert("RGBA")


# the game's card picture sizes, for a role the borrowed identity has no picture of its own for
SIZES = {"": (1920, 1080), "_info": (537, 827), "_profile": (256, 256), "_profile_support_personality": (197, 982)}


def pictures(art, stock, face=None, zoom=1.0, pfp=None):
    """{suffix: picture} for every card role - the full card, info, profile (the pfp the game
    shows in lists) and support portrait: art framed as cards.render frames it, at the size of
    the identity's own picture for that role (else the game's usual size), over that picture
    blurred (else the full card's, else the art's own) - opaque, so DXT1 punches no holes. face: [x, y] in the
    art's pixels when the top of the figure isn't its head (a weapon or wings above it). pfp: a
    picture of its own for the profile (the body standing idle in a fight, idle_frames)."""
    out = {}
    for suffix, role in cards.ROLES.items():
        st = stock.get(suffix)
        size = st.size if st is not None else SIZES[suffix]
        behind = st if st is not None else stock.get("", art)      # none cached: the art itself, blurred
        if suffix == "_profile" and pfp is not None:
            out[suffix] = profile(pfp, stock, fallback=art)
        else:
            out[suffix] = cards.render(role, size, art, behind, face=face, zoom=zoom).convert("RGB")
    return out


SPRITE_ZOOM = 0.5      # a battle sprite's pfp wider than a portrait's: the whole head, hat and all


def profile(sprite, stock, fallback=None):
    """The pfp (the profile card role) from a battle sprite, framed on its face: at the size of
    the identity's own pfp (else the game's usual), over that pfp blurred - else its full card,
    else fallback, else the sprite itself."""
    st = stock.get("_profile")
    size = st.size if st is not None else SIZES["_profile"]
    behind = st if st is not None else stock.get("", fallback if fallback is not None else sprite)
    return cards.render(cards.ROLES["_profile"], size, sprite, behind, face=sprite_face(sprite), zoom=SPRITE_ZOOM).convert("RGB")


def built_idle(files_dir, appearance):
    """A built body - a repaint or a tint, in its own colours - standing idle in a fight, as
    repaint.idle_image renders it: the first __data under files_dir. None when there's none."""
    from . import repaint
    for root, dirs, names in os.walk(files_dir):
        dirs.sort()
        if "__data" in names:
            return repaint.idle_image(os.path.join(root, "__data"), appearance, height=900)
    return None


def sprite_face(img):
    """Where a battle sprite's face is (x, y), or None for an empty picture. Its top isn't its
    head when a weapon is raised, and its middle isn't its body when a blade sticks out: the body
    is the densest band of columns (a spear or a sword is a thin one), the head the first solid
    rows on it, the face a tenth of the body's height below that."""
    from PIL import Image
    a = img.getchannel("A").point(lambda v: 255 if v > 16 else 0)
    box = a.getbbox()
    if not box:
        return None
    left, top, right, bottom = box
    band = max(4, int(0.12 * (bottom - top)))
    cols = list(a.resize((img.width, 1), Image.BOX).getdata())
    run = [0]
    for c in cols:
        run.append(run[-1] + c)
    half = band // 2
    axis = max(range(left, right), key=lambda x: run[min(len(cols), x + half)] - run[max(0, x - half)])
    strip = a.crop((max(0, axis - half), top, min(img.width, axis + half), bottom))
    # half the band filled: a head is; antlers or a stem rising from it aren't (Old G Corp head)
    rows = [v > 0.5 * 255 for v in strip.resize((1, strip.height), Image.BOX).getdata()]
    run_ = max(1, int(0.15 * len(rows)))            # a head goes on into a body; a prop held up doesn't
    head = top + next((i for i in range(len(rows)) if rows[i] and sum(rows[i:i + run_]) >= 0.8 * run_), 0)
    return float(axis), head + 0.12 * (bottom - head)


def idle_body(skin, ids, helper_units):
    """The body an identity of ours wears in a fight (an appearance name): its helper's, else its
    boss's (source), else its base identity's own. None when none is known."""
    if skin.get("helper"):
        return (helper_units.get(skin["helper"]) or {}).get("appearance")
    return skin.get("source") or (ids.get(skin.get("base")) or {}).get("appearance")


def idle_frames(game_dir, cache, data_dir, bodies_wanted, keep, log=print):
    """{appearance: picture (PIL RGBA, cut out)} of each body standing idle in a fight - the first
    moment of its idle, as repaint.idle_image renders it - from the player's cache; kept in
    keep/<appearance>.png and read from there next time. A body that isn't cached is left out."""
    from PIL import Image
    from . import game, repaint
    out, want = {}, {b for b in bodies_wanted if b}
    for app in sorted(want):
        saved = os.path.join(keep, app + ".png")
        if os.path.isfile(saved):
            out[app] = Image.open(saved).convert("RGBA")
    want -= set(out)
    if not want:
        return out
    bodies, have = game.body_index(game_dir, cache, os.path.join(data_dir, "bodies.json"), log=log)
    for app in sorted(want):
        if app not in bodies or bodies[app] not in have:
            continue
        try:
            img = repaint.idle_image(have[bodies[app]], app, height=900)
        except Exception as e:                       # a body that won't render keeps its story art
            log(f"  idle {app}: not rendered ({e})")
            continue
        if img is not None:
            os.makedirs(keep, exist_ok=True)
            img.save(os.path.join(keep, app + ".png"))
            out[app] = img
    return out


def story_bundles(game_dir, cache):
    """The cached story-portrait bundles (paths)."""
    from . import game
    have = game.cache_bundles(cache)
    return [have[h] for h, n in game.catalog_names(game_dir).items() if "standing" in n.lower() and h in have]


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
