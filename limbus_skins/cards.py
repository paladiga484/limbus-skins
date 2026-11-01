"""ID cards: an identity's card pictures - full art, uptie art, the info panel, the profile
portrait and the support portrait, for both - redrawn from art of the character the skin makes
it, for as long as the skin is on (they come back with the stock files).

skins.json "card": "<name>"; the art is the player's own, in <data dir>/art/cards/<name>/
(limbus-skins never ships or downloads any):
  normal.png    the character: a cut-out on transparency, or a whole picture
  gacksung.png  for the uptie cards (else normal.png)
  bg.png        behind a cut-out (else the identity's own card, blurred and darkened)
  card.json     optional: {"face": [x, y]} in normal.png's pixels, "gacksung_face" the same for
                gacksung.png (else: the top of the cut-out), "zoom" / "gacksung_zoom" (below 1
                shows more of the picture), "x": where the face sits across the wide cards
                (0-1, default 0.62)

Every picture keeps its texture's size, so the sprites cut from it stay as they are. An uptie
card the game draws as a Spine animation (SpineIllustPrefab/<id>_gacksung) isn't a texture: the
detail screen may keep it.
"""
import json
import os
import re

ROLES = {"": "wide", "_info": "info", "_profile": "profile", "_profile_support_personality": "support"}


class CardError(Exception):
    pass


def card_name(ident, variant, suffix=""):
    return f"{ident}_{variant}{suffix}"


def art_dir(data_dir, name):
    return os.path.join(data_dir, "art", "cards", name)


def load_art(folder):
    """{"normal", "gacksung", "bg" (or None), "spec"} from an art folder; CardError if unusable."""
    from PIL import Image
    if not os.path.isfile(os.path.join(folder, "normal.png")):
        raise CardError(f"no card art: put normal.png (and gacksung.png, bg.png) in {folder}")
    spec = {}
    if os.path.isfile(os.path.join(folder, "card.json")):
        try:
            with open(os.path.join(folder, "card.json"), encoding="utf-8") as f:
                spec = json.load(f)
        except ValueError as e:
            raise CardError(f"{folder}/card.json: {e}")
    out = {"spec": spec, "bg": None}
    for k in ("normal", "gacksung", "bg"):
        p = os.path.join(folder, f"{k}.png")
        if os.path.isfile(p):
            out[k] = Image.open(p).convert("RGBA")
    out.setdefault("gacksung", out["normal"])
    return out


def is_cutout(img):
    """A figure on transparency (rather than a whole picture)?"""
    a = img.getchannel("A")
    clear = a.histogram()[0:16]
    return sum(clear) > 0.05 * img.width * img.height


def figure_box(img):
    if not is_cutout(img):
        return (0, 0, img.width, img.height)
    box = img.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    return box or (0, 0, img.width, img.height)


def face_point(img, box, given=None):
    """Where the face is: given, else the middle of the cut-out's top tenth, a tenth down."""
    if given:
        return float(given[0]), float(given[1])
    left, top, right, bottom = box
    h = bottom - top
    band = img.getchannel("A").crop((left, top, right, top + max(1, int(0.12 * h))))
    band = band.point(lambda v: 255 if v > 16 else 0)
    hb = band.getbbox()
    cx = left + ((hb[0] + hb[2]) / 2 if hb else (right - left) / 2)
    return cx, top + 0.1 * h


def window(role, size, box, face, x_frac=0.62, zoom=1.0):
    """The part of the art (x, y, w, h, in its pixels) a picture of `size` shows: framed on the
    face, sized by how far the figure reaches below it (so a spike or a raised blade above the
    head doesn't shrink the character)."""
    w_t, h_t = size
    aspect = w_t / h_t
    below = max(1.0, box[3] - face[1]) / zoom
    if role == "wide":                  # head to thighs, the face to one side
        h = below * 0.78
        w = h * aspect
        return face[0] - x_frac * w, face[1] - 0.22 * h, w, h
    if role == "info":                  # head and shoulders down to the waist
        h = below * 0.6
        w = h * aspect
        return face[0] - w / 2, face[1] - 0.2 * h, w, h
    if role == "profile":               # the face
        h = below * 0.2
        return face[0] - h / 2, face[1] - 0.42 * h, h, h
    if role == "support":               # a tall strip, head to feet
        h = below * 1.0
        w = h * aspect
        return face[0] - w / 2, face[1] - 0.08 * h, w, h
    raise ValueError(role)


