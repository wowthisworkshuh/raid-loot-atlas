"""Build rough "dev blockout" models for each map.

Building outlines, floors, walls, roads, water and rocks come from tarkov.dev's community SVG maps
(github.com/the-hideout/tarkov-dev-svg-maps, CC BY-NC-SA 4.0), placed in game coordinates with
tarkov.dev's own map bounds (Labs re-fitted against loot spots). Heights come from the loot spots:
the terrain is the low ground under each area's spots, building tops and floor levels are where the
spots inside each outline sit. The Labyrinth has no SVG, so it is built from its spots alone.

Usage: python build_blockouts.py --svg <tarkov-dev-svg-maps folder> --data <atlas data/> --out <atlas maps/>
"""
import argparse, json, math, os, sys
import numpy as np
from matplotlib.path import Path

sys.path.insert(0, os.path.dirname(__file__))
import svgparse

# svg file, bounds [[x1,z1],[x2,z2]] in game coords, how svg (s,t in 0..1) maps onto them,
# layer -> floor index, and the height band of each floor for the indoor maps.
OUT = {"Underground_Level": -1, "Ground_Level": 0, "First_Floor": 1, "Second_Floor": 2, "Third_Floor": 3}
MAPS = {
    "bigmap": dict(svg="Customs", bounds=[[698, -307], [-372, 237]], uv="s,t", floors=OUT),
    "woods": dict(svg="Woods", bounds=[[646, -914], [-761, 442]], uv="s,t", floors=OUT),
    "shoreline": dict(svg="Shoreline", bounds=[[504, -415], [-1056, 618]], uv="s,t", floors=OUT),
    "interchange": dict(svg="Interchange", bounds=[[598, -442], [-433, 426]], uv="s,t", floors=OUT),
    "lighthouse": dict(svg="Lighthouse", bounds=[[515, -998], [-545, 725]], uv="s,t", floors=OUT),
    "rezervbase": dict(svg="Reserve", bounds=[[289, -274], [-303, 272]], uv="s,t", floors={**OUT, "Bunkers": -1}),
    "sandbox": dict(svg="GroundZero", bounds=[[249, -124], [-99, 364]], uv="s,t", floors=OUT),
    "factory4_day": dict(svg="Factory", bounds=[[77, -64.5], [-65.5, 67.4]], uv="t,1-s", indoor=True,
                         floors={"Basement": -1, "Ground_Floor": 0, "Second_Floor": 1, "Third_Floor": 2},
                         bands={-1: (-50, -1), 0: (-1, 3), 1: (3, 6), 2: (6, 50)}),
    "laboratory": dict(svg="Labs", bounds=[[-93.5, -474], [-297, -184]], uv="1-t,s", indoor=True,
                       floors={"Technical_Level": -1, "First_Level": 0, "Second_Level": 1},
                       bands={-1: (-50, -0.9), 0: (-0.9, 3), 1: (3, 50)}),
}
UV = {"s,t": lambda s, t: (s, t), "t,1-s": lambda s, t: (t, 1 - s), "1-t,s": lambda s, t: (1 - t, s)}
STROKE = {"road_small": 5, "road_medium": 8, "road_large": 12}
R = lambda v: round(float(v), 1)


def flat(ring):
    return [R(v) for xz in ring for v in xz]


class Mapper:
    def __init__(self, vb, bounds, uv):
        (self.x1, self.z1), (self.x2, self.z2) = bounds
        self.vb, self.fn = vb, UV[uv]
        self.scale = abs(self.x2 - self.x1) / vb[2]  # metres per svg unit (roughly the same both ways)

    def __call__(self, r):
        s = (r[:, 0] - self.vb[0]) / self.vb[2]; t = (r[:, 1] - self.vb[1]) / self.vb[3]
        a, b = self.fn(s, t)
        return np.stack([self.x1 + a * (self.x2 - self.x1), self.z1 + b * (self.z2 - self.z1)], 1)


def simplify(ring, tol):
    """Drop points closer than tol to the previous kept point (outlines are drawn in fine detail)."""
    out = [ring[0]]
    for p in ring[1:]:
        if np.hypot(*(p - out[-1])) >= tol:
            out.append(p)
    return np.array(out)


