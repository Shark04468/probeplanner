// Editor/SetupScene.cs
// Utility menus for creating and configuring BrainPlanner scenes.

#if UNITY_EDITOR
using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace BrainPlanner.Editor
{
    public static class SetupScene
    {
        private static Type Find(string fullName)
        {
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var t = asm.GetType(fullName);
                    if (t != null) return t;
                }
                catch
                {
                    // Ignore reflection errors and continue scanning.
                }
            }

            Debug.LogWarning($"[Setup] Type not found: {fullName}");
            return null;
        }

        private static Type TBrainController => Find("BrainPlanner.BrainController");
        private static Type TMeshLoader => Find("BrainPlanner.BrainView.MeshLoader");
        private static Type TRegionManager => Find("BrainPlanner.BrainView.RegionManager");
        private static Type TProbeRenderer => Find("BrainPlanner.BrainView.ProbeRenderer");
        private static Type TReactBridge => Find("BrainPlanner.Bridge.ReactBridge");
        private static Type TCameraCtrl => Find("BrainPlanner.BrainView.CameraController");
        private static Type TBrainShell => Find("BrainPlanner.BrainView.BrainShell");
        private static Type TCoordAxes => Find("BrainPlanner.BrainView.CoordAxes");

        [MenuItem("BrainPlanner/1. Create Scene", true)]
        static bool CreateSceneValidate() => !Application.isPlaying;

        [MenuItem("BrainPlanner/1. Create Scene", false, 1)]
        public static void CreateScene()
        {
            if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            Destroy(GameObject.Find("Main Camera"));
            Destroy(GameObject.Find("Directional Light"));

            var ctrl = new GameObject("BrainController");
            Add(ctrl, TBrainController);
            Add(ctrl, TMeshLoader);
            Add(ctrl, TRegionManager);
            Add(ctrl, TProbeRenderer);
            Add(ctrl, TReactBridge);

            var camGO = new GameObject("MainCamera");
            camGO.tag = "MainCamera";
            var cam = camGO.AddComponent<Camera>();
            cam.backgroundColor = new Color(0.02f, 0.02f, 0.05f);
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.nearClipPlane = 0.05f;
            cam.farClipPlane = 200f;
            cam.fieldOfView = 38f;
            cam.allowMSAA = true;
            camGO.AddComponent<UniversalAdditionalCameraData>();

            var camCtrl = Add(camGO, TCameraCtrl);
            Set(camCtrl, "PivotPoint", new Vector3(0f, -2.8f, -0.8f));
            Set(camCtrl, "InitialDistance", 18f);
            Set(camCtrl, "InitialTheta", 0.55f);
            Set(camCtrl, "InitialPhi", 1.25f);

            MakeLight("Sun", new Vector3(50f, 70f, 30f), 2.2f, Color.white);
            MakeLight("Fill", new Vector3(-60f, -10f, -50f), 0.8f, new Color(0.53f, 0.6f, 1f));
            MakeLight("Bounce", new Vector3(0f, -80f, 0f), 0.4f, new Color(1f, 0.93f, 0.8f));
            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.12f, 0.12f, 0.16f);

            var shellGO = new GameObject("BrainShell");
            shellGO.AddComponent<MeshFilter>();
            shellGO.AddComponent<MeshRenderer>();
            var shellComp = Add(shellGO, TBrainShell);

            var axesGO = new GameObject("CoordAxes");
            var axesComp = Add(axesGO, TCoordAxes);

            var ctrlComp = TBrainController != null ? ctrl.GetComponent(TBrainController) : null;
            Set(ctrlComp, "cameraController", camCtrl);
            Set(ctrlComp, "brainShell", shellComp);
            Set(ctrlComp, "coordAxes", axesComp);

            AssignMaterials(
                TRegionManager != null ? ctrl.GetComponent(TRegionManager) : null,
                TProbeRenderer != null ? ctrl.GetComponent(TProbeRenderer) : null
            );

            System.IO.Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(
                EditorSceneManager.GetActiveScene(),
                "Assets/Scenes/BrainPlanner.unity"
            );

            int found = new Type[]
            {
                TBrainController, TMeshLoader, TRegionManager, TProbeRenderer,
                TReactBridge, TCameraCtrl, TBrainShell, TCoordAxes
            }.Count(t => t != null);

            Debug.Log($"[Setup] Scene created ({found}/8 components found).");
            EditorUtility.DisplayDialog(
                "Scene Created",
                $"Components loaded: {found}/8\n\nNext:\n1) BrainPlanner > 2. Configure URP Pipeline\n2) Press Play to test",
                "OK"
            );
        }

        [MenuItem("BrainPlanner/2. Configure URP Pipeline", true)]
        static bool ConfigureURPValidate() => !Application.isPlaying;

        [MenuItem("BrainPlanner/2. Configure URP Pipeline", false, 2)]
        public static void ConfigureURP()
        {
            var urpAsset = GraphicsSettings.defaultRenderPipeline as UniversalRenderPipelineAsset;

            if (urpAsset == null)
            {
                foreach (var guid in AssetDatabase.FindAssets("t:UniversalRenderPipelineAsset"))
                {
                    var path = AssetDatabase.GUIDToAssetPath(guid);
                    if (path.Contains("UniversalRP.asset")) continue;
                    var a = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(path);
                    if (a != null)
                    {
                        urpAsset = a;
                        break;
                    }
                }
            }

            if (urpAsset == null)
            {
                EditorUtility.DisplayDialog(
                    "URP Asset Not Found",
                    "Please set a URP asset manually in:\nEdit > Project Settings > Graphics",
                    "OK"
                );
                return;
            }

            urpAsset.msaaSampleCount = 4;
            urpAsset.shadowDistance = 50f;
            urpAsset.renderScale = 1f;
            urpAsset.supportsHDR = true;

            EditorUtility.SetDirty(urpAsset);
            GraphicsSettings.defaultRenderPipeline = urpAsset;
            QualitySettings.renderPipeline = urpAsset;
            AssetDatabase.SaveAssets();

            string p = AssetDatabase.GetAssetPath(urpAsset);
            Debug.Log($"[Setup] URP configured: {p}");
            EditorUtility.DisplayDialog("URP Configured", $"{p}\nMSAA 4x | HDR | Shadow Distance 50m", "OK");
        }

        [MenuItem("BrainPlanner/3. Configure WebGL Build", true)]
        static bool ConfigureWebGLValidate() => !Application.isPlaying;

        [MenuItem("BrainPlanner/3. Configure WebGL Build", false, 3)]
        public static void ConfigureWebGL()
        {
            EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL);
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Brotli;
            PlayerSettings.WebGL.decompressionFallback = true;
            PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
            PlayerSettings.WebGL.linkerTarget = WebGLLinkerTarget.Wasm;
            PlayerSettings.WebGL.memorySize = 1024;
            PlayerSettings.WebGL.debugSymbolMode = WebGLDebugSymbolMode.Off;
            PlayerSettings.WebGL.template = "PROJECT:BrainPlanner";
            PlayerSettings.companyName = "BCILab";
            PlayerSettings.productName = "BrainPlanner";
            PlayerSettings.bundleVersion = "1.0.0";
            PlayerSettings.defaultWebScreenWidth = 1280;
            PlayerSettings.defaultWebScreenHeight = 720;
            AssetDatabase.SaveAssets();

            EditorUtility.DisplayDialog(
                "WebGL Configured",
                "Brotli + Decompression Fallback | 1024MB | Exceptions: ExplicitlyThrown | Template: BrainPlanner",
                "OK"
            );
        }

        [MenuItem("BrainPlanner/Diagnostics/List BrainPlanner Types", false, 20)]
        public static void DiagnoseTypes()
        {
            var sb = new System.Text.StringBuilder("=== BrainPlanner Type Scan ===\n\n");
            foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
            {
                try
                {
                    var types = asm.GetTypes()
                        .Where(t => t.Namespace != null && t.Namespace.StartsWith("BrainPlanner"))
                        .ToArray();
                    if (types.Length == 0) continue;

                    sb.AppendLine($"[{asm.GetName().Name}]");
                    foreach (var t in types) sb.AppendLine($"  - {t.FullName}");
                    sb.AppendLine();
                }
                catch
                {
                    // Ignore type-load errors for unrelated assemblies.
                }
            }

            string msg = sb.ToString();
            Debug.Log(msg);

            bool ok = msg.Contains("BrainPlanner.BrainView.RegionManager");
            EditorUtility.DisplayDialog(
                "Diagnostic Result",
                ok
                    ? "Runtime scripts are visible. You can run '1. Create Scene'."
                    : "Runtime types missing. Check Console compile errors under Assets/Scripts.",
                "OK"
            );
        }

        static Component Add(GameObject go, Type t)
        {
            if (t == null || go == null) return null;
            return go.AddComponent(t);
        }

        static void Set(object obj, string name, object value)
        {
            if (obj == null) return;
            var t = obj.GetType();
            const System.Reflection.BindingFlags F =
                System.Reflection.BindingFlags.Public |
                System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance;

            var fi = t.GetField(name, F);
            if (fi != null)
            {
                fi.SetValue(obj, value);
                return;
            }

            var pi = t.GetProperty(name, F);
            if (pi?.CanWrite == true) pi.SetValue(obj, value);
        }

        static void Destroy(UnityEngine.Object o)
        {
            if (o != null) UnityEngine.Object.DestroyImmediate(o);
        }

        static void MakeLight(string name, Vector3 euler, float intensity, Color color)
        {
            var go = new GameObject(name);
            go.transform.eulerAngles = euler;
            var l = go.AddComponent<Light>();
            l.type = LightType.Directional;
            l.intensity = intensity;
            l.color = color;
            l.shadows = LightShadows.Soft;
        }

        static void AssignMaterials(Component rm, Component pr)
        {
            var sF = Shader.Find("BrainPlanner/RegionFill");
            var sO = Shader.Find("BrainPlanner/RegionOutline");
            var sL = Shader.Find("BrainPlanner/UnlitTransparentLine");
            var sP = Shader.Find("BrainPlanner/RegionIdPick");
            var sU = Shader.Find("Universal Render Pipeline/Lit");

            if (rm != null)
            {
                Set(rm, "FillMaterialTemplate", new Material(sF ?? sU));
                Set(rm, "OutlineMaterialTemplate", new Material(sO ?? sU));
                Set(rm, "FallbackMaterialTemplate", new Material(sU));
                Set(rm, "RegionIdPickMaterialTemplate", new Material(sP ?? sU));
            }

            if (pr != null && sU != null)
            {
                Set(pr, "ShankMaterial", new Material(sU));
                Set(pr, "EntryDotMaterial", new Material(sU));
                Set(pr, "DashedLineMaterial", new Material(sL ?? sU));
            }
        }
    }
}
#endif
