"""Build the loot layer data for the 3D map viewer.

Reads SPT database files and writes, per map, every loose-loot spawn point with
the chance each item appears there in one raid, using the pack's loose-loot
multipliers. Also writes container loot odds (contents per container type).

Usage:
  python build_loot_data.py --db <SPT_Data/database> [--loose-db <db with looseLoot>] --out <dir>

The chance at a point is P(point is chosen this raid) x P(item | point chosen).
P(point chosen) is estimated by simulating SPT 4.1's LocationLootGenerator:
  count = round(multiplier x Normal(mean, std)); points drawn by weight
  (their `probability`) without replacement; forced / IsAlwaysSpawn points always spawn.
"""
import argparse, json, math, os
import numpy as np

# The pack's SVM preset: 0.7 x SPT's default loose-loot multiplier (pack-settings.md, SVM 2.2.3)
PACK_LOOSE_MULT = {
    "bigmap": 1.75, "factory4_day": 2.45, "factory4_night": 2.45, "interchange": 1.96,
    "laboratory": 1.96, "lighthouse": 1.96, "rezervbase": 2.03, "shoreline": 2.59,
    "tarkovstreets": 2.1, "woods": 1.33, "sandbox": 1.96, "sandbox_high": 1.96, "labyrinth": 2.1,
}
MAPS = [  # (id, display name, group) — Streets is a to-do, built separately later
    ("bigmap", "Customs", "Customs"),
    ("factory4_day", "Factory (day)", "Factory"),
    ("factory4_night", "Factory (night)", "Factory"),
    ("woods", "Woods", "Woods"),
    ("shoreline", "Shoreline", "Shoreline"),
    ("interchange", "Interchange", "Interchange"),
    ("lighthouse", "Lighthouse", "Lighthouse"),
    ("rezervbase", "Reserve", "Reserve"),
    ("laboratory", "The Lab", "The Lab"),
    ("labyrinth", "The Labyrinth", "The Labyrinth"),
    ("sandbox", "Ground Zero (lvl 1-20)", "Ground Zero"),
    ("sandbox_high", "Ground Zero (lvl 21+)", "Ground Zero"),
]
RUNS = 6000


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def root_tpl_by_key(template):
    out = {}
    for it in template.get("Items", []):
        if "parentId" not in it:  # a root item: the key is its composedKey, or (older dumps) its _id
            out[str(it.get("composedKey", it["_id"]))] = it["_tpl"]
            out.setdefault(str(it["_id"]), it["_tpl"])
    if not out:  # some points have one root without a composed key
        for it in template.get("Items", []):
            if it["_id"] == template.get("Root"):
                out["__root__"] = it["_tpl"]
    return out


def item_shares(sp):
    """P(item tpl | this point chosen), from itemDistribution."""
    keys = root_tpl_by_key(sp["template"])
    dist = sp.get("itemDistribution") or []
    total = sum(d.get("relativeProbability") or 0 for d in dist)
    shares = {}
    if total > 0:
        for d in dist:
            k = d["composedKey"]["key"] if isinstance(d.get("composedKey"), dict) else str(d.get("composedKey"))
            tpl = keys.get(str(k))
            if tpl:
                shares[tpl] = shares.get(tpl, 0) + (d.get("relativeProbability") or 0) / total
    elif keys:
        tpl = next(iter(keys.values()))
        shares[tpl] = 1.0
    return shares


