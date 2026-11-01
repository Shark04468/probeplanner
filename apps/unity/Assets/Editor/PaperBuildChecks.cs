using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using BrainPlanner.BrainView;
using BrainPlanner.Bridge;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BrainPlanner.Editor
{
    public static class PaperBuildChecks
    {
        private static int _checks;

        public static void Run()
        {
            _checks = 0;
            Require(PlayerSettings.runInBackground, "embedded viewer keeps running while parent-page controls have focus");
            Require(BackendAddress.Resolve("http://127.0.0.1:5039/unity/index.html", "http://localhost:5000")
                == "http://127.0.0.1:5039", "same-origin backend");
            Require(BackendAddress.Resolve("http://host/unity/?backend=http%3A%2F%2F127.0.0.1%3A5039%2F", "http://localhost:5000")
                == "http://127.0.0.1:5039", "encoded configured backend");
            Require(BackendAddress.Resolve("https://host/view?backend=https%3A%2F%2Fserver%2Fapi-root", "http://localhost:5000")
                == "https://server/api-root", "HTTPS and path prefix");
            Require(BackendAddress.Resolve("", "http://localhost:5000/") == "http://localhost:5000", "editor fallback");
            foreach (var invalid in new[] { "file:///tmp", "http://user:secret@host", "http://host/?token=x", "http://host/#fragment", "" })
            {
                bool rejected = false;
                try { BackendAddress.Resolve("http://host/?backend=" + Uri.EscapeDataString(invalid), "http://localhost:5000"); }
                catch (ArgumentException) { rejected = true; }
                Require(rejected, "reject invalid backend");
            }
            var temporary = new GameObject("BridgeContractTest");
            try
            {
                var bridge = temporary.AddComponent<ReactBridge>();
                Require(bridge.CommandHandlers.Count == 32, "complete typed command map");
                Require(bridge.CommandHandlers.All(p => ReferenceEquals(p.Value.Target, bridge)), "single component dispatch");
                Require(!bridge.CommandHandlers.ContainsKey("DispatchCommand"), "no recursive dispatch");
            }
            finally { UnityEngine.Object.DestroyImmediate(temporary); }
            var entry = new Vector3(8100f, 700f, 3600f);
            for (int phi = 0; phi < 360; phi += 45)
            {
                float t = 20f * Mathf.Deg2Rad;
                float p = phi * Mathf.Deg2Rad;
                var ccfTip = entry + 4000f * new Vector3(Mathf.Sin(t) * Mathf.Cos(p), Mathf.Cos(t), Mathf.Sin(t) * Mathf.Sin(p));
                var viewerTip = CcfCoordinates.FromMicrometres(entry) + 4f * CcfCoordinates.ProbeDirection(20f, phi);
                Require(Vector3.Distance(viewerTip, CcfCoordinates.FromMicrometres(ccfTip)) < 0.00001f, "CCF/mesh/probe endpoint parity");
            }
            var single = ProbeSpec.Resolve(new ProbeData { probeType = "unknown", shankCount = 1, radiusUm = 35f });
            Require(single.Shanks == 1 && Mathf.Abs(single.DiameterMm - 0.07f) < 1e-6f, "recorded single shank hardware overrides unknown name");
            var custom = ProbeSpec.Resolve(new ProbeData { probeType = "NP2.0-4sh", shankCount = 2, shankPitchUm = 300f });
            Require(custom.Shanks == 2 && Mathf.Abs(custom.ShankSpacingMm - 0.3f) < 1e-6f, "recorded hardware overrides preset");
            bool missingRejected = false;
            try { ProbeSpec.Resolve(new ProbeData { probeType = "unknown" }); }
            catch (ArgumentException) { missingRejected = true; }
            Require(missingRejected, "missing hardware does not invent four shanks");
            var direction = CcfCoordinates.ProbeDirection(20f, 45f);
            Require(Mathf.Abs(Vector3.Dot(direction, CcfCoordinates.ProbeLateralAxis(direction))) < 1e-6f,
                "shank pitch is perpendicular to insertion direction");
            Require(Mathf.Abs(JsonUtility.FromJson<ProbeData>("{\"spin_deg\":37.25}").spin_deg - 37.25f) < 1e-6f,
                "Unity bridge retains recorded spin");
            foreach (var ccfDirection in new[] { Vector3.up, Vector3.forward, new Vector3(0.3f, 0.8f, 0.2f).normalized })
            foreach (float spin in new[] { 0f, 90f, -90f, 37.25f })
            {
                var ccfLateral = Vector3.forward - ccfDirection * Vector3.Dot(Vector3.forward, ccfDirection);
                if (ccfLateral.sqrMagnitude < 1e-12f) ccfLateral = Vector3.Cross(ccfDirection, Vector3.up);
                ccfLateral.Normalize();
                float angle = spin * Mathf.Deg2Rad;
                var rotated = ccfLateral * Mathf.Cos(angle) + Vector3.Cross(ccfDirection, ccfLateral) * Mathf.Sin(angle);
                var expected = new Vector3(rotated.z, -rotated.y, -rotated.x);
                var unityDirection = new Vector3(ccfDirection.z, -ccfDirection.y, -ccfDirection.x);
                Require(Vector3.Distance(CcfCoordinates.ProbeLateralAxis(unityDirection, spin), expected) < 1e-5f,
                    "CCF/Unity spun shank parity, including ML-axis fallback");
            }
            var probeObject = new GameObject("SpunProbeContractTest");
            try
            {
                var renderer = probeObject.AddComponent<ProbeRenderer>();
                renderer.SetProbes(new List<ProbeData> { new ProbeData {
                    probeType = "custom", shankCount = 2, shankPitchUm = 300, radiusUm = 10,
                    ap_mm = -2, ml_mm = -1, depthUm = 4000, spin_deg = 90 } });
                var shanks = probeObject.GetComponentsInChildren<Transform>()
                    .Where(t => t.name.StartsWith("Shank_")).OrderBy(t => t.name).ToArray();
                Require(shanks.Length == 2, "actual renderer builds two shanks");
                for (int i = 0; i < shanks.Length; i++)
                    Require(Vector3.Distance(shanks[i].localPosition,
                        new Vector3(-1f, -2f, -2f + (i == 0 ? 0.15f : -0.15f))) < 1e-5f,
                        "actual spun shank center agrees with CCF physical geometry");
            }
            finally { UnityEngine.Object.DestroyImmediate(probeObject); }
            var cameraObject = new GameObject("DisplayCameraContractTest");
            var regionObject = new GameObject("RegionLayerContractTest");
            try
            {
                var camera = cameraObject.AddComponent<Camera>();
                var regions = regionObject.AddComponent<RegionManager>();
                camera.cullingMask = ~0;
                regions.ConfigureDisplayCamera(camera);
                int layer = LayerMask.NameToLayer("RegionPick");
                if (layer < 0) layer = regions.FallbackPickLayer;
                Require((camera.cullingMask & (1 << layer)) == 0, "picking IDs excluded from display camera");
                Require(camera.cullingMask == (~0 & ~(1 << layer)), "anatomical layers remain visible");
                camera.transform.position = new Vector3(0, 0, -10);
                camera.transform.LookAt(Vector3.zero);
                Require(camera.WorldToViewportPoint(Vector3.right).x > 0.5f, "Unity screen-right reference");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(regionObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
            Require(!MriAppearanceLayerController.ShouldRenderNativeSurfaces(true, "threejs"), "single anatomical surface renderer");
            Require(MriAppearanceLayerController.ShouldRenderNativeSurfaces(true, "unity"), "native legacy surface renderer retained");
            Require(!MriAppearanceLayerController.ShouldRenderNativeSurfaces(false, "unity"), "disabled native surfaces remain hidden");
            CheckNativeSurfaces();
            CheckInteractionVisibility();
            CheckContactHemisphereClipping();
            CheckAtlasEnvelope();
            CheckObjOrientation();
            Require(Type.GetType("UnityEngine.Analytics.AnalyticsSessionInfo, UnityEngine.UnityAnalyticsModule") != null,
                "native engine analytics callback types exist with collection disabled");
            Require(Mathf.Abs(CameraController.FitVerticalFov(38f, 1.6f) - 38f) < 0.0001f,
                "wide viewport preserves reference field of view");
            float portraitAspect = 390f / 844f;
            float portraitFov = CameraController.FitVerticalFov(38f, portraitAspect);
            Require(portraitFov > 38f && portraitFov < 170f, "portrait camera remains finite and widens");
            Require(Mathf.Abs(Mathf.Tan(portraitFov * Mathf.Deg2Rad * 0.5f) * portraitAspect
                - Mathf.Tan(38f * Mathf.Deg2Rad * 0.5f) * 1.5f) < 0.0001f,
                "portrait resize preserves the horizontal field");
            Require(MeshLoader.TryResolveLod("0", false, out int baseLod) && baseLod == 0,
                "response declares actual base LOD");
            Require(MeshLoader.TryResolveLod("2", false, out int coarseLod) && coarseLod == 2,
                "response declares actual coarse LOD");
            Require(!MeshLoader.TryResolveLod(null, false, out _), "missing primary LOD does not invent requested LOD");
            Require(!MeshLoader.TryResolveLod("-1", false, out _), "negative LOD rejected");
            Require(!MeshLoader.TryResolveLod("bad", true, out _), "malformed fallback LOD rejected");
            Require(MeshLoader.TryResolveLod(null, true, out int legacyLod) && legacyLod == 0,
                "known base endpoint permits legacy headerless LOD0");
            var loaderObject = new GameObject("MeshCacheContractTest");
            var mesh = new Mesh();
            try
            {
                var loader = loaderObject.AddComponent<MeshLoader>();
                var cache = (Dictionary<string, MeshLoader.MeshLodResult>)typeof(MeshLoader)
                    .GetField("_meshCache", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(loader);
                cache["382:2"] = new MeshLoader.MeshLodResult { mesh = mesh, resolvedLod = 0 };
                cache["mri_surface:brain_surface:2"] = new MeshLoader.MeshLodResult { mesh = mesh, resolvedLod = 0 };
                bool regionHit = false;
                loader.LoadMeshLod(382, 2, result => regionHit = result.mesh == mesh
                    && result.requestedLod == 2 && result.resolvedLod == 0 && result.cacheSource == "memory");
                Require(regionHit, "region cache fallback retains actual LOD");
                bool surfaceHit = false;
                loader.LoadMriSurfaceMesh("brain_surface", 2, result => surfaceHit = result.mesh == mesh
                    && result.requestedLod == 2 && result.resolvedLod == 0 && result.cacheSource == "memory");
                Require(surfaceHit, "MRI cache fallback retains actual LOD");
                CheckQueuedMeshOwnership(loader, false);
                CheckQueuedMeshOwnership(loader, true);
                CheckDownloadSlotReservation(loader);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(loaderObject);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
            var fillShader = Shader.Find("BrainPlanner/RegionFill");
            Require(fillShader != null, "anatomical fill shader available");
            var fill = new Material(fillShader);
            try { Require(fill.passCount == 1 && fill.FindPass("FrontFace") == 0, "one forward front-surface fill pass"); }
            finally { UnityEngine.Object.DestroyImmediate(fill); }
            Debug.Log($"[PaperBuildChecks] {_checks} contract checks passed.");
        }

        private static void CheckQueuedMeshOwnership(MeshLoader loader, bool mri)
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Instance;
            var active = typeof(MeshLoader).GetField("_activeConcurrent", flags);
            var pending = (HashSet<string>)typeof(MeshLoader).GetField("_inProgress", flags).GetValue(loader);
            var method = typeof(MeshLoader).GetMethod(mri ? "LoadMriSurfaceRoutine" : "LoadRoutine", flags);
            string key = mri ? "mri_surface:skull:1" : "999:1";
            int failures = 0;
            Func<IEnumerator> make = () => (IEnumerator)method.Invoke(loader, new object[] {
                mri ? (object)"skull" : 999, 1, (Action<MeshLoader.MeshLodResult>)(_ => { }),
                (Action<string>)(_ => failures++) });
            active.SetValue(loader, loader.MaxConcurrent);
            var owner = make();
            var waiter = make();
            try
            {
                Require(owner.MoveNext() && pending.Contains(key), "queued mesh owns its key before download admission");
                Require(waiter.MoveNext() && ((CustomYieldInstruction)waiter.Current).keepWaiting,
                    "same-key request waits for owner, not another download slot");
                ((IDisposable)owner).Dispose();
                Require(!pending.Contains(key), "cancelled queued owner releases key");
                Require((int)active.GetValue(loader) == loader.MaxConcurrent, "queued cancellation does not release another request's slot");
                Require(!((CustomYieldInstruction)waiter.Current).keepWaiting, "cancelled owner wakes duplicate waiter");
                Require(!waiter.MoveNext() && failures == 1, "cancelled waiter terminates with one failure callback");
            }
            finally
            {
                ((IDisposable)owner).Dispose();
                ((IDisposable)waiter).Dispose();
                active.SetValue(loader, 0);
            }
        }

        private static void CheckDownloadSlotReservation(MeshLoader loader)
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Instance;
            var active = typeof(MeshLoader).GetField("_activeConcurrent", flags);
            var method = typeof(MeshLoader).GetMethod("LoadRoutine", flags);
            Func<int, IEnumerator> make = id => (IEnumerator)method.Invoke(loader, new object[] {
                id, 1, (Action<MeshLoader.MeshLodResult>)(_ => { }), (Action<string>)(_ => { }) });
            var first = make(991);
            var second = make(992);
            active.SetValue(loader, loader.MaxConcurrent);
            try
            {
                Require(first.MoveNext() && second.MoveNext(), "two distinct requests queue while slots are full");
                active.SetValue(loader, loader.MaxConcurrent - 1);
                Require(!((CustomYieldInstruction)first.Current).keepWaiting
                    && (int)active.GetValue(loader) == loader.MaxConcurrent,
                    "ready predicate reserves its slot before another coroutine is polled");
                Require(((CustomYieldInstruction)second.Current).keepWaiting,
                    "same-frame second waiter cannot take the reserved slot");
                ((IDisposable)first).Dispose();
                Require(!((CustomYieldInstruction)second.Current).keepWaiting,
                    "cancellation frees the slot for a different queued request");
            }
            finally
            {
                ((IDisposable)first).Dispose();
                ((IDisposable)second).Dispose();
                active.SetValue(loader, 0);
            }
        }

        // The recovered scene accumulated multiple copies through the old setup menu.
        // Keep the first controller and its own serialized camera/shell/axes references.
        public static void NormalizeScene()
        {
            var scene = EditorSceneManager.OpenScene("Assets/Scenes/BrainPlanner.unity");
            var controllers = scene.GetRootGameObjects()
                .Select(g => g.GetComponent<BrainController>()).Where(c => c != null).ToArray();
            Require(controllers.Length > 0, "scene controller exists");
            var keep = controllers[0];
            Require(keep.cameraController != null && keep.brainShell != null && keep.coordAxes != null,
                "retained controller owns camera/shell/axes");
            foreach (var other in controllers.Skip(1)) UnityEngine.Object.DestroyImmediate(other.gameObject);
            foreach (var camera in UnityEngine.Object.FindObjectsOfType<CameraController>(true))
                if (camera != keep.cameraController) UnityEngine.Object.DestroyImmediate(camera.gameObject);
            foreach (var shell in UnityEngine.Object.FindObjectsOfType<BrainShell>(true))
                if (shell != keep.brainShell) UnityEngine.Object.DestroyImmediate(shell.gameObject);
            foreach (var axes in UnityEngine.Object.FindObjectsOfType<CoordAxes>(true))
                if (axes != keep.coordAxes) UnityEngine.Object.DestroyImmediate(axes.gameObject);
            foreach (var group in UnityEngine.Object.FindObjectsOfType<Light>(true).GroupBy(l => l.name))
                foreach (var duplicate in group.Skip(1)) UnityEngine.Object.DestroyImmediate(duplicate.gameObject);
            ValidateScene();
            EditorSceneManager.SaveScene(scene);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scene.path, true) };
            AssetDatabase.SaveAssets();
            Debug.Log($"[PaperBuildChecks] Normalized {controllers.Length} controller copies to one; retained serialized references.");
        }

        public static void ValidateScene()
        {
            Require(UnityEngine.Object.FindObjectsOfType<BrainController>(true).Length == 1, "one controller");
            Require(UnityEngine.Object.FindObjectsOfType<CameraController>(true).Length == 1, "one orbit camera");
            Require(UnityEngine.Object.FindObjectsOfType<ReactBridge>(true).Length == 1, "one bridge");
            var root = UnityEngine.Object.FindObjectOfType<BrainController>();
            int receivers = root.GetComponents<MonoBehaviour>()
                .Count(c => c != null && c.GetType().GetMethod("DispatchCommand") != null);
            Require(receivers == 1, "unique SendMessage entry point");
        }

        private static void CheckObjOrientation()
        {
            var root = new GameObject("ObjOrientationContractTest");
            try
            {
                var loader = root.AddComponent<MeshLoader>();
                var parse = typeof(MeshLoader).GetMethod("ParseOBJ", BindingFlags.Instance | BindingFlags.NonPublic);
                const string vertices = "v 5400 440 5700\nv 6400 440 5700\nv 5400 1440 5700\n";
                foreach (int normalSign in new[] { 1, -1 })
                {
                    var mesh = (Mesh)parse.Invoke(loader, new object[] {
                        vertices + "vn 0 0 " + normalSign + "\nf 1//1 2//1 3//1\n" });
                    try
                    {
                        var p = mesh.vertices; var tri = mesh.triangles;
                        var normal = Vector3.Cross(p[tri[1]] - p[tri[0]], p[tri[2]] - p[tri[0]]).normalized;
                        Require(Vector3.Dot(normal, mesh.normals[tri[0]]) > 0.999f,
                            "OBJ front face agrees with supplied anatomical normal after CCF mapping: " + normalSign);
                        Require(p[0] == Vector3.zero && p[1] == Vector3.back && p[2] == Vector3.down,
                            "orientation correction cannot alter CCF vertex coordinates");
                        Require(mesh.normals.All(n => Vector3.Dot(n, normalSign * Vector3.right) > 0.999f),
                            "OBJ source normals are retained rather than inverted to match incorrect winding");
                    }
                    finally { UnityEngine.Object.DestroyImmediate(mesh); }
                }
                var noNormals = (Mesh)parse.Invoke(loader, new object[] { vertices + "f 1 2 3\n" });
                try
                {
                    Require(noNormals.normals.All(n => Vector3.Dot(n, Vector3.right) > 0.999f),
                        "standard OBJ without normals accounts for handedness before normal reconstruction");
                }
                finally { UnityEngine.Object.DestroyImmediate(noNormals); }
                var noisy = (Mesh)parse.Invoke(loader, new object[] { vertices +
                    "v 7400 440 5700\nv 7500 440 5700\nv 7400 540 5700\n" +
                    "vn 0 0 1\nvn 0 0 -1\nf 1//1 2//1 3//1\nf 4//2 5//2 6//2\n" });
                try
                {
                    Require(noisy.triangles.SequenceEqual(new[] { 0, 2, 1, 3, 5, 4 }),
                        "local normal noise cannot independently flip faces or tear mesh winding");
                }
                finally { UnityEngine.Object.DestroyImmediate(noisy); }
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        private static void CheckAtlasEnvelope()
        {
            var root = new GameObject("AtlasEnvelopeContractTest");
            var failedRoot = new GameObject("UnavailableAtlasEnvelopeTest");
            var mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
            try
            {
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                var loader = root.AddComponent<MeshLoader>();
                var cache = (IDictionary)typeof(MeshLoader).GetField("_meshCache", flags).GetValue(loader);
                cache["997:1"] = new MeshLoader.MeshLodResult { mesh = mesh, resolvedLod = 0 };
                var shell = root.AddComponent<BrainShell>();
                typeof(BrainShell).GetMethod("Awake", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(shell, null);
                Require(root.GetComponent<MeshFilter>().sharedMesh == null,
                    "atlas envelope must not fabricate an ellipsoid before a real mesh loads");
                Require(!root.GetComponent<MeshRenderer>().enabled,
                    "unloaded atlas envelope is not visible");
                shell.Initialize(loader);
                var states = new List<BrainShell.SurfaceState>();
                shell.OnSurfaceState = states.Add;
                shell.SetSuppressed(true);
                shell.SetVisible(true);
                Require(root.GetComponent<MeshFilter>().sharedMesh == null && !root.GetComponent<MeshRenderer>().enabled,
                    "MRI brain layer suppresses duplicate envelope without loading it");
                shell.SetSuppressed(false);
                Require(states.Any(s => s.key == "atlas_envelope" && s.source == "allen_ccf_structure_mesh" &&
                    s.structureId == 997 && s.status == "loaded" && s.visible && s.requestedLod == 1 && s.resolvedLod == 0),
                    "atlas envelope reports source and actual fallback LOD");
                Require(root.GetComponent<MeshFilter>().sharedMesh == mesh,
                    "atlas envelope uses shared loader geometry without rescaling or replacement");
                var regions = root.AddComponent<RegionManager>();
                var bridge = root.AddComponent<ReactBridge>();
                typeof(ReactBridge).GetField("_regionMgr", flags).SetValue(bridge, regions);
                typeof(ReactBridge).GetField("_brainShell", flags).SetValue(bridge, shell);
                var material = root.GetComponent<MeshRenderer>().sharedMaterial;
                foreach (string axis in new[] { "x", "y", "z" })
                {
                    regions.SetSlice(axis, true, 0.4f, 0.3f, true, false, 0f);
                    bridge.SetHemisphereFilter("left");
                    Require(material.GetFloat("_SliceAxis") == regions.SliceAxis &&
                        material.GetFloat("_SliceCenter") == regions.SliceCenter && material.GetFloat("_SliceEnabled") == 1f &&
                        material.GetFloat("_SliceUseSlab") == 1f && material.GetFloat("_SliceThickness") == 0.3f &&
                        material.GetFloat("_HemisphereFilter") == -1f, "atlas envelope follows shared clipping: " + axis);
                }
                bridge.SetMriAppearance("{\"enabled\":true,\"showBrainSurface\":true}");
                Require(!root.GetComponent<MeshRenderer>().enabled, "bridge suppresses duplicate envelope in MRI mode");
                bridge.SetMriAppearance("");
                Require(root.GetComponent<MeshRenderer>().enabled, "bridge restores requested real envelope when MRI layer is off");
                bridge.SetStateSnapshot("{\"mriAppearance\":{\"enabled\":true,\"showBrainSurface\":true}}");
                Require(!root.GetComponent<MeshRenderer>().enabled, "snapshot also suppresses duplicate envelope");
                bridge.SetShellVisible("false");
                shell.SetSuppressed(false);
                Require(!root.GetComponent<MeshRenderer>().enabled && states.Last().status == "loaded" && !states.Last().visible,
                    "hidden atlas envelope retains geometry but reports invisible");
                var failedLoader = failedRoot.AddComponent<MeshLoader>();
                ((HashSet<string>)typeof(MeshLoader).GetField("_loadFailed", flags).GetValue(failedLoader)).Add("997:1");
                var failedShell = failedRoot.AddComponent<BrainShell>();
                failedShell.Initialize(failedLoader);
                failedShell.OnSurfaceState = states.Add;
                failedShell.SetVisible(true);
                Require(states.Last().status == "failed" && !states.Last().visible && states.Last().resolvedLod == -1 &&
                    failedRoot.GetComponent<MeshFilter>().sharedMesh == null && !failedRoot.GetComponent<MeshRenderer>().enabled,
                    "missing atlas envelope fails closed without an ellipsoid fallback");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(failedRoot);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        private static void CheckContactHemisphereClipping()
        {
            var root = new GameObject("ContactHemisphereContractTest");
            var material = new Material(Shader.Find("BrainPlanner/RegionOutline"));
            try
            {
                var regions = root.AddComponent<RegionManager>();
                regions.SliceEnabled = true;
                regions.SetContactProfile("slice_contact_persist", "outline", 0f, new[] { "TEST" });
                typeof(RegionManager).GetMethod("ApplySliceToMaterial", BindingFlags.Instance | BindingFlags.NonPublic)
                    .Invoke(regions, new object[] { material, "TEST", false });
                Require(material.GetFloat("_SliceEnabled") == 1f,
                    "contact region must keep slice enabled for its non-contact hemisphere");
                Require(material.GetFloat("_ContactOverride") == 0f,
                    "legacy region-only contact must not invent sampled hemispheres");
                var bridge = root.AddComponent<ReactBridge>();
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                typeof(ReactBridge).GetField("_regionMgr", flags).SetValue(bridge, regions);
                bridge.SetContactProfile("{\"mode\":\"slice_contact_persist\",\"style\":\"outline\",\"regions\":[\"TEST\"],"
                    + "\"regionHemispheres\":[{\"key\":\"TEST\",\"hemispheres\":[\"left\"]}]}" );
                typeof(RegionManager).GetMethod("ApplySliceToMaterial", flags)
                    .Invoke(regions, new object[] { material, "TEST", false });
                Require(material.GetFloat("_SliceEnabled") == 1f && material.GetFloat("_ContactOverride") == 1f,
                    "known contact uses per-fragment override, not whole-mesh slice disable");
                Require(material.GetVector("_ContactHemispheres") == new Vector4(1, 0, 0, 0),
                    "bridge transports sampled left contact without inventing right contact");
                foreach (string shader in new[] { "BrainPlanner/RegionFill", "BrainPlanner/RegionIdPick" })
                {
                    var other = new Material(Shader.Find(shader));
                    try { Require(other.HasProperty("_ContactHemispheres"), "fill and picking support sampled hemispheres"); }
                    finally { UnityEngine.Object.DestroyImmediate(other); }
                }
                regions.SetContactProfile("slice_contact_persist", "outline", 0f, new[] { "TEST" }, new[] {
                    new RegionManager.ContactRegionHemispheres { key = "TEST", hemispheres = new[] { "left" } },
                    new RegionManager.ContactRegionHemispheres { key = "TEST", hemispheres = new[] { "right" } },
                    new RegionManager.ContactRegionHemispheres { key = "NOT_CONTACT", hemispheres = new[] { "left" } },
                });
                typeof(RegionManager).GetMethod("ApplySliceToMaterial", flags)
                    .Invoke(regions, new object[] { material, "TEST", false });
                Require(material.GetVector("_ContactHemispheres") == new Vector4(1, 1, 0, 0),
                    "contacts across probes union both sampled sides");
                typeof(RegionManager).GetMethod("ApplySliceToMaterial", flags)
                    .Invoke(regions, new object[] { material, "NOT_CONTACT", false });
                Require(material.GetVector("_ContactHemispheres") == Vector4.zero,
                    "side evidence cannot grant an exception to an absent contact key");
                regions.SetContactProfile("standard", "outline", 0f, new[] { "TEST" });
                typeof(RegionManager).GetMethod("ApplySliceToMaterial", flags)
                    .Invoke(regions, new object[] { material, "TEST", false });
                Require(material.GetVector("_ContactHemispheres") == Vector4.zero && material.GetFloat("_ContactOverride") == 0f,
                    "replacing or disabling contact clears prior sampled sides");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(material);
            }
        }

        private static void CheckInteractionVisibility()
        {
            var root = new GameObject("InteractionVisibilityContractTest");
            var fillObject = new GameObject("BackgroundFill");
            var outlineObject = new GameObject("BackgroundOutline");
            var fill = new Material(Shader.Find("BrainPlanner/RegionFill"));
            var outline = new Material(Shader.Find("BrainPlanner/RegionOutline"));
            try
            {
                var regions = root.AddComponent<RegionManager>();
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                ((HashSet<string>)typeof(RegionManager).GetField("_focusKeys", flags).GetValue(regions)).Add("TEST");
                ((IDictionary)typeof(RegionManager).GetField("_fillObjects", flags).GetValue(regions))["TEST"] = fillObject;
                ((IDictionary)typeof(RegionManager).GetField("_outlineObjects", flags).GetValue(regions))["TEST"] = outlineObject;
                ((IDictionary)typeof(RegionManager).GetField("_fillMats", flags).GetValue(regions))["TEST"] = fill;
                ((IDictionary)typeof(RegionManager).GetField("_outlineMats", flags).GetValue(regions))["TEST"] = outline;
                regions.LowLoadInteractionMode = true;
                regions.SetInteractionQuality("high");
                Require(fillObject.activeSelf && outlineObject.activeSelf, "background anatomy is initially visible");
                float fillAlpha = fill.GetFloat("_Alpha");
                float outlineAlpha = outline.GetFloat("_OutlineAlpha");
                regions.SetInteractionQuality("low");
                Require(fillObject.activeSelf && outlineObject.activeSelf, "dragging must not hide background anatomy");
                Require(fill.GetFloat("_Alpha") == fillAlpha && outline.GetFloat("_OutlineAlpha") == outlineAlpha,
                    "dragging must not change anatomical opacity");
                regions.SetInteractionQuality("high");
                Require(fillObject.activeSelf && fill.GetFloat("_Alpha") == fillAlpha,
                    "drag release preserves the same anatomical display");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(fillObject);
                UnityEngine.Object.DestroyImmediate(outlineObject);
                UnityEngine.Object.DestroyImmediate(fill);
                UnityEngine.Object.DestroyImmediate(outline);
            }
        }

        private static void CheckNativeSurfaces()
        {
            var root = new GameObject("NativeSurfaceContractTest");
            var mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
            try
            {
                var loader = root.AddComponent<MeshLoader>();
                var cache = (IDictionary)typeof(MeshLoader).GetField("_meshCache", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(loader);
                foreach (string key in new[] { "brain_surface", "outer_tissue" })
                    cache["mri_surface:" + key + ":1"] = new MeshLoader.MeshLodResult { mesh = mesh, resolvedLod = 0 };
                var failed = (HashSet<string>)typeof(MeshLoader).GetField("_loadFailed", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(loader);
                failed.Add("mri_surface:skull:1");
                var regions = root.AddComponent<RegionManager>();
                var surfaces = root.AddComponent<MriAppearanceLayerController>();
                typeof(MriAppearanceLayerController).GetMethod("Awake", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(surfaces, null);
                var states = new List<MriAppearanceLayerController.SurfaceState>();
                surfaces.OnSurfaceState = states.Add;
                surfaces.Apply(true, true, true, true, true, "mri_ccf_overlay");
                foreach (string key in new[] { "brain_surface", "outer_tissue" })
                    Require(states.Any(s => s.key == key && s.status == "loaded" && s.visible && s.requestedLod == 1 && s.resolvedLod == 0),
                        "native surface retains resolved LOD on cached fallback: " + key);
                Require(states.Any(s => s.key == "skull" && s.status == "failed" && !s.visible && s.resolvedLod == -1),
                    "missing surface is reported as failed, not a visible proxy");
                var renderers = root.GetComponentsInChildren<MeshRenderer>(true).Where(r => r.name.EndsWith("_MaskMesh")).ToArray();
                Require(renderers.Length == 3 && renderers.All(r => r.sharedMaterial.shader.name == "BrainPlanner/AnatomicalSurface"),
                    "real surface renderers share the Unity scene and clipping shader");
                Require(!root.GetComponentsInChildren<Transform>(true).Any(t => t.name.EndsWith("_ProxyShell") && t.gameObject.activeSelf),
                    "no proxy shell after a surface failure");
                foreach (string axis in new[] { "x", "y", "z" })
                {
                    regions.SetSlice(axis, true, 0.4f, 0.3f, true, false, 0f);
                    regions.SetHemisphereFilter("left");
                    surfaces.SyncClipping(regions);
                    Require(renderers.All(r => r.sharedMaterial.GetFloat("_SliceCenter") == regions.SliceCenter &&
                        r.sharedMaterial.GetFloat("_SliceAxis") == regions.SliceAxis &&
                        r.sharedMaterial.GetFloat("_SliceEnabled") == 1f &&
                        r.sharedMaterial.GetFloat("_SliceThickness") == 0.3f &&
                        r.sharedMaterial.GetFloat("_SliceUseSlab") == 1f &&
                        r.sharedMaterial.GetFloat("_HemisphereFilter") == -1f), "native surface matches CCF clipping: " + axis);
                }
                var alpha = renderers.Select(r => r.sharedMaterial.GetColor("_BaseColor").a).ToArray();
                surfaces.SetInteractionQuality("low");
                Require(renderers.Select(r => r.sharedMaterial.GetColor("_BaseColor").a).SequenceEqual(alpha),
                    "surface opacity remains stable while dragging");
                surfaces.Apply(false, true, true, true, true, "ccf_only");
                Require(renderers.All(r => !r.gameObject.activeSelf), "CCF-only mode hides all native surface layers");
                Require(states.TakeLast(3).All(s => !s.visible), "hidden state is reported for all surface layers");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        public static void PrepareAndCheck()
        {
            Run();
            NormalizeScene();
        }

        public static void PrepareRuntimeMaterials()
        {
            const string directory = "Assets/Resources/ViewerRuntime";
            System.IO.Directory.CreateDirectory(directory);
            string[] shaders = {
                "Universal Render Pipeline/Lit", "Universal Render Pipeline/Unlit",
                "Universal Render Pipeline/Particles/Unlit", "Sprites/Default",
                "BrainPlanner/RegionFill", "BrainPlanner/RegionOutline",
                "BrainPlanner/UnlitTransparentLine", "BrainPlanner/RegionIdPick", "BrainPlanner/AnatomicalSurface", "BrainPlanner/AtlasSection",
            };
            for (int i = 0; i < shaders.Length; i++)
            {
                var shader = Shader.Find(shaders[i]);
                Require(shader != null, "runtime shader: " + shaders[i]);
                for (int variant = 0; variant < 2; variant++)
                {
                    string path = $"{directory}/shader_{i}_{variant}.mat";
                    var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                    if (material == null)
                    {
                        material = new Material(shader);
                        AssetDatabase.CreateAsset(material, path);
                    }
                    material.shader = shader;
                    if (material.HasProperty("_Surface")) material.SetFloat("_Surface", variant);
                    if (variant == 1)
                    {
                        material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
                        material.EnableKeyword("_EMISSION");
                        material.SetOverrideTag("RenderType", "Transparent");
                        material.renderQueue = 3000;
                    }
                    EditorUtility.SetDirty(material);
                }
            }
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log($"[PaperBuildChecks] Retained {shaders.Length * 2} runtime material variants through Resources.");
        }

        private static void Require(bool condition, string check)
        {
            if (!condition) throw new InvalidOperationException("Paper viewer check failed: " + check);
            _checks++;
        }
    }
}
