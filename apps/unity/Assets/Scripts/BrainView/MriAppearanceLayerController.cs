using UnityEngine;

namespace BrainPlanner.BrainView
{
    /// <summary>
    /// MRI appearance layers for MRI+CCF hybrid mode.
    /// Runtime behavior:
    /// 1) Prefer mask-derived OBJ meshes from the Flask backend.
    /// 2) Proxy shells are available only as an explicit debug fallback; they are disabled by default
    ///    because they look like artificial ellipsoids rather than true MRI-derived anatomy.
    /// </summary>
    public class MriAppearanceLayerController : MonoBehaviour
    {
        [Header("Streaming")]
        public bool PreferMaskDerivedMeshes = true;
        [Range(0, 2)] public int SurfaceLod = 1;
        [Tooltip("Debug-only proxy ellipsoids. Keep disabled for clinical/MRI appearance because they are not mask-derived anatomy.")]
        public bool ShowProxyFallback = false;
        [Tooltip("Debug-only proxy ellipsoid rings. Real MRI mode should use the mask-derived surface meshes instead.")]
        public bool ShowProxyBoundaryRings = false;

        [Header("Fallback CCF-aligned MRI shell dimensions (mm)")]
        public Vector3 BrainSurfaceScale = new Vector3(8.8f, 7.0f, 12.3f);
        public Vector3 LayerOffset = new Vector3(0f, -3.1f, -0.5f);

        [Header("MRI layer visibility")]
        [Range(0.01f, 0.08f)] public float BoundaryLineWidth = 0.026f;
        [Range(0f, 1f)] public float BrainSurfaceAlpha = 0.10f;
        [Range(0f, 1f)] public float OuterBoundaryAlpha = 0.12f;
        [Range(0f, 1f)] public float SkullAlpha = 0.24f;
        public bool LowLoadInteractionMode = false;

        private MeshLoader _loader;

        private LayerState _brainSurface;
        private LayerState _outerBoundary;
        private LayerState _skull;

        [System.Serializable]
        public struct SurfaceState
        {
            public string key;
            public string status;
            public bool visible;
            public int requestedLod;
            public int resolvedLod;
            public string error;
        }
        public System.Action<SurfaceState> OnSurfaceState;

        private class LayerState
        {
            public string key;
            public string backendLayer;
            public GameObject meshObject;
            public GameObject fallbackObject;
            public Material material;
            public Material lineMaterial;
            public Color baseColor;
            public Color lineColor;
            public int renderQueue;
            public GameObject boundaryObject;
            public bool desiredVisible;
            public bool loadRequested;
            public bool meshLoaded;
            public bool meshFailed;
            public float desiredAlpha;
            public int resolvedLod = -1;
            public string error = "";
        }

        void Awake()
        {
            _loader = GetComponent<MeshLoader>() ?? gameObject.AddComponent<MeshLoader>();
            EnsureLayers();
            Apply(false, true, true, true, true, "ccf_only");
        }

        public static bool ShouldRenderNativeSurfaces(bool enabled, string renderer)
        {
            return enabled && !string.Equals(renderer, "threejs", System.StringComparison.OrdinalIgnoreCase);
        }

        public void Apply(
            bool enabled,
            bool showOuterBoundary,
            bool showSkull,
            bool showBrainSurface,
            bool showCcfLabels,
            string overlayMode
        )
        {
            EnsureLayers();
            bool overlay = enabled && string.Equals(
                overlayMode,
                "mri_ccf_overlay",
                System.StringComparison.OrdinalIgnoreCase
            );
            ApplyLayer(_brainSurface, enabled && showBrainSurface, overlay ? BrainSurfaceAlpha : BrainSurfaceAlpha * 0.74f);
            ApplyLayer(_outerBoundary, enabled && showOuterBoundary, overlay ? OuterBoundaryAlpha : OuterBoundaryAlpha * 0.78f);
            ApplyLayer(_skull, enabled && showSkull, overlay ? SkullAlpha : SkullAlpha * 0.74f);
        }

