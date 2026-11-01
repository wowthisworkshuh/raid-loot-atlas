"""Turn a MapDump folder into the files the atlas page loads for that map.

  maps/<id>-geo.bin         model: walk-map floors + the game's low-poly collision shapes (walls, floors, stairs)
  maps/<id>-dump.json       doors (with keys), exits, transits from the game
  maps/<id>-containers.json container positions, matched to the server's container ids

Usage: python build_dump_assets.py --dump <dump/factory4_day> --db <SPT database> --tdev-exits extracts-tarkovdev.txt --out maps

geo.bin layout (little endian): "GEO2", int boxCount, int vertCount, int triCount,
  boxes: float kind (0 wall, 1 prop), cx,cy,cz, sx,sy,sz, qx,qy,qz,qw   (Unity world space, size already scaled)
  floor verts: float x,y,z ; floor tris: uint32 a,b,c

The model is drawn as a readable floor plan, not the raw collision soup:
  floors   the walk map, minus rooftops and strips outside the walls (pieces not reachable from the player
           spawns through any door, unless loot or a container sits on them)
  walls    upright thin collision boxes next to a floor, cut to 2.4 m above each floor they stand on, so every
           floor reads like a plan from above and no ceiling or roof hides it
  props    only big furniture and machines (footprint over 0.6 m both ways), cut to 1.0 m above their floor
  outline  the edges of the walk map (room shapes, ledges), minus navmesh tile seams and the small rings
           around furniture: after the triangles, int segCount, then float x0,y0,z0,x1,y1,z1 per segment
"""
import argparse, json, os, struct, sys
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import navgraph  # noqa: E402

EXIT_KEY = {"bigmap": "C", "factory4_day": "F", "factory4_night": "F", "woods": "W", "shoreline": "S", "interchange": "I",
            "lighthouse": "L", "rezervbase": "R", "laboratory": "LB", "sandbox": "G", "sandbox_high": "G", "labyrinth": "LY"}
MAP_NAMES = {"bigmap": "Customs", "factory4_day": "Factory", "factory4_night": "Factory (night)", "woods": "Woods",
             "shoreline": "Shoreline", "interchange": "Interchange", "lighthouse": "Lighthouse", "rezervbase": "Reserve",
             "laboratory": "The Lab", "labyrinth": "The Labyrinth", "sandbox": "Ground Zero", "sandbox_high": "Ground Zero",
             "tarkovstreets": "Streets of Tarkov", "woods ": "Woods", "terminal": "Terminal"}


def load_coll(path):
    b = open(path, 'rb').read(); assert b[:4] == b'COLL'
    o = 8; nb, = struct.unpack_from('<i', b, o); o += 4
    rec = np.dtype([('layer', '<i4'), ('kind', '<i4'), ('c', '<f4', 3), ('s', '<f4', 3), ('q', '<f4', 4)])
    boxes = np.frombuffer(b, rec, nb, o).copy()
    return boxes


def world_extent(b):
    """World-space size of each oriented box along x, y, z."""
    q = b['q'].astype(np.float64); x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], 1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], 1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], 1)], 1)  # rows = world axes
    return np.einsum('nij,nj->ni', np.abs(R), b['s'].astype(np.float64))


def pick_boxes(boxes, layers, walk_lo=None, walk_hi=None):
    """Low-poly collision (what blocks players), minus tiny bits, the oversized bounds of complex meshes,
    the giant ground planes under the level, and anything far outside the walkable area."""
    want = {k for k, v in layers.items() if v in ("LowPolyCollider", "Default")}
    b = boxes[np.isin(boxes['layer'], [int(k) for k in want])]
    s = np.sort(b['s'], axis=1)
    keep = (s[:, 2] >= 0.35)                    # drop small clutter
    keep &= ~((s[:, 0] > 2.5) & (s[:, 1] > 2.5))  # a box thick in two directions is the bounds of a complex mesh, not a wall
    ext = world_extent(b)
    keep &= (s[:, 2] < 150) & (ext[:, 1] < 30)    # level-wide planes and implausibly tall walls
    if walk_lo is not None:
        c = b['c']
        keep &= np.all((c >= walk_lo - 30) & (c <= walk_hi + 30), axis=1)
    b = b[keep]
    # the same wall often has a box collider and a mesh collider; drop near-duplicates
    key = np.round(np.concatenate([b['c'], b['s']], axis=1) / 0.05).astype(np.int64)
    _, first = np.unique(key, axis=0, return_index=True)
    return b[np.sort(first)]


