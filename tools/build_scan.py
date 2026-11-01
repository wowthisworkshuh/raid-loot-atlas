"""Building interiors from MapDump's building scan (scan.bin, MapDump 0.3+).

The scan samples every building every 25 cm with raycasts against the game's own collision: every upward-facing
surface under each point (floors, stair ramps, roofs, furniture tops, top to bottom) and, on each floor, whether
something solid stands at knee height (0.3-1.1 m), head height (1.5-2.1 m) or glass (1.0-1.8 m). From that:

  floors     walkable surfaces that aren't the top of something (nothing walkable less than 1.6 m below them),
             with at least 6 of their 8 neighbours at about the same height (drops 1-2 cell strips such as wall
             tops), and either something above them (a ceiling) or the walk map near them (balconies, open decks).
             Rooftops nobody walks on are left out, so you look into the rooms.
  stairs     floors sloping 14-53 degrees, drawn as 15 cm steps
  walls      knee and head height both blocked: from the floor up to the next surface above (the wall's real top
             or the next floor), so walls are as tall as the building's storeys
  furniture  knee height blocked only: up to its real top (the surface above it), at most 1.6 m
  lintels    head height blocked only (over doors): from 2.0 m up to the next surface
  glass      a window pane at 1.0-2.0 m
Cells of the same kind and height are merged into boxes.

Usage: python build_scan.py --dump <dump/map> --out maps [--id <model id>]
maps/<id>-geo.bin: "GEO4", int boxCount, then per box float x0, z0, x1, z1, y0, y1, kind
  (0 floor, 1 wall, 2 furniture, 3 glass, 4 stair step), game coordinates
"""
import argparse, json, os, struct, sys, time
import numpy as np

KIND = {"floor": 0, "wall": 1, "furniture": 2, "glass": 3, "stairs": 4}


