"""Stick figures: a body drawn as a stick man below its own head (Greencliff the stickman).

Each frame keeps its head as drawn - the shape on top of the figure, above the neck, and only
what is joined to it there - and everything else becomes sticks: the silhouette is thinned to
its centre lines at a low resolution, the little side spurs thinning leaves are pruned, and each
limb is straightened into a few segments, drawn back bold with round joints. Arms, legs and the
bat keep their pose; the coat and the body are gone. Pure Pillow, no numpy.

skins.json: "stick": {"head": share of the standing height the head takes (about a quarter for
the chibi bodies), "width": stroke as a share of it, "skip": anything thinner than this share
isn't the head even if it's on top (a bat held up), "color": [r, g, b]}.
"""

LOW = 96                    # the silhouette is thinned at most this many pixels across
SPUR = 0.08                 # a branch shorter than this share of the figure is thinning's noise
BEND = 1.2                  # how far (low-res pixels) a limb may stray from its straightened line


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


NEAR = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def _neighbours(on, p):
    x, y = p
    return [(x + dx, y + dy) for dx, dy in NEAR if (x + dx, y + dy) in on]


def limbs(skel, spur):
    """The skeleton's pixels as paths between its ends and joints ([[(x, y)...]...]), with
    branches shorter than `spur` that end in nothing pruned away."""
    on = {(x, y) for y, r in enumerate(skel) for x, v in enumerate(r) if v}
    for _ in range(3):                      # pruning a spur can leave another one
        paths = _paths(on)
        cut = False
        for p in paths:
            ends = [q for q in (p[0], p[-1]) if len(_neighbours(on, q)) <= 1]
            joint = [q for q in (p[0], p[-1]) if len(_neighbours(on, q)) >= 3]
            if ends and joint and len(p) < spur:
                on -= set(p) - set(joint)
                cut = True
            elif len(ends) == 2 and len(p) < 2 * spur:   # a short stick on its own: a streak's
                on -= set(p)
                cut = True
        if not cut:
            break
    return _paths(on)


def _paths(on):
    """Split skeleton pixels into paths running from node to node (an end or a joint)."""
    nodes = {p for p in on if len(_neighbours(on, p)) != 2}
    seen, paths = set(), []
    for start in sorted(nodes):
        for nxt in _neighbours(on, start):
            if (start, nxt) in seen:
                continue
            path, prev, cur = [start], start, nxt
            while True:
                seen.add((prev, cur))
                seen.add((cur, prev))
                path.append(cur)
                if cur in nodes:
                    break
                step = [q for q in _neighbours(on, cur) if q != prev and (cur, q) not in seen]
                if not step:
                    break
                prev, cur = cur, step[0]
            paths.append(path)
    rest = on - {q for p in paths for q in p}          # closed loops with no node on them
    while rest:
        start = min(rest)
        path, cur = [start], start
        rest.discard(start)
        while True:
            step = [q for q in _neighbours(rest, cur)]
            if not step:
                break
            cur = step[0]
            rest.discard(cur)
            path.append(cur)
        paths.append(path + [start])
    return paths


