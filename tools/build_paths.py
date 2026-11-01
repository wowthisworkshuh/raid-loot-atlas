"""Walking graph for loot runs: the walk map grouped into small areas joined where you can walk between them,
plus every door as a link that carries its key.

Areas are grown over the walk map's triangles: about 2.5 m across inside buildings (so doorways, stairs and
corridors keep their shape) and about 7 m across on open ground (so big maps stay small). Each area's point is
the triangle centre nearest its middle, so it always sits on walkable ground.

Usage: python build_paths.py --dump <dump/map> --db <SPT database> --loot data/<map>.json --out maps
           [--svg <tarkov-dev-svg-maps>]  (maps with terrain: which areas are indoors)
           [--base-map <id>]               (Factory night / Ground Zero 21+ use the day / level 1-20 dump)

maps/<model id>-paths.bin: "PTH1", int nodes, int edges, float x,y,z per node, int32 a,b per edge,
  float metres per edge, int16 door per edge (-1 = open walking, else index into the json's doors)
maps/<loot map id>-paths.json: doors [[door id, key tpl or "", "open"|"key"|"breach"|"sealed"]],
  pt (node per loose-loot spot, -1 = not on the walk map), ct {container id: node}, ex [node per exit in -dump.json],
  sp [[x, y, z, node, "pmc"|"scav"|"any", zone]] player spawns
"""
import argparse, json, os, struct, sys, collections
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(__file__))
import navgraph, build_dump_assets as BD  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--db", required=True)
    ap.add_argument("--loot", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--svg"); ap.add_argument("--base-map")
    a = ap.parse_args()
    meta = json.load(open(os.path.join(a.dump, "meta.json"))); bid = meta["map"]
    loot = json.load(open(a.loot)); lid = loot["id"]; base_map = a.base_map or lid
    bin_path = os.path.join(a.out, f"{bid}-paths.bin")
    dumpj = json.load(open(os.path.join(a.out, f"{bid}-dump.json")))
    doors_raw = json.load(open(os.path.join(a.dump, "doors.json")))
    base = json.load(open(os.path.join(a.db, "locations", base_map, "base.json")))
    sps = [s for s in base["SpawnPointParams"] if "Player" in s.get("Categories", [])]

    V, I = navgraph.load_nav(os.path.join(a.dump, "navmesh.bin"))
    pts_loot = np.array([p[:3] for p in loot["points"]])
    conts = [c for c in json.load(open(os.path.join(a.dump, "containers.json"))) if c.get("id")]
    anchors = np.concatenate([pts_loot, np.array([c["pos"] for c in conts]).reshape(-1, 3)])
    spawn_xyz = [[s["Position"][k] for k in "xyz"] for s in sps]
    Vf, If = BD.keep_floors(V, I, doors_raw, spawn_xyz, anchors)
    T, nbr, cent, _ = navgraph.build(Vf, If)
    comp, _ = navgraph.components(nbr)
    n = len(T)
    indoor = BD.indoor_mask(Vf, If, a.dump, bid, a.svg) if (meta.get("terrains", 0) > 0 and a.svg) else np.ones(n, bool)

    # ---- grow areas
    region = -np.ones(n, int); seeds = []
    order = np.lexsort((cent[:, 2] // 6, cent[:, 0] // 6))
    for s in order:
        if region[s] >= 0: continue
        r = 2.5 if indoor[s] else 7.0
        rid = len(seeds); seeds.append(s); region[s] = rid; q = [s]
        while q:
            t = q.pop()
            for u in nbr[t]:
                if region[u] < 0 and indoor[u] == indoor[s] and abs(cent[u][1] - cent[s][1]) < 2.0 \
                        and np.hypot(cent[u][0] - cent[s][0], cent[u][2] - cent[s][2]) <= r:
                    region[u] = rid; q.append(u)
    R = len(seeds)
    members = collections.defaultdict(list)
    for t, r in enumerate(region): members[r].append(t)
    node = np.zeros((R, 3))
    for r, ts in members.items():
        c = cent[ts]; m = c.mean(0); node[r] = c[np.argmin(np.linalg.norm(c - m, axis=1))]

    # ---- walking edges between neighbouring areas
    E = {}
    for t in range(n):
        for u in nbr[t]:
            ra, rb = region[t], region[u]
            if ra < rb and (ra, rb) not in E:
                d = node[rb] - node[ra]
                E[(ra, rb)] = float(np.hypot(d[0], d[2]) + 1.5 * abs(d[1]))  # climbing costs a bit more
    edges = [(ra, rb, w, -1) for (ra, rb), w in E.items()]

    # ---- doors: link the areas either side of every door, carrying its key
    kd = cKDTree(cent)
    door_list = []
    by_id = {d["id"]: d for d in dumpj["doors"]}
    for d in doors_raw:
        if d["type"].split(".")[-1] not in ("Door", "KeycardDoor"): continue
        c = np.array(d.get("boundsCenter") or d["pos"], float); s = np.array(d.get("boundsSize") or [1, 2, 1], float)
        rad = max(s[0], s[2]) / 2 + 1.6; b = c[1] - min(1.0, s[1] / 2 + 0.6)
        best = {}
        for i in kd.query_ball_point(c, rad + 2.0):
            if abs(cent[i][0] - c[0]) < rad and abs(cent[i][2] - c[2]) < rad and -1.2 < cent[i][1] - b < 1.0:
                g = comp[i]; dd = np.linalg.norm(cent[i] - c)
                if g not in best or dd < best[g][0]: best[g] = (dd, i)
        if len(best) < 2: continue  # the walk map already runs through it (an open doorway)
        key = d.get("keyId") or ""
        kind = "open" if d.get("state") != "Locked" else ("key" if key else ("breach" if d.get("fields", {}).get("CanBeBreached") == "True" else "sealed"))
        di = len(door_list); door_list.append([d["id"], key, kind])
        sides = [i for _, i in best.values()]
        for x in range(len(sides)):
            for y in range(x + 1, len(sides)):
                ra, rb = region[sides[x]], region[sides[y]]
                if ra == rb: continue
                w = float(np.linalg.norm(node[ra] - c) + np.linalg.norm(c - node[rb]))
                edges.append((ra, rb, w, di))

    # ---- what sits where
    def node_at(p, r=3.0):
        p = np.asarray(p, float); dd, ix = kd.query(p, k=16)
        cand = [i for d_, i in zip(dd, ix) if d_ < r and cent[i][1] <= p[1] + 0.8] or [i for d_, i in zip(dd, ix) if d_ < r]
        if not cand: return -1
        i = min(cand, key=lambda i: abs(p[1] - cent[i][1]) + 0.5 * np.hypot(*(cent[i][[0, 2]] - p[[0, 2]])))
        return int(region[i])
    pt = [node_at(p) for p in pts_loot]
    ct = {c["id"]: node_at(c["pos"]) for c in conts}
    ex = [node_at([e["x"], e["y"], e["z"]], 8.0) for e in dumpj["exits"]]
    sp = []
    for s in sps:
        p = [s["Position"][k] for k in "xyz"]; sides = set(s.get("Sides", []))
        side = "any" if "All" in sides or {"Pmc", "Savage"} <= sides else ("scav" if "Savage" in sides else "pmc")
        sp.append([round(p[0], 1), round(p[1], 1), round(p[2], 1), node_at(p, 6.0), side, s.get("Infiltration", "")])

    with open(bin_path, "wb") as f:
        f.write(b"PTH1"); f.write(struct.pack("<ii", R, len(edges)))
        f.write(node.astype(np.float32).tobytes())
        f.write(np.array([[e[0], e[1]] for e in edges], np.int32).tobytes())
        f.write(np.array([e[2] for e in edges], np.float32).tobytes())
        f.write(np.array([e[3] for e in edges], np.int16).tobytes())
    json.dump({"map": lid, "doors": door_list, "pt": pt, "ct": ct, "ex": ex, "sp": sp},
              open(os.path.join(a.out, f"{lid}-paths.json"), "w"), separators=(",", ":"))
    kinds = collections.Counter(d[2] for d in door_list)
    print(f"{lid}: {R} areas ({int(indoor.sum())} of {n} triangles indoors), {len(edges)} links, doors {dict(kinds)}, "
          f"spots placed {sum(1 for x in pt if x >= 0)}/{len(pt)}, exits {sum(1 for x in ex if x >= 0)}/{len(ex)}, "
          f"spawns {sum(1 for s in sp if s[3] >= 0)}/{len(sp)} -> {os.path.getsize(bin_path)/1024:.0f} KB")


if __name__ == "__main__":
    main()