def keep_floors(V, I, doors, spawns, anchors):
    """Walk-map pieces joined through every door; keep the ones the spawns reach, plus any with loot on them."""
    from scipy.spatial import cKDTree
    T, nbr, cent, _ = navgraph.build(V, I)
    comp, _ = navgraph.components(nbr)
    kd = cKDTree(cent)
    par = list(range(comp.max() + 1))
    def f(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for d in doors:
        if d["type"].split(".")[-1] not in ("Door", "KeycardDoor"): continue
        c = np.array(d.get("boundsCenter") or d["pos"], float); s = np.array(d.get("boundsSize") or [1, 2, 1], float)
        r = max(s[0], s[2]) / 2 + 1.6; b = c[1] - min(1.0, s[1] / 2 + 0.6)
        g = sorted({int(comp[i]) for i in kd.query_ball_point(c, r + 2.0)
                    if abs(cent[i][0] - c[0]) < r and abs(cent[i][2] - c[2]) < r and -1.2 < cent[i][1] - b < 1.0})
        for x in g[1:]: par[f(x)] = f(g[0])
    root = np.array([f(x) for x in range(len(par))])
    keep = set()
    for p in spawns:
        dd, i = kd.query(p)
        if dd < 3.5: keep.add(int(root[comp[i]]))
    for p in anchors:   # loot or a container sits on it (within 2.5 m, at or below the item)
        dd, ix = kd.query(p, k=8)
        for d_, i in zip(dd, ix):
            if d_ < 2.5 and cent[i][1] <= p[1] + 0.6: keep.add(int(root[comp[i]])); break
    if not keep: return V, I
    m = np.isin(root[comp], list(keep))
    return V, I[m]


def plan_boxes(b, Vf, If):
    """Walls and big props, cut at a readable height above each floor they stand on (see module doc)."""
    from scipy.spatial import cKDTree
    cent = Vf[If].mean(1); kd = cKDTree(cent[:, [0, 2]])
    q = b['q'].astype(np.float64); x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], 1),
                  np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], 1),
                  np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], 1)], 1)
    up = np.abs(R[:, 1, :]); k = up.argmax(1); upright = up.max(1) > 0.97
    s = b['s'].astype(np.float64); n = len(b)
    h = s[np.arange(n), k]
    hor = np.sort(np.where(np.eye(3)[k] > 0, np.inf, s), axis=1)[:, :2]
    thin, longd = hor[:, 0], hor[:, 1]
    wall = upright & (thin < 0.7) & (h >= 1.4) & (longd >= 0.6)
    prop = upright & ~wall & (thin >= 0.6) & (h >= 0.5) & (h < 6)
    out, kinds = [], []
    for i in np.where(wall | prop)[0]:
        c = b['c'][i].astype(np.float64); y0, y1 = c[1] - h[i] / 2, c[1] + h[i] / 2
        # floors near the box: sample its footprint (long axis) and look 1.2 m around
        la = np.argmax(np.where(np.arange(3) == k[i], -1, s[i]))
        ax = R[i][:, la]; half = s[i][la] / 2
        pts = [c[[0, 2]] + ax[[0, 2]] * t for t in np.linspace(-half, half, max(2, int(half / 1.5) + 2))]
        ys = set()
        for p in pts:
            for j in kd.query_ball_point(p, 1.2): ys.add(round(float(cent[j][1]), 1))
        ys = sorted(v for v in ys if y0 - 0.6 <= v <= y1 - 0.3)
        levels = []
        for v in ys:
            if not levels or v - levels[-1] > 1.6: levels.append(v)
        cap = 2.4 if wall[i] else 1.0
        for f in levels:
            lo = max(y0, f - 0.15); hi = min(y1, f + cap)
            if wall[i] and f > y0 + 0.6 and hi - lo < 0.5: continue
            if hi - lo < 0.3: continue
            rec = b[i].copy(); rec['c'][1] = (lo + hi) / 2
            ss = rec['s'].copy(); ss[k[i]] = hi - lo; rec['s'] = ss
            out.append(rec); kinds.append(0 if wall[i] else 1)
    return np.array(kinds, np.float32), np.array(out, dtype=b.dtype)


