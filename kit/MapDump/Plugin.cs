// SurvivalPack.MapDump — writes what the Raid Loot Atlas needs from inside a raid, once per map:
//   navmesh.bin      the walkable surface bots use (NavMesh.CalculateTriangulation): walls, stairs, dead ends
//   doors.json       every door / trunk / keycard door: id, key, state, position, size
//   containers.json  every lootable container: id (matches SPT's staticContainers), position
//   exits.json       extracts and transits: name, type, position
//   colliders.bin    building layouts: every solid box collider (centre, size, rotation) and the triangles
//                    of every mesh collider the game keeps readable; layers.json names their layers
//   terrain.bin      the real ground: each terrain's height grid (downsampled to at most 513 x 513)
//   links.json       how bots get between separate bits of the walk map: Unity off-mesh links and every
//                    component whose type name has "Link" or "NavMesh" in it (EFT's door links, jumps,
//                    ladders), with every position, transform and scalar on it
//   meta.json        map id, game and plugin versions, counts
// Auto session (0.3): Ctrl+F11 in the main menu runs every map that has no scan yet, one after another: it
// clicks through the menus by the buttons' text (side, map, Next/Ready, Fika's Host raid / Start), waits for the
// dump and scan, leaves the raid as Survived and clicks past the end-of-raid screens. Ctrl+F9 writes the button
// texts it can see to ui-texts.txt (to fix names that differ). The texts are in the config (section Session).
//   scan.bin         (0.3) the buildings as the game's own collision sees them, sampled every 25 cm: every
//                    surface under each point (floors, stair ramps, roofs, from repeated downward raycasts) and,
//                    on each floor, whether something solid stands at knee and head height (walls, furniture,
//                    glass). Raycasts work on every collider, including the meshes the game doesn't keep readable,
//                    so this is the real layout inside buildings. Takes a minute or two after the dump; stay in raid.
// Output: <SPT>/BepInEx/survivalpack-mapdump/<mapId>/. Nothing in the raid is changed.
//
// It reads game objects by type name with reflection, so it only references Unity and BepInEx and
// keeps working across SPT updates as long as the type and member names stay the same.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;

namespace SurvivalPack.MapDump
{
    [BepInPlugin("com.survivalpack.mapdump", "SurvivalPack.MapDump", "0.3.0")]
    public class Plugin : BaseUnityPlugin
    {
        ConfigEntry<KeyboardShortcut> _key;
        ConfigEntry<bool> _auto;
        ConfigEntry<float> _autoDelay;
        ConfigEntry<bool> _scan;
        ConfigEntry<float> _scanCell;
        ConfigEntry<int> _scanBudget;
        ConfigEntry<bool> _leave;
        ConfigEntry<KeyboardShortcut> _sessionKey, _textsKey;
        ConfigEntry<string> _sessionMaps, _mapNames, _menuButtons, _sideButton, _uncheck;
        bool _scanning;
        readonly HashSet<string> _done = new HashSet<string>();
        string _msg; float _msgUntil;
        float _worldSeenAt = -1;
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        void Awake()
        {
            _key = Config.Bind("Dump", "Dump now", new KeyboardShortcut(KeyCode.F10, KeyCode.LeftControl), "Write this map's data now (in raid).");
            _auto = Config.Bind("Dump", "Dump automatically", true, "Write each map's data once per game session, a little after the raid loads.");
            _autoDelay = Config.Bind("Dump", "Seconds after load", 20f, "Wait this long after the raid world appears, so every door and container has loaded.");
            _scan = Config.Bind("Scan", "Scan buildings", true, "After the dump, sample the buildings with raycasts (floors, stairs, walls, ceilings) into scan.bin. Takes a minute or two; stay in the raid.");
            _scanCell = Config.Bind("Scan", "Cell size (m)", 0.25f, "Distance between samples. 0.25 shows stairs and thin walls; 0.5 is four times faster.");
            _scanBudget = Config.Bind("Scan", "Milliseconds per frame", 40, "How long the scan may run each frame. Higher is faster but the game stutters more.");
            _leave = Config.Bind("Dump", "Leave raid when done", false, "For a dumping session: when the dump and building scan are written, end the raid as Survived (you keep your gear) so you're back in the menu for the next map.");
            _sessionKey = Config.Bind("Session", "Start or stop", new KeyboardShortcut(KeyCode.F11, KeyCode.LeftControl), "In the main menu: load every map that has no building scan yet, one after another, by itself.");
            _textsKey = Config.Bind("Session", "Write button texts", new KeyboardShortcut(KeyCode.F9, KeyCode.LeftControl), "Write every button text on screen to BepInEx/survivalpack-mapdump/ui-texts.txt.");
            _sessionMaps = Config.Bind("Session", "Maps", "factory4_day,bigmap,woods,shoreline,interchange,lighthouse,rezervbase,sandbox,laboratory,labyrinth", "Maps the session loads, in order (maps that already have scan.bin are skipped).");
            _mapNames = Config.Bind("Session", "Map names on screen", "factory4_day=Factory,bigmap=Customs,woods=Woods,shoreline=Shoreline,interchange=Interchange,lighthouse=Lighthouse,rezervbase=Reserve,sandbox=Ground Zero,laboratory=The Lab,labyrinth=Labyrinth", "How each map is labelled on the map selection screen.");
            _sideButton = Config.Bind("Session", "Side button", "PMC", "Button clicked on the side selection screen (PMC or SCAV).");
            _menuButtons = Config.Bind("Session", "Menu buttons", "Start;Host raid;Next;Ready;Escape from Tarkov;Back to main menu",
                "Buttons the session clicks to move on, first match wins, separated by ;. The map is clicked first on the map screen, and the side button on the side screen.");
            _uncheck = Config.Bind("Session", "Untick before Start", "Use headless", "Tick boxes the session makes sure are off before it presses Start (Fika's Use headless: the walk map only loads on the game that hosts the raid). Separated by ;.");
            Logger.LogInfo("SurvivalPack.MapDump 0.3.0 ready (Ctrl+F10 in raid, Ctrl+F11 in the menu for an auto session)");
        }

        UnityEngine.Object _world;
        float _nextLook;

