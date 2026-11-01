"""Walk graph from a dumped navmesh: weld vertices, connect triangles that share an edge, then stitch
boundary edges that touch along tile seams (T-junctions)."""
import struct, numpy as np, collections

def load_nav(path):
    b = open(path, 'rb').read(); assert b[:4] == b'NAVM'
    ver, nv, ni = struct.unpack_from('<iii', b, 4); o = 16
    V = np.frombuffer(b, '<f4', nv * 3, o).reshape(-1, 3).astype(np.float64); o += nv * 12
    I = np.frombuffer(b, '<i4', ni, o).reshape(-1, 3)
    return V, I

def build(V, I, weld=0.02, seam=0.06):
    key = np.round(V / weld).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True); inv = inv.ravel()
    T = inv[I]
    n = len(T)
    nbr = [set() for _ in range(n)]
    owner = {}
    for t, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (c, a)):
            k = (min(u, v), max(u, v))
            if k in owner:
                o = owner[k]; nbr[t].add(o); nbr[o].add(t); owner[k] = -1
            else:
                owner[k] = t
    # boundary edges: owned by exactly one triangle
    P = V[I]  # per-triangle corner positions (unwelded is fine)
    bnd = []
    for t in range(n):
        for i in range(3):
            u, v = T[t][i], T[t][(i + 1) % 3]
            if owner.get((min(u, v), max(u, v)), -1) == t:
                bnd.append((t, P[t][i], P[t][(i + 1) % 3]))
    # stitch: sample points along each boundary edge into a grid; edges of different triangles that share a
    # sample cell and are nearly collinear are joined
    cell = {}
    q = seam
    for e, (t, p0, p1) in enumerate(bnd):
        L = np.linalg.norm(p1 - p0); m = max(2, int(L / 0.2) + 1)
        for s in np.linspace(0.1, 0.9, m):
            pt = p0 + (p1 - p0) * s
            cell.setdefault(tuple(np.floor(pt / q).astype(int)), []).append(e)
    stitched = 0
    for es in cell.values():
        if len(es) < 2: continue
        for i in range(len(es)):
            for j in range(i + 1, len(es)):
                ta, a0, a1 = bnd[es[i]]; tb, b0, b1 = bnd[es[j]]
                if ta == tb or tb in nbr[ta]: continue
                da = (a1 - a0) / (np.linalg.norm(a1 - a0) + 1e-9); db = (b1 - b0) / (np.linalg.norm(b1 - b0) + 1e-9)
                if abs(np.dot(da, db)) < 0.98: continue
                nbr[ta].add(tb); nbr[tb].add(ta); stitched += 1
    cent = P.mean(1)
    return T, nbr, cent, stitched

def components(nbr):
    n = len(nbr); comp = -np.ones(n, int); sizes = []
    for s in range(n):
        if comp[s] >= 0: continue
        st = [s]; comp[s] = len(sizes); k = 0
        while st:
            x = st.pop(); k += 1
            for y in nbr[x]:
                if comp[y] < 0: comp[y] = comp[s]; st.append(y)
        sizes.append(k)
    return comp, np.array(sizes)
