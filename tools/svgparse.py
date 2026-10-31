"""Minimal SVG reader for the tarkov.dev maps: flattens paths, rects, polygons and circles
(with group transforms) into rings of points in viewBox units, tagged with their layer,
group chain and CSS classes."""
import math, re
import xml.etree.ElementTree as ET
import numpy as np

NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def mat_mul(a, b):
    return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]


def parse_transform(s):
    m = [1, 0, 0, 1, 0, 0]
    if not s:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        v = [float(x) for x in NUM.findall(args)]
        if name == "matrix":
            t = v[:6]
        elif name == "translate":
            t = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
        elif name == "scale":
            t = [v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0]
        elif name == "rotate":
            a = math.radians(v[0]); c, s_ = math.cos(a), math.sin(a)
            t = [c, s_, -s_, c, 0, 0]
            if len(v) == 3:
                t = mat_mul(mat_mul([1, 0, 0, 1, v[1], v[2]], t), [1, 0, 0, 1, -v[1], -v[2]])
        elif name == "skewX":
            t = [1, 0, math.tan(math.radians(v[0])), 1, 0, 0]
        elif name == "skewY":
            t = [1, math.tan(math.radians(v[0])), 0, 1, 0, 0]
        else:
            continue
        m = mat_mul(m, t)
    return m


def apply(m, pts):
    p = np.asarray(pts, dtype=float)
    return np.stack([m[0] * p[:, 0] + m[2] * p[:, 1] + m[4], m[1] * p[:, 0] + m[3] * p[:, 1] + m[5]], axis=1)


def path_rings(d, steps=6):
    """Flatten an SVG path's d attribute into a list of point lists (one per subpath)."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + NUM.pattern, d)
    rings, cur, i = [], [], 0
    x = y = sx = sy = 0.0
    cmd, last_ctrl, last_cmd = None, None, None

    def nums(n):
        nonlocal i
        out = [float(t) for t in toks[i:i + n]]
        i += n
        return out

    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t; i += 1
            if cmd in "Zz":
                if cur:
                    rings.append(cur)
                cur = []; x, y = sx, sy; last_cmd = cmd
                continue
        elif cmd is None:
            i += 1; continue
        rel = cmd.islower(); C = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if C == "M":
            px, py = nums(2)
            if cur:
                rings.append(cur)
            x, y = ox + px, oy + py; sx, sy = x, y; cur = [(x, y)]
            cmd = "l" if rel else "L"
        elif C == "L":
            px, py = nums(2); x, y = ox + px, oy + py; cur.append((x, y))
        elif C == "H":
            (px,) = nums(1); x = ox + px if rel else px; cur.append((x, y))
        elif C == "V":
            (py,) = nums(1); y = oy + py if rel else py; cur.append((x, y))
        elif C in "CS":
            if C == "C":
                x1, y1, x2, y2, px, py = nums(6); x1 += ox; y1 += oy
            else:
                x2, y2, px, py = nums(4)
                if last_ctrl and last_cmd and last_cmd.upper() in "CS":
                    x1, y1 = 2 * x - last_ctrl[0], 2 * y - last_ctrl[1]
                else:
                    x1, y1 = x, y
            x2 += ox; y2 += oy; ex, ey = ox + px, oy + py
            for k in range(1, steps + 1):
                tt = k / steps; mt = 1 - tt
                cur.append((mt ** 3 * x + 3 * mt * mt * tt * x1 + 3 * mt * tt * tt * x2 + tt ** 3 * ex,
                            mt ** 3 * y + 3 * mt * mt * tt * y1 + 3 * mt * tt * tt * y2 + tt ** 3 * ey))
            last_ctrl = (x2, y2); x, y = ex, ey
        elif C in "QT":
            if C == "Q":
                x1, y1, px, py = nums(4); x1 += ox; y1 += oy
            else:
                px, py = nums(2)
                x1, y1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl and last_cmd and last_cmd.upper() in "QT" else (x, y)
            ex, ey = ox + px, oy + py
            for k in range(1, steps + 1):
                tt = k / steps; mt = 1 - tt
                cur.append((mt * mt * x + 2 * mt * tt * x1 + tt * tt * ex, mt * mt * y + 2 * mt * tt * y1 + tt * tt * ey))
            last_ctrl = (x1, y1); x, y = ex, ey
        elif C == "A":
            _, _, _, _, _, px, py = nums(7)
            x, y = ox + px, oy + py; cur.append((x, y))  # arcs are rare here; a straight edge is close enough
        else:
            i += 1
        if C not in "CSQT":
            last_ctrl = None
        last_cmd = cmd
    if cur:
        rings.append(cur)
    return [r for r in rings if len(r) >= 2]


def read(path, styles=None):
    tree = ET.parse(path)
    root = tree.getroot()
    vb = [float(v) for v in root.get("viewBox").split()]
    feats = []

    def tag(e):
        return e.tag.split("}")[-1]

    def walk(e, m, chain, classes, layer):
        for c in e:
            tg = tag(c)
            if tg in ("style", "defs", "symbol", "title", "metadata", "use"):
                continue
            cm = mat_mul(m, parse_transform(c.get("transform")))
            cls = (c.get("class") or "").split() or classes
            if tg == "g":
                lid = layer or c.get("id")
                walk(c, cm, chain + [c.get("id") or ""], cls, lid)
                continue
            rings = []
            if tg == "path" and c.get("d"):
                rings = path_rings(c.get("d"))
            elif tg in ("polygon", "polyline"):
                v = [float(x) for x in NUM.findall(c.get("points", ""))]
                rings = [list(zip(v[0::2], v[1::2]))]
            elif tg == "rect":
                x, y = float(c.get("x", 0)), float(c.get("y", 0))
                w, h = float(c.get("width", 0)), float(c.get("height", 0))
                rings = [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]
            elif tg in ("circle", "ellipse"):
                cx, cy = float(c.get("cx", 0)), float(c.get("cy", 0))
                rx = float(c.get("r") or c.get("rx") or 0); ry = float(c.get("r") or c.get("ry") or 0)
                rings = [[(cx + rx * math.cos(a), cy + ry * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]]
            if not rings:
                continue
            feats.append({
                "layer": layer or "", "chain": chain + [c.get("id") or ""], "cls": cls, "tag": tg,
                "closed": tg != "polyline" and ("z" in (c.get("d") or "z").lower()),
                "rings": [apply(cm, r) for r in rings],
            })

    walk(root, [1, 0, 0, 1, 0, 0], [], [], None)
    return vb, feats