        void Update()
        {
            // finding the raid world scans every object, so only look every 2 seconds
            if (_world == null && Time.realtimeSinceStartup >= _nextLook)
            {
                _nextLook = Time.realtimeSinceStartup + 2f;
                _world = (FindByTypeName("EFT.GameWorld") ?? FindByTypeName("EFT.ClientGameWorld")) as UnityEngine.Object;
                if (_world != null) { _worldSeenAt = Time.realtimeSinceStartup; _retryNav = 0; _blindUntick = null; _untickTries = 0; }
                else _worldSeenAt = -1;
            }
            if (_textsKey.Value.IsDown()) WriteTexts();
            if (_world == null) { if (_sessionKey.Value.IsDown()) ToggleSession(); if (_session != null) SessionMenuTick(); return; } // also true once the raid's world is destroyed
            if (_sessionKey.Value.IsDown() && _session != null) { _session = null; Say("Auto session stopped. This raid still finishes its dump."); }
            if (_leaveAt > 0 && Time.realtimeSinceStartup >= _leaveAt) { _leaveAt = -1; LeaveRaid(_leaveMap); }

            // the map id is filled in a while after the world appears, so read it when dumping
            if (_key.Value.IsDown()) { Dump(MapId(_world)); return; }
            if (_auto.Value && Time.realtimeSinceStartup - _worldSeenAt > _autoDelay.Value)
            {
                var map = MapId(_world);
                if (!_done.Contains(map)) Dump(map);
                else if (_navMissing.Contains(map) && Time.realtimeSinceStartup > _retryNav) // walk map may load later
                {
                    _retryNav = Time.realtimeSinceStartup + 30f;
                    if (NavMesh.CalculateTriangulation().indices.Length > 0) { _done.Remove(map); Dump(map); }
                }
            }
        }
        float _retryNav;
        readonly HashSet<string> _navMissing = new HashSet<string>();

        void OnGUI()
        {
            if (_msg == null || Time.realtimeSinceStartup > _msgUntil) return;
            var style = new GUIStyle(GUI.skin.box) { fontSize = 16, alignment = TextAnchor.MiddleLeft, wordWrap = true };
            GUI.Box(new Rect(20, 20, 560, 64), _msg, style);
        }

        void Say(string m) { _msg = m; _msgUntil = Time.realtimeSinceStartup + 8; Logger.LogInfo(m); }

        void Dump(string map)
        {
            if (_scanning) return; // finish the running scan first
            _done.Add(map);
            try
            {
                var dir = Path.Combine(Paths.BepInExRootPath, "survivalpack-mapdump", Safe(map));
                Directory.CreateDirectory(dir);
                int tris = DumpNavMesh(Path.Combine(dir, "navmesh.bin"));
                int doors = DumpObjects(Path.Combine(dir, "doors.json"), "EFT.Interactive.WorldInteractiveObject", Door, skipTypeName: "EFT.Interactive.LootableContainer");
                int conts = DumpObjects(Path.Combine(dir, "containers.json"), "EFT.Interactive.LootableContainer", Container);
                int exits = DumpObjects(Path.Combine(dir, "exits.json"), "EFT.Interactive.ExfiltrationPoint", Exit);
                int transits = DumpObjects(Path.Combine(dir, "transits.json"), "EFT.Interactive.TransitPoint", Generic);
                var (boxes, meshes, meshTris, unreadable) = DumpColliders(Path.Combine(dir, "colliders.bin"), Path.Combine(dir, "layers.json"));
                int terrains = DumpTerrain(Path.Combine(dir, "terrain.bin"));
                int links = DumpLinks(Path.Combine(dir, "links.json"));
                var meta = new StringBuilder("{");
                Prop(meta, "map", map); meta.Append(',');
                Prop(meta, "plugin", "0.3.0"); meta.Append(',');
                Prop(meta, "unity", Application.unityVersion); meta.Append(',');
                Prop(meta, "game", Application.version); meta.Append(',');
                Prop(meta, "scenes", string.Join(";", Enumerable.Range(0, SceneManager.sceneCount).Select(i => SceneManager.GetSceneAt(i).name))); meta.Append(',');
                Prop(meta, "time", DateTime.UtcNow.ToString("o")); meta.Append(',');
                meta.Append($"\"navmeshTriangles\":{tris},\"doors\":{doors},\"containers\":{conts},\"exits\":{exits},\"transits\":{transits},");
                meta.Append($"\"boxColliders\":{boxes},\"meshColliders\":{meshes},\"meshColliderTriangles\":{meshTris},\"unreadableMeshColliders\":{unreadable},\"terrains\":{terrains},\"links\":{links}}}");
                File.WriteAllText(Path.Combine(dir, "meta.json"), meta.ToString());
                if (tris == 0)
                {
                    _navMissing.Add(map);
                    Say($"Map dump for {map} written WITHOUT the walk map: this game isn't running the AI. Host the raid yourself (not on the headless) or play offline, then dump again. {doors} doors, {conts} containers, {exits} exits saved.");
                    if (_session != null) { _session.Remove(SessionKey(map)); _leaveAt = Time.realtimeSinceStartup + 8f; _leaveMap = map; }
                }
                else
                {
                    _navMissing.Remove(map);
                    Say($"Map dump for {map} written: {tris:N0} walk triangles, {doors} doors, {conts} containers, {exits} exits, {boxes + meshes:N0} wall/floor shapes. Folder: BepInEx/survivalpack-mapdump/{Safe(map)}");
                    if (_scan.Value) StartCoroutine(Scan(dir, map));
                    else if (_session != null) { SessionDone(map); _leaveAt = Time.realtimeSinceStartup + 5f; _leaveMap = map; }
                }
            }
            catch (Exception e)
            {
                _done.Remove(map);
                Say($"Map dump for {map} failed: {e.Message} (details in the BepInEx log)");
                Logger.LogError(e);
            }
        }

        // ---------- auto session: load the maps one after another by clicking through the menus ----------
        List<string> _session;           // maps still to do (null: no session)
        float _nextClick, _menuIdleSince = -1, _onMapScreenSince = -1, _mapClickedAt = -1;
        string _lastClicked;

        static string SessionKey(string map) => map.StartsWith("factory4", StringComparison.Ordinal) ? "factory4_day" : map.StartsWith("sandbox", StringComparison.Ordinal) ? "sandbox" : map;

        void ToggleSession()
        {
            if (_session != null) { _session = null; Say("Auto session stopped."); return; }
            var root = Path.Combine(Paths.BepInExRootPath, "survivalpack-mapdump");
            _session = _sessionMaps.Value.Split(',').Select(m => m.Trim().ToLowerInvariant()).Where(m => m.Length > 0)
                .Where(m => !File.Exists(Path.Combine(root, Safe(m), "scan.bin"))).ToList();
            if (_session.Count == 0) { _session = null; Say("Every map in the session list already has a building scan. Delete a map's folder to redo it."); return; }
            _menuIdleSince = Time.realtimeSinceStartup; _onMapScreenSince = -1; _mapClickedAt = -1;
            Say($"Auto session: {_session.Count} maps to go ({string.Join(", ", _session.Select(DisplayName))}). Ctrl+F11 stops it.");
        }

