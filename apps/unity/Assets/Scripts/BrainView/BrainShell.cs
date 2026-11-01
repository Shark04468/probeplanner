using UnityEngine;

namespace BrainPlanner.BrainView
{
    [RequireComponent(typeof(MeshFilter), typeof(MeshRenderer))]
    public class BrainShell : MonoBehaviour
    {
        // Allen ontology root; MeshLoader applies the same CCF mapping as all regions.
        public const int StructureId = 997;
        public const int RequestedLod = 1;

        [Header("Appearance")]
        [Range(0f, 0.3f)] public float OpacityWhenEmpty = 0.10f;
        [Range(0f, 0.1f)] public float OpacityWhenPopulated = 0.04f;
        public Color ShellColor = new Color(0.1f, 0.25f, 0.40f, 1f);

        [System.Serializable]
        public struct SurfaceState
        {
            public string key;
            public string source;
            public int structureId;
            public string status;
            public bool visible;
            public int requestedLod;
            public int resolvedLod;
            public string error;
        }
        public System.Action<SurfaceState> OnSurfaceState;

        private MeshLoader _loader;
        private MeshFilter _filter;
        private MeshRenderer _renderer;
        private Material _material;
        private bool _requestedVisible;
        private bool _suppressed;
        private bool _loading;
        private bool _failed;
        private int _resolvedLod = -1;
        private string _error = "";

        void Awake()
        {
            if (_renderer != null) return;
            _filter = GetComponent<MeshFilter>();
            _filter.sharedMesh = null;
            _renderer = GetComponent<MeshRenderer>();
            _renderer.enabled = false;
            _renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            _renderer.receiveShadows = false;
            var shader = Shader.Find("BrainPlanner/AnatomicalSurface");
            if (shader == null)
            {
                FinishFailure("Missing anatomical surface shader");
                return;
            }
            _material = new Material(shader) { renderQueue = 2990 };
            _renderer.sharedMaterial = _material;
            SetRegionCount(0);
        }

        public void Initialize(MeshLoader loader)
        {
            Awake();
            _loader = loader;
        }

        public void SetVisible(bool visible)
        {
            Awake();
            _requestedVisible = visible;
            UpdateVisibility();
        }

        public void SetSuppressed(bool suppressed)
        {
            Awake();
            _suppressed = suppressed;
            UpdateVisibility();
        }

        private void UpdateVisibility()
        {
            bool wanted = _requestedVisible && !_suppressed;
            _renderer.enabled = wanted && !_failed && _filter.sharedMesh != null;
            if (wanted && _filter.sharedMesh == null && !_loading && !_failed)
            {
                if (_loader == null)
                {
                    FinishFailure("Atlas mesh loader unavailable");
                    return;
                }
                _loading = true;
                EmitState();
                _loader.LoadMeshLod(StructureId, RequestedLod, result =>
                {
                    if (this == null) return;
                    if (result.mesh == null || result.mesh.vertexCount == 0 || result.resolvedLod < 0)
                    {
                        FinishFailure("Atlas envelope mesh or LOD unavailable");
                        return;
                    }
                    _loading = false;
                    _filter.sharedMesh = result.mesh;
                    _resolvedLod = result.resolvedLod;
                    UpdateVisibility();
                }, error => { if (this != null) FinishFailure(error); });
                return;
            }
            EmitState();
        }

        private void FinishFailure(string error)
        {
            _loading = false;
            _failed = true;
            _error = error ?? "Atlas envelope unavailable";
            _resolvedLod = -1;
            _filter.sharedMesh = null;
            _renderer.enabled = false;
            EmitState();
        }

        private void EmitState()
        {
            OnSurfaceState?.Invoke(new SurfaceState {
                key = "atlas_envelope", source = "allen_ccf_structure_mesh", structureId = StructureId,
                status = _failed ? "failed" : _filter.sharedMesh != null ? "loaded" : _loading ? "loading" : "idle",
                visible = _renderer.enabled, requestedLod = RequestedLod, resolvedLod = _resolvedLod, error = _error,
            });
        }

        public void SyncClipping(RegionManager regions)
        {
            if (regions == null || _material == null) return;
            _material.SetFloat("_HemisphereFilter", regions.HemisphereFilter == "left" ? -1f :
                regions.HemisphereFilter == "right" ? 1f : 0f);
            _material.SetFloat("_SliceEnabled", regions.SliceEnabled ? 1f : 0f);
            _material.SetFloat("_SliceAxis", regions.SliceAxis);
            _material.SetFloat("_SliceCenter", regions.SliceCenter);
            _material.SetFloat("_SliceThickness", regions.SliceThicknessMm);
            _material.SetFloat("_SliceUseSlab", regions.SliceUseSlab ? 1f : 0f);
        }

        public void SetRegionCount(int count)
        {
            if (_material == null) return;
            var color = ShellColor;
            color.a = count > 0 ? OpacityWhenPopulated : OpacityWhenEmpty;
            _material.SetColor("_BaseColor", color);
        }

        void OnDestroy()
        {
            // MeshLoader owns the shared mesh; only this material belongs to the envelope.
            if (_material == null) return;
            if (Application.isPlaying) Destroy(_material);
            else DestroyImmediate(_material);
        }
    }
}
