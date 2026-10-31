// SurvivalPack.MapDump — writes what the Raid Loot Atlas needs from inside a raid, once per map:
//   navmesh.bin      the walkable surface bots use (NavMesh.CalculateTriangulation): walls, stairs, dead ends
//   doors.json       every door / trunk / keycard door: id, key, state, position, size
//   containers.json  every lootable container: id (matches SPT's staticContainers), position
//   exits.json       extracts and transits: name, type, position
//   colliders.bin    building layouts: every solid box collider (centre, size, rotation) and the triangles
//                    of every mesh collider the game keeps readable; layers.json names their layers
//   terrain.bin      the real ground: each terrain's height grid (downsampled to at most 513 x 513)
//   meta.json        map id, game and plugin versions, counts
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
    [BepInPlugin("com.survivalpack.mapdump", "SurvivalPack.MapDump", "0.2.1")]
    public class Plugin : BaseUnityPlugin
    {
        ConfigEntry<KeyboardShortcut> _key;
        ConfigEntry<bool> _auto;
        ConfigEntry<float> _autoDelay;
        readonly HashSet<string> _done = new HashSet<string>();
        string _msg; float _msgUntil;
        float _worldSeenAt = -1;
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        void Awake()
        {
            _key = Config.Bind("Dump", "Dump now", new KeyboardShortcut(KeyCode.F10, KeyCode.LeftControl), "Write this map's data now (in raid).");
            _auto = Config.Bind("Dump", "Dump automatically", true, "Write each map's data once per game session, a little after the raid loads.");
            _autoDelay = Config.Bind("Dump", "Seconds after load", 20f, "Wait this long after the raid world appears, so every door and container has loaded.");
            Logger.LogInfo("SurvivalPack.MapDump 0.2.1 ready (Ctrl+F10 in raid)");
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
                if (_world != null) { _worldSeenAt = Time.realtimeSinceStartup; _retryNav = 0; }
                else _worldSeenAt = -1;
            }
            if (_world == null) return; // also true once the raid's world is destroyed

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
                var meta = new StringBuilder("{");
                Prop(meta, "map", map); meta.Append(',');
                Prop(meta, "plugin", "0.2.1"); meta.Append(',');
                Prop(meta, "unity", Application.unityVersion); meta.Append(',');
                Prop(meta, "game", Application.version); meta.Append(',');
                Prop(meta, "scenes", string.Join(";", Enumerable.Range(0, SceneManager.sceneCount).Select(i => SceneManager.GetSceneAt(i).name))); meta.Append(',');
                Prop(meta, "time", DateTime.UtcNow.ToString("o")); meta.Append(',');
                meta.Append($"\"navmeshTriangles\":{tris},\"doors\":{doors},\"containers\":{conts},\"exits\":{exits},\"transits\":{transits},");
                meta.Append($"\"boxColliders\":{boxes},\"meshColliders\":{meshes},\"meshColliderTriangles\":{meshTris},\"unreadableMeshColliders\":{unreadable},\"terrains\":{terrains}}}");
                File.WriteAllText(Path.Combine(dir, "meta.json"), meta.ToString());
                if (tris == 0)
                {
                    _navMissing.Add(map);
                    Say($"Map dump for {map} written WITHOUT the walk map: this game isn't running the AI. Host the raid yourself (not on the headless) or play offline, then dump again. {doors} doors, {conts} containers, {exits} exits saved.");
                }
                else
                {
                    _navMissing.Remove(map);
                    Say($"Map dump for {map} written: {tris:N0} walk triangles, {doors} doors, {conts} containers, {exits} exits, {boxes + meshes:N0} wall/floor shapes. Folder: BepInEx/survivalpack-mapdump/{Safe(map)}");
                }
            }
            catch (Exception e)
            {
                _done.Remove(map);
                Say($"Map dump for {map} failed: {e.Message} (details in the BepInEx log)");
                Logger.LogError(e);
            }
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