        void SessionDone(string map)
        {
            if (_session == null) return;
            _session.Remove(SessionKey(map));
            if (_session.Count == 0) { _session = null; Say($"Auto session finished: every map is dumped and scanned. Zip BepInEx/survivalpack-mapdump and send it."); }
        }

        string DisplayName(string map)
        {
            foreach (var kv in _mapNames.Value.Split(','))
            {
                var p = kv.Split('='); if (p.Length == 2 && p[0].Trim().Equals(map, StringComparison.OrdinalIgnoreCase)) return p[1].Trim();
            }
            return map;
        }

        void SessionMenuTick()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _nextClick || _scanning) return;
            _nextClick = now + 0.8f;
            if (_session.Count == 0) { _session = null; return; }
            var target = _session[0];
            var texts = VisibleTexts();
            if (texts.Count == 0) return; // loading screen
            string Norm(string x) => x.Trim().ToUpperInvariant();
            var allNames = _mapNames.Value.Split(',').Select(kv => kv.Split('=')).Where(p => p.Length == 2).Select(p => Norm(p[1])).ToList();
            bool mapScreen = texts.Count(t => allNames.Contains(t.text)) >= 3;

            if (mapScreen)
            {
                if (_onMapScreenSince < 0) _onMapScreenSince = now;
                var mine = texts.FirstOrDefault(t => t.text == Norm(DisplayName(target)));
                if (mine.go == null || now - _onMapScreenSince > 45f)
                {   // this map can't be picked (locked, or named differently): skip it
                    Say($"Auto session: couldn't pick {DisplayName(target)} on the map screen, skipping it.");
                    _session.RemoveAt(0); _onMapScreenSince = now; _mapClickedAt = -1;
                    return;
                }
                if (_mapClickedAt < 0 || now - _mapClickedAt > 6f) { Click(mine.go, mine.text); _mapClickedAt = now; return; }
                // map chosen: Next (Ready skips Fika's raid screen and is often greyed out here)
                var next = texts.FirstOrDefault(t => t.text == "NEXT");
                if (next.go != null && now - _mapClickedAt > 1.2f && Usable("NEXT", texts)) { Click(next.go, "NEXT"); return; }
            }
            else { _onMapScreenSince = -1; _mapClickedAt = -1; }

            // Fika's host dialog: untick "Use headless" (the walk map only exists on the game that hosts), then Start
            foreach (var u in _uncheck.Value.Split(';').Select(Norm).Where(u => u.Length > 0))
            {
                var box = texts.FirstOrDefault(t => t.text == u);
                if (box.go == null) continue;
                var tog = FindToggle(box.go);
                bool? on = tog == null ? (bool?)null : Get(tog, "isOn") as bool?;
                if (on == true)
                {
                    if (++_untickTries > 3) { try { tog.GetType().GetProperty("isOn")?.SetValue(tog, false); } catch { } _untickTries = 0; }
                    else Click(((Component)tog).gameObject, "untick " + u);
                    return;
                }
                if (on == null && _blindUntick != u) { _blindUntick = u; Click(box.go, "untick " + u + " (state unknown)"); return; }
                _untickTries = 0;
            }

            // the side screen: pick the side first
            var side = Norm(_sideButton.Value);
            var sideBtn = texts.FirstOrDefault(t => t.text == side);
            if (sideBtn.go != null && _lastClicked != side && texts.Any(t => t.text == "NEXT")) { Click(sideBtn.go, side); return; }

