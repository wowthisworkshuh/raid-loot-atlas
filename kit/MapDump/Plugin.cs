// SurvivalPack.MapDump — writes what the Raid Loot Atlas needs from inside a raid, once per map:
//   navmesh.bin      the walkable surface bots use (NavMesh.CalculateTriangulation): walls, stairs, dead ends
//   doors.json       every door / trunk / keycard door: id, key, state, position, size
//   containers.json  every lootable container: id (matches SPT's staticContainers), position
//   exits.json       extracts and transits: name, type, position
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
    [BepInPlugin("com.survivalpack.mapdump", "SurvivalPack.MapDump", "0.1.0")]
    public class Plugin : BaseUnityPlugin
    {
        ConfigEntry<KeyboardShortcut> _key;
        ConfigEntry<bool> _auto;
        ConfigEntry<float> _autoDelay;
        readonly HashSet<string> _done = new HashSet<string>();
        string _msg; float _msgUntil;
        float _worldSeenAt = -1; string _worldMap;
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        void Awake()
        {
            _key = Config.Bind("Dump", "Dump now", new KeyboardShortcut(KeyCode.F10, KeyCode.LeftControl), "Write this map's data now (in raid).");
            _auto = Config.Bind("Dump", "Dump automatically", true, "Write each map's data once per game session, a little after the raid loads.");
            _autoDelay = Config.Bind("Dump", "Seconds after load", 20f, "Wait this long after the raid world appears, so every door and container has loaded.");
            Logger.LogInfo("SurvivalPack.MapDump 0.1.0 ready (Ctrl+F10 in raid)");
        }

        void Update()
        {
            var world = FindByTypeName("EFT.GameWorld") ?? FindByTypeName("EFT.ClientGameWorld");
            if (world == null) { _worldSeenAt = -1; return; }
            var map = MapId(world);
            if (_worldSeenAt < 0 || map != _worldMap) { _worldSeenAt = Time.realtimeSinceStartup; _worldMap = map; }

            if (_key.Value.IsDown()) { Dump(map); return; }
            if (_auto.Value && !_done.Contains(map) && Time.realtimeSinceStartup - _worldSeenAt > _autoDelay.Value)
                Dump(map);
        }

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
                int doors = DumpObjects(Path.Combine(dir, "doors.json"), "EFT.Interactive.WorldInteractiveObject", Door);
                int conts = DumpObjects(Path.Combine(dir, "containers.json"), "EFT.Interactive.LootableContainer", Container);
                int exits = DumpObjects(Path.Combine(dir, "exits.json"), "EFT.Interactive.ExfiltrationPoint", Exit);
                int transits = DumpObjects(Path.Combine(dir, "transits.json"), "EFT.Interactive.TransitPoint", Generic);
                var meta = new StringBuilder("{");
                Prop(meta, "map", map); meta.Append(',');
                Prop(meta, "plugin", "0.1.0"); meta.Append(',');
                Prop(meta, "unity", Application.unityVersion); meta.Append(',');
                Prop(meta, "game", Application.version); meta.Append(',');
                Prop(meta, "scenes", string.Join(";", Enumerable.Range(0, SceneManager.sceneCount).Select(i => SceneManager.GetSceneAt(i).name))); meta.Append(',');
                Prop(meta, "time", DateTime.UtcNow.ToString("o")); meta.Append(',');
                meta.Append($"\"navmeshTriangles\":{tris},\"doors\":{doors},\"containers\":{conts},\"exits\":{exits},\"transits\":{transits}}}");
                File.WriteAllText(Path.Combine(dir, "meta.json"), meta.ToString());
                Say($"Map dump for {map} written: {tris:N0} walk triangles, {doors} doors, {conts} containers, {exits} exits. Folder: BepInEx/survivalpack-mapdump/{Safe(map)}");
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

        // ---------- objects ----------
        delegate void Writer(StringBuilder sb, Component c);

        int DumpObjects(string path, string typeName, Writer write)
        {
            var type = FindType(typeName);
            var sb = new StringBuilder("[\n");
            int n = 0;
            if (type != null)
            {
                foreach (var o in Resources.FindObjectsOfTypeAll(type))
                {
                    var c = o as Component;
                    if (c == null || !c.gameObject.scene.IsValid()) continue; // skip prefabs that aren't in the level
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
            var id = Str(Get(world, "LocationId", "_locationId", "locationId"));
            if (!string.IsNullOrEmpty(id)) return id.ToLowerInvariant();
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
