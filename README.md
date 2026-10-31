# Raid Loot Atlas

Where loot spawns on every map of our SPT server, and how likely it is each raid.

**Open it:** https://wowthisworkshuh.github.io/raid-loot-atlas/

Pick a map, search an item (or pick a loot type and a minimum value), and the spots light up: the brighter and bigger the glow, the better the odds. The numbered pins are the five best areas. Hover a dot to see everything that can spawn there. Use the floor slice to look at one floor of Labs, Interchange or the Labyrinth at a time.

## How the odds work

- **Loose loot.** Each spot's chance comes from simulating SPT 4.1's loose-loot draw 6,000 times per map with the pack's loot amount (SVM preset: 0.7 x SPT's default). "Expected per raid" is how many of that item you'd find loose on the whole map in an average raid, if you could check every spot.
- **Containers.** The chance per container you open comes straight from SPT 4.1's container loot tables. Container positions aren't stored on the server (they live in the map files), so they show up on the map after the map export below.
- Items added by mods (Tushonka Rare Collectibles) aren't included yet.

## Status

| Part | Status |
| --- | --- |
| Loose-loot spots and odds, 10 maps (Factory day and night, Ground Zero both level brackets) | Done |
| Container loot odds | Done |
| Loose-loot data matched to our server's exact SPT 4.1.6 files | To do: run `kit/collect-server-loot.ps1`, send the zip |
| 3D map models | To do: one map at a time, see below |
| Container positions on the map | To do: comes with each map export |
| **Streets of Tarkov** | **To do** (biggest map; done last) |

## Adding a map model (kit/)

You need: the SPT install, [AssetRipper](https://github.com/AssetRipper/AssetRipper/releases), Unity Hub with a Unity editor, and Node.js.

1. **Get the map into Unity.** Follow DrakiaXYZ's steps: https://gist.github.com/DrakiaXYZ/b828010db2252f5ed34c4c5c839e4ccc (drag all of a map's level files into AssetRipper at once, open the exported project in Unity, drag every scene into the Hierarchy, run the EFTCleaner "show" helpers).
2. **Add the exporter.** In Unity: Package Manager > + > Add package from git URL > `https://github.com/KhronosGroup/UnityGLTF.git`. Copy `kit/Unity/Editor/LootAtlasExporter.cs` into the project's `Assets/Editor/`.
3. **Export.** Tools > Raid Loot Atlas > Export open map. Pick the map, press Export, and choose `kit/container-ids/<map>.txt` when it asks. It writes `<map>.raw.glb` and `<map>-containers.json` to `LootAtlasExport/` in the Unity project. The Console says how many containers it matched.
4. **Shrink it.** `powershell -ExecutionPolicy Bypass -File kit\optimize-map.ps1 -In <path>\<map>.raw.glb` makes `<map>.glb`. Aim for under ~40 MB; use `-Ratio 0.1` if it's bigger.
5. **Publish.** Put `<map>.glb` and `<map>-containers.json` in `maps/` and push. The page finds them by name and lines the spawn points up with the model on its own.

Map file names: `bigmap` (Customs), `factory4_day` (both Factory times), `woods`, `shoreline`, `interchange`, `lighthouse`, `rezervbase` (Reserve), `laboratory` (The Lab), `labyrinth`, `sandbox` (both Ground Zero brackets).

## Rebuilding the data

`tools/build_loot_data.py` turns an SPT database folder into `data/`:

```
python tools/build_loot_data.py --db <SPT database folder> --out data
```

Current data: container tables, item names and categories from SPT 4.1 (sp-tarkov/server-csharp); loose-loot spots from a public copy of SPT's database for game version 0.16.8, because SPT's own copy of those files isn't downloadable from here.

Game content belongs to Battlestate Games. This is a private page for our group.
