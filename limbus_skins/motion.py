"""Limbus-style motion from still poses.

A Library of Ruina (or Lobotomy Corporation) character comes as a few still poses per motion -
put straight onto a Limbus body they flick from one to the next, which is how Ruina looks. This
turns them into Limbus's kind of animation:
- idle breathes (a slow rise and settle, the weight shifting);
- a skill winds up (leans back and crouches), strikes (lunges, stretches, smears) and lands:
  the impact frame flashes and draws the hit in the skill's sin colour - a slash arc, a thrust
  streak or a blunt burst, by its attack type;
- moving leans into the dash, guarding braces, evading leans away, a hit knocks the body back
  with a flash, and a death sinks and darkens.
Characters face left here (repaint turns them as their body needs). Every frame keeps its feet
on the pose's pivot, so the body stands where the game puts it.
"""
import math

from PIL import Image, ImageDraw, ImageFilter

SIN_RGB = {"CRIMSON": (236, 58, 34), "SCARLET": (240, 112, 40), "AMBER": (248, 196, 52), "SHAMROCK": (128, 208, 72),
           "AZURE": (72, 168, 244), "INDIGO": (96, 104, 236), "VIOLET": (178, 92, 226)}
NEUTRAL = (255, 224, 150)
SLASH, PIERCE, BLUNT = "SLASH", "PENETRATE", "HIT"
_WORDS = ((BLUNT, ("greatsword", "mace", "hammer", "gauntlet", "hit", "blunt", "smash", "punch", "slam", "grab")),
          (PIERCE, ("penetrate", "panetrate", "pierce", "thrust", "stab", "lunge", "charge", "peck", "spear", "lance",
                    "hook", "laser", "revolver", "shotgun", "gun", "fire", "range")),
          (SLASH, ("slash", "swing", "sythe", "scythe", "claw", "cut", "judge", "blade", "axe", "sword", "durandal",
                   "dualwield", "atk")))


def attack_of(pose_name, fallback):
    """The attack type a pose draws, by its name (Ruina names poses Slash, Penetrate, Hit...)."""
    low = pose_name.lower()
    for kind, words in _WORDS:
        if any(w in low for w in words):
            return kind
    return fallback


# ---------------------------------------------------------------- one frame

