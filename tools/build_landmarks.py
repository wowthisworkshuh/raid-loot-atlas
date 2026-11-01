"""Landmarks players navigate by: the big props, placed from the game's own collision shapes.

The dump keeps every big prop as a collision box with its exact size, so each kind of prop has a size
"signature" shared by all its copies on every map (the powerline pylon is 7.5 x 13.1 x 29.3 m everywhere).
Known signatures become typed landmarks; a few one-offs are picked out by place; aircraft come from the fan
maps. The page draws a simple low-poly model of each type in its collision box.

Usage: python build_landmarks.py --dumps <dump folder> --svg <tarkov-dev-svg-maps> --out maps [--only a,b]
maps/<id>-landmarks.json: {"items": [{t, x, y, z, h, w, d, yaw}], "wires": [[x0,y0,z0, x1,y1,z1], ...]}
  x, y, z = middle of the base (game coordinates), h = height, w x d = footprint (d along yaw), yaw in
  radians (direction of d: x = sin(yaw), z = cos(yaw))
"""
import argparse, json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import build_dump_assets as BD, terrain_dump, svgparse, build_blockouts as BB  # noqa: E402

# (height, small side, long side) -> type. Matched within 0.4 m.
PREFABS = [
    ((62.2, 6.8, 6.8), "chimney"), ((55.2, 6.0, 6.0), "chimney"), ((39.8, 7.0, 7.0), "chimney"),
    ((49.6, 5.4, 5.4), "chimney"), ((44.8, 7.9, 7.9), "chimney"),
    ((45.0, 36.6, 36.6), "cooling"),
    ((29.3, 7.5, 13.1), "pylon"), ((29.3, 7.5, 13.3), "pylon"),
    ((24.4, 6.2, 10.9), "pylon2"), ((24.4, 6.2, 11.1), "pylon2"),
    ((33.6, 10.8, 12.4), "billboard"), ((31.2, 5.9, 7.0), "billboard"),
    ((26.8, 4.1, 20.4), "gantry"), ((26.6, 3.6, 20.3), "gantry"),
    ((28.3, 11.7, 11.7), "weather"), ((24.5, 11.8, 11.8), "weather"),
    ((18.6, 15.2, 56.0), "portcrane"),
    ((18.3, 22.3, 22.7), "radome"), ((17.6, 21.1, 21.1), "radome"),
    ((15.3, 45.5, 45.5), "tank"),
]
# one-offs by place: (type, x, z, search radius) - the tallest shape there sets the size
SPECIAL = {
    "lighthouse": [("lighthouse", 447.3, 457.7, 6)],
    "bigmap": [("towercrane", 256, -24, 12)],
    "rezervbase": [("mast", 18, -27, 4), ("heli", -125.8, 33.4, 1.5)],
}
AIRCRAFT = {"plane": "plane", "chopper": "heli"}


def axes(b):
    q = b['q'].astype(np.float64); x, y, z, w = q.T
    return np.stack([np.stack([1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], 1),
                     np.stack([2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], 1),
                     np.stack([2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)], 1)], 1)