        public void SetInteractionQuality(string quality)
        {
            EnsureLayers();
            string raw = (quality ?? string.Empty).Trim().ToLowerInvariant();
            bool low = raw == "low" || raw == "drag" || raw == "interactive" || raw == "fast";
            if (LowLoadInteractionMode == low) return;
            LowLoadInteractionMode = low;
            ApplyLayer(_brainSurface, _brainSurface.desiredVisible, _brainSurface.desiredAlpha);
            ApplyLayer(_outerBoundary, _outerBoundary.desiredVisible, _outerBoundary.desiredAlpha);
            ApplyLayer(_skull, _skull.desiredVisible, _skull.desiredAlpha);
        }

        public void SyncClipping(RegionManager regions)
        {
            if (regions == null) return;
            EnsureLayers();
            float hemisphere = regions.HemisphereFilter == "left" ? -1f :
                (regions.HemisphereFilter == "right" ? 1f : 0f);
            foreach (var layer in new[] { _brainSurface, _outerBoundary, _skull })
            {
                var mat = layer.material;
                mat.SetFloat("_HemisphereFilter", hemisphere);
                mat.SetFloat("_SliceEnabled", regions.SliceEnabled ? 1f : 0f);
                mat.SetFloat("_SliceAxis", regions.SliceAxis);
                mat.SetFloat("_SliceCenter", regions.SliceCenter);
                mat.SetFloat("_SliceThickness", regions.SliceThicknessMm);
                mat.SetFloat("_SliceUseSlab", regions.SliceUseSlab ? 1f : 0f);
            }
        }

        private void EmitState(LayerState layer)
        {
            OnSurfaceState?.Invoke(new SurfaceState {
                key = layer.backendLayer,
                status = layer.meshFailed ? "failed" : layer.meshLoaded ? "loaded" :
                    layer.loadRequested ? "loading" : "idle",
                visible = layer.desiredVisible && layer.meshLoaded,
                requestedLod = SurfaceLod,
                resolvedLod = layer.resolvedLod,
                error = layer.error,
            });
        }

        private void EnsureLayers()
        {
            if (_brainSurface != null && _outerBoundary != null && _skull != null) return;
            _brainSurface = CreateLayer(
                "MRI_BrainSurface",
                "brain_surface",
                BrainSurfaceScale,
                new Color(0.30f, 0.92f, 1.0f, 0.42f),
                new Color(0.62f, 1.0f, 1.0f, 0.92f),
                3022
            );
            _outerBoundary = CreateLayer(
                "MRI_OuterTissueBoundary",
                "outer_tissue",
                BrainSurfaceScale * 1.08f,
                new Color(0.96f, 0.72f, 0.42f, 0.24f),
                new Color(1.0f, 0.88f, 0.48f, 0.84f),
                3021
            );
            _skull = CreateLayer(
                "MRI_SkullBoundary",
                "skull",
                BrainSurfaceScale * 1.16f,
                new Color(1.0f, 0.93f, 0.64f, 0.36f),
                new Color(1.0f, 0.98f, 0.72f, 0.96f),
                3020
            );
        }

