# Raid Loot Atlas

Where loot spawns on every map of our SPT server, and how likely it is each raid.

**Open it:** https://wowthisworkshuh.github.io/raid-loot-atlas/

The top bar switches between the **Loot map** (panel tabs Find loot and Keys) and the **Loot run** planner. The map switcher is along the top, and the floor buttons and the Layers menu (what to show, height range) sit on the map; map controls (map, floors, what to show, height range) are on the right. Pick a map, search an item (or pick a loot type and a minimum value), and the spots light up: the brighter and bigger the glow, the better the odds. The numbered pins are the five best areas. Hover a dot to see everything that can spawn there. Extracts (PMC, Scav, both) and transits show as colored markers, with place names on the buildings; toggle them, and the loot glow and hotspot pins, under "Show on map". On Factory, Labs, Interchange, Ground Zero and Reserve, the floor buttons at the bottom of the map show one floor at a time: roofs and higher floors are cut away and only that floor's loot, extracts and names show. Each extract pillar has a dot at the extract's own height. The Height range sliders do the same for any map by hand.

## The map models

Each map is a rough "dev blockout": grey grid boxes like an unfinished Source map. Building outlines, floors, walls, roads, water and rocks come from [tarkov.dev's SVG maps](https://github.com/the-hideout/tarkov-dev-svg-maps) (CC BY-NC-SA 4.0), lined up with tarkov.dev's own map bounds; the orientation for each map was checked by how many loot spots land inside buildings (Labs re-fitted: 79% to 90% of spots inside rooms on the right floor). Heights come from the loot spots themselves: the ground from outdoor spots, building tops and floor levels from the spots inside each outline. Each river, lake or sea gets one flat level from its shoreline, and the ground under it is dug out. The Labyrinth has no outline map, so it's built from its spots alone. Place names, extracts and transits come from tarkov.dev and match live Tarkov, so a few may differ on SPT.

`tools/build_blockouts.py` rebuilds them (needs the SVG repo and tarkov.dev's `src/data/maps.json`). Every map except the Labyrinth and Streets now has a map dump, and is built from it plus the fan maps:

- **Maps with open ground** (Customs, Woods, Shoreline, Lighthouse, Interchange, Reserve): the real ground from the dump's terrain, painted with the fan map's forest, pavement, roads, dirt roads and rail (painted into the ground itself, so nothing flickers), the fan map's water at one level per body, rocks and fences. Buildings come from the walk map (inside a fan-map building outline, under a roof or ceiling, or below ground), with walls traced along their edges like the indoor maps, and the ground pushed down under their floors. Fan-map buildings stay as blocks only where the walk map has no floor inside them. Built by `tools/build_blockouts.py --dumps` then `tools/build_dump_assets.py --svg`.
- **Indoor and city maps** (Factory, Labs, Ground Zero): a floor plan from the walk map: dark floors where you can walk, light walls along every walkable edge (2.4 m on every level, never across a doorway), railings at drops, furniture outlines. Roofs, ceilings and unreachable rooftops are left out.
- **2D map** (Show on map): paints the tarkov.dev fan map onto the ground and floors, matching the floor you're viewing. The SVGs are in `maps/svg` with their licence. The Labs fan map isn't drawn to scale, so it sits up to 1-1.5 m off there.

- **Landmarks** (Show on map): low-poly models of the big props players navigate by, each placed in the game's own collision box (exact position, size and facing) by `tools/build_landmarks.py`: smokestacks, cooling towers, powerline pylons with their wires, tower, gantry and container cranes, billboard towers (IDEA, OLI), the Shoreline weather tower, Reserve's radar dome and radio mast, the lighthouse, Lighthouse's water-treatment tanks, the Woods crash-site plane and Reserve's helicopter. Every copy of a prop shares its collision size, so each type is found by that size on every map.

Place names come from tarkov.dev. Loot spots more than 15 m from the walk map (Factory has 111 leftover spots about 200 m outside the building) are not drawn or counted.

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
| Maps built from the map dumps + fan maps | Done for every map except the Labyrinth (needs a dump) and Streets |
| Container positions on the map | Done (every dumped map) |
| Locked doors, My keys, key values, suggested keys, only-reachable loot | Done (every dumped map) |
| Loot runs | Done (every dumped map): pick a spawn, the route goes through the top stops of your search in the shortest order (exact) to the nearest extract, through doors you have keys for |
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
5. **Building scan (0.3.0):** after each dump the plugin samples the buildings every 25 cm with raycasts against the game's own collision (`scan.bin`): every floor, stair ramp and roof under each point, and whether a wall, furniture or glass stands on each floor. Raycasts reach the collision meshes the game doesn't keep readable, so this is the real layout inside buildings. It takes a minute or two per map with a progress box top-left; stay in the raid until it says the scan is written. Settings: `BepInEx\config\com.survivalpack.mapdump.cfg` (Scan).

What it powers, once the dumps are in:

- **Loot run** (Loot run mode; `tools/build_paths.py` → `maps/<id>-paths.bin/json`): a four-step planner. 1: where you spawn (list grouped by place, or click a spawn dot). 2: what you're after (Anything worth ₽50k+ / ₽100k+, Valuables, Tech parts, Medical, Keys and keycards, or a specific item with suggestions). 3: the goal, either **Fill my bag** (value to bring home plus your free slots; the route never plans more loot than fits, using each item's real size) or **Find a number of it**. 4: which keys you have (tick them right there), and whether to also plan through doors you don't have keys for (each such door counts as an 80 m detour, so it's only used for the loot behind it). Spots and containers within ~6 m form places, valued for the search; the route is the shortest walk from the spawn through places that reach the goal and then to the nearest extract (best gain per extra metre, then 2-opt, moves, drops and swaps, then the exact order for up to 12 places).
- **My keys:** tell the map which keys you have. Doors show green if you have the key and red if you don't, and the loot glow and routes only use rooms you can get into.
- **Key values:** for each key, what loot its rooms add to what you can already reach, on average per raid, at handbook prices. Rooms come from `tools/build_reach.py`: the walk map's pieces joined through every unlocked door, with locked doors as gates between them. Rooms the walk map doesn't reach (some vaults have no bot walk map inside) show as "Rooms not mapped" and their loot always shows.
- **Suggested keys:** the keys per map whose rooms are worth the most.
- **Interior layouts:** walls traced round the edges of the walkable areas (every room, corridor and stairwell the bots can reach), filled in with the collision shapes, on the real terrain.