def build(mid, dump, svgdir):
    layers = json.load(open(os.path.join(dump, "layers.json")))
    b = BD.load_coll(os.path.join(dump, "colliders.bin"))
    ln = np.array([layers.get(str(l), str(l)) for l in b['layer']])
    b = b[np.isin(ln, ["HighPolyCollider", "LowPolyCollider", "Default"])]
    R = axes(b); up = np.abs(R[:, 1, :]).argmax(1); s = b['s'].astype(np.float64); c = b['c'].astype(np.float64)
    n = len(b); H = s[np.arange(n), up]
    hk = np.array([[k for k in range(3) if k != u] for u in up])           # the two horizontal local axes
    hs = np.take_along_axis(s, hk, 1); order = np.argsort(hs, 1); hk = np.take_along_axis(hk, order, 1); hs = np.sort(hs, 1)
    long_dir = R[np.arange(n), :, hk[:, 1]]                                # world direction of the long side
    yaw = np.arctan2(long_dir[:, 0], long_dir[:, 2])
    meta = json.load(open(os.path.join(dump, "meta.json")))
    G = terrain_dump.DumpGround(terrain_dump.load_tiles(os.path.join(dump, "terrain.bin"))) if meta.get("terrains") else None
    base = c[:, 1] - H / 2
    items = []
    def add(t, i, h=None, y0=None):
        items.append({"t": t, "x": round(c[i, 0], 2), "y": round(base[i] if y0 is None else y0, 2), "z": round(c[i, 2], 2),
                      "h": round(H[i] if h is None else h, 2), "w": round(hs[i, 0], 2), "d": round(hs[i, 1], 2), "yaw": round(float(yaw[i]), 4)})
    for (ph, pa, pb), t in PREFABS:
        m = (np.abs(H - ph) < 0.4) & (np.abs(hs[:, 0] - pa) < 0.4) & (np.abs(hs[:, 1] - pb) < 0.4)
        for i in np.where(m)[0]: add(t, i)
    for t, x, z, r in SPECIAL.get(mid, []):
        near = np.where(np.hypot(c[:, 0] - x, c[:, 2] - z) < r)[0]
        if not len(near): continue
        top = (c[near, 1] + H[near] / 2).max(); i = near[np.argmax(H[near] * (hs[near, 1] < 30))]
        gy = float(G.at(np.array([c[i, 0]]), np.array([c[i, 2]]))[0]) if G else base[near].min()
        if np.isnan(gy): gy = base[near].min()
        add(t, i, h=top - gy, y0=gy)
    # merge copies at the same place (most props have two collision meshes)
    items.sort(key=lambda it: -it["h"]); kept = []
    for it in items:
        if any(k["t"] == it["t"] and math.hypot(k["x"] - it["x"], k["z"] - it["z"]) < max(3.0, 0.3 * min(it["d"], k["d"])) for k in kept): continue
        kept.append(it)
    items = kept
    # aircraft from the fan map
    cfg = BB.MAPS.get(mid)
    if cfg:
        vb, feats = svgparse.read(os.path.join(svgdir, cfg["svg"] + ".svg")); M = BB.Mapper(vb, cfg["bounds"], cfg["uv"])
        rings = []
        for ft in feats:
            t = next((AIRCRAFT[k] for k in ft["cls"] if k in AIRCRAFT), None)
            if not t or cfg["floors"].get(ft["layer"]) != 0 or any("drone" in x.lower() for x in ft["chain"]): continue
            for r in ft["rings"]:
                if len(r) >= 3: rings.append((t, M(r)))
        # pieces of one aircraft (fuselage, wings, rotor) sit within a few metres of each other
        groups = []
        for t, pts in rings:
            lo, hi = pts.min(0), pts.max(0)
            g = next((g for g in groups if g[0] == t and (lo <= g[2] + 4).all() and (hi >= g[1] - 4).all()), None)
            if g: g[1][:] = np.minimum(g[1], lo); g[2][:] = np.maximum(g[2], hi); g[3].append(pts)
            else: groups.append([t, lo.copy(), hi.copy(), [pts]])
        for t, lo, hi, parts in groups:
            pts = np.concatenate(parts); mu = pts.mean(0); u, sv, vt = np.linalg.svd(pts - mu, full_matrices=False)
            ax = vt[0]; proj = (pts - mu) @ vt.T; ext = proj.max(0) - proj.min(0)
            if ext[0] > (30 if t == "heli" else 60) or ext[0] < 4: continue   # drones, hangars and stray shapes
            gy = float(G.at(np.array([mu[0]]), np.array([mu[1]]))[0]) if G else 0.0
            items.append({"t": t, "x": round(float(mu[0]), 2), "y": round(gy, 2), "z": round(float(mu[1]), 2),
                          "h": round(4.5 if t == "heli" else 6.0, 2), "w": round(float(ext[1]), 2), "d": round(float(ext[0]), 2),
                          "yaw": round(float(math.atan2(ax[0], ax[1])), 4)})
    # power lines: the wires run across each pylon's cross-arm (the line goes at right angles to the arm), so
    # each pylon links to the nearest pylon of its type ahead and behind it along that direction (within 55
    # degrees, up to 260 m: spans are ~140 m but some are 160 m). A link needs both pylons to pick each other,
    # or one to pick the other with nothing better on that side, so a line never jumps across to another line.
    wires = []
    for t, arm in (("pylon", 13.2), ("pylon2", 11.0)):
        P = [it for it in items if it["t"] == t]
        if len(P) < 2: continue
        xy = np.array([[p["x"], p["z"]] for p in P])
        pick = {}
        for a, p in enumerate(P):
            v = np.array([math.cos(p["yaw"]), -math.sin(p["yaw"])])           # along the line
            dl = xy - xy[a]; dist = np.hypot(*dl.T); dist[a] = 1e9
            c = (dl @ v) / dist
            for sgn in (1, -1):
                ok = np.where((sgn * c > 0.57) & (dist < 260) & (dist > 20))[0]
                if len(ok): pick[(a, sgn)] = int(ok[np.argmin(dist[ok] / (sgn * c[ok]))])
        chosen = {a: set(b for (x, _), b in pick.items() if x == a) for a in range(len(P))}
        used = set()
        for a in range(len(P)):
            for bidx in chosen[a]:
                key = (min(a, bidx), max(a, bidx))
                if key in used: continue
                # mutual, or b has no pick on the side facing a
                vb = np.array([math.cos(P[bidx]["yaw"]), -math.sin(P[bidx]["yaw"])]); side = 1 if (xy[a] - xy[bidx]) @ vb > 0 else -1
                if a not in chosen[bidx] and (bidx, side) in pick: continue
                used.add(key); p, q = P[a], P[bidx]
                for side in (-0.42, 0.42):
                    for yfrac in (0.78, 0.92):
                        def tip(pp):
                            return [pp["x"] + math.sin(pp["yaw"]) * side * arm, pp["y"] + pp["h"] * yfrac, pp["z"] + math.cos(pp["yaw"]) * side * arm]
                        # pick the matching side on the far pylon (arms may point either way)
                        e0 = tip(p); f1 = tip(q); f2 = [2 * q["x"] - f1[0], f1[1], 2 * q["z"] - f1[2]]
                        e1 = f1 if math.dist(e0, f1) <= math.dist(e0, f2) else f2
                        wires.append([round(v, 2) for v in e0 + e1])
    return items, wires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", required=True); ap.add_argument("--svg", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    a = ap.parse_args()
    for mid in sorted(os.listdir(a.dumps)):
        if a.only and mid not in a.only.split(","): continue
        d = os.path.join(a.dumps, mid)
        if mid.startswith("scene_") or not os.path.exists(os.path.join(d, "colliders.bin")): continue
        items, wires = build(mid, d, a.svg)
        json.dump({"items": items, "wires": wires}, open(os.path.join(a.out, f"{mid}-landmarks.json"), "w"), separators=(",", ":"))
        from collections import Counter
        print(f"{mid}: {dict(Counter(it['t'] for it in items))}, {len(wires)} wire spans")


if __name__ == "__main__":
    main()
