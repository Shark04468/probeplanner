#if UNITY_EDITOR
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BrainPlanner.BrainView;
using UnityEngine;

// Editor-only fixture: actual coroutine scheduling and deferred RemoveFill destruction.
public class RegionLodPlayModeDriver : MonoBehaviour
{
    private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

    [Serializable]
    private class CaseResult
    {
        public string name;
        public bool passed;
        public bool removedRootDestroyed;
        public bool removedMaterialDestroyed;
        public bool completed;
        public int orphanObjects;
        public int missingMaterialRenderers;
        public int retainedChildren;
        public string error;
    }

    [Serializable]
    private class Report
    {
        public string role = "actual_play_mode_synthetic_mesh_lifecycle_not_ui_acceptance";
        public string editor;
        public int passed;
        public int failed;
        public CaseResult[] cases;
    }

    private float _deadline;

    private void Awake() { _deadline = Time.realtimeSinceStartup + 120; }
    private void Update()
    {
        if (Time.realtimeSinceStartup > _deadline)
        {
            Debug.LogError("RegionLodPlayModeChecks watchdog expired");
            UnityEditor.EditorApplication.Exit(2);
        }
    }

    private IEnumerator Start()
    {
        var rows = new List<CaseResult>();
        foreach (string mode in new[] { "remove_deferred", "remove_reselect", "inactive_retained", "duplicate_resolved_lod", "unchanged" })
        {
            var result = new CaseResult { name = mode };
            rows.Add(result);
            yield return Check(mode, result);
        }
        var report = new Report {
            editor = Application.unityVersion, cases = rows.ToArray(),
            passed = rows.Count(r => r.passed), failed = rows.Count(r => !r.passed)
        };
        var path = Environment.GetEnvironmentVariable("PROBEPLANNER_LIFECYCLE_RESULT");
        if (string.IsNullOrEmpty(path) || File.Exists(path))
            throw new InvalidOperationException("Require a new result path");
        File.WriteAllText(path, JsonUtility.ToJson(report, true));
        Debug.Log("[RegionLodPlayModeChecks] passed=" + report.passed + " failed=" + report.failed);
        UnityEditor.EditorApplication.Exit(report.failed == 0 ? 0 : 1);
    }

    private IEnumerator Check(string mode, CaseResult row)
    {
        var owner = new GameObject("LodLifetimeOwner");
        var manager = owner.AddComponent<RegionManager>();
        manager.MaxOfflineLod = 2;
        var loader = owner.GetComponent<MeshLoader>();
        var cache = (Dictionary<string, MeshLoader.MeshLodResult>)typeof(MeshLoader)
            .GetField("_meshCache", PrivateInstance).GetValue(loader);
        var pending = (HashSet<string>)typeof(MeshLoader).GetField("_inProgress", PrivateInstance).GetValue(loader);
        var roots = (Dictionary<string, GameObject>)typeof(RegionManager).GetField("_fillObjects", PrivateInstance).GetValue(manager);
        var mats = (Dictionary<string, Material>)typeof(RegionManager).GetField("_fillMats", PrivateInstance).GetValue(manager);
        var meshes = new[] { Triangle(1), Triangle(2), Triangle(3) };
        var material = new Material(Shader.Find("BrainPlanner/RegionFill"));
        var originalRoot = MakeRoot(owner, meshes[0], material);
        roots["lifetime_fixture"] = originalRoot;
        mats["lifetime_fixture"] = material;
        pending.Add("382:1");
        bool completed = false;
        manager.StartCoroutine(Observe(Attach(manager, originalRoot, material), () => completed = true, e => row.error = e));
        yield return null;

        bool remove = mode.StartsWith("remove");
        if (remove)
            typeof(RegionManager).GetMethod("RemoveFill", PrivateInstance).Invoke(manager, new object[] { "lifetime_fixture" });
        if (mode == "inactive_retained") originalRoot.SetActive(false);
        yield return null;
        yield return null;
        row.removedRootDestroyed = originalRoot == null;
        row.removedMaterialDestroyed = material == null;

        var retained = originalRoot;
        Material replacementMaterial = null;
        bool replacementComplete = mode != "remove_reselect";
        if (mode == "remove_reselect")
        {
            replacementMaterial = new Material(Shader.Find("BrainPlanner/RegionFill"));
            retained = MakeRoot(owner, meshes[0], replacementMaterial);
            roots["lifetime_fixture"] = retained;
            mats["lifetime_fixture"] = replacementMaterial;
            manager.StartCoroutine(Observe(Attach(manager, retained, replacementMaterial),
                () => replacementComplete = true, e => row.error = e));
        }
        for (int lod = 1; lod <= 2; lod++)
            cache["382:" + lod] = new MeshLoader.MeshLodResult {
                mesh = mode == "duplicate_resolved_lod" ? meshes[0] : meshes[lod],
                requestedLod = lod, resolvedLod = mode == "duplicate_resolved_lod" ? 0 : lod
            };
        pending.Remove("382:1");
        for (int i = 0; i < 12; i++) yield return null;
        var orphans = gameObject.scene.GetRootGameObjects().Where(o => o.name == "LOD1" || o.name == "LOD2").ToArray();
        row.orphanObjects = orphans.Length;
        row.missingMaterialRenderers = orphans.SelectMany(o => o.GetComponentsInChildren<Renderer>()).Count(r => r.sharedMaterial == null);
        row.retainedChildren = retained != null ? retained.transform.childCount : 0;
        row.completed = completed && replacementComplete;
        int expected = mode == "remove_deferred" ? 0 : mode == "duplicate_resolved_lod" ? 1 : 3;
        row.passed = string.IsNullOrEmpty(row.error) && row.completed && row.orphanObjects == 0 &&
            row.retainedChildren == expected && (!remove || (row.removedRootDestroyed && row.removedMaterialDestroyed));
        foreach (var orphan in orphans) Destroy(orphan);
        Destroy(owner);
        if (material != null) Destroy(material);
        if (replacementMaterial != null) Destroy(replacementMaterial);
        foreach (var mesh in meshes) Destroy(mesh);
        yield return null;
        yield return null;
    }

    private static IEnumerator Observe(IEnumerator inner, Action completed, Action<string> failed)
    {
        while (true)
        {
            bool more;
            try { more = inner.MoveNext(); }
            catch (Exception error) { failed(error.ToString()); yield break; }
            if (!more) break;
            yield return inner.Current;
        }
        completed();
    }

    private static IEnumerator Attach(RegionManager manager, GameObject root, Material material)
    {
        return (IEnumerator)typeof(RegionManager).GetMethod("AttachOfflineLods", PrivateInstance).Invoke(manager,
            new object[] { root.transform, 382, "lifetime_fixture", material, "BrainFill",
                root.GetComponentsInChildren<Renderer>(true).ToList(), root.GetComponent<LODGroup>(), 0 });
    }

    private static GameObject MakeRoot(GameObject owner, Mesh mesh, Material mat)
    {
        var root = new GameObject("Fill_lifetime_fixture");
        root.transform.SetParent(owner.transform, false);
        root.AddComponent<LODGroup>();
        var child = new GameObject("LOD0");
        child.transform.SetParent(root.transform, false);
        child.AddComponent<MeshFilter>().sharedMesh = mesh;
        child.AddComponent<MeshRenderer>().sharedMaterial = mat;
        return root;
    }

    private static Mesh Triangle(float size)
    {
        var mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right * size, Vector3.up * size }, triangles = new[] { 0, 1, 2 } };
        mesh.RecalculateNormals();
        mesh.RecalculateBounds();
        return mesh;
    }
}
#endif