def outline(V, I, min_loop=6.0):
    """Boundary edges of the walk map, minus tile seams (another boundary edge lies along it) and loops
    shorter than min_loop metres (holes round furniture and pillars)."""
    from scipy.spatial import cKDTree
    cnt = {}
    for t in I:
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            k = (min(u, v), max(u, v)); cnt[k] = cnt.get(k, 0) + 1
    E = np.array([k for k, n in cnt.items() if n == 1], int).reshape(-1, 2)
    if not len(E): return np.zeros((0, 6))
    a, b = V[E[:, 0]], V[E[:, 1]]
    d = b - a; L = np.linalg.norm(d, axis=1); u = d / np.maximum(L, 1e-9)[:, None]
    # seams: sample points along each edge; if a point sits on another collinear boundary edge, it's a seam
    seam = np.zeros(len(E), bool)
    samp, own = [], []
    for i in range(len(E)):
        m = max(2, int(L[i] / 0.25) + 1)
        for t in np.linspace(0.15, 0.85, m): samp.append(a[i] + d[i] * t); own.append(i)
    samp = np.array(samp); own = np.array(own)
    kd = cKDTree(samp)
    for i, j in kd.query_pairs(0.06):
        ei, ej = own[i], own[j]
        if ei != ej and abs(np.dot(u[ei], u[ej])) > 0.98: seam[ei] = seam[ej] = True
    E, L, a, b = E[~seam], L[~seam], a[~seam], b[~seam]
    # loops: union the edges' vertices, drop short loops
    par = {}
    def f(x):
        par.setdefault(x, x)
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for x, y in E: par[f(x)] = f(y)
    root = np.array([f(x) for x, _ in E])
    tot = {}
    for r, l in zip(root, L): tot[r] = tot.get(r, 0) + l
    keep = np.array([tot[r] >= min_loop for r in root])
    return np.concatenate([a[keep], b[keep]], axis=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--db", required=True)
    ap.add_argument("--tdev-exits", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--loot", help="data/<map>.json: walk pieces with loot on them are kept even if no door joins them")
    a = ap.parse_args()
    meta = json.load(open(os.path.join(a.dump, "meta.json")))
    mid = meta["map"]
    layers = json.load(open(os.path.join(a.dump, "layers.json")))
    locale = json.load(open(os.path.join(a.db, "locales/global/en.json"), encoding="utf-8"))
    handbook = {x["Id"]: x["Price"] for x in json.load(open(os.path.join(a.db, "templates/handbook.json")))["Items"]}

    # ---- model
    V, I = navgraph.load_nav(os.path.join(a.dump, "navmesh.bin"))
    loot_pts = np.array([p[:3] for p in json.load(open(a.loot))["points"]]) if a.loot else np.zeros((0, 3))
    cont_pts = np.array([c["pos"] for c in json.load(open(os.path.join(a.dump, "containers.json")))]).reshape(-1, 3)
    base = os.path.join(a.db, "locations", mid, "base.json")
    spawns = [[s["Position"][k] for k in "xyz"] for s in json.load(open(base))["SpawnPointParams"]
              if "Player" in s.get("Categories", [])] if os.path.exists(base) else []
    doors_raw = json.load(open(os.path.join(a.dump, "doors.json")))
    Vf, If = keep_floors(V, I, doors_raw, spawns, np.concatenate([loot_pts, cont_pts]))
    boxes = pick_boxes(load_coll(os.path.join(a.dump, "colliders.bin")), layers, V.min(0), V.max(0))
    kinds, boxes = plan_boxes(boxes, Vf, If)
    key = np.round(Vf / 0.02).astype(np.int64)
    uk, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    Vw = Vf[first]; Iw = inv.ravel()[If]
    Iw = Iw[(Iw[:, 0] != Iw[:, 1]) & (Iw[:, 1] != Iw[:, 2]) & (Iw[:, 0] != Iw[:, 2])]
    path = os.path.join(a.out, f"{mid}-geo.bin")
    with open(path, "wb") as f:
        f.write(b"GEO2"); f.write(struct.pack("<iii", len(boxes), len(Vw), len(Iw)))
        out = np.zeros((len(boxes), 11), np.float32)
        out[:, 0] = kinds; out[:, 1:4] = boxes['c']; out[:, 4:7] = boxes['s']; out[:, 7:11] = boxes['q']
        f.write(out.tobytes()); f.write(Vw.astype(np.float32).tobytes()); f.write(Iw.astype(np.uint32).tobytes())
        segs = outline(Vw, Iw)
        f.write(struct.pack("<i", len(segs))); f.write(segs.astype(np.float32).tobytes())
    print(f"  outline segments: {len(segs)}")
    print(f"{mid}: {int((kinds == 0).sum())} wall pieces, {int((kinds == 1).sum())} props, {len(Iw)} floor triangles "
          f"(of {len(I)}) -> {os.path.getsize(path)/1024:.0f} KB")

    # ---- doors
    doors = []
    for d in json.load(open(os.path.join(a.dump, "doors.json"))):
        t = d["type"].split(".")[-1]
        if t not in ("Door", "KeycardDoor", "Trunk"):
            continue
        k = d.get("keyId") or ""
        doors.append({
            "id": d["id"], "t": t.lower().replace("keycarddoor", "keycard"), "state": d.get("state"),
            "key": k, "keyName": locale.get(f"{k} Name", "") if k else "", "keyShort": locale.get(f"{k} ShortName", "") if k else "",
            "keyPrice": handbook.get(k, 0) if k else 0,
            "c": [round(v, 2) for v in (d.get("boundsCenter") or d["pos"])], "s": [round(v, 2) for v in (d.get("boundsSize") or [1, 2, 0.2])],
            "breach": d.get("fields", {}).get("CanBeBreached") == "True",
        })
    # ---- exits: the game's own, named like tarkov.dev where one is within 6 m
    tdev = []
    for line in open(a.tdev_exits, encoding="utf-8"):
        if line.startswith("#") or not line.strip():
            continue
        k, kind, name, x, y, z = line.strip().split("|")
        if k == EXIT_KEY.get(mid) and kind != "t":
            tdev.append((name, np.array([float(x), float(y), float(z)])))
    exits = []
    for e in json.load(open(os.path.join(a.dump, "exits.json"))):
        p = np.array(e["pos"]); t = e["type"].split(".")[-1]
        kind = "s" if t.startswith("Scav") else "p"
        best = min(tdev, key=lambda n: np.linalg.norm(n[1] - p), default=None)
        raw = e.get("exitName") or e["name"]
        clean = raw.split("_", 1)[1] if raw.lower().startswith(("lab_", "factory_", "fact_")) else raw
        clean = clean.replace("_", " ").strip()
        same = next((n for n, _ in tdev if n.lower() == clean.lower()), None)
        name = best[0] if best is not None and np.linalg.norm(best[1] - p) < 6 else (same or clean)
        if t == "SecretExfiltrationPoint":
            name += " (secret)"
        exits.append({"n": name, "k": kind, "x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)})
    # merge PMC + Scav exits at the same place into one "both" marker
    merged = []
    for e in exits:
        twin = next((m for m in merged if m["n"] == e["n"] and abs(m["x"] - e["x"]) < 6 and abs(m["z"] - e["z"]) < 6 and {m["k"], e["k"]} == {"p", "s"}), None)
        if twin: twin["k"] = "a"
        else: merged.append(dict(e))
    for t in json.load(open(os.path.join(a.dump, "transits.json"))):
        loc = (t.get("fields", {}).get("parameters.location") or "").lower()
        merged.append({"n": MAP_NAMES.get(loc, loc or t["name"]), "k": "t", "x": round(t["pos"][0], 2), "y": round(t["pos"][1], 2), "z": round(t["pos"][2], 2)})
    json.dump({"map": mid, "source": "SurvivalPack.MapDump " + meta.get("plugin", ""), "doors": doors, "exits": merged},
              open(os.path.join(a.out, f"{mid}-dump.json"), "w"), separators=(",", ":"))
    # ---- containers
    conts = [{"id": c["id"], "x": round(c["pos"][0], 2), "y": round(c["pos"][1], 2), "z": round(c["pos"][2], 2)}
             for c in json.load(open(os.path.join(a.dump, "containers.json"))) if c.get("id")]
    json.dump(conts, open(os.path.join(a.out, f"{mid}-containers.json"), "w"), separators=(",", ":"))
    locked = [d for d in doors if d["state"] == "Locked"]
    print(f"  doors {len(doors)} (locked {len(locked)}, needing a key {sum(1 for d in locked if d['key'])}), exits {len(merged)}, containers {len(conts)}")
    print("  keys:", sorted({d['keyName'] for d in doors if d['key']}))


if __name__ == "__main__":
    main()
