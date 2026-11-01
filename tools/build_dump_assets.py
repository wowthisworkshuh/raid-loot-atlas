"""Turn a MapDump folder into the files the atlas page loads for that map.

  maps/<id>-geo.bin         model: walk-map floors + the game's low-poly collision shapes (walls, floors, stairs)
  maps/<id>-dump.json       doors (with keys), exits, transits from the game
  maps/<id>-containers.json container positions, matched to the server's container ids

Usage: python build_dump_assets.py --dump <dump/factory4_day> --db <SPT database> --tdev-exits extracts-tarkovdev.txt --out maps

geo.bin layout (little endian): "GEO3", int wallCount, int vertCount, int triCount,
  walls: float x0,y0,z0, x1,y1,z1, kind (0 wall 2.4 m, 1 railing at a drop 1.0 m, 2 furniture outline 0.9 m)
  floor verts: float x,y,z ; floor tris: uint32 a,b,c

Everything comes from the game's own walk map, so walls always meet floors and every level gets its walls:
  floors   the walk map, minus rooftops and strips outside the walls (pieces not reachable from the player
           spawns through any door, unless loot or a container sits on them)
  walls    the walk map's edges pushed 0.3 m out (the bots' clearance) to where the real wall is; edges with
           walk map below them are drops and get a railing; holes under 6 m round are furniture; nothing is
           drawn across doorways (doors are drawn from doors.json) or along navmesh tile seams
Fan maps (tarkov.dev) still give the place names. The Labs fan map isn't to scale (levels drawn up to
~15% larger than the real rooms), so its walls would cut through rooms; Factory's fits within 0.3-0.5 m.
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


class Blockers:
    """The game's collision shapes, indexed for 'how tall is whatever stands right here?'."""
    def __init__(self, dump, cell=2.0):
        layers = json.load(open(os.path.join(dump, "layers.json")))
        b = load_coll(os.path.join(dump, "colliders.bin"))
        want = [int(k) for k, v in layers.items() if v in ("LowPolyCollider", "Default", "HighPolyCollider", "DoorLowPolyCollider")]
        b = b[np.isin(b['layer'], want)]
        ext = world_extent(b)
        shell = (ext[:, 0] > 12) & (ext[:, 2] > 12) & (ext[:, 1] > 4)   # bounds of a whole building's mesh: no use here
        b = b[~shell]; ext = ext[~shell]
        self.c = b['c'].astype(np.float64); self.h = b['s'].astype(np.float64) / 2
        q = b['q'].astype(np.float64); x, y, z, w = q.T
        self.R = np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], 1),
                           np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], 1),
                           np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], 1)], 1)
        self.top = self.c[:, 1] + ext[:, 1] / 2; self.bot = self.c[:, 1] - ext[:, 1] / 2
        lo = self.c - ext / 2; hi = self.c + ext / 2; self.cell = cell
        from collections import defaultdict
        g = defaultdict(list)
        for k in range(len(self.c)):
            if (hi[k, 0] - lo[k, 0]) * (hi[k, 2] - lo[k, 2]) > 4000: continue
            for i in range(int(np.floor(lo[k, 0] / cell)), int(np.floor(hi[k, 0] / cell)) + 1):
                for j in range(int(np.floor(lo[k, 2] / cell)), int(np.floor(hi[k, 2] / cell)) + 1):
                    g[(i, j)].append(k)
        self.g = {k: np.array(v) for k, v in g.items()}

    def height(self, p, floor, margin=0.12):
        """Height above `floor` of the tallest shape that stands on that floor (bottom below floor + 1 m) and
        contains point p (x, z) within margin; 0 if nothing does."""
        ks = self.g.get((int(np.floor(p[0] / self.cell)), int(np.floor(p[2] / self.cell))))
        if ks is None: return 0.0
        ks = ks[(self.bot[ks] < floor + 1.0) & (self.top[ks] > floor + 0.2)]
        if not len(ks): return 0.0
        d = np.array([p[0], floor + 0.5, p[2]]) - self.c[ks]
        loc = np.einsum('nji,nj->ni', self.R[ks], d)   # into each box's own axes
        h = self.h[ks]
        inside = (np.abs(loc[:, 0]) <= h[:, 0] + margin) & (np.abs(loc[:, 2]) <= h[:, 2] + margin) & (np.abs(loc[:, 1]) <= h[:, 1] + 3.0)
        return float((self.top[ks][inside] - floor).max()) if inside.any() else 0.0