def simulate(points, mean, std, mult, rng):
    """Inclusion probability of each random point under SPT's weighted draw without replacement."""
    n = len(points)
    if n == 0:
        return np.zeros(0)
    w = np.array([max(p["probability"] or 0, 1e-12) for p in points])
    hits = np.zeros(n)
    for _ in range(RUNS // 500):
        u = rng.random((500, n))
        keys = np.log(u) / w  # Efraimidis-Spirakis: top-k of u^(1/w) == top-k of log(u)/w
        ks = np.clip(np.round(mult * rng.normal(mean, std, 500)), 0, n).astype(int)
        order = np.argsort(-keys, axis=1)
        ranks = np.empty_like(order)
        rows = np.arange(500)[:, None]
        ranks[rows, order] = np.arange(n)[None, :]
        hits += (ranks < ks[:, None]).sum(axis=0)
    return hits / (RUNS // 500 * 500)


def build_map(mid, loose, rng):
    mult = PACK_LOOSE_MULT[mid]
    sc = loose["spawnpointCount"]
    rand_pts, fixed_pts = [], []
    for sp in loose["spawnpoints"]:
        if sp["template"].get("IsAlwaysSpawn") or sp["probability"] == 1:
            fixed_pts.append(sp)
        else:
            rand_pts.append(sp)
    for sp in loose.get("spawnpointsForced", []):
        fixed_pts.append(dict(sp, forced=True))
    p_rand = simulate(rand_pts, sc["mean"], sc["std"], mult, rng)

    # merge by location (SPT keeps one spawn point per locationId)
    locs = {}
    def add(sp, p):
        pos = sp["template"]["Position"]
        key = sp.get("locationId") or f"({pos['x']}, {pos['y']}, {pos['z']})"
        L = locs.setdefault(key, {"pos": pos, "p": 0.0, "items": {}, "forced": False, "group": sp["template"].get("Id", "")})
        L["p"] = min(1.0, L["p"] + p)
        L["forced"] |= bool(sp.get("forced"))
        for tpl, s in item_shares(sp).items():
            L["items"][tpl] = L["items"].get(tpl, 0) + p * s
    for sp, p in zip(rand_pts, p_rand):
        add(sp, float(p))
    for sp in fixed_pts:
        add(sp, 1.0)

    cov = [sum(item_shares(sp).values()) for sp in rand_pts + fixed_pts]
    unmatched = sum(1 for c in cov if c < 0.99)
    points, entries = [], {}
    for i, L in enumerate(locs.values()):
        pos = L["pos"]
        points.append([round(pos["x"], 2), round(pos["y"], 2), round(pos["z"], 2), round(L["p"], 4), 1 if L["forced"] else 0])
        for tpl, c in L["items"].items():
            if c >= 0.00005 or L["forced"]:
                entries.setdefault(tpl, []).append([i, float(f"{min(c, 1.0):.3g}")])
    return {
        "id": mid, "mult": mult, "spawnpointCount": sc,
        "expectedPoints": round(sum(p[3] for p in points), 1), "unmatchedPoints": unmatched,
        "points": points, "entries": entries,
    }


def container_odds(static_loot, items_used):
    """P(at least one of each item) in one searched container, per container type."""
    out = {}
    for ctpl, d in static_loot.items():
        counts = d.get("itemcountDistribution") or []
        ctot = sum(c["relativeProbability"] for c in counts) or 1
        dist = d.get("itemDistribution") or []
        itot = sum(x["relativeProbability"] for x in dist) or 1
        odds = {}
        for x in dist:
            q = x["relativeProbability"] / itot
            p = sum(c["relativeProbability"] / ctot * (1 - (1 - q) ** c["count"]) for c in counts)
            if p >= 0.0005:
                odds[x["tpl"]] = float(f"{p:.3g}")
                items_used.add(x["tpl"])
        out[ctpl] = odds
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--loose-db", help="database folder to take looseLoot.json from (default: --db)")
    ap.add_argument("--items-db", help="database folder to take templates/items.json from (default: --db)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    loose_db = a.loose_db or a.db
    items_db = a.items_db or a.db
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(7)

    locale = load(os.path.join(a.db, "locales/global/en.json"))
    handbook = load(os.path.join(a.db, "templates/handbook.json"))
    templates = load(os.path.join(items_db, "templates/items.json"))

    used = set()
    index = {"maps": [], "streets": "todo"}
    for mid, name, group in MAPS:
        loose = load(os.path.join(loose_db, "locations", mid, "looseLoot.json"))
        m = build_map(mid, loose, rng)
        static_loot = load(os.path.join(a.db, "locations", mid, "staticLoot.json"))
        sc = load(os.path.join(a.db, "locations", mid, "staticContainers.json"))
        cont_count, cont_ids = {}, {}
        for c in sc.get("staticContainers", []):
            root = next((it["_tpl"] for it in c["template"]["Items"] if it["_id"] == c["template"]["Root"]), None)
            if root:
                cont_count[root] = cont_count.get(root, 0) + 1
                cont_ids[c["template"]["Id"]] = [root, round(c.get("probability", 1), 3)]
        m["containers"] = {"count": cont_count, "ids": cont_ids, "odds": container_odds(static_loot, used)}
        os.makedirs(os.path.join(a.out, "container-ids"), exist_ok=True)
        with open(os.path.join(a.out, "container-ids", f"{mid}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(cont_ids)) + "\n")
        m["name"], m["group"] = name, group
        used.update(m["entries"].keys())
        used.update(cont_count.keys())
        with open(os.path.join(a.out, f"{mid}.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, separators=(",", ":"))
        index["maps"].append({"id": mid, "name": name, "group": group, "points": len(m["points"]),
                              "items": len(m["entries"]), "expectedPoints": m["expectedPoints"], "mult": m["mult"]})
        print(f"{mid:15} points={len(m['points']):5} items={len(m['entries']):4} expected/raid={m['expectedPoints']} unmatched={m['unmatchedPoints']}")

    hb_items = {x["Id"]: x for x in handbook["Items"]}
    cats = {c["Id"]: [locale.get(c["Id"], c["Id"]), c.get("ParentId")] for c in handbook["Categories"]}
    meta = {}
    for tpl in sorted(used):
        t = templates.get(tpl, {})
        props = t.get("_props", {})
        hb = hb_items.get(tpl)
        name = locale.get(f"{tpl} Name") or props.get("Name") or t.get("_name") or tpl
        short = locale.get(f"{tpl} ShortName") or props.get("ShortName") or ""
        cat = hb["ParentId"] if hb else ("quest" if props.get("QuestItem") else "other")
        meta[tpl] = [name, short, cat, hb["Price"] if hb else 0]
    cats["quest"] = ["Quest items", None]
    cats["other"] = ["Other", None]
    index["items"] = meta
    index["categories"] = cats
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"))
    print("items:", len(meta), "categories:", len(cats))


if __name__ == "__main__":
    main()
