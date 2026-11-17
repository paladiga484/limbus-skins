"""The title screen's pictures (myosotis's TitlePatch): a background in place of the season's
video, and a logo in place of Limbus Company's. Each is made once, when it's chosen, into
<data>/title/ as the client patch loads pictures (covers.dxt1_file / dxt5_file); `client`
puts them beside the patch for a session and takes them away after."""
import os

BACKGROUND = "background.dxt"
LOGO = "logo.dxt"
OFF = "off"                      # the game's own background: the package's default picture stays away
SCREEN = (1920, 1080)
LOGO_BOX = (807, 399)            # the game's own logo's size: ours fits inside it


def folder(data_dir):
    return os.path.join(data_dir, "title")


def cover(img, size):
    """img cropped about its centre to size's shape, then scaled to it: it fills the screen."""
    from PIL import Image
    w, h = img.size
    tw, th = size
    scale = max(tw / w, th / h)
    cw, ch = round(tw / scale), round(th / scale)
    left, top = (w - cw) // 2, (h - ch) // 2
    return img.crop((left, top, left + cw, top + ch)).resize(size, Image.LANCZOS)


def frame(img, size):
    """The background: a picture near the screen's shape fills it (cover); a much narrower one -
    a portrait, a card - stands full height in the middle, on black."""
    from PIL import Image
    w, h = img.size
    if w / h >= 0.75 * size[0] / size[1]:
        return cover(img, size)
    scale = size[1] / h
    pic = img.resize((max(1, round(w * scale)), size[1]), Image.LANCZOS)
    out = Image.new("RGB", size, (0, 0, 0))
    out.paste(pic, ((size[0] - pic.width) // 2, 0))
    return out


def fit(img, box):
    """img scaled to fit inside box, its shape kept (never enlarged past twice its size)."""
    from PIL import Image
    w, h = img.size
    scale = min(box[0] / w, box[1] / h, 2.0)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def save(picture, data_dir, logo=False, stretch=False):
    """Make the background (or, logo=True, the logo) from a picture file; its path. stretch: the
    background is pulled to the screen's edges, its shape not kept."""
    from PIL import Image
    from .covers import dxt1_file, dxt5_file
    with Image.open(picture) as src:
        img = src.convert("RGBA")
    blob = dxt5_file(fit(img, LOGO_BOX)) if logo else dxt1_file(img.convert("RGB").resize(SCREEN, Image.LANCZOS) if stretch else frame(img.convert("RGB"), SCREEN))
    os.makedirs(folder(data_dir), exist_ok=True)
    out = os.path.join(folder(data_dir), LOGO if logo else BACKGROUND)
    with open(out + ".tmp", "wb") as f:
        f.write(blob)
    os.replace(out + ".tmp", out)
    if not logo:
        _remove(os.path.join(folder(data_dir), OFF))
    return out


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def clear(data_dir, logo=False):
    """The game's own background (or logo) again - also instead of the package's default picture."""
    _remove(os.path.join(folder(data_dir), LOGO if logo else BACKGROUND))
    if not logo:
        os.makedirs(folder(data_dir), exist_ok=True)
        open(os.path.join(folder(data_dir), OFF), "wb").close()


INTRO = ("intro-glow.pic", "intro-rays.pic", "intro-disc.pic", "intro-text.pic", "intro-layout.txt")


def intro_layers(img):
    """A glowing emblem on black (a sun with rays over a line of text) as the intro's layers:
    (glow, rays, disc, text) RGBA images, and the layout: {"text_below": the text's middle below
    the sun's, "text_width": its width - both in the sun's square sides}. The sun is found as the brightest blob's middle; the
    disc is everything within its radius, the rays the rest of the sun, the text what lies
    below it. Each pixel's alpha is its brightness, its colour the emblem's own."""
    from PIL import Image, ImageFilter, ImageOps
    import math
    rgb = img.convert("RGB")
    w, h = rgb.size
    lum = ImageOps.grayscale(rgb).point(lambda v: 0 if v < 26 else min(255, int((v - 26) * 1.25)))
    col = Image.new("RGB", (w, h), (247, 228, 190))      # the emblem's cream
    full = Image.merge("RGBA", (*col.split(), lum))
    rows = [sum(1 for x in range(0, w, 2) if lum.getpixel((x, y)) > 160) for y in range(h)]
    # the text: the lowest band of bright rows, below a gap
    bands, start = [], None
    for y, n in enumerate(rows + [0]):
        if n and start is None:
            start = y
        elif not n and start is not None:
            bands.append((start, y))
            start = None
    text_band = bands[-1] if len(bands) > 1 else None
    sun_bottom = text_band[0] if text_band else h
    # the disc: the widest bright row run above the text is its diameter
    best = (0, 0, 0)
    for y in range(h // 8, sun_bottom):
        run = x0 = 0
        for x in range(w):
            if lum.getpixel((x, y)) > 160:
                if not run:
                    x0 = x
                run += 1
                if run > best[0]:
                    best = (run, x0, y)
            else:
                run = 0
    cx = best[1] + best[0] / 2
    # its height, down the middle column (a keyhole off-centre can break a row, not that column)
    col_best, run, y0 = (0, 0), 0, 0
    for y in range(sun_bottom):
        if lum.getpixel((int(cx), y)) > 160:
            if not run:
                y0 = y
            run += 1
            if run > col_best[0]:
                col_best = (run, y0)
        else:
            run = 0
    r = max(best[0], col_best[0]) / 2
    cy = col_best[1] + col_best[0] / 2
    side = int(r * 4.6) // 2 * 2
    box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    above = full.copy()                                  # the sun alone: nothing of the text in its square
    if text_band:
        a = above.getchannel("A")
        a.paste(0, (0, max(0, text_band[0] - 4), w, h))
        above.putalpha(a)
    sun = above.crop(box)
    mask_disc = Image.new("L", sun.size, 0)
    c = side / 2
    mask_disc.putdata([255 if math.hypot(x - c, y - c) <= r * 1.06 else 0 for y in range(side) for x in range(side)])
    disc = sun.copy()
    disc.putalpha(Image.composite(sun.getchannel("A"), Image.new("L", sun.size, 0), mask_disc))
    rays = sun.copy()
    rays.putalpha(Image.composite(Image.new("L", sun.size, 0), sun.getchannel("A"), mask_disc))
    glow = sun.filter(ImageFilter.GaussianBlur(side / 18))
    glow.putalpha(glow.getchannel("A").point(lambda v: min(255, v * 2)))
    if text_band:
        pad = (text_band[1] - text_band[0]) // 3
        text = full.crop((0, text_band[0] - pad, w, text_band[1] + pad))
        tb = text.getchannel("A").getbbox() or (0, 0, 1, 1)
        layout = {"text_below": round(((text_band[0] - pad + (tb[1] + tb[3]) / 2) - cy) / side, 4),
                  "text_width": round((tb[2] - tb[0]) / side, 4)}
        text = text.crop(tb)
    else:
        text = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        layout = {"text_below": 0.6, "text_width": 0.01}
    return glow, rays, disc, text, layout


def save_intro(picture, data_dir):
    """The intro over the game's opening logo, from a picture of an emblem on black."""
    from PIL import Image
    from .covers import rgba_file
    with Image.open(picture) as src:
        layers = intro_layers(src)
    os.makedirs(folder(data_dir), exist_ok=True)
    *pics, layout = layers
    for name, blob in zip(INTRO, [rgba_file(i) for i in pics] + [
            "".join(f"{k}\t{v}\n" for k, v in layout.items()).encode()]):
        out = os.path.join(folder(data_dir), name)
        with open(out + ".tmp", "wb") as f:
            f.write(blob)
        os.replace(out + ".tmp", out)
    return layout


def files(data_dir):
    """{file name: path} of the title pictures chosen (and OFF, when the game's own was)."""
    return {f: os.path.join(folder(data_dir), f) for f in (BACKGROUND, LOGO, OFF) + INTRO
            if os.path.isfile(os.path.join(folder(data_dir), f))}