def plan_walls(V, I, doors, push=0.3, min_loop=6.0, owner_ok=None, blockers=None):
    """Walls traced from the walk map's edges, so every room, corridor and stairwell has walls that meet its
    floor exactly. Returns rows x0,y0,z0,x1,y1,z1,kind with kind 0 = wall (2.4 m), 1 = railing at a drop
    (1.0 m), 2 = furniture outline (a hole in the walk map under 6 m round, 0.9 m).
    Tile seams (another boundary edge lies along it) and doorways (inside a door's box) get nothing."""
    from scipy.spatial import cKDTree
    own = {}
    for t, tri in enumerate(I):
        for u, v in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            k = (min(u, v), max(u, v)); own[k] = -1 if k in own else t
    E = [(k, t) for k, t in own.items() if t >= 0 and (owner_ok is None or owner_ok[t])]
    if not E: return np.zeros((0, 7)), np.zeros((0, 4, 3))
    ek = np.array([k for k, _ in E]); et = np.array([t for _, t in E])
    a, b = V[ek[:, 0]], V[ek[:, 1]]
    d = b - a; L = np.linalg.norm(d, axis=1); u = d / np.maximum(L, 1e-9)[:, None]
    # outward normal (horizontal), away from the owning triangle's middle
    nrm = np.c_[-u[:, 2], np.zeros(len(u)), u[:, 0]]
    mid = (a + b) / 2; cen = V[I[et]].mean(1)
    flip = np.einsum('ij,ij->i', nrm, mid - cen) < 0; nrm[flip] *= -1
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-9)[:, None]
    # tile seams
    samp, sown = [], []
    for i in range(len(ek)):
        m = max(2, int(L[i] / 0.25) + 1)
        for t in np.linspace(0.15, 0.85, m): samp.append(a[i] + d[i] * t); sown.append(i)
    samp = np.array(samp); sown = np.array(sown); seam = np.zeros(len(ek), bool)
    for i, j in cKDTree(samp).query_pairs(0.06):
        x, y = sown[i], sown[j]
        if x != y and abs(np.dot(u[x], u[y])) > 0.98: seam[x] = seam[y] = True
    keep = ~seam & (L > 0.05)
    # loops: short ones are furniture holes
    par = {}
    def f(x):
        par.setdefault(x, x)
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for (x, y), k in zip(ek, keep):
        if k: par[f(x)] = f(y)
    tot = {}
    for (x, _), l, k in zip(ek, L, keep):
        if k: tot[f(x)] = tot.get(f(x), 0) + l
    kind = np.array([0 if tot.get(f(x), 0) >= min_loop else 2 for x, _ in ek])
    # drops: walk map below just outside the edge -> railing
    cent = V[I].mean(1); kd2 = cKDTree(cent[:, [0, 2]])
    for i in np.where(keep & (kind == 0))[0]:
        p = mid[i] + nrm[i] * 0.8
        js = kd2.query_ball_point(p[[0, 2]], 0.7)
        if any(mid[i][1] - 6 < cent[j][1] < mid[i][1] - 0.6 for j in js): kind[i] = 1
    # walls vs props: the walk map stops at crates, shelves and cars just like at walls. Look at what the game
    # has just beyond each edge: 1.8 m or taller is a wall, lower is a prop (drawn as furniture)
    if blockers is not None:
        for i in np.where(keep & (kind == 0))[0]:
            hs = [blockers.height(mid[i] + nrm[i] * dd, mid[i][1] - 0.1) for dd in (0.35, 0.6)]
            h = max(hs)
            if 0.0 < h < 1.8: kind[i] = 2
    # doorways
    db = []
    for dr in doors:
        if dr["type"].split(".")[-1] not in ("Door", "KeycardDoor"): continue
        c = np.array(dr.get("boundsCenter") or dr["pos"], float); s_ = np.array(dr.get("boundsSize") or [1, 2, 1], float)
        db.append((c, max(s_[0], s_[2]) / 2 + 0.7, c[1] - min(1.0, s_[1] / 2 + 0.6)))
    if db:
        dkd = cKDTree(np.array([c[[0, 2]] for c, _, _ in db]))
        for i in np.where(keep)[0]:
            for j in dkd.query_ball_point(mid[i][[0, 2]], 2.5):
                c, r, base = db[j]
                if abs(mid[i][0] - c[0]) < r and abs(mid[i][2] - c[2]) < r and -1.0 < mid[i][1] - base < 1.5: keep[i] = False; break
    out, strips = [], []
    for i in np.where(keep)[0]:
        off = nrm[i] * (push if kind[i] != 2 else 0.1)
        out.append([*(a[i] + off), *(b[i] + off), kind[i]])
        if kind[i] == 0: strips.append([a[i], b[i], b[i] + off, a[i] + off])  # floor reaches the wall foot
    return np.array(out, float).reshape(-1, 7), np.array(strips, float).reshape(-1, 4, 3)