def straighten(path, tol=BEND):
    """Douglas-Peucker: the fewest points that keep the path within tol of its line."""
    if len(path) < 3:
        return list(path)
    (x0, y0), (x1, y1) = path[0], path[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = (dx * dx + dy * dy) ** 0.5
    if norm == 0:
        dist = [((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 for x, y in path]
    else:
        dist = [abs(dy * (x - x0) - dx * (y - y0)) / norm for x, y in path]
    i = max(range(len(path)), key=lambda k: dist[k])
    if dist[i] <= tol:
        return [path[0], path[-1]]
    return straighten(path[:i + 1], tol)[:-1] + straighten(path[i:], tol)


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


def _component(grid, seeds):
    """The cells of grid (0/1 rows) connected to any of seeds (8-neighbours)."""
    h, w = len(grid), len(grid[0]) if grid else 0
    todo = [s for s in seeds if 0 <= s[1] < h and 0 <= s[0] < w and grid[s[1]][s[0]]]
    got = set(todo)
    while todo:
        x, y = todo.pop()
        for dx, dy in NEAR:
            q = (x + dx, y + dy)
            if q not in got and 0 <= q[1] < h and 0 <= q[0] < w and grid[q[1]][q[0]]:
                got.add(q)
                todo.append(q)
    return got


def stickify(img, head_px, width_px, color=(60, 60, 60), skip_px=None):
    """img (RGBA) as a stick man: its head - the top head_px rows of its figure, counted from the
    top of its thickest part when skip_px is given, and only what is joined to the head there -
    kept as drawn, everything else redrawn as straight sticks width_px thick. Same size; a
    picture with nothing in it comes back as it was."""
    from PIL import Image, ImageDraw
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return img
    left, top, right, bottom = box
    head_top = _head_top(alpha, box, skip_px) if skip_px else top
    neck = head_top + int(head_px)
    if neck >= bottom:
        return img
    w, h = right - left, bottom - top
    k = max(1.0, max(w, h) / LOW)
    lw, lh = max(1, round(w / k)), max(1, round(h / k))
    low = alpha.crop(box).resize((lw, lh), Image.BILINEAR).load()
    grid = [[1 if low[x, y] > 110 else 0 for x in range(lw)] for y in range(lh)]
    ln = min(lh, max(0, round((neck - top) / k)))       # the neck, in low-res rows
    lt = min(lh - 1, max(0, round((head_top - top) / k)))

    # the head: the biggest shape above the neck once it is worn down by an eighth of the head's
    # height, so an arm or a bat that only touches it comes loose (the head, many times thicker,
    # stays) - then grown back as far, inside the outline
    above = [r if y < ln else [0] * lw for y, r in enumerate(grid)]
    depth = max(1, round((ln - lt) / 8))
    worn = above
    for _ in range(depth):
        worn = [[1 if worn[y][x] and all(0 <= y + dy < lh and 0 <= x + dx < lw and worn[y + dy][x + dx]
                                         for dx, dy in NEAR) else 0 for x in range(lw)] for y in range(lh)]
    head, left_ = set(), {(x, y) for y in range(lh) for x in range(lw) if worn[y][x]}
    while left_:                            # the biggest blob that survives is the head (a bat held
        blob = _component(worn, [min(left_)])   # up in front of it, or above it, is too thin to)
        left_ -= blob
        if len(blob) > len(head):
            head = blob
    if not head:                            # nothing that thick on top: the plain shape there
        head = _component(above, [(x, y) for y in range(lt, min(ln, lt + 3)) for x in range(lw) if above[y][x]])
    for _ in range(depth + 1):
        head |= {(x + dx, y + dy) for x, y in head for dx, dy in NEAR
                 if 0 <= y + dy < lh and 0 <= x + dx < lw and above[y + dy][x + dx]}
    body = [[0 if (x, y) in head else v for x, v in enumerate(r)] for y, r in enumerate(grid)]

    def at(p):
        return left + (p[0] + 0.5) * k, top + (p[1] + 0.5) * k

    # keep the head's own pixels (full resolution, by the low-res cells it covers)
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    for x, y in head:
        md.rectangle([left + x * k - 1, top + y * k - 1, left + (x + 1) * k + 1, top + (y + 1) * k + 1], fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    draw = ImageDraw.Draw(out)
    fill = tuple(color[:3]) + (255,)
    r = max(1.0, width_px / 2)
    joints, sticks = [], limbs(thin(body), max(3, SPUR * lh))
    for path in sticks:
        pts = [at(p) for p in straighten(path)]
        if len(pts) > 1:
            draw.line(pts, fill=fill, width=max(1, round(width_px)))
        joints += [pts[0], pts[-1]] + pts[1:-1]
    for cx, cy in joints:                               # round joints and ends
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    # a neck from the bottom of the head to the nearest stick below it (not a bat held above it)
    if head:
        hx = sum(x for x, _ in head) / len(head)
        hy = max(y for _, y in head)
        below = [p for path in sticks for p in path if p[1] >= hy]
        if below:
            near = min(below, key=lambda p: (p[0] - hx) ** 2 + (p[1] - hy) ** 2)
            draw.line([at((hx, hy)), at(near)], fill=fill, width=max(1, round(width_px)))
            draw.ellipse([at(near)[0] - r, at(near)[1] - r, at(near)[0] + r, at(near)[1] + r], fill=fill)
    return out