## Link the loot run to your game (kit/AtlasLink)

`SurvivalPack.AtlasLink` is a small BepInEx plugin for your own game. While you play it answers on your PC only (`http://127.0.0.1:47320/raid`, read only) with the map you're on, your side, your spawn and where you are now. Nothing in the game is changed, and only the atlas site (or a page on your PC) can read it from a browser.

1. Get `SurvivalPack.AtlasLink.dll` and put it in `<SPT>\BepInEx\plugins\SurvivalPack.AtlasLink\`. Each friend who wants it puts it in their own game. There's no release yet; to build it yourself, install the [.NET SDK](https://dotnet.microsoft.com/download) and run `dotnet build kit\AtlasLink\SurvivalPack.AtlasLink.csproj -c Release -p:SptPath="C:\SPT"` (the DLL lands in `kit\AtlasLink\bin\Release\netstandard2.1\`).
2. Open the atlas, switch to **Loot run** and press **Link to my game**. Chrome or Edge may ask to let the site reach devices on your network: allow it (the plugin only answers on this PC). Safari can't link.
3. Start a raid. The atlas switches to that map and plans from your real spawn with your current settings. A pink arrow shows where you are and which way you face.
4. Mid-raid, **Re-plan from where I am** starts a new route where you stand and skips places you've walked right past this raid.

Settings (port, allowed sites, the in-game message) are in `BepInEx\config\com.survivalpack.atlaslink.cfg`. Positions are the game's own coordinates, the same ones the map dumps use.

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
