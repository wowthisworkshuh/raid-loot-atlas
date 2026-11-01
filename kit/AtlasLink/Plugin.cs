// SurvivalPack.AtlasLink — lets the Raid Loot Atlas page see where you are in a raid, so a loot run can start
// from your real spawn and re-plan from where you stand.
//
// It answers on this PC only (127.0.0.1), read-only, and changes nothing in the game:
//   GET http://127.0.0.1:47320/raid
//   {"app":"atlaslink","v":1,"inRaid":true,"map":"rezervbase","side":"Usec","raid":"638...","alive":true,
//    "spawn":[x,y,z],"pos":[x,y,z],"yaw":123.4}
// Positions are the game's own world coordinates (the same ones the map dumps use).
// A web page can read it only if it's the atlas site (or a page on this PC); other sites get no CORS header,
// so their browsers won't hand them the answer. Requests must name 127.0.0.1 or localhost as the host, which
// stops DNS-rebinding tricks.
//
// Like MapDump it reads the game by type and member names with reflection, so it only references Unity and
// BepInEx and keeps working across SPT updates as long as those names stay the same.
using System;
using System.Globalization;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;

namespace SurvivalPack.AtlasLink
{
    [BepInPlugin("com.survivalpack.atlaslink", "SurvivalPack.AtlasLink", Version)]
    public class Plugin : BaseUnityPlugin
    {
        const string Version = "0.1.0";
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        ConfigEntry<int> _port;
        ConfigEntry<string> _sites;
        ConfigEntry<bool> _say;
        string[] _allowed = new string[0];
        TcpListener _listener;
        Thread _thread;
        volatile bool _stop;

        readonly object _lock = new object();
        string _json = "{\"app\":\"atlaslink\",\"v\":1,\"inRaid\":false}";
        long _lastRead; // UTC ticks of the atlas page's last read (written by the server thread)

        // raid state, main thread only
        object _world;
        string _raid, _map;
        Vector3 _spawn;
        bool _haveSpawn, _told;
        float _playerSeenAt = -1, _nextLook, _nextWrite;
        string _msg;
        float _msgUntil;

