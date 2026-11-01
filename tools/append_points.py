"""Give loose-loot spots added after the walk-map files were built (mod spots, see build_loot_data.py
--collectibles) their room and walk-graph area, without rebuilding those files.

Room (maps/<id>-reach.json "pt"): the room of the nearest older spot on the same floor (within 4 m across and
1.2 m up or down; failing that within 15 m), else -1 (always shown). Runs again cleanly: the files are cut
back to the data's first mod spot (data "mods" > "firstModPoint") before the new spots are added. Area (maps/<id>-paths.json "pt"): the walk-graph area whose point is
nearest, on the spot's floor or just under it (within 8 m across), else -1 (left out of loot runs).

Usage: python append_points.py --data data --maps maps
"""
import argparse, json, os, struct
import numpy as np

MODEL_OF = {"factory4_night": "factory4_day", "sandbox_high": "sandbox"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--maps", required=True)
    a = ap.parse_args()
    for fn in sorted(os.listdir(a.data)):
        if not fn.endswith(".json") or fn == "index.json": continue
        d = json.load(open(os.path.join(a.data, fn))); mid = d.get("id")
        if not mid: continue
        P = np.array([p[:3] for p in d["points"]], float)
        for kind in ("reach", "paths"):
            path = os.path.join(a.maps, f"{mid}-{kind}.json")
            if not os.path.exists(path): continue
            j = json.load(open(path))
            first = min((m.get("firstModPoint", len(P)) for m in d.get("mods", {}).values()), default=len(P))
            j["pt"] = j["pt"][:first]; old = len(j["pt"])
            if old >= len(P): continue
            if kind == "reach":
                ok = np.array([r != -2 for r in j["pt"]])
                for i in range(old, len(P)):
                    q = P[i]; dxz = np.hypot(*(P[:old, [0, 2]] - q[[0, 2]]).T); dy = np.abs(P[:old, 1] - q[1])
                    c = np.where((dxz < 4) & (dy < 1.2) & ok)[0]
                    if not len(c): c = np.where((dxz < 15) & (dy < 1.2) & ok)[0]
                    j["pt"].append(int(j["pt"][c[np.argmin(dxz[c] + dy[c])]]) if len(c) else -1)
            else:
                b = open(os.path.join(a.maps, f"{MODEL_OF.get(mid, mid)}-paths.bin"), "rb").read()
                n = struct.unpack_from("<i", b, 4)[0]; N = np.frombuffer(b, np.float32, n * 3, 12).reshape(-1, 3)
                for i in range(old, len(P)):
                    q = P[i]; dxz = np.hypot(*(N[:, [0, 2]] - q[[0, 2]]).T); dy = N[:, 1] - q[1]
                    c = np.where((dxz < 8) & (dy > -2.5) & (dy < 1.0))[0]
                    j["pt"].append(int(c[np.argmin(np.abs(dy[c]) + 0.5 * dxz[c])]) if len(c) else -1)
            json.dump(j, open(path, "w"), separators=(",", ":"))
            new = j["pt"][old:]
            print(f"{mid} {kind}: +{len(new)} spots, {sum(1 for x in new if x >= 0)} placed")


if __name__ == "__main__":
    main()
