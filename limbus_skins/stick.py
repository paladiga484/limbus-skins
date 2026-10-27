"""Stick figures: a body drawn as thin lines below its own head (Greencliff the stickman).

Each frame keeps its head as drawn - the top of the figure, down to the neck - and everything
below becomes the silhouette's centre line (thinned at a low resolution, then drawn back as a
stroke), so arms, legs and the bat keep their pose as sticks. Pure Pillow, no numpy.

skins.json: "stick": {"head": share of the standing height the head takes (about a third for
the chibi bodies), "width": stroke as a share of it, "skip": anything thinner than this share
isn't the head even if it's on top (a bat held up), "color": [r, g, b]}.
"""

LOW = 96                    # the silhouette is thinned at most this many pixels across


def thin(grid):
    """Zhang-Suen thinning of a 0/1 grid (list of rows); returns a new grid."""
    h, w = len(grid), len(grid[0]) if grid else 0
    g = [[0] * (w + 2)] + [[0] + list(r) + [0] for r in grid] + [[0] * (w + 2)]
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            drop = []
            for y in range(1, h + 1):
                up, row, down = g[y - 1], g[y], g[y + 1]
                for x in range(1, w + 1):
                    if not row[x]:
                        continue
                    p = (up[x], up[x + 1], row[x + 1], down[x + 1], down[x], down[x - 1], row[x - 1], up[x - 1])
                    n = sum(p)
                    if not 2 <= n <= 6:
                        continue
                    if sum(1 for i in range(8) if not p[i] and p[(i + 1) % 8]) != 1:
                        continue
                    if step == 0 and (p[0] * p[2] * p[4] or p[2] * p[4] * p[6]):
                        continue
                    if step == 1 and (p[0] * p[2] * p[6] or p[0] * p[4] * p[6]):
                        continue
                    drop.append((y, x))
            for y, x in drop:
                g[y][x] = 0
            changed = changed or bool(drop)
    return [r[1:-1] for r in g[1:-1]]


def _head_top(alpha, box, skip_px):
    """The top of the figure's thickest part: anything thinner than skip_px (a bat held up, a
    swing's streak) is worn away first, so the head - not the bat - is what's on top."""
    from PIL import Image, ImageFilter
    left, top, right, bottom = box
    k = max(1.0, max(right - left, bottom - top) / LOW)
    size = 2 * max(1, round(skip_px / k / 2)) + 1
    low = alpha.crop(box).resize((max(1, round((right - left) / k)), max(1, round((bottom - top) / k))), Image.BILINEAR)
    worn = low.point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.MinFilter(size))
    core = worn.getbbox()
    return top if not core else top + max(0, int((core[1] - size // 2) * k))


def stickify(img, head_px, width_px, color=(60, 60, 60), skip_px=None):
    """img (RGBA) with everything below the head - the top head_px rows of its figure, counted
    from the top of its thickest part when skip_px is given - redrawn as lines width_px thick.
    Same size; a picture with nothing in it comes back as it was."""
    from PIL import Image, ImageDraw
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return img
    left, top, right, bottom = box
    neck = (_head_top(alpha, box, skip_px) if skip_px else top) + int(head_px)
    if neck >= bottom:
        return img
    w, h = right - left, bottom - neck
    k = max(1.0, max(w, h) / LOW)
    lw, lh = max(1, round(w / k)), max(1, round(h / k))
    low = alpha.crop((left, neck, right, bottom)).resize((lw, lh), Image.BILINEAR)
    px = low.load()
    skel = thin([[1 if px[x, y] > 110 else 0 for x in range(lw)] for y in range(lh)])

    out = img.copy()
    cut = Image.new("RGBA", (img.width, img.height - neck), (0, 0, 0, 0))
    out.paste(cut, (0, neck))
    draw = ImageDraw.Draw(out)
    fill = tuple(color[:3]) + (255,)
    r = max(1.0, width_px / 2)

    def at(x, y):
        return left + (x + 0.5) * k, neck + (y + 0.5) * k

    for y in range(lh):
        for x in range(lw):
            if not skel[y][x]:
                continue
            cx, cy = at(x, y)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
            for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < lw and ny < lh and skel[ny][nx]:
                    draw.line([at(x, y), at(nx, ny)], fill=fill, width=max(1, round(width_px)))
    # a neck from the head down to where the stick body starts
    tops = [(x, y) for y in range(lh) for x in range(lw) if skel[y][x]]
    if tops:
        y0 = min(y for _, y in tops)
        xs = [x for x, y in tops if y == y0]
        cx, cy = at(sum(xs) / len(xs), y0)
        draw.line([(cx, neck - r), (cx, cy)], fill=fill, width=max(1, round(width_px)))
    return out