        void Awake()
        {
            _port = Config.Bind("Link", "Port", 47320, "Port the atlas page reads (this PC only). The atlas expects 47320; change it only if something else uses that port.");
            _sites = Config.Bind("Link", "Allowed sites", "https://wowthisworkshuh.github.io",
                "Web sites allowed to read your raid position, comma separated. Pages served from this PC (localhost) are always allowed.");
            _say = Config.Bind("Link", "Show raid message", true, "When a raid starts and the atlas is open and linked, say so for a few seconds.");
            _allowed = _sites.Value.Split(',').Select(s => s.Trim().TrimEnd('/')).Where(s => s.Length > 0).ToArray();
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port.Value);
                _listener.Start();
                _thread = new Thread(Serve) { IsBackground = true, Name = "AtlasLink" };
                _thread.Start();
                Logger.LogInfo($"SurvivalPack.AtlasLink {Version}: the atlas can read your raid at http://127.0.0.1:{_port.Value}/raid");
            }
            catch (Exception e)
            {
                Logger.LogError($"AtlasLink couldn't listen on port {_port.Value}: {e.Message}. Pick another port in BepInEx/config/com.survivalpack.atlaslink.cfg.");
            }
        }

        void OnDestroy()
        {
            _stop = true;
            try { _listener?.Stop(); } catch { }
        }

        // ---------- game side ----------
        void Update()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _nextWrite) return;
            _nextWrite = now + 0.25f;

            object world = World();
            Component player = world == null ? null : Get(world, "MainPlayer") as Component;
            if (world == null || player == null)
            {
                if (_raid != null) { _raid = null; _world = null; _haveSpawn = false; _playerSeenAt = -1; Set("{\"app\":\"atlaslink\",\"v\":1,\"inRaid\":false}"); }
                return;
            }
            if (!ReferenceEquals(world, _world))
            {   // a new raid (or a transit to the next map)
                _world = world; _raid = DateTime.UtcNow.Ticks.ToString(Inv); _haveSpawn = false; _told = false; _playerSeenAt = -1; _map = null;
            }
            if (string.IsNullOrEmpty(_map)) _map = MapId(world); // filled in a moment after the world appears

            Vector3 p = player.transform.position;
            if (p == Vector3.zero) return;
            if (_playerSeenAt < 0) _playerSeenAt = now;
            // the spawn: where you stand when the raid starts. Keep following for the first 3 s (placement), and
            // take a jump of more than 50 m in the first minute as being moved to the real spawn
            if (!_haveSpawn || now - _playerSeenAt < 3f || (now - _playerSeenAt < 60f && (p - _last).sqrMagnitude > 2500f)) { _spawn = p; _haveSpawn = true; }
            _last = p;

            float yaw = Yaw(player);
            object side = Get(player, "Side");
            bool? alive = null;
            try { var hc = Get(player, "HealthController"); if (hc != null && Get(hc, "IsAlive") is bool b) alive = b; } catch { }

            var sb = new StringBuilder(256);
            sb.Append("{\"app\":\"atlaslink\",\"v\":1,\"inRaid\":true,");
            Prop(sb, "map", _map); sb.Append(',');
            Prop(sb, "side", side == null ? null : Convert.ToString(side, Inv)); sb.Append(',');
            Prop(sb, "raid", _raid); sb.Append(',');
            if (alive.HasValue) sb.Append("\"alive\":").Append(alive.Value ? "true" : "false").Append(',');
            Vec(sb, "spawn", _spawn); sb.Append(',');
            Vec(sb, "pos", p); sb.Append(',');
            sb.Append("\"yaw\":").Append(yaw.ToString("0.#", Inv)).Append('}');
            Set(sb.ToString());

            // say it once per raid, and only if the atlas is actually reading (open and linked)
            if (!_told && _say.Value && now - _playerSeenAt > 2f && DateTime.UtcNow.Ticks - Interlocked.Read(ref _lastRead) < TimeSpan.TicksPerSecond * 10)
            {
                _told = true;
                Say("Raid Loot Atlas: your spawn is in the loot run planner");
            }
        }
        Vector3 _last;

        static float Yaw(Component player)
        {
            try
            {   // EFT's Player.Rotation is (yaw, pitch) in degrees
                if (Get(player, "Rotation") is Vector2 r) return Mathf.Repeat(r.x, 360f);
            }
            catch { }
            var cam = Camera.main;
            return Mathf.Repeat((cam != null ? cam.transform : player.transform).eulerAngles.y, 360f);
        }

        PropertyInfo _inst, _instd;
        object World()
        {
            try
            {
                if (_inst == null)
                {
                    if (Time.realtimeSinceStartup < _nextLook) return null;
                    _nextLook = Time.realtimeSinceStartup + 2f;
                    var gw = FindType("EFT.GameWorld"); var single = FindType("Comfort.Common.Singleton`1");
                    if (gw == null || single == null) return null;
                    var st = single.MakeGenericType(gw);
                    _inst = st.GetProperty("Instance", BindingFlags.Public | BindingFlags.Static);
                    _instd = st.GetProperty("Instantiated", BindingFlags.Public | BindingFlags.Static);
                    if (_inst == null) return null;
                }
                if (_instd != null && _instd.GetValue(null) is bool ok && !ok) return null;
                var w = _inst.GetValue(null);
                return (w as UnityEngine.Object) == null ? null : w;
            }
            catch { return null; }
        }

        static string MapId(object world)
        {
            var id = Convert.ToString(Get(world, "LocationId", "_locationId", "locationId"), Inv);
            return string.IsNullOrEmpty(id) ? null : id.ToLowerInvariant();
        }

        void OnGUI()
        {
            if (_msg == null || Time.realtimeSinceStartup > _msgUntil) return;
            var style = new GUIStyle(GUI.skin.box) { fontSize = 15, alignment = TextAnchor.MiddleLeft, wordWrap = true };
            GUI.Box(new Rect(20, 20, 470, 40), _msg, style);
        }
        void Say(string m) { _msg = m; _msgUntil = Time.realtimeSinceStartup + 6; Logger.LogInfo(m); }

        void Set(string json) { lock (_lock) _json = json; }

        // ---------- the tiny web server (its own thread) ----------
        void Serve()
        {
            while (!_stop)
            {
                TcpClient c;
                try { c = _listener.AcceptTcpClient(); }
                catch { if (_stop) return; Thread.Sleep(200); continue; }
                try
                {
                    using (c)
                    {
                        c.ReceiveTimeout = 2000; c.SendTimeout = 2000;
                        Handle(c.GetStream());
                    }
                }
                catch { }
            }
        }

        void Handle(NetworkStream s)
        {
            var buf = new byte[8192]; int n = 0;
            while (n < buf.Length)
            {
                int r = s.Read(buf, n, buf.Length - n);
                if (r <= 0) break;
                n += r;
                if (Encoding.ASCII.GetString(buf, 0, n).Contains("\r\n\r\n")) break;
            }
            var lines = Encoding.ASCII.GetString(buf, 0, n).Split(new[] { "\r\n" }, StringSplitOptions.None);
            var first = lines[0].Split(' ');
            if (first.Length < 2) return;
            string method = first[0], path = first[1].Split('?')[0], origin = null, host = null;
            foreach (var l in lines.Skip(1))
            {
                int i = l.IndexOf(':'); if (i <= 0) continue;
                var k = l.Substring(0, i).Trim(); var v = l.Substring(i + 1).Trim();
                if (k.Equals("Origin", StringComparison.OrdinalIgnoreCase)) origin = v;
                else if (k.Equals("Host", StringComparison.OrdinalIgnoreCase)) host = v.ToLowerInvariant();
            }
            int port = ((IPEndPoint)_listener.LocalEndpoint).Port;
            if (host != "127.0.0.1:" + port && host != "localhost:" + port && host != "[::1]:" + port)
            {
                Write(s, "403 Forbidden", "", "wrong host", "text/plain");
                return;
            }
            bool ok = origin != null && Allowed(origin);
            var cors = ok ? "Access-Control-Allow-Origin: " + origin + "\r\nVary: Origin\r\nAccess-Control-Allow-Private-Network: true\r\n" : "";
            if (method == "OPTIONS")
            {
                Write(s, "204 No Content", cors + "Access-Control-Allow-Methods: GET\r\nAccess-Control-Allow-Headers: *\r\nAccess-Control-Max-Age: 600\r\n", "", null);
                return;
            }
            if (method != "GET") { Write(s, "405 Method Not Allowed", cors, "", null); return; }
            if (path == "/raid")
            {
                if (ok) Interlocked.Exchange(ref _lastRead, DateTime.UtcNow.Ticks);
                string body; lock (_lock) body = _json;
                Write(s, "200 OK", cors, body, "application/json");
                return;
            }
            if (path == "/")
            {
                Write(s, "200 OK", cors, "SurvivalPack.AtlasLink " + Version + " is running. Open the Raid Loot Atlas, Loot run, and turn on Link to my game.", "text/plain");
                return;
            }
            Write(s, "404 Not Found", cors, "not found", "text/plain");
        }

        bool Allowed(string origin)
        {
            var o = origin.TrimEnd('/');
            if (o.StartsWith("http://localhost", StringComparison.OrdinalIgnoreCase) || o.StartsWith("http://127.0.0.1", StringComparison.Ordinal)) return true;
            return _allowed.Any(a => a.Equals(o, StringComparison.OrdinalIgnoreCase));
        }

        static void Write(NetworkStream s, string status, string extra, string body, string type)
        {
            var b = Encoding.UTF8.GetBytes(body ?? "");
            var head = "HTTP/1.1 " + status + "\r\n" + (type != null ? "Content-Type: " + type + "; charset=utf-8\r\n" : "")
                + "Content-Length: " + b.Length.ToString(Inv) + "\r\nCache-Control: no-store\r\nConnection: close\r\n" + extra + "\r\n";
            var h = Encoding.ASCII.GetBytes(head);
            s.Write(h, 0, h.Length);
            if (b.Length > 0) s.Write(b, 0, b.Length);
            s.Flush();
        }

        // ---------- helpers ----------
        static object Get(object o, params string[] names)
        {
            const BindingFlags F = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.FlattenHierarchy;
            for (var t = o.GetType(); t != null; t = t.BaseType)
                foreach (var n in names)
                {
                    PropertyInfo p = null; FieldInfo f = null;
                    try { p = t.GetProperty(n, F | BindingFlags.DeclaredOnly); } catch { } // ambiguous names: skip
                    if (p != null && p.GetIndexParameters().Length == 0) try { return p.GetValue(o); } catch { }
                    try { f = t.GetField(n, F | BindingFlags.DeclaredOnly); } catch { }
                    if (f != null) try { return f.GetValue(o); } catch { }
                }
            return null;
        }

        static readonly System.Collections.Generic.Dictionary<string, Type> TypeCache = new System.Collections.Generic.Dictionary<string, Type>();
        static Type FindType(string name)
        {
            if (TypeCache.TryGetValue(name, out var t) && t != null) return t;
            t = AppDomain.CurrentDomain.GetAssemblies().Select(a => { try { return a.GetType(name); } catch { return null; } }).FirstOrDefault(x => x != null);
            TypeCache[name] = t;
            return t;
        }

        static void Prop(StringBuilder sb, string k, string v)
        {
            sb.Append('"').Append(k).Append("\":");
            if (v == null) { sb.Append("null"); return; }
            sb.Append('"');
            foreach (var ch in v)
            {
                if (ch == '"' || ch == '\\') sb.Append('\\').Append(ch);
                else if (ch < 32) sb.Append("\\u").Append(((int)ch).ToString("x4", Inv));
                else sb.Append(ch);
            }
            sb.Append('"');
        }
        static void Vec(StringBuilder sb, string k, Vector3 v) =>
            sb.Append('"').Append(k).Append("\":[").Append(v.x.ToString("0.##", Inv)).Append(',').Append(v.y.ToString("0.##", Inv)).Append(',').Append(v.z.ToString("0.##", Inv)).Append(']');
    }
}