def roofed(C, dump, cell=2.0):
    """True for points with a flat collision shape (roof, ceiling, upper floor) 1.6-12 m above them."""
    layers = json.load(open(os.path.join(dump, "layers.json")))
    b = load_coll(os.path.join(dump, "colliders.bin"))
    want = [int(k) for k, v in layers.items() if v in ("LowPolyCollider", "Default", "HighPolyCollider")]
    b = b[np.isin(b['layer'], want)]
    ext = world_extent(b); c = b['c'].astype(np.float64)
    slab = (ext[:, 1] < 3.0) & (ext[:, 0] > 1.5) & (ext[:, 2] > 1.5) & (ext[:, 0] < 120) & (ext[:, 2] < 120)
    c, ext = c[slab], ext[slab]
    lo = c - ext / 2; hi = c + ext / 2
    from collections import defaultdict
    grid = defaultdict(list)
    for k in range(len(c)):
        for i in range(int(np.floor(lo[k, 0] / cell)), int(np.floor(hi[k, 0] / cell)) + 1):
            for j in range(int(np.floor(lo[k, 2] / cell)), int(np.floor(hi[k, 2] / cell)) + 1):
                grid[(i, j)].append(k)
    out = np.zeros(len(C), bool)
    ci = np.floor(C[:, 0] / cell).astype(int); cj = np.floor(C[:, 2] / cell).astype(int)
    for t in range(len(C)):
        ks = grid.get((ci[t], cj[t]))
        if not ks: continue
        ks = np.array(ks); x, y, z = C[t]
        m = (lo[ks, 0] <= x) & (x <= hi[ks, 0]) & (lo[ks, 2] <= z) & (z <= hi[ks, 2]) & (lo[ks, 1] > y + 1.6) & (lo[ks, 1] < y + 12)
        out[t] = m.any()
    return out


