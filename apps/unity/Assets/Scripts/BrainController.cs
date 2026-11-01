// BrainController.cs
// Root coordinator mounted on the BrainController GameObject.

using BrainPlanner.BrainView;
using BrainPlanner.Bridge;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace BrainPlanner
{
    [DisallowMultipleComponent]
    public class BrainController : MonoBehaviour
    {
        private static BrainController _instance;

        [Header("Subsystems (auto-wired)")]
        public MeshLoader meshLoader;
        public RegionManager regionManager;
        public ProbeRenderer probeRenderer;
        public ReactBridge reactBridge;
        public RuntimePostFxController postFxController;
        public MriAppearanceLayerController mriAppearanceLayerController;

        [Header("Scene References")]
        public CameraController cameraController;
        public BrainShell brainShell;
        public CoordAxes coordAxes;

        [Header("Flask")]
        public string FlaskBase = "http://localhost:5000";

        [Header("Visual Quality")]
        public bool AutoTuneLighting = true;
        public bool ForceCameraBackground = true;
        public Color CameraBackgroundColor = new Color(0.048f, 0.095f, 0.175f, 1f);
        [Range(0f, 1f)] public float DefaultEnvironmentLighting = 0.62f;

        private readonly List<Light> _environmentLights = new();
        private readonly List<float> _environmentLightBaseIntensity = new();
        private float _baseAmbientIntensity = 1.0f;
        private float _baseReflectionIntensity = 0.24f;
        private bool _lightingProfileReady;

        void Awake()
        {
            if (_instance != null && _instance != this)
            {
                Debug.LogWarning(
                    $"[BrainController] Duplicate controller detected on '{name}', disabling this GameObject."
                );
                gameObject.SetActive(false);
                return;
            }
            _instance = this;

            // Resolve before any subsystem starts loading assets.
            FlaskBase = BackendAddress.Resolve(Application.absoluteURL, FlaskBase);

            // Runtime-created meshes need explicit build-time shader references.
            Resources.LoadAll<Material>("ViewerRuntime");
            if (Shader.Find("Universal Render Pipeline/Lit") == null)
                throw new System.InvalidOperationException("Viewer runtime shaders are missing from this build.");

            meshLoader = GetOrAdd<MeshLoader>();
            regionManager = GetOrAdd<RegionManager>();
            probeRenderer = GetOrAdd<ProbeRenderer>();
            reactBridge = GetOrAdd<ReactBridge>();
            postFxController = GetOrAdd<RuntimePostFxController>();
            mriAppearanceLayerController = GetOrAdd<MriAppearanceLayerController>();

            meshLoader.FlaskBase = FlaskBase;

            if (cameraController == null)
                cameraController = FindObjectOfType<CameraController>();

            if (brainShell == null)
                brainShell = FindObjectOfType<BrainShell>();

            if (coordAxes == null)
                coordAxes = FindObjectOfType<CoordAxes>();

            DisableDuplicateComponents();
            ApplyDefaultViewState();
            ApplyVisualQualityDefaults();

            Debug.Log("[BrainController] Subsystems initialized.");
        }

        void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        void Start()
        {
            CheckLayers();
        }

        [ContextMenu("Debug/Load All Outlines")]
        public void TestLoadAllOutlines()
        {
            regionManager.LoadAllOutlines();
        }

        [ContextMenu("Debug/Select VISp + CA1 + CP")]
        public void TestSelectRegions()
        {
            regionManager.SetSelected(new[] { "VISp", "CA1", "CP" });
        }

        [ContextMenu("Debug/Add One Probe")]
        public void TestAddProbe()
        {
            probeRenderer.SetProbes(
                "[{\"ap_mm\":1.8,\"ml_mm\":-1.2,\"theta_deg\":15,\"phi_deg\":0," +
                "\"depthUm\":4000,\"entryDvCorr_mm\":0,\"probeType\":\"NP2.0-4sh\"}]"
            );
        }

        private T GetOrAdd<T>() where T : Component
        {
            T comp = GetComponent<T>();
            if (comp == null) comp = gameObject.AddComponent<T>();
            return comp;
        }

        private void CheckLayers()
        {
            int outlineLayer = LayerMask.NameToLayer("BrainOutline");
            int fillLayer = LayerMask.NameToLayer("BrainFill");
            if (outlineLayer == -1 || fillLayer == -1)
            {
                Debug.LogWarning(
                    "[BrainController] Missing layer 'BrainOutline' or 'BrainFill'.\n" +
                    "Please add these User Layers in Project Settings > Tags and Layers."
                );
            }
        }

        private void DisableDuplicateComponents()
        {
            DisableDuplicatesOf(brainShell, "BrainShell");
            DisableDuplicatesOf(mriAppearanceLayerController, "MriAppearanceLayerController");
            DisableDuplicatesOf(coordAxes, "CoordAxes");
            DisableDuplicatesOf(cameraController, "CameraController");
        }

        private void DisableDuplicatesOf<T>(T preferred, string label) where T : Component
        {
            var all = FindObjectsOfType<T>(true);
            if (all == null || all.Length <= 1) return;

            T keep = preferred != null ? preferred : all[0];
            for (int i = 0; i < all.Length; i++)
            {
                var current = all[i];
                if (current == null || current == keep) continue;
                if (!current.gameObject.activeSelf) continue;

                current.gameObject.SetActive(false);
                Debug.LogWarning(
                    $"[BrainController] Disabled duplicate {label}: {current.gameObject.name}"
                );
            }
        }

        private void ApplyDefaultViewState()
        {
            if (brainShell != null)
            {
                brainShell.Initialize(GetComponent<MeshLoader>());
                brainShell.SetVisible(false);
            }

            if (coordAxes != null)
                coordAxes.gameObject.SetActive(false);

            if (probeRenderer != null)
                probeRenderer.SetShowExtensionLines(false);
        }

        private void ApplyVisualQualityDefaults()
        {
            if (!AutoTuneLighting) return;

            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.30f, 0.37f, 0.46f, 1f);
            RenderSettings.ambientIntensity = 1.24f;
            RenderSettings.reflectionIntensity = 0.24f;

            if (cameraController != null && cameraController.ControlledCamera != null)
            {
                TuneCamera(cameraController.ControlledCamera);
                postFxController?.Bootstrap(cameraController.ControlledCamera);
            }
            else
            {
                var cam = Camera.main ?? FindObjectOfType<Camera>();
                if (cam != null)
                {
                    TuneCamera(cam);
                    postFxController?.Bootstrap(cam);
                }
            }

            var allLights = FindObjectsOfType<Light>(true);
            int directionalCount = 0;
            for (int i = 0; i < allLights.Length; i++)
            {
                var l = allLights[i];
                if (l == null || l.type != LightType.Directional) continue;
                directionalCount++;
            }

            if (directionalCount == 0)
            {
                CreatePresentationLight("BP_KeyLight", new Vector3(38f, -36f, 0f), 2.20f, new Color(1f, 0.97f, 0.92f));
                CreatePresentationLight("BP_FillLight", new Vector3(12f, 130f, 0f), 1.36f, new Color(0.72f, 0.85f, 1f));
                CreatePresentationLight("BP_RimLight", new Vector3(-24f, 218f, 0f), 1.02f, new Color(0.52f, 0.80f, 1f));
                CaptureEnvironmentLightingProfile();
                SetEnvironmentLighting(DefaultEnvironmentLighting);
                return;
            }

            int rank = 0;
            for (int i = 0; i < allLights.Length; i++)
            {
                var l = allLights[i];
                if (l == null || l.type != LightType.Directional) continue;
                l.shadows = rank == 0 ? LightShadows.Soft : LightShadows.None;
                l.shadowStrength = rank == 0 ? 0.28f : 0f;
                l.shadowBias = 0.05f;
                l.shadowNormalBias = 0.3f;
                l.cookieSize = 10f;
                if (rank == 0)
                {
                    l.intensity = Mathf.Max(2.10f, l.intensity);
                    l.color = new Color(1f, 0.97f, 0.92f, 1f);
                }
                else if (rank == 1)
                {
                    l.intensity = Mathf.Max(1.34f, l.intensity);
                    l.color = new Color(0.72f, 0.85f, 1f, 1f);
                }
                else
                {
                    l.intensity = Mathf.Max(0.92f, l.intensity);
                    l.color = new Color(0.55f, 0.78f, 1f, 1f);
                }
                rank++;
            }

            CaptureEnvironmentLightingProfile();
            SetEnvironmentLighting(DefaultEnvironmentLighting);
        }

        private void TuneCamera(Camera cam)
        {
            if (cam == null) return;
            cam.allowHDR = true;
            cam.allowMSAA = true;
            cam.depthTextureMode = DepthTextureMode.Depth;
            if (ForceCameraBackground)
            {
                cam.clearFlags = CameraClearFlags.SolidColor;
                cam.backgroundColor = CameraBackgroundColor;
            }
        }

        private void CreatePresentationLight(string name, Vector3 euler, float intensity, Color color)
        {
            var go = new GameObject(name);
            go.transform.SetParent(transform, false);
            go.transform.eulerAngles = euler;
            var l = go.AddComponent<Light>();
            l.type = LightType.Directional;
            l.intensity = intensity;
            l.color = color;
            l.shadows = LightShadows.None;
        }

        private void CaptureEnvironmentLightingProfile()
        {
            _environmentLights.Clear();
            _environmentLightBaseIntensity.Clear();
            var allLights = FindObjectsOfType<Light>(true);
            for (int i = 0; i < allLights.Length; i++)
            {
                var l = allLights[i];
                if (l == null || l.type != LightType.Directional) continue;
                _environmentLights.Add(l);
                _environmentLightBaseIntensity.Add(Mathf.Max(0.01f, l.intensity));
            }
            _baseAmbientIntensity = Mathf.Max(0.01f, RenderSettings.ambientIntensity);
            _baseReflectionIntensity = Mathf.Max(0.0f, RenderSettings.reflectionIntensity);
            _lightingProfileReady = true;
        }

        public void SetEnvironmentLighting(float normalized)
        {
            if (!_lightingProfileReady)
            {
                CaptureEnvironmentLightingProfile();
            }

            float n = Mathf.Clamp01(normalized);
            float ambientScale = Mathf.Lerp(0.55f, 1.65f, n);
            float reflectionScale = Mathf.Lerp(0.55f, 1.35f, n);
            float directScale = Mathf.Lerp(0.58f, 1.48f, n);

            RenderSettings.ambientIntensity = _baseAmbientIntensity * ambientScale;
            RenderSettings.reflectionIntensity = _baseReflectionIntensity * reflectionScale;

            int count = Mathf.Min(_environmentLights.Count, _environmentLightBaseIntensity.Count);
            for (int i = 0; i < count; i++)
            {
                var l = _environmentLights[i];
                if (l == null) continue;
                l.intensity = _environmentLightBaseIntensity[i] * directScale;
            }

            float exposureBias = Mathf.Lerp(-0.18f, 0.34f, n);
            postFxController?.SetExposureBias(exposureBias);
        }
    }
}