def area(ring):
    x, z = ring[:, 0], ring[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(z, 1)) - np.dot(z, np.roll(x, 1)))


def levels_of(ys, gap=1.7):
    """Floor heights from the spots inside one outline: groups of spots separated by gaps."""
    if len(ys) == 0:
        return []
    ys = np.sort(ys); out = [ys[0]]
    for a, b in zip(ys[:-1], ys[1:]):
        if b - a > gap:
            out.append(b)
    return out


class Terrain:
    """Rough ground height grid from the loot spots (low quantile per cell, holes filled by diffusion)."""
    def __init__(self, pts, bounds, n=110):
        xs, zs = sorted([bounds[0][0], bounds[1][0]]), sorted([bounds[0][1], bounds[1][1]])
        self.x0, self.z0 = xs[0], zs[0]
        self.dx = (xs[1] - xs[0]) / (n - 1); self.dz = (zs[1] - zs[0]) / (n - 1)
        self.n = n
        h = np.full((n, n), np.nan)
        ix = np.clip(np.round((pts[:, 0] - self.x0) / self.dx).astype(int), 0, n - 1)
        iz = np.clip(np.round((pts[:, 2] - self.z0) / self.dz).astype(int), 0, n - 1)
        cells = {}
        for a, b, y in zip(iz, ix, pts[:, 1]):
            cells.setdefault((a, b), []).append(y)
        for (a, b), ys in cells.items():
            h[a, b] = np.quantile(ys, 0.2)
        known = ~np.isnan(h)
        base = np.nanmedian(h)
        g = np.where(known, h, base)
        for _ in range(400):  # fill the gaps smoothly between known cells
            p = np.pad(g, 1, mode="edge")
            avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
            g = np.where(known, h, avg)
        p = np.pad(g, 1, mode="edge")  # one light smoothing pass so spots on tables don't make spikes
        g = (p[1:-1, 1:-1] * 4 + p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 8
        self.h = g

    def at(self, x, z):
        fx = np.clip((np.asarray(x) - self.x0) / self.dx, 0, self.n - 1.001)
        fz = np.clip((np.asarray(z) - self.z0) / self.dz, 0, self.n - 1.001)
        i, j = fz.astype(int), fx.astype(int); u, v = fz - i, fx - j
        h = self.h
        return (h[i, j] * (1 - u) * (1 - v) + h[i + 1, j] * u * (1 - v) + h[i, j + 1] * (1 - u) * v + h[i + 1, j + 1] * u * v)

    def json(self):
        return {"x0": R(self.x0), "z0": R(self.z0), "dx": round(self.dx, 3), "dz": round(self.dz, 3), "n": self.n,
                "h": [R(v) for v in self.h.ravel()]}


def build_svg_map(mid, cfg, svgdir, pts):
    vb, feats = svgparse.read(os.path.join(svgdir, cfg["svg"] + ".svg"))
    M = Mapper(vb, cfg["bounds"], cfg["uv"])
    tol = max(0.4, M.scale * 0.6)
    indoor = cfg.get("indoor", False)
    xz = pts[:, [0, 2]]
    terrain = None
    if not indoor:
        # terrain from outdoor spots only: spots inside a building outline are on its floors, not the ground
        indoors = np.zeros(len(pts), bool)
        for ft in feats:
            if cfg["floors"].get(ft["layer"]) == 0 and set(ft["cls"]) & {"building", "structure"}:
                for raw in ft["rings"]:
                    if len(raw) >= 3:
                        indoors |= Path(M(raw)).contains_points(xz)
        terrain = Terrain(pts[~indoors], cfg["bounds"])
    items, counts = [], {}

    def add(it):
        items.append(it); counts[it["t"] + ":" + it.get("k", "")] = counts.get(it["t"] + ":" + it.get("k", ""), 0) + 1

    def inside(ring):
        return Path(ring).contains_points(xz)

    # floor heights for indoor maps: low quantile of spots in each band
    band_floor = {}
    if indoor:
        for f, (lo, hi) in cfg["bands"].items():
            ys = pts[(pts[:, 1] >= lo) & (pts[:, 1] < hi), 1]
            band_floor[f] = float(np.quantile(ys, 0.03)) - 0.1 if len(ys) else lo

    for ft in feats:
        cls = set(ft["cls"]); floor = cfg["floors"].get(ft["layer"])
        if floor is None:
            continue
        group = ft["chain"][1] if len(ft["chain"]) > 1 else ""
        stroked = bool(cls & {"road_small", "road_medium", "road_large", "fence", "railroad", "road_gravel", "road_tarmac", "powerline"}) and not cls & {"cement", "tarmac", "gravel"}
        for raw in ft["rings"]:
            ring = M(raw)
            if stroked:
                if "powerline" in cls:
                    continue
                line = simplify(ring, tol)
                if len(line) < 2:
                    continue
                if "fence" in cls:
                    if indoor:
                        continue
                    add({"t": "fence", "p": flat(line)})
                else:
                    w = next((STROKE[c] for c in cls if c in STROKE), 3 if "railroad" in cls else 5) * M.scale
                    k = "rail" if "railroad" in cls else ("dirt" if "road_gravel" in cls else "road")
                    it = {"t": "line", "k": k, "p": flat(line), "w": R(max(1.2, w))}
                    if floor > 0 and terrain is not None:  # ramps on upper floors (Interchange)
                        it["y"] = R(float(np.median(terrain.at(line[:, 0], line[:, 1]))) + 4 * floor)
                    add(it)
                continue
            ring = simplify(ring, tol)
            if len(ring) < 3 or area(ring) < 1.0:
                continue
            a = area(ring)
            if indoor:
                y0 = band_floor.get(floor, 0.0)
                if "wall" in cls or group.startswith("Wall"):
                    add({"t": "block", "k": "wall", "r": flat(ring), "b": R(y0), "h": R(y0 + 3.4)})
                elif group.startswith("Obstacles"):
                    add({"t": "block", "k": "prop", "r": flat(ring), "b": R(y0), "h": R(y0 + 1.3)})
                elif group == "Building":
                    add({"t": "block", "k": "bld", "r": flat(ring), "b": R(y0), "h": R(y0 + 3.4)})
                elif "floor" in cls or group == "Rooms" or "cement" in cls:
                    add({"t": "slab", "r": flat(ring), "y": R(y0)})
                    if group == "Rooms":  # Labs: room outlines become walls
                        add({"t": "fence", "k": "room", "p": flat(np.vstack([ring, ring[:1]])), "b": R(y0), "h": R(y0 + 3.2)})
                continue
            gx, gz = ring[:, 0], ring[:, 1]
            ground = float(np.min(terrain.at(gx, gz))) if terrain is not None else 0.0
            if cls & {"water"}:
                add({"t": "flat", "k": "water", "r": flat(ring)})
            elif cls & {"cement", "tarmac", "gravel", "wood"} and floor <= 0:
                k = "pave" if "cement" in cls else "wood" if "wood" in cls else "road"
                add({"t": "flat", "k": k, "r": flat(ring)})
            elif "trees" in cls:
                add({"t": "flat", "k": "veg", "r": flat(ring)})
            elif "rock" in cls:
                add({"t": "block", "k": "rock", "r": flat(ring), "b": R(ground - 0.5), "h": R(ground + min(4, 1 + math.sqrt(a) * 0.15))})
            elif cls & {"building", "structure", "chopper", "plane", "misc"} and floor == 0:
                ys = pts[inside(ring), 1]
                ys = ys[(ys > ground - 1.5) & (ys < ground + 60)]
                top = (float(np.max(ys)) + 2.6) if len(ys) else ground + (7 if a > 600 else 4.5 if a > 40 else 2.5)
                if cls & {"misc", "chopper", "plane"}:
                    top = min(top, ground + 3)
                top = max(top, ground + 2.5)
                add({"t": "block", "k": "bld", "r": flat(ring), "b": R(ground - 0.3), "h": R(top)})
            elif (cls & {"floor", "locked", "building", "cement", "tarmac"}) and floor != 0:
                ys = pts[inside(ring), 1]
                if floor > 0:
                    lv = levels_of(ys[ys > ground + 1.8])
                    y = (lv[floor - 1] - 0.1) if len(lv) >= floor else ground + 3.3 * floor
                    add({"t": "slab", "r": flat(ring), "y": R(y)})
                else:
                    below = ys[ys < ground - 1.5]
                    y = float(np.min(below)) - 0.1 if len(below) else ground - 3.5
                    add({"t": "slab", "k": "under", "r": flat(ring), "y": R(y)})
    out = {"id": mid, "source": "tarkov.dev SVG maps (CC BY-NC-SA 4.0) + loot spot heights",
           "ground": terrain.json() if terrain is not None else None, "items": items}
    if indoor:
        out["floorY"] = {str(k): R(v) for k, v in band_floor.items()}
    return out, counts


def build_from_spots(mid, pts, cell=2.0):
    """No outline map exists: floor tiles where spots are, walls round the edge of each floor."""
    items = []
    ys = np.sort(pts[:, 1]); lv = levels_of(ys, gap=2.2)
    edges = lv[1:] + [1e9]
    counts = {}
    for li, (lo, hi) in enumerate(zip(lv, edges)):
        sel = pts[(pts[:, 1] >= lo - 0.01) & (pts[:, 1] < hi)]
        if len(sel) < 3:
            continue
        y0 = float(np.quantile(sel[:, 1], 0.05)) - 0.15
        ix = np.floor(sel[:, 0] / cell).astype(int); iz = np.floor(sel[:, 2] / cell).astype(int)
        occ = set(zip(ix, iz))
        occ |= {(a + da, b + db) for a, b in occ for da in (-1, 0, 1) for db in (-1, 0, 1)}  # rooms are wider than their spots
        # close one-cell gaps so corridors join up
        grow = {(a, b) for a in range(min(x for x, _ in occ) - 1, max(x for x, _ in occ) + 2)
                for b in range(min(z for _, z in occ) - 1, max(z for _, z in occ) + 2)
                if (a, b) not in occ and sum((a + d, b) in occ for d in (-1, 1)) == 2 or (a, b) not in occ and sum((a, b + d) in occ for d in (-1, 1)) == 2}
        occ |= grow
        rows = {}
        for a, b in occ:
            rows.setdefault(b, []).append(a)
        for b, xs in rows.items():  # merge each row into runs of tiles
            xs.sort(); start = prev = xs[0]
            for x in xs[1:] + [None]:
                if x is not None and x == prev + 1:
                    prev = x; continue
                x0, x1, z0, z1 = start * cell, (prev + 1) * cell, b * cell, (b + 1) * cell
                items.append({"t": "slab", "r": [R(x0), R(z0), R(x1), R(z0), R(x1), R(z1), R(x0), R(z1)], "y": R(y0)})
                if x is not None:
                    start = prev = x
        for a, b in occ:
            for (da, db), seg in {(-1, 0): ((a, b), (a, b + 1)), (1, 0): ((a + 1, b), (a + 1, b + 1)),
                                  (0, -1): ((a, b), (a + 1, b)), (0, 1): ((a, b + 1), (a + 1, b + 1))}.items():
                if (a + da, b + db) not in occ:
                    (p, q), (r, s) = seg
                    items.append({"t": "fence", "k": "room", "p": [R(p * cell), R(q * cell), R(r * cell), R(s * cell)], "b": R(y0), "h": R(y0 + 3.0)})
        counts[f"floor {li}"] = len(occ)
    return {"id": mid, "source": "built from loot spots only (no outline map exists)", "ground": None, "items": items}, counts


TDEV_KEY = {"bigmap": "customs", "factory4_day": "factory", "woods": "woods", "shoreline": "shoreline", "interchange": "interchange",
            "lighthouse": "lighthouse", "rezervbase": "reserve", "laboratory": "the-lab", "sandbox": "ground-zero", "labyrinth": "the-labyrinth"}
EXIT_KEY = {"bigmap": "C", "factory4_day": "F", "woods": "W", "shoreline": "S", "interchange": "I", "lighthouse": "L",
            "rezervbase": "R", "laboratory": "LB", "sandbox": "G", "labyrinth": "LY"}


def markers(mid, out, tdev_maps, exits_txt, pts):
    """Place names (tarkov.dev map labels) and extracts/transits, with heights that sit on the blockout."""
    G = out["ground"]
    def ground(x, z):
        if not G:
            return None
        n = G["n"]; h = np.array(G["h"]).reshape(n, n)
        j = int(np.clip(round((x - G["x0"]) / G["dx"]), 0, n - 1)); i = int(np.clip(round((z - G["z0"]) / G["dz"]), 0, n - 1))
        return float(h[i, j])
    blocks = [(np.array(it["r"]).reshape(-1, 2), it["h"]) for it in out["items"] if it["t"] == "block" and it.get("k") == "bld"]
    def roof(x, z):
        tops = [h for ring, h in blocks if Path(ring).contains_point((x, z))]
        return max(tops) if tops else None
    cfg = next((m for g in tdev_maps for m in g.get("maps", []) if m.get("key") == TDEV_KEY[mid]), None)
    labels = []
    floor_y = {float(k): v for k, v in (out.get("floorY") or {}).items()}
    for lb in (cfg or {}).get("labels", []):
        x, z = lb["position"][:2]
        lo, hi = lb.get("bottom", -1e4), lb.get("top", 1e4)
        if lo > -1e3:
            y = lo + 2.4
        else:
            g, r = ground(x, z), roof(x, z)
            near = pts[(np.abs(pts[:, 0] - x) < 25) & (np.abs(pts[:, 2] - z) < 25), 1]
            base = g if g is not None else (float(np.median(near)) if len(near) else 0.0)
            y = max(base, r or base) + 3
        labels.append({"n": lb["text"], "x": R(x), "y": R(y), "z": R(z), "lo": R(max(lo, -1e3)), "hi": R(min(hi, 1e3)), "s": lb.get("size", 60)})
    exits, seen = [], set()
    xs = [b[0] for b in MAPS[mid]["bounds"]] if mid in MAPS else [pts[:, 0].min() - 20, pts[:, 0].max() + 20]
    zs = [b[1] for b in MAPS[mid]["bounds"]] if mid in MAPS else [pts[:, 2].min() - 20, pts[:, 2].max() + 20]
    for line in exits_txt:
        k, kind, name, x, y, z = line.split("|")
        if k != EXIT_KEY[mid]:
            continue
        x, y, z = float(x), float(y), float(z)
        if not (min(xs) - 40 <= x <= max(xs) + 40 and min(zs) - 40 <= z <= max(zs) + 40):
            continue  # tarkov.dev lists a couple of Woods exits under Ground Zero
        key = (name, kind)
        if key in seen:
            continue
        seen.add(key)
        exits.append({"n": name, "k": kind, "x": x, "y": y, "z": z})
    # an exit both sides can use: merge the PMC and Scav entries into one "any" marker
    by = {}
    for e in exits:
        by.setdefault(e["n"], []).append(e)
    merged = []
    for name, es in by.items():
        kinds = {e["k"] for e in es}
        if "t" in kinds:
            merged += [e for e in es if e["k"] == "t"]; es = [e for e in es if e["k"] != "t"]
            if not es:
                continue
            kinds = {e["k"] for e in es}
        e = dict(es[0]); e["k"] = "a" if {"p", "s"} <= kinds or "-" in kinds else es[0]["k"]
        merged.append(e)
    out["labels"], out["exits"] = labels, merged
    return len(labels), len(merged)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", required=True); ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--tdev-maps", required=True, help="tarkov.dev src/data/maps.json (labels)")
    a = ap.parse_args()
    tdev = json.load(open(a.tdev_maps))
    exits_txt = [l.strip() for l in open(os.path.join(os.path.dirname(__file__), "extracts-tarkovdev.txt"), encoding="utf-8") if l.strip() and not l.startswith("#")]
    os.makedirs(a.out, exist_ok=True)
    for mid in list(MAPS) + ["labyrinth"]:
        d = json.load(open(os.path.join(a.data, f"{mid}.json")))
        pts = np.array([[p[0], p[1], p[2]] for p in d["points"]])
        out, counts = build_svg_map(mid, MAPS[mid], a.svg, pts) if mid in MAPS else build_from_spots(mid, pts)
        nl, ne = markers(mid, out, tdev, exits_txt, pts)
        counts["labels"], counts["exits"] = nl, ne
        path = os.path.join(a.out, f"{mid}-blockout.json")
        with open(path, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        print(f"{mid:13} {os.path.getsize(path) / 1024:7.0f} KB  {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