def indoor_mask(V, I, dump, mid, svgdir, step=0.5, near=0.8):
    """Outdoor maps: which walk-map triangles are buildings (inside a fan-map building outline, grown by
    1 m) or clearly off the ground (upper floors, bridges, basements, bunkers). The rest is open ground,
    which the page draws as the real terrain instead."""
    import terrain_dump, svgparse, build_blockouts as BB
    from matplotlib.path import Path
    from scipy.ndimage import binary_dilation
    ground = terrain_dump.DumpGround(terrain_dump.load_tiles(os.path.join(dump, "terrain.bin")))
    C = V[I].mean(1)
    g = ground.at(C[:, 0], C[:, 2])
    # off the ground only counts where it's clearly a building: under a roof or ceiling (a collision slab
    # 1.6-12 m above), or well below the ground (basements, bunkers, tunnels). Rocks and slopes don't.
    off = np.isnan(g) | (C[:, 1] - g < -2.5) | roofed(C, dump)
    cfg = BB.MAPS.get(mid)
    if not cfg: return off
    vb, feats = svgparse.read(os.path.join(svgdir, cfg["svg"] + ".svg")); M = BB.Mapper(vb, cfg["bounds"], cfg["uv"])
    x0, z0 = C[:, 0].min() - 5, C[:, 2].min() - 5
    nx = int((C[:, 0].max() + 5 - x0) / step) + 1; nz = int((C[:, 2].max() + 5 - z0) / step) + 1
    mask = np.zeros((nz, nx), bool); gx = x0 + np.arange(nx) * step; gz = z0 + np.arange(nz) * step
    for ft in feats:
        if cfg["floors"].get(ft["layer"]) != 0 or not set(ft["cls"]) & {"building", "structure"}: continue
        for raw in ft["rings"]:
            if len(raw) < 3: continue
            r = M(raw); lo, hi = r.min(0), r.max(0)
            j0, j1 = np.searchsorted(gx, lo[0]), np.searchsorted(gx, hi[0]); i0, i1 = np.searchsorted(gz, lo[1]), np.searchsorted(gz, hi[1])
            if j1 <= j0 or i1 <= i0: continue
            sub = np.stack(np.meshgrid(gx[j0:j1], gz[i0:i1]), -1).reshape(-1, 2)
            mask[i0:i1, j0:j1] |= Path(r).contains_points(sub).reshape(i1 - i0, j1 - j0)
    mask = binary_dilation(mask, iterations=int(round(1.0 / step)))
    j = np.clip(((C[:, 0] - x0) / step).astype(int), 0, nx - 1); i = np.clip(((C[:, 2] - z0) / step).astype(int), 0, nz - 1)
    return off | mask[i, j]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--db", required=True)
    ap.add_argument("--tdev-exits", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--svg", help="tarkov-dev-svg-maps folder: on maps with terrain, building outlines decide what is indoors")
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
    key = np.round(Vf / 0.02).astype(np.int64)
    uk, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    Vw = Vf[first]; Iw = inv.ravel()[If]
    Iw = Iw[(Iw[:, 0] != Iw[:, 1]) & (Iw[:, 1] != Iw[:, 2]) & (Iw[:, 0] != Iw[:, 2])]
    outdoor = meta.get("terrains", 0) > 0 and a.svg
    blockers = Blockers(a.dump)
    if outdoor:
        indoor = indoor_mask(Vw, Iw, a.dump, mid, a.svg)
        walls, strips = plan_walls(Vw, Iw, doors_raw, owner_ok=indoor, blockers=blockers)
        Iw = Iw[indoor]
    else:
        walls, strips = plan_walls(Vw, Iw, doors_raw, blockers=blockers)
    if len(strips):
        n0 = len(Vw); Vw = np.concatenate([Vw, strips.reshape(-1, 3)])
        q = n0 + np.arange(len(strips) * 4).reshape(-1, 4)
        Iw = np.concatenate([Iw, q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
    path = os.path.join(a.out, f"{mid}-geo.bin")
    with open(path, "wb") as f:
        f.write(b"GEO3"); f.write(struct.pack("<iii", len(walls), len(Vw), len(Iw)))
        f.write(walls.astype(np.float32).tobytes()); f.write(Vw.astype(np.float32).tobytes()); f.write(Iw.astype(np.uint32).tobytes())
    kc = np.bincount(walls[:, 6].astype(int), minlength=3)
    print(f"{mid}: {kc[0]} wall pieces, {kc[1]} railings, {kc[2]} furniture edges, {len(Iw)} floor triangles "
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