            foreach (var b in _menuButtons.Value.Split(';').Select(Norm).Where(b => b.Length > 0))
            {
                var hit = texts.FirstOrDefault(t => t.text == b);
                if (hit.go == null || !Usable(b, texts)) continue;
                Click(hit.go, b);
                _menuIdleSince = now;
                return;
            }
            if (now - _menuIdleSince > 90f)
            {
                Say($"Auto session stuck: no button to press here (next map {DisplayName(target)}). Click on yourself; it picks up again. Ctrl+F9 writes the texts on screen for a fix.");
                _menuIdleSince = now;
            }
        }

        int _untickTries; string _blindUntick;
        // the tick box that belongs to a label: on the label, a parent, or a parent's other children (a Toggle has a bool isOn)
        static Component FindToggle(GameObject label)
        {
            var t = label.transform;
            for (int up = 0; up < 4 && t != null; up++, t = t.parent)
            {
                Component best = null; float bd = float.MaxValue;
                foreach (var c in t.GetComponentsInChildren<Component>(true))
                {
                    if (c == null) continue;
                    var p = c.GetType().GetProperty("isOn", BindingFlags.Public | BindingFlags.Instance);
                    if (p == null || p.PropertyType != typeof(bool)) continue;
                    float d = Vector3.Distance(c.transform.position, label.transform.position); // the box nearest the label
                    if (d < bd) { bd = d; best = c; }
                }
                if (best != null) return best;
            }
            return null;
        }

        // a button that's clicked again and again on an unchanged screen is greyed out: try the next one for a while
        string _screenSig; int _sameClicks; readonly Dictionary<string, float> _skipUntil = new Dictionary<string, float>();
        bool Usable(string button, List<(string text, GameObject go)> texts)
        {
            float now = Time.realtimeSinceStartup;
            if (_skipUntil.TryGetValue(button, out var until) && now < until) return false;
            var sig = string.Join("|", texts.Select(t => t.text).Distinct().OrderBy(x => x).Take(40));
            if (button == _lastClicked && sig == _screenSig) { if (++_sameClicks >= 3) { _skipUntil[button] = now + 20f; _sameClicks = 0; Logger.LogInfo($"Auto session: {button} does nothing here, trying the next button"); return false; } }
            else _sameClicks = 0;
            _screenSig = sig;
            return true;
        }

        void Click(GameObject go, string what)
        {
            _lastClicked = what; _menuIdleSince = Time.realtimeSinceStartup;
            try
            {
                var es = FindType("UnityEngine.EventSystems.EventSystem");
                var cur = es?.GetProperty("current", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                var ped = Activator.CreateInstance(FindType("UnityEngine.EventSystems.PointerEventData"), cur);
                var ee = FindType("UnityEngine.EventSystems.ExecuteEvents");
                var exec = ee.GetMethods(BindingFlags.Public | BindingFlags.Static).First(m => m.Name == "ExecuteHierarchy" && m.IsGenericMethodDefinition);
                foreach (var (iface, prop) in new[] { ("IPointerEnterHandler", "pointerEnterHandler"), ("IPointerDownHandler", "pointerDownHandler"), ("IPointerUpHandler", "pointerUpHandler"), ("IPointerClickHandler", "pointerClickHandler") })
                {
                    var it = FindType("UnityEngine.EventSystems." + iface);
                    var fn = ee.GetProperty(prop, BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                    if (it != null && fn != null) exec.MakeGenericMethod(it).Invoke(null, new object[] { go, ped, fn });
                }
                Logger.LogInfo($"Auto session: clicked {what}");
            }
            catch (Exception e) { Logger.LogWarning($"Auto session: clicking {what} failed: {e}"); }
        }

        // every active, enabled text on screen (TextMeshPro and Unity UI), upper-cased, rich-text tags removed
        List<(string text, GameObject go)> VisibleTexts()
        {
            var res = new List<(string, GameObject)>();
            foreach (var tn in new[] { "TMPro.TMP_Text", "UnityEngine.UI.Text" })
            {
                var t = FindType(tn); if (t == null) continue;
                var prop = t.GetProperty("text", BindingFlags.Public | BindingFlags.Instance);
                foreach (var o in UnityEngine.Object.FindObjectsOfType(t))
                {
                    if (!(o is Behaviour c) || !c.isActiveAndEnabled) continue;
                    string s; try { s = prop?.GetValue(o) as string; } catch { continue; }
                    if (string.IsNullOrWhiteSpace(s)) continue;
                    s = System.Text.RegularExpressions.Regex.Replace(s, "<[^>]*>", "").Trim().ToUpperInvariant();
                    if (s.Length > 0 && s.Length < 60) res.Add((s, c.gameObject));
                }
            }
            return res;
        }

        void WriteTexts()
        {
            try
            {
                var root = Path.Combine(Paths.BepInExRootPath, "survivalpack-mapdump"); Directory.CreateDirectory(root);
                var lines = VisibleTexts().Select(t => t.text + "    (" + PathOf(t.go.transform) + ")").Distinct().ToList();
                File.WriteAllLines(Path.Combine(root, "ui-texts.txt"), lines);
                Say($"{lines.Count} texts on screen written to BepInEx/survivalpack-mapdump/ui-texts.txt");
            }
            catch (Exception e) { Say("Writing the texts failed: " + e.Message); }
        }
        static string PathOf(Transform t) { var s = t.name; for (int i = 0; i < 4 && t.parent != null; i++) { t = t.parent; s = t.name + "/" + s; } return s; }

        // ---------- building scan ----------
        // SCN1: "SCN1", int version 1, float coarse cell (1 m), int k (fine cells per coarse side), float x0, float z0,
        //       int cx, int cz; then cx*cz bytes: 1 = coarse cell scanned (a building, a bunker, anything that isn't
        //       open ground); then for every scanned coarse cell in row-major order (z rows, x columns), its k*k fine
        //       cells in row-major order, each: byte n, then n surfaces top to bottom: int16 height (cm), byte up
        //       (normal.y: 0..255 = -1..1), byte flags (1 walkable, 2 solid 0.3-1.1 m above, 4 solid 1.5-2.1 m above,
        //       8 terrain, 16 glass 1.0-1.8 m above).
        // Fine cell (i, j) of coarse cell (a, b) is centred at x0 + a + (i + 0.5) / k, z0 + b + (j + 0.5) / k.
        System.Collections.IEnumerator Scan(string dir, string map)
        {
            _scanning = true;
            var path = Path.Combine(dir, "scan.bin");
            var clock = System.Diagnostics.Stopwatch.StartNew(); var slice = System.Diagnostics.Stopwatch.StartNew();
            var world = _world;
            BinaryWriter w = null;
            try
            {
                var nav = NavMesh.CalculateTriangulation(); var V = nav.vertices;
                if (V.Length == 0) yield break;
                float minX = float.MaxValue, maxX = float.MinValue, minZ = float.MaxValue, maxZ = float.MinValue, minY = float.MaxValue, maxY = float.MinValue;
                foreach (var v in V) { minX = Math.Min(minX, v.x); maxX = Math.Max(maxX, v.x); minZ = Math.Min(minZ, v.z); maxZ = Math.Max(maxZ, v.z); minY = Math.Min(minY, v.y); maxY = Math.Max(maxY, v.y); }
                minX = Mathf.Floor(minX - 10); minZ = Mathf.Floor(minZ - 10); maxX += 10; maxZ += 10;
                float top = maxY + 50, bottom = minY - 15;
                int cx = Mathf.CeilToInt(maxX - minX), cz = Mathf.CeilToInt(maxZ - minZ);
                int k = Math.Max(1, Mathf.RoundToInt(1f / Mathf.Clamp(_scanCell.Value, 0.1f, 1f)));
                float fc = 1f / k, half = fc * 0.45f;
                int mSurf = LayerMask.GetMask("LowPolyCollider", "Terrain", "Default");
                int mWall = LayerMask.GetMask("LowPolyCollider", "Default");
                int mGlass = LayerMask.GetMask("TransparentCollider");
                const QueryTriggerInteraction NoTrig = QueryTriggerInteraction.Ignore;

                // the lowest walk-map point in each coarse cell (finds tunnels and bunkers under the ground)
                var navLow = new float[cx * cz];
                for (int i = 0; i < navLow.Length; i++) navLow[i] = float.MaxValue;
                void MarkNav(Vector3 p) { int a = (int)(p.x - minX), b = (int)(p.z - minZ); if (a >= 0 && b >= 0 && a < cx && b < cz) navLow[b * cx + a] = Math.Min(navLow[b * cx + a], p.y); }
                foreach (var v in V) MarkNav(v);
                var I = nav.indices;
                for (int t = 0; t + 2 < I.Length; t += 3) MarkNav((V[I[t]] + V[I[t + 1]] + V[I[t + 2]]) / 3f);

                // pass 1: which coarse cells have anything but open ground (first hit isn't the terrain, or the walk map
                // runs more than 2 m under the first hit)
                var mark = new bool[cx * cz];
                for (int b = 0; b < cz; b++)
                {
                    for (int a = 0; a < cx; a++)
                    {
                        int i = b * cx + a;
                        if (Physics.Raycast(new Vector3(minX + a + 0.5f, top, minZ + b + 0.5f), Vector3.down, out var hit, top - bottom, mSurf, NoTrig))
                            mark[i] = !(hit.collider is TerrainCollider) || navLow[i] < hit.point.y - 2f;
                        else mark[i] = navLow[i] != float.MaxValue;
                    }
                    if (slice.ElapsedMilliseconds > _scanBudget.Value)
                    {
                        if (world == null || (UnityEngine.Object)world == null) { Say($"Building scan for {map} stopped: the raid ended."); yield break; }
                        Progress(map, 0.1f * b / cz, clock); yield return null; slice.Restart();
                    }
                }
                // grow by 2 m so walls on the edge of a building are inside the scan
                var grown = new bool[cx * cz]; int scanned = 0;
                for (int b = 0; b < cz; b++)
                    for (int a = 0; a < cx; a++)
                    {
                        bool any = false;
                        for (int db = -2; db <= 2 && !any; db++)
                            for (int da = -2; da <= 2 && !any; da++)
                            {
                                int aa = a + da, bb = b + db;
                                if (aa >= 0 && bb >= 0 && aa < cx && bb < cz && mark[bb * cx + aa]) any = true;
                            }
                        grown[b * cx + a] = any; if (any) scanned++;
                    }

                w = new BinaryWriter(File.Create(path));
                w.Write(Encoding.ASCII.GetBytes("SCN1")); w.Write(1); w.Write(1f); w.Write(k); w.Write(minX); w.Write(minZ); w.Write(cx); w.Write(cz);
                foreach (var g in grown) w.Write((byte)(g ? 1 : 0));

                // pass 2: every surface under each fine cell, and what stands on each floor
                var ys = new short[16]; var ups = new byte[16]; var fls = new byte[16];
                long surfaces = 0; int done = 0;
                var hb = new Vector3(half, 0.4f, half); var hh = new Vector3(half, 0.3f, half);
                for (int b = 0; b < cz; b++)
                {
                    for (int a = 0; a < cx; a++)
                    {
                        if (!grown[b * cx + a]) continue;
                        for (int j = 0; j < k; j++)
                            for (int i = 0; i < k; i++)
                            {
                                float x = minX + a + (i + 0.5f) * fc, z = minZ + b + (j + 0.5f) * fc, y = top;
                                int n = 0;
                                while (n < 16 && y > bottom && Physics.Raycast(new Vector3(x, y, z), Vector3.down, out var hit, y - bottom, mSurf, NoTrig))
                                {
                                    float hy = hit.point.y, ny = hit.normal.y; byte fl = 0;
                                    if (hit.collider is TerrainCollider) fl |= 8;
                                    if (ny > 0.6f)
                                    {
                                        fl |= 1;
                                        if (Physics.CheckBox(new Vector3(x, hy + 0.7f, z), hb, Quaternion.identity, mWall, NoTrig)) fl |= 2;
                                        if (Physics.CheckBox(new Vector3(x, hy + 1.8f, z), hh, Quaternion.identity, mWall, NoTrig)) fl |= 4;
                                        else if (Physics.CheckBox(new Vector3(x, hy + 1.4f, z), hb, Quaternion.identity, mGlass, NoTrig)) fl |= 16;
                                    }
                                    ys[n] = (short)Mathf.Clamp(Mathf.RoundToInt(hy * 100f), short.MinValue, short.MaxValue);
                                    ups[n] = (byte)Mathf.Clamp(Mathf.RoundToInt((ny + 1f) * 127.5f), 0, 255); fls[n] = fl; n++;
                                    y = hy - 0.05f;
                                }
                                w.Write((byte)n);
                                for (int q = 0; q < n; q++) { w.Write(ys[q]); w.Write(ups[q]); w.Write(fls[q]); }
                                surfaces += n;
                            }
                        done++;
                        if (slice.ElapsedMilliseconds > _scanBudget.Value)
                        {
                            if (world == null || (UnityEngine.Object)world == null) { w.Dispose(); w = null; File.Delete(path); Say($"Building scan for {map} stopped: the raid ended. Load the map again to finish it."); yield break; }
                            Progress(map, 0.1f + 0.9f * done / Math.Max(1, scanned), clock); yield return null; slice.Restart();
                        }
                    }
                }
                w.Dispose(); w = null;
                var info = new StringBuilder("{");
                Prop(info, "map", map); info.Append(',');
                info.Append($"\"cell\":{(1f / k).ToString(Inv)},\"k\":{k},\"x0\":{minX.ToString(Inv)},\"z0\":{minZ.ToString(Inv)},\"cx\":{cx},\"cz\":{cz},");
                info.Append($"\"scannedCells\":{scanned},\"fineCells\":{(long)scanned * k * k},\"surfaces\":{surfaces},\"seconds\":{(int)clock.Elapsed.TotalSeconds}}}");
                File.WriteAllText(Path.Combine(dir, "scan.json"), info.ToString());
                Say($"Building scan for {map} written: {scanned:N0} m² of buildings, {surfaces:N0} surfaces, {(int)clock.Elapsed.TotalSeconds} s. " + (_leave.Value ? "Leaving the raid in 5 s." : "You can leave the raid."));
                SessionDone(map);
                if (_leave.Value || _session != null) { _leaveAt = Time.realtimeSinceStartup + 5f; _leaveMap = map; }
            }
            finally
            {
                if (w != null) { w.Dispose(); try { File.Delete(path); } catch { } }
                _scanning = false;
            }
        }

        float _leaveAt = -1; string _leaveMap;

        // ends the raid the way extract mods do: Singleton<AbstractGame>.Instance.Stop(profileId, ExitStatus.Survived, exitName, delay)
        void LeaveRaid(string map)
        {
            try
            {
                var ag = FindType("EFT.AbstractGame"); var single = FindType("Comfort.Common.Singleton`1"); var gw = FindType("EFT.GameWorld");
                var game = single.MakeGenericType(ag).GetProperty("Instance", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                var world = single.MakeGenericType(gw).GetProperty("Instance", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                var player = world == null ? null : Get(world, "MainPlayer");
                var profileId = player == null ? null : Str(Get(player, "ProfileId"));
                if (game == null || profileId == null) throw new Exception("no game or player");
                var stop = game.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                    .Where(m => m.Name == "Stop").Select(m => (m, ps: m.GetParameters()))
                    .Where(x => x.ps.Length >= 2 && x.ps[0].ParameterType == typeof(string) && x.ps[1].ParameterType.IsEnum && x.ps[1].ParameterType.Name == "ExitStatus")
                    .OrderByDescending(x => x.ps.Length).FirstOrDefault();
                if (stop.m == null) throw new Exception("no Stop method");
                var status = Enum.Parse(stop.ps[1].ParameterType, "Survived");
                var args = new object[stop.ps.Length]; args[0] = profileId; args[1] = status;
                for (int i = 2; i < args.Length; i++)
                {
                    var t = stop.ps[i].ParameterType;
                    args[i] = t == typeof(string) ? "MapDump" : t == typeof(float) ? 0f : (stop.ps[i].HasDefaultValue ? stop.ps[i].DefaultValue : (t.IsValueType ? Activator.CreateInstance(t) : null));
                }
                stop.m.Invoke(game, args);
                Logger.LogInfo($"Left the raid on {map} after the dump");
            }
            catch (Exception e)
            {
                Say($"Dump and scan for {map} are done, but leaving automatically didn't work ({e.Message}). Leave the raid yourself.");
                Logger.LogWarning(e);
            }
        }

        void Progress(string map, float f, System.Diagnostics.Stopwatch clock)
        {
            var left = f > 0.05f ? $", about {Mathf.CeilToInt((float)clock.Elapsed.TotalSeconds * (1 - f) / f)} s left" : "";
            _msg = $"Scanning the buildings of {map}: {Mathf.FloorToInt(f * 100)}%{left}. Stay in the raid.";
            _msgUntil = Time.realtimeSinceStartup + 5;
        }

        // ---------- navmesh ----------
        static int DumpNavMesh(string path)
        {
            var t = NavMesh.CalculateTriangulation();
            using (var w = new BinaryWriter(File.Create(path)))
            {
                w.Write(Encoding.ASCII.GetBytes("NAVM")); w.Write(1);
                w.Write(t.vertices.Length); w.Write(t.indices.Length);
                foreach (var v in t.vertices) { w.Write(v.x); w.Write(v.y); w.Write(v.z); }
                foreach (var i in t.indices) w.Write(i);
                foreach (var a in t.areas) w.Write(a);
            }
            return t.indices.Length / 3;
        }

        // ---------- building layouts ----------
        // COLL v1: int boxCount, then per box: int layer, int kind (0 box collider, 1 bounds of a mesh collider the game
        //          doesn't keep readable), float3 centre (world), float3 size (scaled), float4 rotation;
        //          int meshCount, then per mesh: int layer, int vertCount, int indexCount, float3 verts (world), int indices.
        // Triggers, disabled colliders and moving bodies are skipped; so is anything above 2,000,000 mesh triangles in total.
        static (int, int, int, int) DumpColliders(string path, string layersPath)
        {
            var cols = UnityEngine.Object.FindObjectsOfType<Collider>()
                .Where(c => c.enabled && !c.isTrigger && c.gameObject.activeInHierarchy && !(c is TerrainCollider) && !(c is CharacterController))
                .Where(c => { var rb = c.attachedRigidbody; return rb == null || rb.isKinematic; })
                .ToList();
            var boxes = cols.OfType<BoxCollider>().ToList();
            var meshes = cols.OfType<MeshCollider>().ToList();
            int meshTris = 0, unreadable = 0, written = 0;
            var unreadableMeshes = meshes.Where(m => m.sharedMesh != null && !m.sharedMesh.isReadable).ToList();
            unreadable = unreadableMeshes.Count;
            using (var w = new BinaryWriter(File.Create(path)))
            {
                w.Write(Encoding.ASCII.GetBytes("COLL")); w.Write(1);
                w.Write(boxes.Count + unreadableMeshes.Count);
                void Box(Transform t, int layer, int kind, Vector3 localCenter, Vector3 localSize)
                {
                    var c = t.TransformPoint(localCenter);
                    var s = Vector3.Scale(localSize, t.lossyScale);
                    w.Write(layer); w.Write(kind);
                    w.Write(c.x); w.Write(c.y); w.Write(c.z);
                    w.Write(Mathf.Abs(s.x)); w.Write(Mathf.Abs(s.y)); w.Write(Mathf.Abs(s.z));
                    var q = t.rotation; w.Write(q.x); w.Write(q.y); w.Write(q.z); w.Write(q.w);
                }
                foreach (var b in boxes) Box(b.transform, b.gameObject.layer, 0, b.center, b.size);
                foreach (var m in unreadableMeshes) Box(m.transform, m.gameObject.layer, 1, m.sharedMesh.bounds.center, m.sharedMesh.bounds.size);
                var keep = new List<(MeshCollider, Mesh)>();
                foreach (var m in meshes)
                {
                    var mesh = m.sharedMesh;
                    if (mesh == null || !mesh.isReadable) continue;
                    int n = (int)(mesh.GetIndexCount(0) / 3);
                    for (int sm = 1; sm < mesh.subMeshCount; sm++) n += (int)(mesh.GetIndexCount(sm) / 3);
                    if (meshTris + n > 2000000) continue;
                    meshTris += n; keep.Add((m, mesh));
                }
                w.Write(keep.Count);
                foreach (var (m, mesh) in keep)
                {
                    var verts = mesh.vertices; var idx = mesh.triangles; var tr = m.transform;
                    w.Write(m.gameObject.layer); w.Write(verts.Length); w.Write(idx.Length);
                    foreach (var v in verts) { var p = tr.TransformPoint(v); w.Write(p.x); w.Write(p.y); w.Write(p.z); }
                    foreach (var i in idx) w.Write(i);
                    written++;
                }
            }
            var sb = new StringBuilder("{");
            for (int i = 0; i < 32; i++) { if (i > 0) sb.Append(','); Prop(sb, i.ToString(Inv), LayerMask.LayerToName(i)); }
            File.WriteAllText(layersPath, sb.Append('}').ToString());
            return (boxes.Count, written, meshTris, unreadable);
        }

        // TERR v1: int count, then per terrain: float3 position, float3 size, int res, float heights[res*res] (metres, row = z)
        static int DumpTerrain(string path)
        {
            var terrains = Terrain.activeTerrains;
            using (var w = new BinaryWriter(File.Create(path)))
            {
                w.Write(Encoding.ASCII.GetBytes("TERR")); w.Write(1);
                w.Write(terrains.Length);
                foreach (var t in terrains)
                {
                    var d = t.terrainData; var p = t.transform.position; var size = d.size;
                    int res = Mathf.Min(d.heightmapResolution, 513);
                    w.Write(p.x); w.Write(p.y); w.Write(p.z); w.Write(size.x); w.Write(size.y); w.Write(size.z); w.Write(res);
                    for (int z = 0; z < res; z++)
                        for (int x = 0; x < res; x++)
                            w.Write(d.GetInterpolatedHeight(x / (float)(res - 1), z / (float)(res - 1)));
                }
            }
            return terrains.Length;
        }

        // ---------- bot links ----------
        int DumpLinks(string path)
        {
            var sb = new StringBuilder("[\n");
            int n = 0;
            var counts = new Dictionary<string, int>();
            foreach (var o in Resources.FindObjectsOfTypeAll(typeof(Behaviour)))
            {
                var b = o as Behaviour;
                if (b == null || !b.gameObject.scene.IsValid()) continue;
                var tn = b.GetType().Name;
                bool link = b is OffMeshLink || tn.IndexOf("Link", StringComparison.OrdinalIgnoreCase) >= 0
                            || (tn.IndexOf("NavMesh", StringComparison.OrdinalIgnoreCase) >= 0 && !(b is NavMeshAgent));
                if (!link) continue;
                counts[b.GetType().FullName] = counts.TryGetValue(b.GetType().FullName, out var k) ? k + 1 : 1;
                if (n > 0) sb.Append(",\n");
                sb.Append('{');
                Prop(sb, "type", b.GetType().FullName); sb.Append(',');
                Prop(sb, "name", b.gameObject.name); sb.Append(',');
                Prop(sb, "active", b.isActiveAndEnabled); sb.Append(',');
                Vec(sb, "pos", b.transform.position); sb.Append(',');
                Vec(sb, "rot", b.transform.eulerAngles);
                if (b is OffMeshLink ol)
                {
                    if (ol.startTransform != null) { sb.Append(','); Vec(sb, "start", ol.startTransform.position); }
                    if (ol.endTransform != null) { sb.Append(','); Vec(sb, "end", ol.endTransform.position); }
                    sb.Append(','); Prop(sb, "bidirectional", ol.biDirectional);
                    sb.Append(','); Prop(sb, "linkActivated", ol.activated);
                }
                sb.Append(",\"members\":{");
                bool first = true;
                foreach (var (name, value) in LinkMembers(b))
                {
                    if (!first) sb.Append(',');
                    first = false;
                    switch (value)
                    {
                        case Vector3 v: Vec(sb, name, v); break;
                        case Transform t: Vec(sb, name + "@world", t.position); break;
                        case Component c: Prop(sb, name + "@object", c.gameObject.name + " (" + c.GetType().Name + ")"); break;
                        default: Prop(sb, name, Str(value)); break;
                    }
                }
                sb.Append("}}");
                n++;
            }
            sb.Append("\n]\n");
            File.WriteAllText(path, sb.ToString());
            Logger.LogInfo("links: " + string.Join(", ", counts.Select(kv => kv.Key + " x" + kv.Value)));
            return n;
        }

        // positions, transforms, other components (by name) and scalars, plus Vector3s inside arrays/lists
        static IEnumerable<(string, object)> LinkMembers(object o)
        {
            const BindingFlags F = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
            var seen = new HashSet<string>();
            for (var tt = o.GetType(); tt != null && tt != typeof(MonoBehaviour) && tt != typeof(Behaviour) && tt != typeof(object); tt = tt.BaseType)
            {
                foreach (var f in tt.GetFields(F))
                {
                    if (!seen.Add(f.Name)) continue;
                    object v; try { v = f.GetValue(o); } catch { continue; }
                    if (v == null) continue;
                    if (v is Vector3 || v is Transform || v is string || v.GetType().IsEnum || v.GetType().IsPrimitive) { yield return (f.Name, v); continue; }
                    if (v is Component) { yield return (f.Name, v); continue; }
                    if (v is IEnumerable list && !(v is string))
                    {
                        int i = 0;
                        foreach (var item in list)
                        {
                            if (i >= 16) break;
                            if (item is Vector3 || item is Transform) yield return (f.Name + "[" + i + "]", item);
                            else if (item is Component) yield return (f.Name + "[" + i + "]", item);
                            i++;
                        }
                    }
                }
                foreach (var p in tt.GetProperties(F))
                {
                    if (p.GetIndexParameters().Length > 0 || !p.CanRead || !seen.Add(p.Name)) continue;
                    var pt = p.PropertyType;
                    if (!(pt == typeof(Vector3) || pt == typeof(string) || pt.IsEnum || pt.IsPrimitive)) continue;
                    object v; try { v = p.GetValue(o); } catch { continue; }
                    if (v != null) yield return (p.Name, v);
                }
            }
        }

        // ---------- objects ----------
        delegate void Writer(StringBuilder sb, Component c);

        int DumpObjects(string path, string typeName, Writer write, string skipTypeName = null)
        {
            var type = FindType(typeName);
            var skip = skipTypeName != null ? FindType(skipTypeName) : null;
            var sb = new StringBuilder("[\n");
            int n = 0;
            if (type != null)
            {
                foreach (var o in Resources.FindObjectsOfTypeAll(type))
                {
                    var c = o as Component;
                    if (c == null || !c.gameObject.scene.IsValid()) continue; // skip prefabs that aren't in the level
                    if (skip != null && skip.IsInstanceOfType(c)) continue;
                    if (n > 0) sb.Append(",\n");
                    sb.Append('{');
                    write(sb, c);
                    sb.Append('}');
                    n++;
                }
            }
            else Logger.LogWarning($"type {typeName} not found");
            sb.Append("\n]\n");
            File.WriteAllText(path, sb.ToString());
            return n;
        }

        void Common(StringBuilder sb, Component c)
        {
            Prop(sb, "type", c.GetType().FullName); sb.Append(',');
            Prop(sb, "name", c.gameObject.name); sb.Append(',');
            Prop(sb, "active", c.gameObject.activeInHierarchy); sb.Append(',');
            Vec(sb, "pos", c.transform.position); sb.Append(',');
            Vec(sb, "rot", c.transform.eulerAngles);
            var col = c.GetComponentInChildren<Collider>(true);
            if (col != null) { sb.Append(','); Vec(sb, "boundsCenter", col.bounds.center); sb.Append(','); Vec(sb, "boundsSize", col.bounds.size); }
        }

        void Door(StringBuilder sb, Component c)
        {
            Common(sb, c); sb.Append(',');
            Prop(sb, "id", Str(Get(c, "Id", "_id", "ObjectId"))); sb.Append(',');
            Prop(sb, "keyId", Str(Get(c, "KeyId", "_keyId", "keyId"))); sb.Append(',');
            Prop(sb, "state", Str(Get(c, "DoorState", "_doorState", "doorState"))); sb.Append(',');
            Prop(sb, "initialState", Str(Get(c, "InitialDoorState", "_initialDoorState")));
            Extra(sb, c);
        }

        void Container(StringBuilder sb, Component c)
        {
            Common(sb, c); sb.Append(',');
            Prop(sb, "id", Str(Get(c, "Id", "_id")));
            var owner = Get(c, "ItemOwner");
            var root = owner != null ? Get(owner, "RootItem") : null;
            if (root != null) { sb.Append(','); Prop(sb, "tpl", Str(Get(root, "TemplateId", "_tpl"))); }
        }

        void Exit(StringBuilder sb, Component c)
        {
            Common(sb, c); sb.Append(',');
            var settings = Get(c, "Settings", "settings");
            Prop(sb, "exitName", Str(settings != null ? Get(settings, "Name", "name") : null)); sb.Append(',');
            Prop(sb, "exitType", Str(settings != null ? Get(settings, "ExfiltrationType", "exfiltrationType") : null)); sb.Append(',');
            Prop(sb, "status", Str(Get(c, "Status", "_status")));
            Extra(sb, c);
        }

        void Generic(StringBuilder sb, Component c) { Common(sb, c); Extra(sb, c); }

        // all string / enum / bool / number members, plus those of a settings-like child object
        void Extra(StringBuilder sb, Component c)
        {
            sb.Append(",\"fields\":{");
            bool first = true;
            void Add(string k, object v)
            {
                if (!first) sb.Append(',');
                first = false;
                Prop(sb, k, Str(v));
            }
            foreach (var (name, value) in Members(c))
            {
                if (value == null) continue;
                var t = value.GetType();
                if (t == typeof(string) || t.IsEnum || t.IsPrimitive) Add(name, value);
                else if (name.IndexOf("settings", StringComparison.OrdinalIgnoreCase) >= 0 || name.IndexOf("param", StringComparison.OrdinalIgnoreCase) >= 0)
                    foreach (var (n2, v2) in Members(value))
                        if (v2 != null && (v2 is string || v2.GetType().IsEnum || v2.GetType().IsPrimitive)) Add(name + "." + n2, v2);
            }
            sb.Append('}');
        }

        static IEnumerable<(string, object)> Members(object o)
        {
            const BindingFlags F = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            var t = o.GetType();
            var seen = new HashSet<string>();
            for (var tt = t; tt != null && tt != typeof(MonoBehaviour) && tt != typeof(object); tt = tt.BaseType)
            {
                foreach (var f in tt.GetFields(F | BindingFlags.DeclaredOnly))
                {
                    if (!seen.Add(f.Name)) continue;
                    object v; try { v = f.GetValue(o); } catch { continue; }
                    yield return (f.Name, v);
                }
                foreach (var p in tt.GetProperties(F | BindingFlags.DeclaredOnly))
                {
                    if (p.GetIndexParameters().Length > 0 || !p.CanRead || !seen.Add(p.Name)) continue;
                    var pt = p.PropertyType;
                    if (!(pt == typeof(string) || pt.IsEnum || pt.IsPrimitive)) continue; // don't run arbitrary getters
                    object v; try { v = p.GetValue(o); } catch { continue; }
                    yield return (p.Name, v);
                }
            }
        }

        static object Get(object o, params string[] names)
        {
            const BindingFlags F = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.FlattenHierarchy;
            for (var t = o.GetType(); t != null; t = t.BaseType)
                foreach (var n in names)
                {
                    var f = t.GetField(n, F); if (f != null) try { return f.GetValue(o); } catch { }
                    var p = t.GetProperty(n, F); if (p != null && p.GetIndexParameters().Length == 0) try { return p.GetValue(o); } catch { }
                }
            return null;
        }

        static string Str(object v) => v == null ? null : Convert.ToString(v, Inv);

        // ---------- helpers ----------
        static readonly Dictionary<string, Type> TypeCache = new Dictionary<string, Type>();
        static Type FindType(string name)
        {
            if (TypeCache.TryGetValue(name, out var t)) return t;
            t = AppDomain.CurrentDomain.GetAssemblies().Select(a => { try { return a.GetType(name); } catch { return null; } }).FirstOrDefault(x => x != null);
            TypeCache[name] = t;
            return t;
        }

        static object FindByTypeName(string name)
        {
            var t = FindType(name);
            return t == null ? null : UnityEngine.Object.FindObjectOfType(t);
        }

        static string MapId(object world)
        {
            string id = null;
            try
            {   // Comfort.Common.Singleton<GameWorld>.Instance.LocationId, as SPT mods read it
                var gw = FindType("EFT.GameWorld"); var single = FindType("Comfort.Common.Singleton`1");
                if (gw != null && single != null)
                {
                    var inst = single.MakeGenericType(gw).GetProperty("Instance", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                    if (inst != null) id = Str(Get(inst, "LocationId", "_locationId", "locationId"));
                }
            }
            catch { }
            if (string.IsNullOrEmpty(id)) id = Str(Get(world, "LocationId", "_locationId", "locationId"));
            if (!string.IsNullOrEmpty(id)) return id.ToLowerInvariant();
            // fall back to the level's first scene (e.g. Factory_Rework_Day_Scripts), not the UI scene
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var n = SceneManager.GetSceneAt(i).name;
                if (n.IndexOf("UI", StringComparison.Ordinal) >= 0 || n.StartsWith("PhysicsWorld", StringComparison.Ordinal)) continue;
                return ("scene_" + n).ToLowerInvariant();
            }
            return SceneManager.GetActiveScene().name.ToLowerInvariant();
        }

        static string Safe(string s) => new string(s.Select(ch => char.IsLetterOrDigit(ch) || ch == '_' || ch == '-' ? ch : '_').ToArray());

        static void Prop(StringBuilder sb, string k, string v)
        {
            sb.Append('"').Append(k).Append("\":");
            if (v == null) { sb.Append("null"); return; }
            sb.Append('"');
            foreach (var ch in v)
            {
                if (ch == '"' || ch == '\\') sb.Append('\\').Append(ch);
                else if (ch < 32) sb.Append("\\u").Append(((int)ch).ToString("x4"));
                else sb.Append(ch);
            }
            sb.Append('"');
        }
        static void Prop(StringBuilder sb, string k, bool v) => sb.Append('"').Append(k).Append("\":").Append(v ? "true" : "false");
        static void Vec(StringBuilder sb, string k, Vector3 v) =>
            sb.Append('"').Append(k).Append("\":[").Append(v.x.ToString("0.###", Inv)).Append(',').Append(v.y.ToString("0.###", Inv)).Append(',').Append(v.z.ToString("0.###", Inv)).Append(']');
    }
}