        private LayerState CreateLayer(
            string objectName,
            string backendLayer,
            Vector3 fallbackScale,
            Color color,
            Color lineColor,
            int renderQueue
        )
        {
            var shader = Shader.Find("BrainPlanner/AnatomicalSurface");
            if (shader == null) throw new System.InvalidOperationException("Missing anatomical surface shader");
            var mat = new Material(shader);
            ConfigureTransparentMaterial(mat, color, renderQueue);
            var lineMat = new Material(Shader.Find("Sprites/Default") ?? Shader.Find("Universal Render Pipeline/Unlit") ?? Shader.Find("Standard"));
            ConfigureLineMaterial(lineMat, lineColor, renderQueue + 10);

            var meshGo = new GameObject(objectName + "_MaskMesh");
            meshGo.transform.SetParent(transform, false);
            meshGo.transform.localPosition = Vector3.zero;
            meshGo.transform.localRotation = Quaternion.identity;
            meshGo.transform.localScale = Vector3.one;
            meshGo.AddComponent<MeshFilter>();
            var meshRenderer = meshGo.AddComponent<MeshRenderer>();
            meshRenderer.sharedMaterial = mat;
            meshRenderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            meshRenderer.receiveShadows = false;
            meshGo.SetActive(false);

            var fallback = CreateShell(objectName + "_ProxyShell", fallbackScale, mat);
            fallback.SetActive(false);
            var boundary = CreateBoundaryRings(objectName + "_BoundaryLines", fallbackScale, lineMat);
            boundary.SetActive(false);

            return new LayerState
            {
                key = objectName,
                backendLayer = backendLayer,
                meshObject = meshGo,
                fallbackObject = fallback,
                material = mat,
                lineMaterial = lineMat,
                baseColor = color,
                lineColor = lineColor,
                renderQueue = renderQueue,
                boundaryObject = boundary,
            };
        }

        private GameObject CreateShell(string objectName, Vector3 scale, Material material)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            go.name = objectName;
            go.transform.SetParent(transform, false);
            go.transform.localPosition = LayerOffset;
            go.transform.localRotation = Quaternion.identity;
            go.transform.localScale = scale;
            var collider = go.GetComponent<Collider>();
            if (collider != null) Destroy(collider);
            var renderer = go.GetComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            return go;
        }

        private GameObject CreateBoundaryRings(string objectName, Vector3 scale, Material material)
        {
            var root = new GameObject(objectName);
            root.transform.SetParent(transform, false);
            root.transform.localPosition = LayerOffset;
            root.transform.localRotation = Quaternion.identity;
            root.transform.localScale = Vector3.one;

            AddRing(root, "AxialRing", scale.x, scale.z, Axis.Y, material);
            AddRing(root, "CoronalRing", scale.x, scale.y, Axis.Z, material);
            AddRing(root, "SagittalRing", scale.z, scale.y, Axis.X, material);
            return root;
        }

        private enum Axis { X, Y, Z }

        private void AddRing(GameObject parent, string name, float radiusA, float radiusB, Axis normalAxis, Material material)
        {
            const int count = 160;
            var go = new GameObject(name);
            go.transform.SetParent(parent.transform, false);
            var line = go.AddComponent<LineRenderer>();
            line.useWorldSpace = false;
            line.loop = true;
            line.positionCount = count;
            line.widthMultiplier = BoundaryLineWidth;
            line.numCornerVertices = 3;
            line.numCapVertices = 3;
            line.sharedMaterial = material;
            line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            line.receiveShadows = false;

            float a = radiusA * 0.5f;
            float b = radiusB * 0.5f;
            for (int i = 0; i < count; i++)
            {
                float t = (Mathf.PI * 2f * i) / count;
                float ca = Mathf.Cos(t);
                float sa = Mathf.Sin(t);
                Vector3 p;
                switch (normalAxis)
                {
                    case Axis.X:
                        p = new Vector3(0f, sa * b, ca * a);
                        break;
                    case Axis.Z:
                        p = new Vector3(ca * a, sa * b, 0f);
                        break;
                    default:
                        p = new Vector3(ca * a, 0f, sa * b);
                        break;
                }
                line.SetPosition(i, p);
            }
        }