def _inside(win, img, face):
    """A window over a whole picture, shrunk about the face if it must be, then moved to stay
    inside it."""
    x, y, w, h = win
    k = min(1.0, img.width / w, img.height / h)
    x, y = face[0] - (face[0] - x) * k, face[1] - (face[1] - y) * k
    w, h = w * k, h * k
    return min(max(0.0, x), img.width - w), min(max(0.0, y), img.height - h), w, h


def cover(img, size):
    """img scaled to fill size, centred, cut to it."""
    from PIL import Image
    w, h = size
    k = max(w / img.width, h / img.height)
    im = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def backdrop(stock, size, bg=None):
    """Behind a cut-out: bg, else the stock picture blurred and darkened."""
    from PIL import ImageEnhance, ImageFilter
    if bg is not None:                  # softened and dimmed a little: the figure stands out
        im = cover(bg, size).convert("RGBA").filter(ImageFilter.GaussianBlur(max(1, max(size) / 240)))
        return ImageEnhance.Brightness(im).enhance(0.8)
    im = cover(stock.convert("RGBA"), size).filter(ImageFilter.GaussianBlur(max(4, max(size) / 40)))
    im = ImageEnhance.Color(im).enhance(0.6)
    return ImageEnhance.Brightness(im).enhance(0.45)


def render(role, size, art, stock, bg=None, face=None, x_frac=0.62, zoom=1.0):
    """One card picture of `size` for `role` from art (RGBA)."""
    from PIL import Image
    cut = is_cutout(art)
    box = figure_box(art)
    if cut:
        f = face_point(art, box, face)
    else:                                   # a whole picture: the face a third of the way down
        f = (float(face[0]), float(face[1])) if face else (art.width / 2, art.height * 0.3)
    win = window(role, size, box, f, x_frac, zoom)
    if not cut:
        win = _inside(win, art, f)
    x, y, w, h = win
    crop = art.crop((round(x), round(y), round(x + w), round(y + h))).resize(size, Image.LANCZOS)
    canvas = backdrop(stock, size, bg) if cut else Image.new("RGBA", size)
    canvas.alpha_composite(crop)
    return canvas


def apply(env, ident, art):
    """Redraw identity `ident`'s card textures in a loaded bundle (UnityPy env) from art
    (load_art). Returns how many were redrawn; CardError when the bundle has none of them."""
    from .game import unitypy
    UnityPy = unitypy()
    names = {card_name(ident, v, s): (v, role) for v in ("normal", "gacksung") for s, role in ROLES.items()}
    spec = art["spec"]
    done = 0
    for o in env.objects:
        if o.type.name != "Texture2D":
            continue
        t = o.read()
        hit = names.get(t.m_Name)
        if not hit:
            continue
        variant, role = hit
        stock = t.image
        own = variant == "normal" or art[variant] is art["normal"]   # an uptie card without a picture of its own: normal's
        face = spec.get("face") if own else spec.get("gacksung_face")
        zoom = float(spec.get("zoom", 1.0) if own else spec.get("gacksung_zoom", 1.0))
        img = render(role, stock.size, art[variant], stock, art.get("bg"), face, float(spec.get("x", 0.62)), zoom)
        F = UnityPy.enums.TextureFormat
        keep = t.m_TextureFormat in (F.DXT1, F.DXT5, F.RGB24, F.RGBA32)  # the game's own: small, and as it expects
        t.set_image(img if t.m_TextureFormat != F.DXT1 else img.convert("RGB"),
                    target_format=t.m_TextureFormat if keep else F.RGBA32, mipmap_count=max(1, t.m_MipCount or 1))
        t.save()
        done += 1
    if not done:
        raise CardError(f"no card pictures of {ident} in this bundle")
    return done


def has_card(env, ident):
    return any(o.type.name == "Texture2D" and o.peek_name() == card_name(ident, "normal") for o in env.objects)


CARD_PATH = re.compile(r"/Sprite/Unit/CG/(\d+)_normal\.png$")


def find_bundle(game_dir, cache, ident):
    """Path of the cached bundle with identity ident's card pictures: p<id>_assets_all, else
    the shared unitsprite_* bundles of the first identities (opened to look)."""
    from .game import cache_bundles, catalog_names, unitypy
    names, have = catalog_names(game_dir), cache_bundles(cache)
    want = f"p{ident}_assets_all"
    for h, path in have.items():
        if names.get(h) == want:
            return path
    UnityPy = unitypy()
    for h, path in sorted(have.items()):
        if names.get(h, "").startswith("unitsprite_"):
            try:
                env = UnityPy.load(path)
            except Exception:
                continue
            if any(m.group(1) == str(ident) for m in map(CARD_PATH.search, env.container) if m):
                return path
    raise CardError(f"identity {ident}'s card pictures aren't in the game's cache")
