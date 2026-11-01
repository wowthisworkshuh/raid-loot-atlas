"""Which loot each key opens up: split the walk map into rooms joined by doors.

Rooms are the walk map's connected pieces, joined through every door that isn't locked. Locked doors become
gates between rooms: a key gate, a breachable gate, or a sealed one. The page walks the gates from the
player spawns with the keys you have, so loot behind doors you can't open drops out of the glow, and each
key's value is the loot its rooms add.

Usage: python build_reach.py --dump <dump/laboratory> --db <SPT database> --loot data/laboratory.json --out maps
       (Factory night uses the day dump: --loot data/factory4_night.json --base-map factory4_night)

Writes maps/<loot map id>-reach.json:
  start   room ids the player spawns are in
  gates   [roomA, roomB, doorId, key tpl or "", "key" | "breach" | "sealed"]
  pt      room id per loose-loot spot (-1: not on the walk map, or a room no door leads to: always shown;
          -2: more than 15 m from any walk map, i.e. outside the playable map: not drawn or counted)
  ct      {container id: room id}
"""
import argparse, json, os, sys, collections
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(__file__))
import navgraph  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--db", required=True)
    ap.add_argument("--loot", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--base-map", help="map id for base.json spawns (default: the loot file's id)")
    a = ap.parse_args()
    loot = json.load(open(a.loot)); lid = loot["id"]
    V, I = navgraph.load_nav(os.path.join(a.dump, "navmesh.bin"))
    if len(I) == 0:
        sys.exit("this dump has no walk map (the raid wasn't hosted by the dumping game)")
    T, nbr, cent, _ = navgraph.build(V, I)
    comp, _ = navgraph.components(nbr)
    kd = cKDTree(cent)

    doors = [d for d in json.load(open(os.path.join(a.dump, "doors.json")))
             if d["type"].split(".")[-1] in ("Door", "KeycardDoor")]
    # door links (hosted dumps) give the points either side of each door: use them to find its two sides
    sides = collections.defaultdict(list)
    lp = os.path.join(a.dump, "links.json")
    for x in (json.load(open(lp)) if os.path.exists(lp) else []):
        if x["type"] == "NavMeshDoorLink":
            m = x["members"]
            for k in ("Open1", "Open2", "Close1", "Close2_Normal", "MidOpen", "MidClose"):
                p = m.get(k)
                if p and any(p): sides[m.get("DoorId", "")].append(p)

    def groups_near(d):
        c = np.array(d.get("boundsCenter") or d["pos"], float); s = np.array(d.get("boundsSize") or [1, 2, 1], float)
        r = max(s[0], s[2]) / 2 + 1.6
        base = c[1] - min(1.0, s[1] / 2 + 0.6)
        g = set()
        for i in kd.query_ball_point(c, r + 2.0):
            if abs(cent[i][0] - c[0]) < r and abs(cent[i][2] - c[2]) < r and -1.2 < cent[i][1] - base < 1.0:
                g.add(int(comp[i]))
        for p in sides.get(d["id"], []):
            dd, i = kd.query(p)
            if dd < 1.0: g.add(int(comp[i]))
        return g

    # rooms: pieces joined through doors that aren't locked
    par = list(range(comp.max() + 1))
    def f(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    locked = []
    for d in doors:
        g = sorted(groups_near(d))
        if d.get("state") == "Locked":
            locked.append((d, g)); continue
        for x in g[1:]: par[f(x)] = f(g[0])
    room_of_piece = np.array([f(x) for x in range(len(par))])
    ids = {r: i for i, r in enumerate(sorted(set(room_of_piece.tolist())))}
    room = np.array([ids[r] for r in room_of_piece])

    def room_at(p, r=2.5):
        p = np.asarray(p, float); dd, ix = kd.query(p, k=16)
        c = [i for d_, i in zip(dd, ix) if d_ < r and cent[i][1] <= p[1] + 0.6]
        if not c: c = [i for d_, i in zip(dd, ix) if d_ < r]
        if not c: return -1
        i = min(c, key=lambda i: abs(p[1] - cent[i][1]) + 0.5 * np.hypot(*(cent[i][[0, 2]] - p[[0, 2]])))
        return int(room[comp[i]])

    gates = []
    for d, g in locked:
        rs = sorted({int(room[x]) for x in g})
        if len(rs) < 2: continue
        k = d.get("keyId") or ""
        kind = "key" if k else ("breach" if d.get("fields", {}).get("CanBeBreached") == "True" else "sealed")
        for x in rs[1:]: gates.append([rs[0], x, d["id"], k, kind])

    bm = a.base_map or lid
    base = json.load(open(os.path.join(a.db, "locations", bm, "base.json")))
    sp = [s["Position"] for s in base["SpawnPointParams"] if "Player" in s.get("Categories", [])]
    start = collections.Counter(room_at([s["x"], s["y"], s["z"]], 3.5) for s in sp)
    start.pop(-1, None)
    starts = [r for r, n in start.items() if n >= 1]

    # rooms no door reaches even with every gate open: their loot is always shown
    adj = collections.defaultdict(set)
    for x, y, *_ in gates: adj[x].add(y); adj[y].add(x)
    seen = set(starts); st = list(starts)
    while st:
        x = st.pop()
        for y in adj[x]:
            if y not in seen: seen.add(y); st.append(y)
    kd_all = cKDTree(V[I].mean(1))
    pt = []
    for p in loot["points"]:
        if kd_all.query(p[:3])[0] > 15: pt.append(-2); continue
        r = room_at(p[:3]); pt.append(r if r in seen else -1)
    ct = {}
    for c in json.load(open(os.path.join(a.dump, "containers.json"))):
        if not c.get("id"): continue
        r = room_at(c["pos"]); ct[c["id"]] = r if r in seen else -1
    used = sorted({x for g in gates for x in g[:2]} | set(starts))
    out = {"map": lid, "start": starts, "gates": gates, "pt": pt, "ct": ct}
    json.dump(out, open(os.path.join(a.out, f"{lid}-reach.json"), "w"), separators=(",", ":"))

    # report: loot spots each key adds on its own
    def reach(keys, breach=True):
        ok = set(starts); st = list(starts)
        while st:
            x = st.pop()
            for g in gates:
                if x not in g[:2]: continue
                if g[4] == "sealed" or (g[4] == "breach" and not breach) or (g[4] == "key" and g[3] not in keys): continue
                y = g[1] if g[0] == x else g[0]
                if y not in ok: ok.add(y); st.append(y)
        return ok
    base_ok = reach(set())
    free = sum(1 for r in pt if r == -1 or r in base_ok)
    print(f"  {pt.count(-2)} loose spots are outside the playable map (more than 15 m from any walk map)")
    print(f"{lid}: {len(set(room.tolist()))} rooms, {len(gates)} gates, spawns in {len(starts)} room(s) "
          f"({sum(start.values())}/{len(sp)} spawns placed); loot spots reachable without keys {free}/{len(pt)}")
    names = json.load(open(os.path.join(a.db, "locales/global/en.json"), encoding="utf-8"))
    for k in sorted({g[3] for g in gates if g[3]}):
        ok = reach({k}); n = sum(1 for r in pt if r >= 0 and r in ok and r not in base_ok)
        nc = sum(1 for r in ct.values() if r >= 0 and r in ok and r not in base_ok)
        print(f"  {names.get(k + ' Name', k)[:46]:46s} +{n} loot spots, +{nc} containers")


if __name__ == "__main__":
    main()