        private void ApplyLayer(LayerState layer, bool visible, float alpha)
        {
            if (layer == null) return;
            layer.desiredVisible = visible;
            layer.desiredAlpha = alpha;
            // Dragging changes neither anatomy nor layer opacity.
            float effectiveAlpha = alpha;
            SetMaterialAlpha(layer.material, effectiveAlpha);
            SetMaterialAlpha(layer.lineMaterial, Mathf.Clamp01(effectiveAlpha * 2.4f));
            if (!visible)
            {
                if (layer.meshObject != null) layer.meshObject.SetActive(false);
                if (layer.fallbackObject != null) layer.fallbackObject.SetActive(false);
                if (layer.boundaryObject != null) layer.boundaryObject.SetActive(false);
                EmitState(layer);
                return;
            }

            if (PreferMaskDerivedMeshes && !layer.meshLoaded && !layer.meshFailed && !layer.loadRequested)
            {
                RequestMaskMesh(layer);
            }

            // Low-load interaction should reduce visual cost, not replace true MRI geometry with a proxy ellipsoid.
            bool showMesh = PreferMaskDerivedMeshes && layer.meshLoaded && layer.meshObject != null;
            bool showProxy = visible && !showMesh && ShowProxyFallback;
            if (layer.meshObject != null) layer.meshObject.SetActive(visible && showMesh);
            if (layer.fallbackObject != null) layer.fallbackObject.SetActive(showProxy);
            if (layer.boundaryObject != null) layer.boundaryObject.SetActive(showProxy && ShowProxyBoundaryRings);
            EmitState(layer);
        }

        private void RequestMaskMesh(LayerState layer)
        {
            if (_loader == null || layer == null) return;
            layer.loadRequested = true;
            EmitState(layer);
            _loader.LoadMriSurfaceMesh(
                layer.backendLayer,
                SurfaceLod,
                result =>
                {
                    var filter = layer.meshObject != null ? layer.meshObject.GetComponent<MeshFilter>() : null;
                    if (filter == null || result.mesh == null)
                    {
                        layer.meshFailed = true;
                        layer.loadRequested = false;
                        layer.error = "Missing parsed surface mesh";
                        EmitState(layer);
                        return;
                    }
                    filter.sharedMesh = result.mesh;
                    layer.meshLoaded = true;
                    layer.meshFailed = false;
                    layer.loadRequested = false;
                    layer.resolvedLod = result.resolvedLod;
                    layer.error = "";
                    ApplyLayer(layer, layer.desiredVisible, layer.desiredAlpha);
                },
                err =>
                {
                    Debug.LogWarning($"[MRI Appearance] mask mesh unavailable layer={layer.backendLayer}: {err}");
                    layer.meshFailed = true;
                    layer.loadRequested = false;
                    layer.error = err;
                    ApplyLayer(layer, layer.desiredVisible, layer.desiredAlpha);
                }
            );
        }

        private static void ConfigureTransparentMaterial(Material mat, Color color, int renderQueue)
        {
            if (mat == null) return;
            mat.color = color;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Surface")) mat.SetFloat("_Surface", 1f);
            if (mat.HasProperty("_Blend")) mat.SetFloat("_Blend", 0f);
            if (mat.HasProperty("_Cull")) mat.SetFloat("_Cull", 2f);
            if (mat.HasProperty("_ZWrite")) mat.SetFloat("_ZWrite", 0f);
            mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            mat.renderQueue = renderQueue;
        }

        private static void ConfigureLineMaterial(Material mat, Color color, int renderQueue)
        {
            if (mat == null) return;
            mat.color = color;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", color);
            if (mat.HasProperty("_Surface")) mat.SetFloat("_Surface", 1f);
            if (mat.HasProperty("_Blend")) mat.SetFloat("_Blend", 0f);
            if (mat.HasProperty("_ZWrite")) mat.SetFloat("_ZWrite", 0f);
            mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            mat.renderQueue = renderQueue;
        }

        private static void SetMaterialAlpha(Material mat, float alpha)
        {
            if (mat == null) return;
            var c = mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor") : mat.color;
            c.a = Mathf.Clamp01(alpha);
            mat.color = c;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", c);
        }
    }
}
