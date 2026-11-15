using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using BrainPlanner.BrainView;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BrainPlanner.Editor
{
    public static class RegionLodLifetimeChecks
    {
        private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

        [Serializable]
        private class CaseResult
        {
            public string name;
            public bool passed;
            public bool resumed;
            public int orphanObjects;
            public int orphanRenderers;
            public int survivingChildren;
            public string error;
        }

        [Serializable]
        private class Report
        {
            public string role = "synthetic_actual_editor_lifetime_regression_not_ui_acceptance";
            public string editor;
            public int passed;
            public int failed;
            public CaseResult[] cases;
        }

        public static void Run()
        {
            var rows = new List<CaseResult>();
            foreach (var loss in new[] { "none", "root", "material", "group", "root_and_material" })
            foreach (var boundary in new[] { 0, 1, 2 })
                rows.Add(Check(loss, boundary));
            var report = new Report {
                editor = Application.unityVersion,
                cases = rows.ToArray(),
                passed = rows.Count(r => r.passed),
                failed = rows.Count(r => !r.passed)
            };
            var path = Environment.GetEnvironmentVariable("PROBEPLANNER_LIFECYCLE_RESULT");
            if (string.IsNullOrEmpty(path) || File.Exists(path))
                throw new InvalidOperationException("Require a new PROBEPLANNER_LIFECYCLE_RESULT path");
            File.WriteAllText(path, JsonUtility.ToJson(report, true));
            Debug.Log("[RegionLodLifetimeChecks] passed=" + report.passed + " failed=" + report.failed);
            EditorApplication.Exit(report.failed == 0 ? 0 : 1);
        }

        private static CaseResult Check(string loss, int boundary)
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var owner = new GameObject("LifetimeFixtureOwner");
            var root = new GameObject("Fill_lifetime_fixture");
            root.transform.SetParent(owner.transform, false);
            var manager = owner.AddComponent<RegionManager>();
            var loader = owner.GetComponent<MeshLoader>() ?? owner.AddComponent<MeshLoader>();
            typeof(RegionManager).GetField("_loader", PrivateInstance).SetValue(manager, loader);
            manager.MaxOfflineLod = 2;
            var group = root.AddComponent<LODGroup>();
            var material = new Material(Shader.Find("BrainPlanner/RegionFill"));
            var meshes = new[] { Triangle(1), Triangle(2), Triangle(3) };
            var initial = new GameObject("LOD0");
            initial.transform.SetParent(root.transform, false);
            initial.AddComponent<MeshFilter>().sharedMesh = meshes[0];
            var renderer = initial.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            var renderers = new List<Renderer> { renderer };
            var cache = (Dictionary<string, MeshLoader.MeshLodResult>)typeof(MeshLoader)
                .GetField("_meshCache", PrivateInstance).GetValue(loader);
            var keyMethod = typeof(MeshLoader).GetMethod("CacheKey", BindingFlags.NonPublic | BindingFlags.Static);
            for (int lod = 1; lod <= 2; lod++)
                cache[(string)keyMethod.Invoke(null, new object[] { 382, lod })] = new MeshLoader.MeshLodResult {
                    mesh = meshes[lod], requestedLod = lod, resolvedLod = lod, cacheSource = "fixture"
                };
            var iterator = (IEnumerator)typeof(RegionManager).GetMethod("AttachOfflineLods", PrivateInstance)
                .Invoke(manager, new object[] { root.transform, 382, "lifetime_fixture", material,
                    "BrainFill", renderers, group, 0 });
            var result = new CaseResult { name = loss + "_boundary_" + boundary };
            try
            {
                for (int i = 0; i < boundary; i++)
                {
                    if (!iterator.MoveNext()) throw new Exception("Fixture did not reach async boundary");
                    if (!(iterator.Current is WaitUntil wait) || wait.keepWaiting)
                        throw new Exception("Fixture cache did not complete the mesh request");
                }
                if (loss == "root" || loss == "root_and_material") UnityEngine.Object.DestroyImmediate(root);
                if (loss == "material" || loss == "root_and_material") UnityEngine.Object.DestroyImmediate(material);
                if (loss == "group") UnityEngine.Object.DestroyImmediate(group);
                result.resumed = iterator.MoveNext();
                if (loss == "none")
                {
                    int remaining = 3;
                    while (result.resumed && remaining-- > 0) result.resumed = iterator.MoveNext();
                }
                var roots = owner.scene.GetRootGameObjects().Where(o => o != owner).ToArray();
                result.orphanObjects = roots.Length;
                result.orphanRenderers = roots.Sum(o => o.GetComponentsInChildren<Renderer>(true).Length);
                result.survivingChildren = root != null ? root.transform.childCount : 0;
                int expectedChildren = loss == "none" ? 3 : loss.StartsWith("root") ? 0 : Math.Max(1, boundary);
                result.passed = !result.resumed && result.orphanObjects == 0 &&
                    result.survivingChildren == expectedChildren;
            }
            catch (Exception ex) { result.error = ex.ToString(); }
            finally
            {
                (iterator as IDisposable)?.Dispose();
                foreach (var go in owner.scene.GetRootGameObjects()) UnityEngine.Object.DestroyImmediate(go);
                if (material != null) UnityEngine.Object.DestroyImmediate(material);
                foreach (var mesh in meshes) UnityEngine.Object.DestroyImmediate(mesh);
            }
            return result;
        }

        private static Mesh Triangle(float size)
        {
            var mesh = new Mesh();
            mesh.vertices = new[] { Vector3.zero, Vector3.right * size, Vector3.up * size };
            mesh.triangles = new[] { 0, 1, 2 };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }
    }
}
