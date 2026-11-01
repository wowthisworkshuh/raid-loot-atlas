"""Turn a MapDump folder into the files the atlas page loads for that map.

  maps/<id>-geo.bin         model: walk-map floors + the game's low-poly collision shapes (walls, floors, stairs)
  maps/<id>-dump.json       doors (with keys), exits, transits from the game
  maps/<id>-containers.json container positions, matched to the server's container ids

Usage: python build_dump_assets.py --dump <dump/factory4_day> --db <SPT database> --tdev-exits extracts-tarkovdev.txt --out maps

geo.bin layout (little endian): "GEO1", int boxCount, int vertCount, int triCount,
  boxes: float cx,cy,cz, sx,sy,sz, qx,qy,qz,qw   (Unity world space, size already scaled)
  floor verts: float x,y,z ; floor tris: uint32 a,b,c
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True); ap.add_argument("--db", required=True)
    ap.add_argument("--tdev-exits", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    meta = json.load(open(os.path.join(a.dump, "meta.json")))
    mid = meta["map"]
    layers = json.load(open(os.path.join(a.dump, "layers.json")))
    locale = json.load(open(os.path.join(a.db, "locales/global/en.json"), encoding="utf-8"))
    handbook = {x["Id"]: x["Price"] for x in json.load(open(os.path.join(a.db, "templates/handbook.json")))["Items"]}

    # ---- model
    V, I = navgraph.load_nav(os.path.join(a.dump, "navmesh.bin"))
    boxes = pick_boxes(load_coll(os.path.join(a.dump, "colliders.bin")), layers, V.min(0), V.max(0))
    key = np.round(V / 0.02).astype(np.int64)
    uk, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    Vw = V[first]; Iw = inv.ravel()[I]
    Iw = Iw[(Iw[:, 0] != Iw[:, 1]) & (Iw[:, 1] != Iw[:, 2]) & (Iw[:, 0] != Iw[:, 2])]
    path = os.path.join(a.out, f"{mid}-geo.bin")
    with open(path, "wb") as f:
        f.write(b"GEO1"); f.write(struct.pack("<iii", len(boxes), len(Vw), len(Iw)))
        out = np.zeros((len(boxes), 10), np.float32)
        out[:, 0:3] = boxes['c']; out[:, 3:6] = boxes['s']; out[:, 6:10] = boxes['q']
        f.write(out.tobytes()); f.write(Vw.astype(np.float32).tobytes()); f.write(Iw.astype(np.uint32).tobytes())
    print(f"{mid}: {len(boxes)} wall/floor shapes, {len(Iw)} walk triangles -> {os.path.getsize(path)/1024:.0f} KB")

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