def read_scan(path):
    b = open(path, "rb").read()
    assert b[:4] == b"SCN1", "not a building scan"
    ver, cc, k, x0, z0, cx, cz = struct.unpack_from("<ififfii", b, 4)
    o = 32
    mask = np.frombuffer(b, np.uint8, cx * cz, o).astype(bool); o += cx * cz
    coarse = np.flatnonzero(mask)
    nf = len(coarse) * k * k
    counts = np.empty(nf, np.int32); starts = np.empty(nf, np.int64)
    pos = o
    for i in range(nf):                       # one count byte per cell, then 4 bytes per surface
        n = b[pos]; counts[i] = n; starts[i] = pos + 1; pos += 1 + 4 * n
    B = np.frombuffer(b, np.uint8)
    tot = int(counts.sum())
    first = np.zeros(nf, np.int64); first[1:] = np.cumsum(counts)[:-1]
    cell = np.repeat(np.arange(nf), counts)
    rank = np.arange(tot) - first[cell]
    at = starts[cell] + 4 * rank
    y = (B[at].astype(np.int32) | (B[at + 1].astype(np.int32) << 8)).astype(np.int16).astype(np.float32) / 100
    up = B[at + 2].astype(np.float32) / 127.5 - 1
    fl = B[at + 3].astype(np.int32)
    # fine cell -> global grid (gx, gz)
    f = np.arange(nf); c = coarse[f // (k * k)]; r = f % (k * k)
    gx = (c % cx) * k + r % k; gz = (c // cx) * k + r // k
    return dict(k=k, x0=x0, z0=z0, cx=cx, cz=cz, counts=counts, first=first, gx=gx, gz=gz,
                cell=cell, rank=rank, y=y, up=up, fl=fl)


def nav_points(dump):
    """Points on the walk map, about every 0.7 m (vertices, and a grid inside every triangle)."""
    p = os.path.join(dump, "navmesh.bin")
    if not os.path.exists(p): return np.zeros((0, 3), np.float32)
    b = open(p, "rb").read(); assert b[:4] == b"NAVM"
    nv, ni = struct.unpack_from("<ii", b, 8)
    V = np.frombuffer(b, np.float32, nv * 3, 16).reshape(-1, 3)
    I = np.frombuffer(b, np.int32, ni, 16 + nv * 12).reshape(-1, 3)
    T = V[I]; pts = [V]
    e = np.max(np.linalg.norm(T - np.roll(T, 1, axis=1), axis=2), axis=1)
    for n in range(1, 40):
        sel = np.where(np.ceil(e / 0.7).clip(1, 39) == n)[0]
        if not len(sel): continue
        ij = np.array([(i, j) for i in range(n + 1) for j in range(n + 1 - i)], np.float32) / n
        w = np.c_[ij, 1 - ij.sum(1)]
        pts.append(np.einsum("pw,twc->tpc", w, T[sel]).reshape(-1, 3))
    return np.concatenate(pts).astype(np.float32)


def classify(S, nav):
    k, gx, gz = S["k"], S["gx"], S["gz"]
    y, up, fl, cell, rank = S["y"], S["up"], S["fl"], S["cell"], S["rank"]
    cnt, first = S["counts"], S["first"]
    n = len(y); I = np.arange(n); cs = 1.0 / k
    walk = (fl & 1) > 0; terr = (fl & 8) > 0; low = (fl & 2) > 0; high = (fl & 4) > 0; glass = (fl & 16) > 0
    has_above = rank > 0
    above = np.where(has_above, y[np.maximum(I - 1, 0)], np.inf)
    has_below = rank < cnt[cell] - 1
    bi = np.minimum(I + 1, n - 1)
    below_y = np.where(has_below, y[bi], -np.inf)
    on_top = has_below & walk[bi] & (y - below_y < 1.6)                 # standing on something walkable: a top
    ramp = walk & (up < 0.97) & (up > 0.6)
    GX, GZ = int(gx.max()) + 2, int(gz.max()) + 2
    grid = np.full((GZ, GX), -1, np.int64); grid[gz, gx] = np.arange(len(gx))
    maxc = int(cnt.max()) if len(cnt) else 0

    def nb_cells(si, dx, dz):
        zz = gz[cell[si]] + dz; xx = gx[cell[si]] + dx
        ok = (zz >= 0) & (xx >= 0) & (zz < GZ) & (xx < GX)
        nf = np.where(ok, grid[np.clip(zz, 0, GZ - 1), np.clip(xx, 0, GX - 1)], -1)
        return nf

    def scan_nb(nf, fn):
        """fn(q_index_array, valid_mask) over every surface q of cells nf (vectorised over the 16 slots)."""
        for q in range(maxc):
            m = (nf >= 0) & (q < cnt[np.maximum(nf, 0)])
            si = np.where(m, first[np.maximum(nf, 0)] + q, 0)
            fn(si, m)

    # ---- floor pieces: walkable, not a top, joined to neighbours within 30 cm
    cand = walk & ~terr & ~on_top
    ci = np.flatnonzero(cand)
    ea, eb = [], []
    for dx, dz in ((1, 0), (0, 1)):
        nf = nb_cells(ci, dx, dz); best = np.full(len(ci), -1, np.int64)
        def f(si, m):
            hit = m & cand[si] & (np.abs(y[si] - y[ci]) < 0.3) & (best < 0)
            best[hit] = si[hit]
        scan_nb(nf, f)
        ok = best >= 0; ea.append(ci[ok]); eb.append(best[ok])
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    pos = np.full(n, -1, np.int64); pos[ci] = np.arange(len(ci))
    a_, b_ = pos[np.concatenate(ea)], pos[np.concatenate(eb)]
    G = coo_matrix((np.ones(len(a_)), (a_, b_)), shape=(len(ci), len(ci)))
    nc, lab = connected_components(G, directed=False)
    size = np.bincount(lab, minlength=nc)
    if len(nav) and len(ci):
        from scipy.spatial import cKDTree
        kd = cKDTree(np.c_[nav[:, 0], nav[:, 2], nav[:, 1] * 2])
        px = S["x0"] + (gx[cell[ci]] + 0.5) / k; pz = S["z0"] + (gz[cell[ci]] + 0.5) / k
        d, _ = kd.query(np.c_[px, pz, y[ci] * 2], distance_upper_bound=1.2)
        navnear = np.isfinite(d)
    else:
        navnear = np.zeros(len(ci), bool)
    ceil = has_above[ci] & (above[ci] - y[ci] < 12)
    c_nav = np.bincount(lab, weights=navnear, minlength=nc) / np.maximum(size, 1)
    c_ceil = np.bincount(lab, weights=ceil, minlength=nc) / np.maximum(size, 1)
    bx0 = np.full(nc, 1 << 30); bx1 = np.full(nc, -1); bz0 = np.full(nc, 1 << 30); bz1 = np.full(nc, -1)
    np.minimum.at(bx0, lab, gx[cell[ci]]); np.maximum.at(bx1, lab, gx[cell[ci]])
    np.minimum.at(bz0, lab, gz[cell[ci]]); np.maximum.at(bz1, lab, gz[cell[ci]])
    area = size * cs * cs; thin = np.minimum(bx1 - bx0 + 1, bz1 - bz0 + 1) * cs < 1.5
    is_floor = ((area >= 4) & ~thin & ((c_ceil >= 0.5) | (c_nav >= 0.3))) | (c_nav >= 0.6)
    floor = np.zeros(n, bool); floor[ci[is_floor[lab]]] = True
    small = (area < 4) | thin
    top = on_top.copy(); top[ci[~is_floor[lab] & small[lab]]] = True       # furniture and wall tops
    roofs = int((~is_floor[lab] & ~small[lab]).sum())

    boxes = []
    def add(si, kind, y0, y1):
        si = np.asarray(si, np.int64); y0 = np.asarray(y0, np.float32); y1 = np.asarray(y1, np.float32)
        ok = (y1 - y0) > 0.05
        boxes.append((cell[si][ok], np.full(int(ok.sum()), kind), y0[ok], y1[ok]))
    def add_cells(cells_, kind, y0, y1):
        y0 = np.asarray(y0, np.float32); y1 = np.asarray(y1, np.float32); ok = (y1 - y0) > 0.05
        boxes.append((np.asarray(cells_)[ok], np.full(int(ok.sum()), kind), y0[ok], y1[ok]))

    # floors and stairs
    st = floor & ramp; f0 = floor & ~ramp
    yq = np.round(y / 0.15) * 0.15
    add(np.flatnonzero(f0), 0, y[f0] - 0.12, y[f0])
    sti = np.flatnonzero(st)
    # a flight of stairs is drawn solid down to the floor under it (or 15 cm deep if it's over a drop)
    under = np.where(has_below[sti] & (y[sti] - below_y[sti] < 3.5), below_y[sti], yq[sti] - 0.15)
    add(sti, 4, np.minimum(under, yq[sti] - 0.15), yq[sti])

    # tops of things: from what they stand on (their own cell, else the highest floor next to their piece)
    ground = floor | (walk & terr)
    ti = np.flatnonzero(top)
    base = np.where(has_below[ti] & walk[bi[ti]] & (y[ti] - below_y[ti] < 8), below_y[ti], -np.inf)
    nbb = np.full(len(ti), -np.inf)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nf = nb_cells(ti, dx, dz)
        def f(si, m):
            hit = m & ground[si] & (y[si] < y[ti] - 0.2) & (y[ti] - y[si] < 8)
            nbb[hit] = np.maximum(nbb[hit], y[si][hit])
        scan_nb(nf, f)
    tl = np.where(pos[ti] >= 0, lab[np.maximum(pos[ti], 0)], -1)
    cb = np.full(nc + 1, -np.inf); np.maximum.at(cb, np.where(tl >= 0, tl, nc), nbb)
    base = np.where(np.isfinite(base), base, np.where(tl >= 0, cb[np.where(tl >= 0, tl, nc)], nbb))
    ok = np.isfinite(base)
    h = y[ti] - base
    add(ti[ok & (h < 1.6)], 2, base[ok & (h < 1.6)], y[ti][ok & (h < 1.6)])
    add(ti[ok & (h >= 1.6)], 1, base[ok & (h >= 1.6)], y[ti][ok & (h >= 1.6)])

    # what the scan's knee/head/glass checks found on floors (walls and furniture standing on the floor collider)
    sb = (floor | (walk & terr)) & walk
    w = sb & low & high
    wt = np.where((above[w] - y[w] > 1.0) & (above[w] - y[w] < 8), above[w], y[w] + 2.6)
    add(np.flatnonzero(w), 1, y[w], wt)
    fu = sb & low & ~high
    ft = np.where(above[fu] - y[fu] < 1.6, above[fu], y[fu] + 0.9)
    add(np.flatnonzero(fu), 2, y[fu], np.maximum(ft, y[fu] + 0.3))
    li = sb & high & ~low & (above - y > 2.1) & (above - y < 6)
    add(np.flatnonzero(li), 1, y[li] + 2.0, above[li])
    gl = sb & glass & ~high
    add(np.flatnonzero(gl), 3, y[gl] + 1.0, y[gl] + 2.0)

    # walls the game keeps as solid meshes leave no surface at floor height: next to a floor, a column with
    # nothing within 35 cm of the floor, no floor just below (that would be a drop), and something above, is
    # wall from the floor up to that something. Grown two more cells for thick walls.
    src = np.flatnonzero(floor); src_y = y[src]; src_cell = cell[src]
    seen = set(); wall_cells = []
    for step in range(3):
        new_c, new_y = [], []
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            zz = gz[src_cell] + dz; xx = gx[src_cell] + dx
            okc = (zz >= 0) & (xx >= 0) & (zz < GZ) & (xx < GX)
            nf = np.where(okc, grid[np.clip(zz, 0, GZ - 1), np.clip(xx, 0, GX - 1)], -1)
            near = np.zeros(len(src), bool); drop = np.zeros(len(src), bool); topy = np.full(len(src), np.inf); topi = np.zeros(len(src), np.int64)
            for q in range(maxc):
                m = (nf >= 0) & (q < cnt[np.maximum(nf, 0)])
                si = np.where(m, first[np.maximum(nf, 0)] + q, 0)
                dyv = y[si] - src_y
                near |= m & ~terr[si] & (np.abs(dyv) < 0.35)
                drop |= m & walk[si] & ~terr[si] & (dyv <= -0.35) & (dyv > -2.5)
                above_m = m & (dyv >= 0.35)
                lower = above_m & (y[si] < topy)
                topy = np.where(lower, y[si], topy); topi = np.where(lower, si, topi)
            hit = (nf >= 0) & ~near & ~drop & np.isfinite(topy) & (topy - src_y < 10)
            # under a floor or a stair (a solid platform or flight): no wall, the floor/stair box covers it
            under_floor = floor[topi] & (topy - src_y < 3.2)
            hit &= ~under_floor
            new_c.append(nf[hit]); new_y.append(np.c_[src_y[hit], np.minimum(topy[hit], src_y[hit] + 8)])
        nc_ = np.concatenate(new_c); ny_ = np.concatenate(new_y) if new_y else np.zeros((0, 2))
        keep = []
        for i_, (c_, yy) in enumerate(zip(nc_.tolist(), ny_.tolist())):
            key = (c_, round(yy[0], 1))
            if key in seen: continue
            seen.add(key); keep.append(i_)
        keep = np.array(keep, np.int64)
        if not len(keep): break
        wall_cells.append((nc_[keep], ny_[keep]))
        # grow: the new wall cells act as floors at the same height for the next step
        src_cell = nc_[keep]; src_y = ny_[keep][:, 0]
        src = np.zeros(len(src_cell), np.int64)
    for c_, yy in wall_cells:
        add_cells(c_, 1, yy[:, 0], yy[:, 1])

    c = np.concatenate([b[0] for b in boxes]); kd_ = np.concatenate([b[1] for b in boxes])
    y0 = np.concatenate([b[2] for b in boxes]); y1 = np.concatenate([b[3] for b in boxes])
    stats = {"floor cells": int(f0.sum()), "stair cells": int(st.sum()), "tops": int(ok.sum()), "wall cells (checks)": int(w.sum()),
             "wall cells (solid)": int(sum(len(x[0]) for x in wall_cells)), "furniture cells": int(fu.sum()),
             "glass": int(gl.sum()), "roof cells left out": roofs}
    return gx[c], gz[c], kd_, y0, y1, stats


def merge(gx, gz, kind, y0, y1, k, x0, z0):
    """Greedy: runs along x, then runs of identical runs along z. Heights snapped to 5 cm (walls 10 cm)."""
    q0 = np.round(y0 / np.where(kind == 1, 0.1, 0.05)).astype(np.int64)
    q1 = np.round(y1 / np.where(kind == 1, 0.1, 0.05)).astype(np.int64)
    o = np.lexsort((gx, gz, q1, q0, kind))
    gx, gz, kind, q0, q1, y0, y1 = gx[o], gz[o], kind[o], q0[o], q1[o], y0[o], y1[o]
    same = np.r_[False, (kind[1:] == kind[:-1]) & (q0[1:] == q0[:-1]) & (q1[1:] == q1[:-1]) & (gz[1:] == gz[:-1]) & (gx[1:] == gx[:-1] + 1)]
    run = np.cumsum(~same) - 1
    rs = np.flatnonzero(~same); re = np.r_[rs[1:], len(gx)] - 1
    R = dict(kind=kind[rs], q0=q0[rs], q1=q1[rs], gz=gz[rs], xa=gx[rs], xb=gx[re], y0=y0[rs], y1=y1[rs])
    o = np.lexsort((R["gz"], R["xb"], R["xa"], R["q1"], R["q0"], R["kind"]))
    R = {a: v[o] for a, v in R.items()}
    same = np.r_[False, (R["kind"][1:] == R["kind"][:-1]) & (R["q0"][1:] == R["q0"][:-1]) & (R["q1"][1:] == R["q1"][:-1])
                 & (R["xa"][1:] == R["xa"][:-1]) & (R["xb"][1:] == R["xb"][:-1]) & (R["gz"][1:] == R["gz"][:-1] + 1)]
    bs = np.flatnonzero(~same); be = np.r_[bs[1:], len(R["gz"])] - 1
    out = np.c_[x0 + R["xa"][bs] / k, z0 + R["gz"][bs] / k, x0 + (R["xb"][bs] + 1) / k, z0 + (R["gz"][be] + 1) / k,
                R["y0"][bs], R["y1"][bs], R["kind"][bs]].astype(np.float32)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--out", required=True); ap.add_argument("--id")
    a = ap.parse_args()
    t = time.time()
    meta = json.load(open(os.path.join(a.dump, "meta.json"))) if os.path.exists(os.path.join(a.dump, "meta.json")) else {}
    mid = a.id or meta.get("map") or os.path.basename(a.dump.rstrip("/"))
    S = read_scan(os.path.join(a.dump, "scan.bin"))
    nav = nav_points(a.dump)
    gx, gz, kind, y0, y1, stats = classify(S, nav)
    boxes = merge(gx, gz, kind, y0, y1, S["k"], S["x0"], S["z0"])
    path = os.path.join(a.out, f"{mid}-geo.bin")
    with open(path, "wb") as f:
        f.write(b"GEO4"); f.write(struct.pack("<i", len(boxes))); f.write(boxes.tobytes())
    kc = np.bincount(boxes[:, 6].astype(int), minlength=5)
    print(f"{mid}: {len(S['y']):,} surfaces in {len(S['gx']):,} cells; {stats}; boxes floor {kc[0]}, wall {kc[1]}, "
          f"furniture {kc[2]}, glass {kc[3]}, stairs {kc[4]} -> {os.path.getsize(path) / 1024:.0f} KB ({time.time() - t:.0f} s)")


if __name__ == "__main__":
    main()
