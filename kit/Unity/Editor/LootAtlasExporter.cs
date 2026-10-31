// Raid Loot Atlas map exporter (Unity editor script)
//
// Put this file in Assets/Editor/ of the Unity project you made from a map with AssetRipper
// (see kit/README.md). Open all of that map's scenes, then use
// Tools > Raid Loot Atlas > Export open map.
//
// It writes two files to <project>/LootAtlasExport/:
//   <mapId>.raw.glb          a light, untextured model: LOD0 meshes only, small props skipped,
//                            terrain turned into a mesh, one flat color per material
//   <mapId>-containers.json  world positions of every loot container the server knows about,
//                            matched by name against kit/container-ids/<mapId>.txt
//
// Needs the UnityGLTF package (Window > Package Manager > + > Add package from git URL:
// https://github.com/KhronosGroup/UnityGLTF.git). Tested API shape: UnityGLTF 2.x.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityGLTF;

public class LootAtlasExporter : EditorWindow
{
    static readonly string[] MapIds = {
        "bigmap", "factory4_day", "woods", "shoreline", "interchange", "lighthouse",
        "rezervbase", "laboratory", "labyrinth", "sandbox",
    };
    int mapIndex;
    float minSize = 0.6f;       // skip props smaller than this (metres, largest side)
    int terrainRes = 257;       // terrain mesh grid per side
    bool keepTextures = false;  // untextured keeps files small; textures can push a map past 1 GB

    [MenuItem("Tools/Raid Loot Atlas/Export open map")]
    static void Open() => GetWindow<LootAtlasExporter>("Raid Loot Atlas");

    void OnGUI()
    {
        EditorGUILayout.HelpBox("Open every scene of ONE map first (drag them all into the Hierarchy).", MessageType.Info);
        mapIndex = EditorGUILayout.Popup("Map", mapIndex, MapIds);
        minSize = EditorGUILayout.Slider("Skip props smaller than (m)", minSize, 0f, 3f);
        terrainRes = EditorGUILayout.IntSlider("Terrain detail", terrainRes, 65, 513);
        keepTextures = EditorGUILayout.Toggle("Keep textures (big files)", keepTextures);
        if (GUILayout.Button("Export")) Export(MapIds[mapIndex]);
    }

    void Export(string mapId)
    {
        string outDir = Path.Combine(Directory.GetParent(Application.dataPath).FullName, "LootAtlasExport");
        Directory.CreateDirectory(outDir);

        // 1. Containers: match scene object names to the server's container ids
        string idFile = EditorUtility.OpenFilePanel($"Pick kit/container-ids/{mapId}.txt", "", "txt");
        var ids = string.IsNullOrEmpty(idFile) ? new HashSet<string>()
            : new HashSet<string>(File.ReadAllLines(idFile).Select(l => l.Trim()).Where(l => l.Length > 0));
        var found = new StringBuilder("[\n");
        int matched = 0;
        foreach (var t in AllTransforms())
        {
            if (!ids.Contains(t.name)) continue;
            var p = t.position;
            found.Append(matched == 0 ? "" : ",\n")
                 .Append($"  {{\"id\":\"{t.name}\",\"x\":{F(p.x)},\"y\":{F(p.y)},\"z\":{F(p.z)}}}");
            matched++;
        }
        found.Append("\n]\n");
        File.WriteAllText(Path.Combine(outDir, $"{mapId}-containers.json"), found.ToString());
        Debug.Log($"[Loot Atlas] {matched} of {ids.Count} containers found by name.");

        // 2. Build a temporary export root out of copies, so the opened scenes stay untouched
        var root = new GameObject("LootAtlasExport");
        try
        {
            var mats = new Dictionary<int, Material>();
            int meshes = 0, skipped = 0;
            var lod0 = new HashSet<Renderer>();
            var notLod0 = new HashSet<Renderer>();
            foreach (var g in Object.FindObjectsOfType<LODGroup>())
            {
                var lods = g.GetLODs();
                for (int i = 0; i < lods.Length; i++)
                    foreach (var r in lods[i].renderers) if (r) (i == 0 ? lod0 : notLod0).Add(r);
            }
            foreach (var mr in Object.FindObjectsOfType<MeshRenderer>())
            {
                if (!mr.enabled || !mr.gameObject.activeInHierarchy || notLod0.Contains(mr)) { skipped++; continue; }
                var mf = mr.GetComponent<MeshFilter>();
                if (!mf || !mf.sharedMesh) { skipped++; continue; }
                var n = mr.name.ToLowerInvariant();
                if (n.Contains("shadow") || n.Contains("collider") || n.Contains("occlu") || n.Contains("decal") || n.Contains("_lod1") || n.Contains("_lod2") || n.Contains("_lod3")) { skipped++; continue; }
                var size = mr.bounds.size;
                if (Mathf.Max(size.x, size.y, size.z) < minSize) { skipped++; continue; }

                var copy = new GameObject(mr.name);
                copy.transform.SetParent(root.transform, false);
                copy.transform.SetPositionAndRotation(mr.transform.position, mr.transform.rotation);
                copy.transform.localScale = mr.transform.lossyScale;
                copy.AddComponent<MeshFilter>().sharedMesh = mf.sharedMesh;
                var cr = copy.AddComponent<MeshRenderer>();
                cr.sharedMaterials = mr.sharedMaterials.Select(m => keepTextures ? m : Flat(m, mats)).ToArray();
                meshes++;
            }
            foreach (var terrain in Object.FindObjectsOfType<Terrain>())
            {
                if (!terrain.gameObject.activeInHierarchy || !terrain.terrainData) continue;
                var go = new GameObject("Terrain_" + terrain.name);
                go.transform.SetParent(root.transform, false);
                go.transform.position = terrain.transform.position;
                go.AddComponent<MeshFilter>().sharedMesh = TerrainMesh(terrain.terrainData, terrainRes);
                go.AddComponent<MeshRenderer>().sharedMaterial = FlatColor(new Color(0.42f, 0.44f, 0.38f), mats);
                meshes++;
            }
            // Axis markers: the viewer reads these to line spawn points up with the model whatever
            // axis flip the glTF exporter applies.
            Marker(root, "AtlasAxisX", new Vector3(100, 0, 0));
            Marker(root, "AtlasAxisZ", new Vector3(0, 0, 100));

            Debug.Log($"[Loot Atlas] exporting {meshes} meshes ({skipped} skipped)…");
            var ctx = new ExportContext(GLTFSettings.GetOrCreateSettings());
            var exporter = new GLTFSceneExporter(new[] { root.transform }, ctx);
            exporter.SaveGLB(outDir, $"{mapId}.raw");
            Debug.Log($"[Loot Atlas] wrote {outDir}/{mapId}.raw.glb and {mapId}-containers.json");
            EditorUtility.RevealInFinder(Path.Combine(outDir, $"{mapId}.raw.glb"));
        }
        finally { DestroyImmediate(root); }
    }

