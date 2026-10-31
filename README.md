# Raid Loot Atlas

Where loot spawns on every map of our SPT server, and how likely it is each raid.

**Open it:** https://wowthisworkshuh.github.io/raid-loot-atlas/

Pick a map, search an item (or pick a loot type and a minimum value), and the spots light up: the brighter and bigger the glow, the better the odds. The numbered pins are the five best areas. Hover a dot to see everything that can spawn there. Extracts (PMC, Scav, both) and transits show as colored markers, with place names on the buildings; toggle them under "Show on map". On Factory, Labs, Interchange, Ground Zero and Reserve, the floor buttons at the bottom of the map show one floor at a time: roofs and higher floors are cut away and only that floor's loot, extracts and names show. Each extract pillar has a dot at the extract's own height. The Height range sliders do the same for any map by hand.

## The map models

Each map is a rough "dev blockout": grey grid boxes like an unfinished Source map. Building outlines, floors, walls, roads, water and rocks come from [tarkov.dev's SVG maps](https://github.com/the-hideout/tarkov-dev-svg-maps) (CC BY-NC-SA 4.0), lined up with tarkov.dev's own map bounds; the orientation for each map was checked by how many loot spots land inside buildings (Labs re-fitted: 79% to 90% of spots inside rooms on the right floor). Heights come from the loot spots themselves: the ground from outdoor spots, building tops and floor levels from the spots inside each outline. Each river, lake or sea gets one flat level from its shoreline, and the ground under it is dug out. The Labyrinth has no outline map, so it's built from its spots alone. Place names, extracts and transits come from tarkov.dev and match live Tarkov, so a few may differ on SPT.

`tools/build_blockouts.py` rebuilds them (needs the SVG repo and tarkov.dev's `src/data/maps.json`). A real map export (below) replaces the blockout for any map that has one.

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
| Rough 3D blockouts, extracts, transits and place names, 10 maps | Done |
| Exported map models (real geometry) | Optional: one map at a time, see below |
| Container positions on the map | To do: comes with each map export |
| **Streets of Tarkov** | **To do** (biggest map; done last) |

## Adding a real map model (kit/)

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
