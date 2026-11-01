"""Real ground from a MapDump terrain.bin (Unity terrains), sampled onto one grid for the page.

TERR v1: int count, then per terrain: float3 position, float3 size, int res, float heights[res*res]
(heights relative to the terrain's position, row = z).
maps/<id>-terrain.bin (written by build_blockouts.py --dumps): "TRN1", int nx, int nz, float x0, z0, dx, dz,
hmin, step, then uint16 heights (h = hmin + v*step, row = z), then uint8 ground kind per vertex
(0 ground, 1 forest, 2 pavement, 3 road, 4 dirt road, 5 rail, 6 wood, 7 under water).
"""
import struct, numpy as np


def load_tiles(path):
    b = open(path, 'rb').read(); assert b[:4] == b'TERR'
    _, n = struct.unpack_from('<ii', b, 4); o = 12; out = []
    for _ in range(n):
        px, py, pz, sx, sy, sz = struct.unpack_from('<6f', b, o); o += 24
        res, = struct.unpack_from('<i', b, o); o += 4
        h = np.frombuffer(b, '<f4', res * res, o).reshape(res, res).astype(np.float64); o += res * res * 4
        out.append(dict(px=px, py=py, pz=pz, sx=sx, sz=sz, res=res, h=h))
    return out


class DumpGround:
    """Bilinear height lookup over the dumped terrain tiles (NaN off the tiles)."""
    def __init__(self, tiles):
        self.tiles = tiles

    def at(self, x, z):
        x = np.asarray(x, float); z = np.asarray(z, float); out = np.full(x.shape, np.nan)
        for t in self.tiles:
            r = t['res']; u = (x - t['px']) / t['sx'] * (r - 1); v = (z - t['pz']) / t['sz'] * (r - 1)
            m = (u >= 0) & (u <= r - 1) & (v >= 0) & (v <= r - 1) & np.isnan(out)
            if not m.any(): continue
            uu = np.clip(u[m], 0, r - 1.0001); vv = np.clip(v[m], 0, r - 1.0001)
            j, i = uu.astype(int), vv.astype(int); a, b_ = uu - j, vv - i; h = t['h']
            out[m] = t['py'] + (h[i, j] * (1 - a) * (1 - b_) + h[i, j + 1] * a * (1 - b_) + h[i + 1, j] * (1 - a) * b_ + h[i + 1, j + 1] * a * b_)
        return out


def grid(ground, x0, x1, z0, z1, step):
    nx = int(np.ceil((x1 - x0) / step)) + 1; nz = int(np.ceil((z1 - z0) / step)) + 1
    gx = x0 + np.arange(nx) * step; gz = z0 + np.arange(nz) * step
    GX, GZ = np.meshgrid(gx, gz)
    H = ground.at(GX, GZ)
    if np.isnan(H).any():  # off the tiles: nearest known height
        from scipy.ndimage import distance_transform_edt
        idx = distance_transform_edt(np.isnan(H), return_distances=False, return_indices=True)
        H = H[tuple(idx)]
    return gx, gz, H


def write(path, gx, gz, H, kind):
    hmin = float(H.min()); step = max((float(H.max()) - hmin) / 65000, 0.002)
    q = np.round((H - hmin) / step).astype(np.uint16)
    with open(path, 'wb') as f:
        f.write(b'TRN1'); f.write(struct.pack('<ii', len(gx), len(gz)))
        f.write(struct.pack('<6f', gx[0], gz[0], gx[1] - gx[0], gz[1] - gz[0], hmin, step))
        f.write(q.tobytes()); f.write(kind.astype(np.uint8).tobytes())


def read(path):
    b = open(path, 'rb').read(); assert b[:4] == b'TRN1'
    nx, nz = struct.unpack_from('<ii', b, 4); x0, z0, dx, dz, hmin, step = struct.unpack_from('<6f', b, 12)
    q = np.frombuffer(b, '<u2', nx * nz, 36).reshape(nz, nx); kind = np.frombuffer(b, 'u1', nx * nz, 36 + nx * nz * 2).reshape(nz, nx)
    gx = x0 + np.arange(nx) * dx; gz = z0 + np.arange(nz) * dz
    return gx, gz, hmin + q.astype(np.float64) * step, kind.copy()


def carve(terrain_path, geo_path, below=0.15, near=1.2):
    """Push the ground under building floors that sit near it, so the ground never shows through a floor.
    Only ground points inside a floor triangle move, and only to just under that floor, so nothing is dug
    out round the outside of buildings."""
    gx, gz, H, kind = read(terrain_path)
    b = open(geo_path, 'rb').read(); assert b[:4] == b'GEO3'
    nw, nv, nt = struct.unpack_from('<iii', b, 4); o = 16 + nw * 28
    V = np.frombuffer(b, '<f4', nv * 3, o).reshape(-1, 3).astype(np.float64); I = np.frombuffer(b, '<u4', nt * 3, o + nv * 12).reshape(-1, 3)
    P = V[I]; lo = P.min(1); hi = P.max(1)
    dx = gx[1] - gx[0]; dz = gz[1] - gz[0]; n = 0
    for t in range(len(I)):
        j0 = max(0, int(np.ceil((lo[t, 0] - gx[0]) / dx))); j1 = min(len(gx) - 1, int(np.floor((hi[t, 0] - gx[0]) / dx)))
        i0 = max(0, int(np.ceil((lo[t, 2] - gz[0]) / dz))); i1 = min(len(gz) - 1, int(np.floor((hi[t, 2] - gz[0]) / dz)))
        if j1 < j0 or i1 < i0: continue
        X, Z = np.meshgrid(gx[j0:j1 + 1], gz[i0:i1 + 1])
        a, b_, c = P[t]
        # barycentric inside test and the floor's height at each ground point
        v0, v1 = (b_ - a)[[0, 2]], (c - a)[[0, 2]]; den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(den) < 1e-9: continue
        px, pz = X - a[0], Z - a[2]
        u = (px * v1[1] - v1[0] * pz) / den; v = (v0[0] * pz - px * v0[1]) / den
        inside = (u >= -0.02) & (v >= -0.02) & (u + v <= 1.02)
        if not inside.any(): continue
        fy = a[1] + u * (b_[1] - a[1]) + v * (c[1] - a[1]) - below
        sub = H[i0:i1 + 1, j0:j1 + 1]
        m = inside & (sub > fy) & (sub < fy + near)
        if m.any(): sub[m] = fy[m]; n += int(m.sum())
    write(terrain_path, gx, gz, H, kind)
    return n
