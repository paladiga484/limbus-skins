"""The title screen's pictures (myosotis's TitlePatch): a background in place of the season's
video, and a logo in place of Limbus Company's. Each is made once, when it's chosen, into
<data>/title/ as the client patch loads pictures (covers.dxt1_file / dxt5_file); `client`
puts them beside the patch for a session and takes them away after."""
import os

BACKGROUND = "background.dxt"
LOGO = "logo.dxt"
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


def fit(img, box):
    """img scaled to fit inside box, its shape kept (never enlarged past twice its size)."""
    from PIL import Image
    w, h = img.size
    scale = min(box[0] / w, box[1] / h, 2.0)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def save(picture, data_dir, logo=False):
    """Make the background (or, logo=True, the logo) from a picture file; its path."""
    from PIL import Image
    from .covers import dxt1_file, dxt5_file
    with Image.open(picture) as src:
        img = src.convert("RGBA")
    blob = dxt5_file(fit(img, LOGO_BOX)) if logo else dxt1_file(cover(img.convert("RGB"), SCREEN))
    os.makedirs(folder(data_dir), exist_ok=True)
    out = os.path.join(folder(data_dir), LOGO if logo else BACKGROUND)
    with open(out + ".tmp", "wb") as f:
        f.write(blob)
    os.replace(out + ".tmp", out)
    return out


def clear(data_dir, logo=False):
    try:
        os.remove(os.path.join(folder(data_dir), LOGO if logo else BACKGROUND))
    except FileNotFoundError:
        pass


def files(data_dir):
    """{file name: path} of the title pictures chosen."""
    return {f: os.path.join(folder(data_dir), f) for f in (BACKGROUND, LOGO)
            if os.path.isfile(os.path.join(folder(data_dir), f))}
