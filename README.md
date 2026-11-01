# Raid Loot Atlas

Where loot spawns on every map of our SPT server, and how likely it is each raid.

**Open it:** https://wowthisworkshuh.github.io/raid-loot-atlas/

Loot search and results are on the left; map controls (map, floors, what to show, height range) are on the right. Pick a map, search an item (or pick a loot type and a minimum value), and the spots light up: the brighter and bigger the glow, the better the odds. The numbered pins are the five best areas. Hover a dot to see everything that can spawn there. Extracts (PMC, Scav, both) and transits show as colored markers, with place names on the buildings; toggle them, and the loot glow and hotspot pins, under "Show on map". On Factory, Labs, Interchange, Ground Zero and Reserve, the floor buttons at the bottom of the map show one floor at a time: roofs and higher floors are cut away and only that floor's loot, extracts and names show. Each extract pillar has a dot at the extract's own height. The Height range sliders do the same for any map by hand.

## The map models

Each map is a rough "dev blockout": grey grid boxes like an unfinished Source map. Building outlines, floors, walls, roads, water and rocks come from [tarkov.dev's SVG maps](https://github.com/the-hideout/tarkov-dev-svg-maps) (CC BY-NC-SA 4.0), lined up with tarkov.dev's own map bounds; the orientation for each map was checked by how many loot spots land inside buildings (Labs re-fitted: 79% to 90% of spots inside rooms on the right floor). Heights come from the loot spots themselves: the ground from outdoor spots, building tops and floor levels from the spots inside each outline. Each river, lake or sea gets one flat level from its shoreline, and the ground under it is dug out. The Labyrinth has no outline map, so it's built from its spots alone. Place names, extracts and transits come from tarkov.dev and match live Tarkov, so a few may differ on SPT.

`tools/build_blockouts.py` rebuilds them (needs the SVG repo and tarkov.dev's `src/data/maps.json`). A map dump (below) replaces the blockout for any map that has one (Factory and Labs so far), drawn as a readable floor plan by `tools/build_dump_assets.py`: floors only where you can walk, walls from the game's collision shapes cut to 2.4 m above every floor, the outline of every walkable area, and big furniture as low blocks. Roofs, ceilings and unreachable rooftops are left out. Loot spots more than 15 m from the walk map (Factory has 111 leftover spots about 200 m outside the building) are not drawn or counted.

## How the odds work

- **Loose loot.** Each spot's chance comes from simulating SPT 4.1's loose-loot draw 6,000 times per map with the pack's loot amount (SVM preset: 0.7 x SPT's default). "Expected per raid" is how many of that item you'd find loose on the whole map in an average raid, if you could check every spot.
- **Containers.** The chance per container you open comes straight from SPT 4.1's container loot tables. Container positions aren't stored on the server (they live in the map files), so they show up on the map after the map export below.
- An item that always spawns at one of a group of spots (the Labs residential keycard, the Labyrinth keys) counts once per raid, shared across its spots.
- Items added by mods (Tushonka Rare Collectibles) aren't included yet.

## Status

| Part | Status |
| --- | --- |
| Loose-loot spots and odds, 10 maps (Factory day and night, Ground Zero both level brackets) | Done |
| Container loot odds | Done |
| Loose-loot data matched to our server's exact SPT 4.1.6 files | To do: run `kit/collect-server-loot.ps1`, send the zip |
| Rough 3D blockouts, extracts, transits and place names, 10 maps | Done |
| Real map layouts from the map dump (walls, floors, stairs, walk map) | Factory and Labs done; other maps need a dump each |
| Container positions on the map | Factory and Labs done; others come with their dump |
| Locked doors, My keys, key values, suggested keys, only-reachable loot | Factory and Labs done |
| Loot runs | To do |
| **Streets of Tarkov** | **To do** (biggest map; done last) |

## Map dump: walkable areas, locked doors, keys and containers (kit/MapDump)

Loot runs, key values and locked doors need data only the game has. `SurvivalPack.MapDump` is a small BepInEx plugin for your own game (not the server or the headless) that writes it once per map, about 20 seconds after a raid loads:

- `navmesh.bin`: the walkable surface the bots use, so routes go round walls, up stairs and never through places you can't reach
- `doors.json`: every door, trunk and keycard door with the key it needs
- `containers.json`: every container's position, matched to the server's container ids
- `exits.json`, `transits.json`: every extract and transit as your SPT version has them
- `colliders.bin` (+ `layers.json`): building layouts, from the game's own collision shapes: every box collider, the triangles of every mesh collider the game keeps readable, and the outline boxes of the rest
- `terrain.bin`: the real ground heights, replacing the rough ground estimated from loot spots

It only reads; nothing in the raid changes.

1. Download `SurvivalPack.MapDump.dll` from the [latest MapDump release](https://github.com/wowthisworkshuh/raid-loot-atlas/releases) (built by GitHub Actions from `kit/MapDump` on every change) and put it in `<SPT>\BepInEx\plugins\SurvivalPack.MapDump\`.
2. Or build it yourself: install the [.NET SDK](https://dotnet.microsoft.com/download) and run `powershell -ExecutionPolicy Bypass -File kit\MapDump\build-mapdump.ps1 -Spt "C:\SPT"`.
3. **Host the raid yourself** (Fika: Host raid without picking the headless, or play offline): the walk map only loads on the game that runs the AI, so a raid on the headless dumps everything except the walk map. Load into Factory first. When the box top-left says the dump was written, send the `BepInEx\survivalpack-mapdump\factory4_day` folder to Claude to check the format. Then do the other maps (Customs, Woods, Shoreline, Interchange, Lighthouse, Reserve, Labs, Labyrinth, Ground Zero). Ctrl+F10 writes it again.
4. `build-mapdump.ps1 -Spt "C:\SPT" -Collect` zips every map's dump to your Desktop. `-Uninstall` removes the plugin.

What it powers, once the dumps are in:

- **Loot run:** pick your spawn and the route goes through the top hotspots for your current loot search in the shortest possible order (solved exactly over the walkable map), then to whichever extract or transit finishes it shortest. Every extract is open from every spawn on this server and raids have no time limit, so only the path matters. The route lists the keys it needs at each locked door.
- **My keys:** tell the map which keys you have. Doors show green if you have the key and red if you don't, and the loot glow and routes only use rooms you can get into.
- **Key values:** for each key, what loot its rooms add to what you can already reach, on average per raid, at handbook prices. Rooms come from `tools/build_reach.py`: the walk map's pieces joined through every unlocked door, with locked doors as gates between them. Rooms the walk map doesn't reach (some vaults have no bot walk map inside) show as "Rooms not mapped" and their loot always shows.
- **Suggested keys:** the keys per map whose rooms are worth the most.
- **Interior layouts:** walls traced round the edges of the walkable areas (every room, corridor and stairwell the bots can reach), filled in with the collision shapes, on the real terrain.

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