class Pose:
    """A picture and its feet (pixels, top-down) on a canvas with room around it."""

    def __init__(self, img, pivot):
        w, h = img.size
        self.h = h * (1 - pivot[1]) or h             # feet to the top of the head
        u = max(self.h, h * 0.5)                      # room for what is drawn round it: the hit in front
        left, right, top, bottom = int(0.85 * u) + 8, int(0.4 * u) + 8, int(0.3 * u) + 8, int(0.15 * u) + 8
        self.img = Image.new("RGBA", (w + left + right, h + top + bottom), (0, 0, 0, 0))
        self.img.alpha_composite(img.convert("RGBA"), (left, top))
        self.feet = (left + pivot[0] * w, top + (1 - pivot[1]) * h)

    def body(self):
        return self.img.getchannel("A").getbbox() or (0, 0, 1, 1)

    def move(self, lean=0.0, sx=1.0, sy=1.0, dx=0.0, dy=0.0):
        """Scaled about the feet, leant (degrees; + leans forward, i.e. to the left) about them,
        then shifted (fractions of its height; - is forward). The feet stay where they were."""
        fx, fy = self.feet
        if sx != 1.0 or sy != 1.0:
            W, H = self.img.size
            big = self.img.resize((max(1, round(W * sx)), max(1, round(H * sy))), Image.BICUBIC)
            out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            out.paste(big, (round(fx - fx * sx), round(fy - fy * sy)))        # the feet stay put
            self.img = out
        if lean:
            self.img = self.img.rotate(lean, center=(fx, fy), resample=Image.BICUBIC)
        if dx or dy:
            self.img = _shifted(self.img, dx * self.h, dy * self.h)
        return self

    def smear(self, n=3, step=0.05, behind=1):
        """Fading copies trailing behind it (behind=1: to the right, as it moves left)."""
        base = self.img
        out = Image.new("RGBA", base.size, (0, 0, 0, 0))
        for k in range(n, 0, -1):
            ghost = base.copy()
            ghost.putalpha(ghost.getchannel("A").point(lambda v, k=k: int(v * 0.32 / k)))
            out = Image.alpha_composite(out, _shifted(ghost, behind * k * step * self.h, 0))
        self.img = Image.alpha_composite(out, base)
        return self

    def flash(self, rgb=(255, 255, 255), rim=0.75, wash=0.06):
        """A bright rim round the body and a wash over it: the moment of the hit."""
        x0, y0, x1, y1 = self.body()
        m = int(self.h / 30) + 6
        box = (max(0, x0 - m), max(0, y0 - m), min(self.img.width, x1 + m), min(self.img.height, y1 + m))
        part = self.img.crop(box)
        a = part.getchannel("A")
        glow = Image.new("RGBA", part.size, rgb + (0,))
        glow.putalpha(a.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(max(2, self.h / 90)))
                      .point(lambda v: int(v * rim)))
        over = Image.new("RGBA", part.size, rgb + (0,))
        over.putalpha(a.point(lambda v: int(v * wash)))
        self.img.paste(Image.alpha_composite(glow, Image.alpha_composite(part, over)), box[:2])
        return self

    def darken(self, amount):
        if amount <= 0:
            return self
        shade = Image.new("RGBA", self.img.size, (12, 6, 8, 0))
        shade.putalpha(self.img.getchannel("A").point(lambda v: int(v * amount)))
        self.img = Image.alpha_composite(self.img, shade)
        return self

    def front(self):
        """Where the blow lands: the middle of the front-most drawn pixels (the leftmost tenth of
        the body's width), as (x, y)."""
        a = self.img.getchannel("A")
        x0, y0, x1, y1 = self.body()
        band = a.crop((x0, y0, x0 + max(2, (x1 - x0) // 10), y1))
        ys = [y for y in range(band.height) for x in range(0, band.width, 2) if band.getpixel((x, y)) > 64]
        return x0, y0 + (sorted(ys)[len(ys) // 2] if ys else (y1 - y0) // 2)

    def fx(self, attack, rgb, big=1.0):
        """The hit itself, drawn where the blow lands (in front, to the left); big: a boss's, larger."""
        x0, y0, x1, y1 = self.body()
        h = self.h * big
        hx, hy = self.front()
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if attack == SLASH:                              # a crescent swept in front, thin at both ends
            rx, ry = 0.62 * h, 0.46 * h
            cx, cy = hx + 0.6 * rx, hy
            box = (cx - rx, cy - ry, cx + rx, cy + ry)
            steps = 18
            for i in range(steps):
                a0, a1 = 112 + i * (136 / steps), 112 + (i + 1) * (136 / steps)
                w = max(1, int(0.075 * h * math.sin(math.pi * (i + 0.5) / steps)))
                d.arc(box, a0, a1, fill=rgb + (230,), width=w)
            core = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            dc = ImageDraw.Draw(core)
            for i in range(steps):
                a0, a1 = 116 + i * (128 / steps), 116 + (i + 1) * (128 / steps)
                w = max(1, int(0.03 * h * math.sin(math.pi * (i + 0.5) / steps)))
                dc.arc(box, a0, a1, fill=(255, 255, 255, 255), width=w)
            layer = Image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(max(1.5, h / 160))), core)
        elif attack == PIERCE:                           # a streak driven forward, pointed at its tip
            y = hy
            tip, back = hx - 0.55 * h, hx + 0.35 * h
            t = 0.05 * h
            d.polygon([(back, y - t), (tip, y), (back, y + t)], fill=rgb + (225,))
            for k, off in enumerate((-0.11, 0.1, -0.2)):
                d.line([(back - 0.1 * h * k, y + off * h), (tip + 0.25 * h + 0.08 * h * k, y + off * h)],
                       fill=rgb + (150,), width=max(1, int(0.008 * h)))
            layer = layer.filter(ImageFilter.GaussianBlur(max(1.2, h / 200)))
            ImageDraw.Draw(layer).polygon([(back, y - t * 0.35), (tip + 0.06 * h, y), (back, y + t * 0.35)],
                                          fill=(255, 255, 255, 255))
        else:                                            # a burst where the blow lands
            cx, cy, r = hx - 0.12 * h, hy, 0.2 * h
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=rgb + (230,), width=max(2, int(0.035 * h)))
            for k in range(10):
                a = 2 * math.pi * k / 10 + 0.3
                p = [(cx + math.cos(a) * r * 0.55, cy + math.sin(a) * r * 0.55),
                     (cx + math.cos(a + 0.12) * r * 1.6, cy + math.sin(a + 0.12) * r * 1.6),
                     (cx + math.cos(a - 0.12) * r * 0.9, cy + math.sin(a - 0.12) * r * 0.9)]
                d.polygon(p, fill=rgb + (200,))
            layer = layer.filter(ImageFilter.GaussianBlur(max(1.2, h / 220)))
            ImageDraw.Draw(layer).ellipse((cx - r * 0.35, cy - r * 0.35, cx + r * 0.35, cy + r * 0.35), fill=(255, 255, 255, 235))
        bb = layer.getchannel("A").getbbox()
        if bb:                                        # the glow round it, blurred where it is
            m = int(h / 15) + 6
            box = (max(0, bb[0] - m), max(0, bb[1] - m), min(layer.width, bb[2] + m), min(layer.height, bb[3] + m))
            part = layer.crop(box)
            glow = part.filter(ImageFilter.GaussianBlur(max(3, h / 45)))
            self.img.paste(Image.alpha_composite(Image.alpha_composite(self.img.crop(box), glow), part), box[:2])
        return self

    def done(self):
        """(picture cropped to what's drawn, pivot as fractions bottom-up)."""
        bb = self.img.getchannel("A").getbbox() or (0, 0, 1, 1)
        img = self.img.crop(bb)
        fx, fy = self.feet
        return img, ((fx - bb[0]) / img.width, (bb[3] - fy) / img.height)


def _toward_white(rgb, k=0.5):
    return tuple(int(c + (255 - c) * k) for c in rgb)


def _shifted(img, dx, dy):
    """img moved by (dx, dy) pixels on a canvas of its own size (what leaves the canvas is cut)."""
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (round(dx), round(dy)))
    return out


# ---------------------------------------------------------------- whole motions

IDLE_FRAMES = 8


def idle(poses):
    """A breathing loop from the first pose (a character drawn with more idle poses keeps them)."""
    if len(poses) > 2:
        return list(poses)
    n, img, pv, k = poses[0]
    out = []
    for i in range(IDLE_FRAMES):
        ph = 2 * math.pi * i / IDLE_FRAMES
        s = math.sin(ph)
        out.append((f"{n}_breathe{i}", *Pose(img, pv).move(lean=0.5 * math.sin(ph + 0.7), sx=1 - 0.005 * s,
                                                          sy=1 + 0.014 * s).done(), k))
    return out


def skill(poses, attack, rgb, boss=False):
    """Wind-up, then for each pose a lunging strike and its impact, then the recovery. A motion
    already drawn in many poses (Lobotomy Corporation's own clips) keeps them and gains the impact.
    A boss hits harder: a bigger hit, a brighter flash, and the impact held a frame longer."""
    big, rim = (1.6, 1.0) if boss else (1.0, 0.75)
    out = []
    if len(poses) >= 4:
        hit = round(0.6 * (len(poses) - 1))
        for i, (n, img, pv, k) in enumerate(poses):
            p = Pose(img, pv)
            if i == hit - 1:
                p.smear()
            if i == hit:
                p.flash(_toward_white(rgb), rim=rim).fx(attack_of(n, attack), rgb, big)
            out.append((f"{n}_m{i}", *p.done(), k))
            if i == hit and boss:
                out.append((f"{n}_m{i}_held", *Pose(img, pv).flash(_toward_white(rgb), rim=rim * 0.6).fx(attack_of(n, attack), rgb, big * 1.15).done(), k))
        return out
    n, img, pv, k = poses[0]
    out.append((f"{n}_windup", *Pose(img, pv).move(lean=-4, sx=1.025, sy=0.97, dx=0.02).done(), k))
    for i, (n, img, pv, k) in enumerate(poses):
        out.append((f"{n}_strike{i}", *Pose(img, pv).move(lean=3, sx=0.985, sy=1.02, dx=-0.03).smear().done(), k))
        out.append((f"{n}_impact{i}", *Pose(img, pv).move(lean=2, dx=-0.035).flash(_toward_white(rgb), rim=rim).fx(attack_of(n, attack), rgb, big).done(), k))
        if boss:
            out.append((f"{n}_impact{i}_held", *Pose(img, pv).move(lean=2, dx=-0.035).flash(_toward_white(rgb), rim=rim * 0.6)
                        .fx(attack_of(n, attack), rgb, big * 1.15).done(), k))
    n, img, pv, k = poses[-1]
    out.append((f"{n}_recover", *Pose(img, pv).move(lean=1, dx=-0.01).done(), k))
    return out


def dash(poses):
    return [(f"{n}_dash{i}", *Pose(img, pv).move(lean=6, sx=0.985, sy=1.015).smear(4, 0.06).done(), k)
            for i, (n, img, pv, k) in enumerate(poses)]


def guard(poses):
    n, img, pv, k = poses[0]
    return [(f"{n}_brace", *Pose(img, pv).move(lean=-2, sx=1.02, sy=0.97).done(), k),
            (f"{n}_braced", *Pose(img, pv).move(lean=-2, sx=1.02, sy=0.97).flash((190, 225, 255), rim=0.6, wash=0.08).done(), k)]


def evade(poses):
    n, img, pv, k = poses[0]
    return [(f"{n}_lean", *Pose(img, pv).move(lean=-8, dx=0.05).smear(2, 0.05, behind=-1).done(), k),
            (f"{n}_back", *Pose(img, pv).move(lean=-4, dx=0.03).done(), k)]


def hurt(poses):
    n, img, pv, k = poses[0]
    return [(f"{n}_struck", *Pose(img, pv).move(lean=-5, dx=0.03).flash((255, 120, 110), rim=0.6, wash=0.12).done(), k),
            (f"{n}_reel", *Pose(img, pv).move(lean=-3, dx=0.02).done(), k)]


def death(poses, boss=False, rgb=NEUTRAL):
    n, img, pv, k = poses[-1]
    if boss:                                         # a boss goes out with a blast: flash, burst, sink, gone dark
        return [(f"{n}_end0", *Pose(img, pv).move(lean=-4).flash((255, 255, 255), rim=1.0, wash=0.45).done(), k),
                (f"{n}_end1", *Pose(img, pv).move(lean=-9, dx=0.02).flash(_toward_white(rgb), rim=0.9, wash=0.2).fx(BLUNT, rgb, 2.2).done(), k),
                (f"{n}_end2", *Pose(img, pv).move(lean=-15, sy=0.97, dx=0.04).darken(0.3).fx(BLUNT, rgb, 3.0).done(), k),
                (f"{n}_end3", *Pose(img, pv).move(lean=-20, sy=0.94, dx=0.06).darken(0.6).done(), k)]
    return [(f"{n}_fall{i}", *Pose(img, pv).move(lean=-6 * (i + 1), sy=1 - 0.015 * i, dx=0.02 * i).darken(0.12 * i).done(), k)
            for i in range(3)]


def animate(kind, poses, attack=None, sin=None, boss=False):
    """[(name, picture, pivot, tag)] for a motion kind ("idle", "move", "gud", "miss", "dmg", "dead",
    "S1"...) from its still poses [(name, picture, pivot, tag)] - the tag (the source's pixels per
    unit, say) rides along with each frame drawn from that pose. boss: a boss's - bigger hits, a
    death that bursts."""
    rgb = SIN_RGB.get(sin or "", NEUTRAL)
    if kind == "idle":
        return idle(poses)
    if kind == "move":
        return dash(poses)
    if kind == "gud":
        return guard(poses)
    if kind == "miss":
        return evade(poses)
    if kind == "dmg":
        return hurt(poses)
    if kind == "dead":
        return death(poses, boss, rgb)
    if kind.startswith("S"):
        return skill(poses, attack or {"S1": SLASH, "S2": PIERCE}.get(kind, BLUNT), rgb, boss)
    return list(poses)