    static IEnumerable<Transform> AllTransforms()
    {
        for (int s = 0; s < SceneManager.sceneCount; s++)
        {
            var scene = SceneManager.GetSceneAt(s);
            if (!scene.isLoaded) continue;
            foreach (var go in scene.GetRootGameObjects())
                foreach (var t in go.GetComponentsInChildren<Transform>(true)) yield return t;
        }
    }

    static void Marker(GameObject root, string name, Vector3 at)
    {
        var m = new GameObject(name);
        m.transform.SetParent(root.transform, false);
        m.transform.position = at;
    }

    static Material Flat(Material src, Dictionary<int, Material> cache)
    {
        var c = new Color(0.6f, 0.62f, 0.64f);
        if (src && src.HasProperty("_Color")) c = src.color;
        if (src && src.HasProperty("_BaseColor")) c = src.GetColor("_BaseColor");
        if (src && src.mainTexture is Texture2D tex) c *= AverageColor(tex);
        return FlatColor(c, cache);
    }

    static readonly Dictionary<Texture2D, Color> texAvg = new Dictionary<Texture2D, Color>();
    static Color AverageColor(Texture2D tex)
    {
        if (texAvg.TryGetValue(tex, out var c)) return c;
        c = Color.white;
        try
        {   // render to a tiny target so unreadable / compressed textures work too
            var rt = RenderTexture.GetTemporary(4, 4, 0, RenderTextureFormat.ARGB32);
            Graphics.Blit(tex, rt);
            var prev = RenderTexture.active; RenderTexture.active = rt;
            var small = new Texture2D(4, 4, TextureFormat.RGBA32, false);
            small.ReadPixels(new Rect(0, 0, 4, 4), 0, 0); small.Apply();
            RenderTexture.active = prev; RenderTexture.ReleaseTemporary(rt);
            var px = small.GetPixels(); c = new Color(px.Average(p => p.r), px.Average(p => p.g), px.Average(p => p.b));
            DestroyImmediate(small);
        }
        catch { }
        return texAvg[tex] = c;
    }

    static Material FlatColor(Color c, Dictionary<int, Material> cache)
    {
        // quantize so the whole map ends up with a few dozen materials
        int key = Mathf.RoundToInt(c.r * 15) * 256 + Mathf.RoundToInt(c.g * 15) * 16 + Mathf.RoundToInt(c.b * 15);
        if (cache.TryGetValue(key, out var m)) return m;
        m = new Material(Shader.Find("Standard")) { color = new Color(Mathf.Round(c.r * 15) / 15, Mathf.Round(c.g * 15) / 15, Mathf.Round(c.b * 15) / 15) };
        m.SetFloat("_Glossiness", 0.1f);
        m.name = "flat_" + key;
        return cache[key] = m;
    }

    static Mesh TerrainMesh(TerrainData d, int res)
    {
        var size = d.size;
        var verts = new Vector3[res * res];
        for (int z = 0; z < res; z++)
            for (int x = 0; x < res; x++)
            {
                float u = x / (float)(res - 1), v = z / (float)(res - 1);
                verts[z * res + x] = new Vector3(u * size.x, d.GetInterpolatedHeight(u, v), v * size.z);
            }
        var tris = new int[(res - 1) * (res - 1) * 6];
        int i = 0;
        for (int z = 0; z < res - 1; z++)
            for (int x = 0; x < res - 1; x++)
            {
                int a = z * res + x;
                tris[i++] = a; tris[i++] = a + res; tris[i++] = a + 1;
                tris[i++] = a + 1; tris[i++] = a + res; tris[i++] = a + res + 1;
            }
        var mesh = new Mesh { indexFormat = UnityEngine.Rendering.IndexFormat.UInt32, vertices = verts, triangles = tris };
        mesh.RecalculateNormals();
        return mesh;
    }

    static string F(float v) => v.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
}
#endif
