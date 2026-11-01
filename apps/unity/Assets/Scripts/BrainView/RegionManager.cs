using System.Collections;
using System.Collections.Generic;
using System;
using BrainPlanner.Data;
using UnityEngine;

namespace BrainPlanner.BrainView
{
    public class RegionManager : MonoBehaviour
    {
        [Header("Materials")]
        public Material OutlineMaterialTemplate;
        public Material FillMaterialTemplate;
        public Material FallbackMaterialTemplate;
        public Material RegionIdPickMaterialTemplate;

        [Header("Rendering")]
        [Range(0f, 1f)] public float FillOpacity = 0.36f;
        [Range(0f, 1f)] public float OutlineOpacityNormal = 0.12f;
        [Range(0f, 1f)] public float OutlineOpacitySelected = 0.88f;
        [Range(0f, 2f)] public float RimIntensitySelected = 1.35f;
        [Range(0f, 1f)] public float MinOutlineOpacityNormal = 0.03f;
        [Range(0f, 1f)] public float MinOutlineOpacitySelected = 0.10f;

        [Header("Offline LOD")]
        public bool UseOfflineLod = true;
        public bool UseOfflineLodForOutlines = false;
        public bool UseOfflineLodForFills = true;
        [Range(1, 2)] public int MaxOfflineLod = 2;
        [Range(0, 2)] public int OutlineRequestedLod = 1;
        [Range(64, 4000)] public int DenseAtlasThreshold = 360;
        [Range(0.25f, 1f)] public float DenseAtlasOutlineAttenuation = 0.55f;

        [Header("Picking")]
        public bool UseGpuRegionIdPicking = true;
        public bool AllowRaycastPickFallback = false;
        [Range(1, 4)] public int PickTextureScale = 1;
        [Range(8, 30)] public int FallbackPickLayer = 30;

        [Header("Transparency")]
        public bool UseWeightedOit = true;
        [Range(0.2f, 2.5f)] public float OitWeightScale = 1.1f;
        [Range(0.3f, 3f)] public float OitDepthWeightNear = 1.85f;
        [Range(0.05f, 1.2f)] public float OitDepthWeightFar = 0.24f;
        [Range(0.5f, 4f)] public float OitDepthWeightExp = 1.9f;

        [Header("MRI Appearance")]
        public bool UseMriAppearance = true;
        public bool MriShowOuterBoundary = true;
        public bool MriShowSkull = true;
        public bool MriShowBrainSurface = true;
        public bool MriShowCcfLabels = true;
        public string MriOverlayMode = "mri_ccf_overlay";
        [Range(0f, 1f)] public float MriAppearanceBlend = 0.96f;
        [Range(0f, 1f)] public float MriAppearanceSaturationFloor = 0.05f;
        [Range(0f, 1f)] public float MriAppearanceSaturationCeil = 0.28f;
        [Range(0f, 1f)] public float MriAppearanceValueFloor = 0.42f;
        [Range(0f, 1f)] public float MriAppearanceValueCeil = 0.90f;
        [Range(0f, 1f)] public float MriBoundaryMinAlpha = 0.24f;
        [Range(0f, 1f)] public float MriContextMinAlpha = 0.14f;
        [Range(0.4f, 2f)] public float MriBoundaryBrightness = 1.28f;
        [Range(0.4f, 2f)] public float MriBackgroundBrightness = 0.80f;

        [Header("Color Accessibility")]
        public bool EnableColorBlindSafeAdjust = true;
        [Range(0f, 1f)] public float ColorBlindSafeStrength = 0.62f;
        [Range(0f, 1f)] public float ColorBlindMinLuminanceDelta = 0.08f;

        [Header("Pinpoint Focus Style")]
        public bool UsePinpointFocusStyle = true;
        [Range(0.1f, 1f)] public float NonSelectedOutlineScale = 0.28f;
        [Range(0.1f, 1f)] public float NonSelectedFillScale = 0.82f;
        [Range(0f, 1f)] public float FocusFillOpacityFloor = 0.16f;
        [Range(0f, 1f)] public float RiskContextFillOpacityFloor = 0.12f;
        [Range(0.4f, 2f)] public float SelectedBrightness = 1.42f;
        [Range(0.4f, 2f)] public float UnselectedBrightness = 1.14f;
        [Range(0f, 1.6f)] public float SelectedEmissiveBoost = 0.24f;
        [Range(0f, 1.6f)] public float UnselectedEmissiveBoost = 0.10f;

        [Header("Interaction Performance")]
        public bool LowLoadInteractionMode = false;
        [Range(0.05f, 1f)] public float LowLoadNonFocusFillScale = 0.18f;
        [Range(0.05f, 1f)] public float LowLoadNonFocusOutlineScale = 0.45f;

        [Header("Aggregate Fill Guard")]
        public bool SuppressAggregateRegionFill = true;
        [Range(8, 1200)] public int AggregateFillSubregionThreshold = 96;
        [Range(20000, 25000000)] public int AggregateFillVoxelThreshold = 1200000;
        [Range(3f, 16f)] public float AggregateFillExtentThresholdMm = 8.8f;

        [Header("Aggregate Outline Guard")]
        public bool SuppressAggregateRegionOutline = true;
        [Range(8, 1200)] public int AggregateOutlineSubregionThreshold = 96;
        [Range(20000, 25000000)] public int AggregateOutlineVoxelThreshold = 1200000;

        [Header("Slice")]
        public bool SliceEnabled = false;
        [Range(0, 2)] public int SliceAxis = 1; // 0=x(sagittal), 1=y(horizontal), 2=z(coronal)
        public bool SliceUseSlab = false;
        [Range(0f, 2f)] public float SliceThicknessMm = 0.25f;
        public bool SliceSnapToBoundary = true;
        [Range(0f, 2f)] public float SliceSnapThresholdMm = 0.18f;
        [Range(-12f, 6f)] public float SliceCenter = 0f;
        [Range(-12f, 6f)] public float SliceY = 0f; // legacy alias for y-axis center
        [Range(-16f, 2f)] public float SliceYMin = -8.8f; // legacy y-range min
        [Range(-4f, 10f)] public float SliceYMax = 0.6f; // legacy y-range max

        [Header("Contact Overlay")]
        public string ContactDisplayMode = "standard"; // standard | slice_contact_persist | contact_only
        public string ContactDisplayStyle = "hybrid";  // outline | fill | hybrid
        [Range(0f, 2000f)] public float ContactMinLengthUm = 0f;
        [Range(0f, 1f)] public float ContactFillOpacity = 0.18f;
        [Range(0f, 2f)] public float ContactOutlineBoost = 1.12f;
        [Range(0f, 2f)] public float ContactRimBoost = 1.20f;

        [Header("Fallback Sphere")]
        public float FallbackSphereScale = 1.6f;
        public bool EnableOutlineFallbackProxy = false;

        private MeshLoader _loader;
        private readonly Dictionary<string, GameObject> _outlineObjects = new();
        private readonly Dictionary<string, Material> _outlineMats = new();
        private readonly Dictionary<string, GameObject> _fillObjects = new();
        private readonly Dictionary<string, Material> _fillMats = new();
        private readonly Dictionary<string, Material> _pickMats = new();
        private readonly Dictionary<int, string> _pickIdToRegion = new();
        private readonly Dictionary<string, int> _regionToPickId = new();
        private readonly List<RegionInfo> _regions = new();
        private readonly Dictionary<string, RegionInfo> _regionByKey = new();
        private readonly HashSet<string> _aggregateFillGuardKeys = new(System.StringComparer.OrdinalIgnoreCase)
        {
            "root", "grey", "CH", "CTX", "CTXpl", "Isocortex", "BS", "CNU", "HB", "IB", "CB", "CBX", "HEM", "fiber tracts"
        };
        private readonly HashSet<string> _aggregateOutlineGuardKeys = new(System.StringComparer.OrdinalIgnoreCase)
        {
            "root", "grey", "CH", "CTX", "CTXpl", "Isocortex", "BS", "CNU", "HB", "IB", "CB", "CBX", "HEM", "fiber tracts"
        };
        private readonly HashSet<string> _defaultContextKeys = new(System.StringComparer.OrdinalIgnoreCase)
        {
            "VISp", "SSp", "MOp", "MOs", "CA1", "DG", "CP", "VPM", "ACA", "MRN", "RSP", "VISl", "VISam", "VISpm", "PFC"
        };
        private readonly HashSet<string> _fillSuppressedNotified = new();
        private readonly HashSet<string> _sliceRangeContributorKeys = new();
        private readonly Dictionary<string, Bounds> _sliceBoundsByKey = new();
        private readonly List<float>[] _sliceBoundarySamples = { new List<float>(4096), new List<float>(4096), new List<float>(4096) };
        private Bounds _sliceBounds;
        private bool _sliceBoundsValid;
        private readonly HashSet<string> _contactKeys = new(System.StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, Vector4> _contactHemispheres = new(System.StringComparer.OrdinalIgnoreCase);

        [System.Serializable]
        public class ContactRegionHemispheres
        {
            public string key;
            public string[] hemispheres;
        }

        private HashSet<string> _selectedKeys = new();
        private bool _selectedOnly;
        private bool _clinicalFocusEnabled = true;
        private readonly HashSet<string> _focusCategories = new(System.StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> _focusGroups = new(System.StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> _focusKeys = new(System.StringComparer.OrdinalIgnoreCase);
        private string _hemisphereFilter = "all";

        private Camera _pickCamera;
        private RenderTexture _pickRt;
        private Texture2D _pickReadback;
        private int _nextPickId = 1;

        public System.Action<int, int> OnOutlineProgress;
        public System.Action<string, float, string, int, int> OnStreamStage;
        public System.Action<string> OnFillLoaded;
        public System.Action<string> OnFillFailed;
        public System.Action<string, string> OnFillSuppressed;
        public System.Action OnSliceChanged;
        private float _requestedSliceNormalized = 1f;
        private bool _requestedSliceEnabled;
        public bool SliceBoundsFinalized { get; private set; }

        private int _outlineLoaded;
        private int _outlineTotal;
        private int _outlineFailed;
        private int _outlineExcluded;
        public int OutlineFailed => _outlineFailed;
        public int OutlineExcluded => _outlineExcluded;
        private float _outlineOpacityNormalBase;
        private float _outlineOpacitySelectedBase;

        public struct RegionPickInfo
        {
            public string key;
            public string hemisphere;
            public Vector3 worldPoint;
        }

        public bool SelectedOnly => _selectedOnly;
        public string HemisphereFilter => _hemisphereFilter;
        public int RegionCount => _regions.Count;

        void Awake()
        {
            _loader = GetComponent<MeshLoader>() ?? gameObject.AddComponent<MeshLoader>();
            if (QualitySettings.antiAliasing < 4) QualitySettings.antiAliasing = 4;
            _outlineOpacityNormalBase = OutlineOpacityNormal;
            _outlineOpacitySelectedBase = OutlineOpacitySelected;
            ResetRegionCatalogToDefault();
            EnsurePickCamera();
        }

        void Start()
        {
            ConfigureDisplayCamera(Camera.main);
        }

        public void ConfigureDisplayCamera(Camera camera)
        {
            if (camera == null || camera == _pickCamera) return;
            camera.cullingMask &= ~(1 << ResolvePickLayer());
        }

        public void SetRegionCatalog(IEnumerable<RegionInfo> regions)
        {
            _regions.Clear();
            _regionByKey.Clear();

            if (regions != null)
            {
                foreach (var r in regions)
                {
                    if (r == null || string.IsNullOrEmpty(r.key) || r.structureId <= 0) continue;
                    if (_regionByKey.ContainsKey(r.key)) continue;
                    var normalized = CloneRegionInfo(r);
                    _regions.Add(normalized);
                    _regionByKey[normalized.key] = normalized;
                }
            }

            if (_regions.Count == 0)
            {
                ResetRegionCatalogToDefault();
            }
        }

        void OnDestroy()
        {
            foreach (var m in _outlineMats.Values) if (m != null) Destroy(m);
            foreach (var m in _fillMats.Values) if (m != null) Destroy(m);
            foreach (var m in _pickMats.Values) if (m != null) Destroy(m);
            if (_pickReadback != null) Destroy(_pickReadback);
            if (_pickRt != null)
            {
                _pickRt.Release();
                Destroy(_pickRt);
            }
            if (_pickCamera != null) Destroy(_pickCamera.gameObject);
        }

        public void LoadAllOutlines()
        {
            if (_regions.Count == 0) ResetRegionCatalogToDefault();
            ClearOutlineObjects();
            _outlineTotal = 0;
            _outlineLoaded = 0;
            _outlineFailed = 0;
            _outlineExcluded = 0;
            OnStreamStage?.Invoke("bootstrap", 0f, "Preparing region stream", _outlineLoaded, _outlineTotal);
            StartCoroutine(LoadOutlinesBatched());
        }

        public void ClearLoadedMeshes()
        {
            StopAllCoroutines();
            ClearOutlineObjects();
            foreach (var kv in _fillObjects)
            {
                if (kv.Value != null) Destroy(kv.Value);
            }
            foreach (var kv in _fillMats)
            {
                if (kv.Value != null) Destroy(kv.Value);
            }
            _fillObjects.Clear();
            _fillMats.Clear();
            _fillSuppressedNotified.Clear();
            _outlineTotal = 0;
            _outlineLoaded = 0;
            OnOutlineProgress?.Invoke(0, 0);
            OnStreamStage?.Invoke("idle", 0f, "Waiting for backend catalog", 0, 0);
        }

        public void SetSelected(IEnumerable<string> keys)
        {
            var next = new HashSet<string>(keys ?? new string[0]);
            _fillSuppressedNotified.RemoveWhere(k => !next.Contains(k));

            var toRemove = new List<string>();
            foreach (var key in _fillObjects.Keys)
            {
                if (!ShouldKeepFillForKey(key, next.Contains(key))) toRemove.Add(key);
            }
            foreach (var key in toRemove) RemoveFill(key);

            foreach (var kv in _outlineMats)
            {
                SetOutlineMaterialState(kv.Key, kv.Value, next.Contains(kv.Key), highlight: false);
            }

            _selectedKeys = next;
            foreach (var key in _selectedKeys)
            {
                if (!_fillObjects.ContainsKey(key)) StartCoroutine(LoadFill(key));
            }
            foreach (var key in _fillMats.Keys)
            {
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
            ApplyVisibilityFilters();
        }

        public void SetFillOpacity(float opacity)
        {
            FillOpacity = Mathf.Clamp01(opacity);
            foreach (var key in _fillMats.Keys)
            {
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
        }

        public void SetGlobalOpacity(float opacity)
        {
            float clamped = Mathf.Clamp01(opacity);
            SetFillOpacity(clamped);
            OutlineOpacityNormal = Mathf.Lerp(MinOutlineOpacityNormal, _outlineOpacityNormalBase, clamped);
            OutlineOpacitySelected = Mathf.Lerp(MinOutlineOpacitySelected, _outlineOpacitySelectedBase, clamped);
            foreach (var kv in _outlineMats)
            {
                SetOutlineMaterialState(kv.Key, kv.Value, _selectedKeys.Contains(kv.Key), highlight: false);
            }
        }

        public void SetWeightedOit(bool enabled, float weightScale = -1f)
        {
            UseWeightedOit = enabled;
            if (weightScale > 0f) OitWeightScale = weightScale;
            foreach (var key in _fillMats.Keys)
            {
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
        }

        public void SetInteractionQuality(string quality)
        {
            string raw = (quality ?? string.Empty).Trim().ToLowerInvariant();
            bool low = raw == "low" || raw == "drag" || raw == "interactive" || raw == "fast";
            if (LowLoadInteractionMode == low) return;
            LowLoadInteractionMode = low;
            ApplyVisibilityFilters();
            foreach (var kv in _outlineMats)
            {
                SetOutlineMaterialState(kv.Key, kv.Value, _selectedKeys.Contains(kv.Key), highlight: false);
            }
            foreach (var kv in _fillMats)
            {
                SetFillMaterialState(kv.Key, _selectedKeys.Contains(kv.Key));
            }
        }

        public void SetMriAppearance(bool enabled)
        {
            if (UseMriAppearance == enabled) return;
            UseMriAppearance = enabled;
            RefreshAppearanceMaterials();
            ApplyVisibilityFilters();
        }

        public void SetMriAppearance(
            bool enabled,
            bool showOuterBoundary,
            bool showSkull,
            bool showBrainSurface,
            bool showCcfLabels,
            string overlayMode
        )
        {
            bool changed =
                UseMriAppearance != enabled ||
                MriShowOuterBoundary != showOuterBoundary ||
                MriShowSkull != showSkull ||
                MriShowBrainSurface != showBrainSurface ||
                MriShowCcfLabels != showCcfLabels ||
                !string.Equals(MriOverlayMode, overlayMode ?? string.Empty, StringComparison.OrdinalIgnoreCase);
            UseMriAppearance = enabled;
            MriShowOuterBoundary = showOuterBoundary;
            MriShowSkull = showSkull;
            MriShowBrainSurface = showBrainSurface;
            MriShowCcfLabels = showCcfLabels;
            MriOverlayMode = string.IsNullOrWhiteSpace(overlayMode) ? "mri_ccf_overlay" : overlayMode.Trim();
            if (!changed) return;
            RefreshAppearanceMaterials();
            ApplyVisibilityFilters();
        }

        public void SetCoronalSlice(bool enabled, float normalized)
        {
            // Backward-compatible API used by older frontend snapshots.
            SetSlice("y", enabled, normalized, SliceThicknessMm, SliceUseSlab, SliceSnapToBoundary, SliceSnapThresholdMm);
        }

        public void SetSlice(
            string axis,
            bool enabled,
            float normalized,
            float thicknessMm,
            bool useSlab,
            bool snapToBoundary,
            float snapThresholdMm
        )
        {
            int axisIndex = ParseSliceAxis(axis);
            float t = Mathf.Clamp01(normalized);
            _requestedSliceNormalized = t;
            _requestedSliceEnabled = enabled;
            if (!_sliceBoundsValid)
            {
                RecomputeSliceRangeFromOutlines();
            }

            float axisMin = GetSliceAxisMin(axisIndex);
            float axisMax = GetSliceAxisMax(axisIndex);
            if (axisMax < axisMin)
            {
                float tmp = axisMax;
                axisMax = axisMin;
                axisMin = tmp;
            }

            float center = Mathf.Lerp(axisMin, axisMax, t);
            bool snapping = snapToBoundary && snapThresholdMm > 0f;
            if (snapping)
            {
                center = SnapSliceCenterToBoundary(axisIndex, center, snapThresholdMm);
            }

            SliceAxis = axisIndex;
            SliceUseSlab = useSlab && thicknessMm > 0.0005f;
            SliceThicknessMm = Mathf.Clamp(thicknessMm, 0f, 2f);
            SliceSnapToBoundary = snapToBoundary;
            SliceSnapThresholdMm = Mathf.Clamp(snapThresholdMm, 0f, 2f);
            SliceEnabled = enabled && t < 0.9995f;
            SliceCenter = center;
            if (SliceAxis == 1)
            {
                SliceY = center;
            }
            ApplySliceToAllMaterials();
            OnSliceChanged?.Invoke();
        }

        public void FinalizeSliceRange()
        {
            // Replay the request against the complete bounds, not early streaming geometry.
            SliceBoundsFinalized = true;
            SetSlice(AtlasSectionController.AxisName(SliceAxis), _requestedSliceEnabled,
                _requestedSliceNormalized, SliceThicknessMm, SliceUseSlab, SliceSnapToBoundary, SliceSnapThresholdMm);
        }

        public void SetContactProfile(string mode, string style, float minLengthUm, IEnumerable<string> regions,
            IEnumerable<ContactRegionHemispheres> regionHemispheres = null)
        {
            ContactDisplayMode = NormalizeContactDisplayMode(mode);
            ContactDisplayStyle = NormalizeContactDisplayStyle(style);
            ContactMinLengthUm = Mathf.Max(0f, minLengthUm);

            _contactKeys.Clear();
            _contactHemispheres.Clear();
            if (regions != null)
            {
                foreach (var raw in regions)
                {
                    string key = (raw ?? string.Empty).Trim();
                    if (!string.IsNullOrEmpty(key))
                    {
                        _contactKeys.Add(key);
                    }
                }
            }

            if (regionHemispheres != null)
            {
                foreach (var item in regionHemispheres)
                {
                    string key = item?.key?.Trim();
                    if (string.IsNullOrEmpty(key) || !_contactKeys.Contains(key) || item.hemispheres == null) continue;
                    _contactHemispheres.TryGetValue(key, out var sides);
                    foreach (string side in item.hemispheres)
                    {
                        if (side == "left") sides.x = 1f;
                        else if (side == "right") sides.y = 1f;
                        else if (side == "midline") sides.z = 1f;
                    }
                    _contactHemispheres[key] = sides;
                }
            }

            bool shouldKeepContactFill =
                ContactDisplayMode != "standard" &&
                ContactStyleShowsFill(ContactDisplayStyle);
            if (shouldKeepContactFill)
            {
                foreach (var key in _contactKeys)
                {
                    if (!_fillObjects.ContainsKey(key))
                    {
                        StartCoroutine(LoadFill(key));
                    }
                }
            }
            else
            {
                var staleFills = new List<string>();
                foreach (var key in _fillObjects.Keys)
                {
                    if (!ShouldKeepFillForKey(key, _selectedKeys.Contains(key))) staleFills.Add(key);
                }
                foreach (var key in staleFills) RemoveFill(key);
            }

            foreach (var kv in new List<KeyValuePair<string, Material>>(_outlineMats))
            {
                SetOutlineMaterialState(kv.Key, kv.Value, _selectedKeys.Contains(kv.Key), highlight: false);
            }
            foreach (var key in new List<string>(_fillMats.Keys))
            {
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
            ApplyVisibilityFilters();
            ApplySliceToAllMaterials();
        }

        public void SetSelectedOnly(bool selectedOnly)
        {
            _selectedOnly = selectedOnly;
            ApplyVisibilityFilters();
        }

        public void SetClinicalFocus(
            bool enabled,
            IEnumerable<string> categories,
            IEnumerable<string> groups = null,
            IEnumerable<string> keys = null
        )
        {
            _clinicalFocusEnabled = enabled;
            _focusCategories.Clear();
            _focusGroups.Clear();
            _focusKeys.Clear();
            if (categories != null)
            {
                foreach (var raw in categories)
                {
                    string cat = NormalizeCategory(raw);
                    if (!string.IsNullOrEmpty(cat)) _focusCategories.Add(cat);
                }
            }
            if (groups != null)
            {
                foreach (var raw in groups)
                {
                    string group = NormalizeGroup(raw);
                    if (!string.IsNullOrEmpty(group)) _focusGroups.Add(group);
                }
            }
            if (keys != null)
            {
                foreach (var raw in keys)
                {
                    string key = (raw ?? string.Empty).Trim();
                    if (!string.IsNullOrEmpty(key)) _focusKeys.Add(key);
                }
            }

            var staleFills = new List<string>();
            foreach (var key in _fillObjects.Keys)
            {
                if (!ShouldKeepFillForKey(key, _selectedKeys.Contains(key))) staleFills.Add(key);
            }
            foreach (var key in staleFills) RemoveFill(key);

            if (_clinicalFocusEnabled)
            {
                // Clinical default: selected + current system/risk context/contact regions get readable fill.
                // The full hierarchy remains outline-only unless explicitly selected.
                foreach (var key in _focusKeys)
                {
                    if (!_fillObjects.ContainsKey(key) && ShouldKeepFillForKey(key, _selectedKeys.Contains(key)))
                    {
                        StartCoroutine(LoadFill(key));
                    }
                }
            }

            foreach (var kv in _outlineMats)
            {
                SetOutlineMaterialState(kv.Key, kv.Value, _selectedKeys.Contains(kv.Key), highlight: false);
            }
            foreach (var key in _fillMats.Keys)
            {
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
            ApplyVisibilityFilters();
        }

        public void SetHemisphereFilter(string hemisphere)
        {
            _hemisphereFilter = NormalizeHemisphere(hemisphere);
            ApplyHemisphereMaterialFilter();
            ApplyVisibilityFilters();
        }

        public void HighlightRegion(string key, bool highlight)
        {
            if (_outlineMats.TryGetValue(key, out var mat))
            {
                SetOutlineMaterialState(key, mat, _selectedKeys.Contains(key), highlight);
            }
        }

        public bool TryPickRegionFromScreenPoint(Vector2 screenPoint, Camera cam, out RegionPickInfo pick)
        {
            pick = default;
            if (cam == null) return false;
            if (UseGpuRegionIdPicking && TryPickRegionWithIdBuffer(screenPoint, cam, out pick)) return true;
            if (UseGpuRegionIdPicking && !AllowRaycastPickFallback) return false;
            return TryPickRegionByRaycast(screenPoint, cam, out pick);
        }

        public bool TryGetRegionWorldPoint(string key, out Vector3 point, out string hemisphere)
        {
            point = Vector3.zero;
            hemisphere = "left";
            if (string.IsNullOrEmpty(key)) return false;

            if (_fillObjects.TryGetValue(key, out var fill) && fill != null)
            {
                point = fill.transform.position;
            }
            else if (_outlineObjects.TryGetValue(key, out var outline) && outline != null)
            {
                point = outline.transform.position;
            }
            else
            {
                var info = TryGetRegionInfo(key);
                if (info == null) return false;
                point = info.WorldPos;
            }

            hemisphere = point.x >= 0f ? "right" : "left";
            return true;
        }

        private IEnumerator LoadOutlinesBatched()
        {
            var ordered = new List<RegionInfo>(_regions.Count);
            _outlineFailed = 0;
            _outlineExcluded = 0;
            for (int i = 0; i < _regions.Count; i++)
            {
                var info = _regions[i];
                if (info == null) continue;
                if (ShouldSuppressOutlineByMetadata(info.key, info, out _))
                {
                    _outlineExcluded++;
                    continue;
                }
                ordered.Add(info);
            }
            ordered.Sort((a, b) =>
            {
                int sa = StreamPriority(a);
                int sb = StreamPriority(b);
                if (sa != sb) return sa.CompareTo(sb);
                int pa = OutlinePriority(a);
                int pb = OutlinePriority(b);
                if (pa != pb) return pa.CompareTo(pb);
                return a.structureId.CompareTo(b.structureId);
            });
            _outlineTotal = ordered.Count;
            _outlineLoaded = 0;
            SliceBoundsFinalized = false;
            OnOutlineProgress?.Invoke(_outlineLoaded, _outlineTotal);
            int preferred = _loader != null ? Mathf.Clamp(_loader.MaxConcurrent, 1, 8) : 4;
            int batchSize = Application.platform == RuntimePlatform.WebGLPlayer
                ? Mathf.Clamp(preferred, 2, 4)
                : Mathf.Clamp(preferred, 2, 10);
            if (ordered.Count >= 1000)
            {
                // Full-brain streaming can otherwise monopolize the WebGL main thread while
                // OBJ text is parsed. Keep the browser responsive even if streaming is slower.
                batchSize = Application.platform == RuntimePlatform.WebGLPlayer
                    ? Mathf.Clamp(preferred, 2, 4)
                    : Mathf.Clamp(preferred + 2, 6, 14);
            }
            int requestedLod = Mathf.Clamp(OutlineRequestedLod, 0, 2);
            if (UseOfflineLod)
            {
                if (ordered.Count >= 1000) requestedLod = Mathf.Max(requestedLod, 2);
                else if (ordered.Count >= 360) requestedLod = Mathf.Max(requestedLod, 1);
            }
            OnStreamStage?.Invoke("mesh_stream", 0.02f, "Streaming shell and coarse outlines", _outlineLoaded, _outlineTotal);

            for (int i = 0; i < ordered.Count; i += batchSize)
            {
                int remaining = Mathf.Min(batchSize, ordered.Count - i);
                var batch = ordered.GetRange(i, remaining);
                int done = 0;

                foreach (var info in batch)
                {
                    _loader.LoadMeshLod(
                        info.structureId,
                        requestedLod,
                        result =>
                        {
                            CreateOutlineObject(info.key, info.structureId, result.mesh, result.resolvedLod);
                            _outlineLoaded++;
                            OnOutlineProgress?.Invoke(_outlineLoaded, _outlineTotal);
                            done++;
                        },
                        _ =>
                        {
                            _outlineFailed++;
                            OnOutlineProgress?.Invoke(_outlineLoaded, _outlineTotal);
                            done++;
                        }
                    );
                }

                yield return new WaitUntil(() => done >= batch.Count);
                float pct = _outlineTotal > 0 ? (float)(_outlineLoaded + _outlineFailed) / _outlineTotal : 0f;
                string detail = pct < 0.55f
                    ? "Loading major systems"
                    : pct < 0.92f
                        ? "Loading subregions"
                        : "Finalizing render state";
                OnStreamStage?.Invoke("mesh_stream", pct, detail, _outlineLoaded, _outlineTotal);
                if (Application.platform == RuntimePlatform.WebGLPlayer)
                {
                    yield return null;
                    yield return new WaitForSeconds(0.01f);
                }
                else
                {
                    yield return null;
                }
                if (i > 0 && (i / batchSize) % 6 == 0)
                {
                    // Keep WebGL/UI responsive while streaming dense catalogs.
                    yield return null;
                }
            }
            FinalizeSliceRange();
            string finalDetail = $"Mesh stream finished: {_outlineLoaded} loaded, {_outlineFailed} failed, {_outlineExcluded} excluded by volume/aggregate policy";
            OnStreamStage?.Invoke(_outlineFailed == 0 ? "ready" : "partial", 1f, finalDetail, _outlineLoaded, _outlineTotal);
        }

        private void CreateOutlineFallback(string key, RegionInfo info)
        {
            if (!EnableOutlineFallbackProxy) return;
            if (_outlineObjects.ContainsKey(key) || info == null) return;
            if (ShouldSuppressOutlineByMetadata(key, info, out _)) return;
            var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            go.name = $"OutlineFallback_{key}";
            go.transform.SetParent(transform, false);
            go.transform.localPosition = info.WorldPos;
            float radius = Mathf.Max(0.14f, GetRegionRadius(key) * 0.45f);
            go.transform.localScale = Vector3.one * (radius * 2f);

            var mr = go.GetComponent<MeshRenderer>();
            var mat = CreateRuntimeMaterial(OutlineMaterialTemplate, "BrainPlanner/RegionOutline", "Universal Render Pipeline/Lit");
            var c = ResolveDisplayColor(info.color, info.category, info.groupKey, key, info.structureId);
            c.a = Mathf.Max(OutlineOpacityNormal, 0.12f);
            if (mat.HasProperty("_RegionColor")) mat.SetColor("_RegionColor", c);
            mat.color = c;
            ApplySliceToMaterial(mat, key, false);
            mr.sharedMaterial = mat;
            mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            mr.receiveShadows = false;

            int layer = LayerMask.NameToLayer("BrainOutline");
            if (layer >= 0) SetLayerRecursively(go, layer);

            var target = go.GetComponent<RegionHitTarget>() ?? go.AddComponent<RegionHitTarget>();
            target.RegionKey = key;

            _outlineObjects[key] = go;
            _outlineMats[key] = mat;
            RegisterSliceBoundsForKey(key, go);
            SetOutlineMaterialState(key, mat, _selectedKeys.Contains(key), false);
            ApplyVisibilityForKey(key);
        }

        private void ClearOutlineObjects()
        {
            foreach (var kv in _outlineObjects)
            {
                if (kv.Value != null) Destroy(kv.Value);
            }
            foreach (var kv in _outlineMats)
            {
                if (kv.Value != null) Destroy(kv.Value);
            }
            foreach (var kv in _pickMats)
            {
                if (kv.Value != null) Destroy(kv.Value);
            }
            _outlineObjects.Clear();
            _outlineMats.Clear();
            _pickMats.Clear();
            _pickIdToRegion.Clear();
            _regionToPickId.Clear();
            _nextPickId = 1;
            _sliceRangeContributorKeys.Clear();
            _sliceBoundsByKey.Clear();
            for (int i = 0; i < _sliceBoundarySamples.Length; i++)
            {
                _sliceBoundarySamples[i].Clear();
            }
            _sliceBoundsValid = false;
            SliceBoundsFinalized = false;
        }

        private void CreateOutlineObject(string key, int structureId, Mesh mesh, int resolvedLod)
        {
            if (_outlineObjects.ContainsKey(key) || mesh == null) return;
            var info = TryGetRegionInfo(key);
            if (info == null) return;
            if (ShouldSuppressOutlineByMetadata(key, info, out _)) return;

            var mat = CreateRuntimeMaterial(OutlineMaterialTemplate, "BrainPlanner/RegionOutline");
            var displayColor = ResolveDisplayColor(info.color, info.category, info.groupKey, key, structureId);
            var c = displayColor;
            c.a = OutlineOpacityNormal;
            mat.color = c;
            if (mat.HasProperty("_RegionColor")) mat.SetColor("_RegionColor", displayColor);
            if (mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", HemisphereFilterAsFloat());
            ApplySliceToMaterial(mat, key, false);

            var root = CreateLodObject(
                $"Outline_{key}",
                key,
                structureId,
                mesh,
                mat,
                "BrainOutline",
                true,
                true,
                UseOfflineLodForOutlines,
                Mathf.Clamp(resolvedLod, 0, 2)
            );
            _outlineObjects[key] = root;
            _outlineMats[key] = mat;
            RegisterSliceBoundsForKey(key, root);
            SetOutlineMaterialState(key, mat, _selectedKeys.Contains(key), false);
            ApplyVisibilityForKey(key);
        }

        private IEnumerator LoadFill(string key)
        {
            var info = TryGetRegionInfo(key);
            if (info == null)
            {
                OnFillFailed?.Invoke(key);
                yield break;
            }
            if (ShouldSuppressFillByMetadata(key, info, out string metadataReason))
            {
                if (_fillSuppressedNotified.Add(key))
                {
                    OnFillSuppressed?.Invoke(key, metadataReason);
                }
                yield break;
            }
            bool done = false;
            Mesh mesh = null;
            bool failed = false;
            int resolvedLod = -1;
            _loader.LoadMeshLod(
                info.structureId,
                0,
                r =>
                {
                    mesh = r.mesh;
                    resolvedLod = r.resolvedLod;
                    done = true;
                },
                _ =>
                {
                    failed = true;
                    done = true;
                }
            );
            yield return new WaitUntil(() => done);
            if (!ShouldKeepFillForKey(key, _selectedKeys.Contains(key))) yield break;
            if (failed || mesh == null)
            {
                OnFillFailed?.Invoke(key);
                yield break;
            }

            if (ShouldSuppressFillByMesh(key, mesh, out string meshReason))
            {
                if (_fillSuppressedNotified.Add(key))
                {
                    OnFillSuppressed?.Invoke(key, meshReason);
                }
                yield break;
            }

            CreateFillObject(key, info.structureId, mesh, info, resolvedLod);
            _fillSuppressedNotified.Remove(key);
            OnFillLoaded?.Invoke(key);
        }

        private void CreateFillObject(string key, int structureId, Mesh mesh, RegionInfo info, int resolvedLod)
        {
            if (_fillObjects.ContainsKey(key) || mesh == null) return;
            var mat = CreateRuntimeMaterial(FillMaterialTemplate, "BrainPlanner/RegionFill");
            if (info != null)
            {
                var baseColor = ResolveDisplayColor(info.color, info.category, info.groupKey, key, structureId);
                baseColor.a = Mathf.Clamp01(FillOpacity);
                if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", baseColor);
                mat.color = baseColor;
                if (mat.HasProperty("_RimColor"))
                {
                    var rim = Color.Lerp(baseColor, Color.white, 0.40f);
                    rim.a = 1f;
                    mat.SetColor("_RimColor", rim);
                }
            }
            if (mat.HasProperty("_DepthWeightNear")) mat.SetFloat("_DepthWeightNear", OitDepthWeightNear);
            if (mat.HasProperty("_DepthWeightFar")) mat.SetFloat("_DepthWeightFar", OitDepthWeightFar);
            if (mat.HasProperty("_DepthWeightExp")) mat.SetFloat("_DepthWeightExp", OitDepthWeightExp);
            var root = CreateLodObject(
                $"Fill_{key}",
                key,
                structureId,
                mesh,
                mat,
                "BrainFill",
                true,
                false,
                UseOfflineLodForFills,
                Mathf.Clamp(resolvedLod, 0, 2)
            );
            _fillObjects[key] = root;
            _fillMats[key] = mat;
            SetFillMaterialState(key, true);
            ApplyVisibilityForKey(key);
        }

        private GameObject CreateLodObject(
            string objectName,
            string regionKey,
            int structureId,
            Mesh sourceMesh,
            Material sharedMaterial,
            string layerName,
            bool addCollider,
            bool createPickProxy,
            bool attachOfflineLods,
            int baseResolvedLod
        )
        {
            var root = new GameObject(objectName);
            root.transform.SetParent(transform, false);
            var lodRenderers = new List<Renderer>();
            var group = root.AddComponent<LODGroup>();

            var lod0 = CreateLodLevel(root.transform, $"LOD{baseResolvedLod}", sourceMesh, sharedMaterial, layerName, regionKey, addCollider);
            lodRenderers.Add(lod0);
            ApplyLodGroup(group, lodRenderers);

            if (UseOfflineLod && attachOfflineLods)
            {
                StartCoroutine(
                    AttachOfflineLods(
                        root.transform,
                        structureId,
                        regionKey,
                        sharedMaterial,
                        layerName,
                        lodRenderers,
                        group,
                        Mathf.Clamp(baseResolvedLod, 0, 2)
                    )
                );
            }
            if (createPickProxy)
            {
                CreatePickProxy(root.transform, regionKey, sourceMesh);
            }
            return root;
        }

        private IEnumerator AttachOfflineLods(
            Transform root,
            int structureId,
            string regionKey,
            Material mat,
            string layerName,
            List<Renderer> lodRenderers,
            LODGroup group,
            int baseResolvedLod
        )
        {
            int max = Mathf.Clamp(MaxOfflineLod, 1, 2);
            int startLod = Mathf.Max(1, baseResolvedLod + 1);
            for (int reqLod = 1; reqLod <= max; reqLod++)
            {
                if (reqLod < startLod) continue;
                bool done = false;
                MeshLoader.MeshLodResult result = default;
                _loader.LoadMeshLod(structureId, reqLod, r => { result = r; done = true; }, _ => { done = true; });
                yield return new WaitUntil(() => done);
                if (result.mesh == null) continue;
                if (ContainsMesh(lodRenderers, result.mesh)) continue;
                var r1 = CreateLodLevel(root, $"LOD{result.resolvedLod}", result.mesh, mat, layerName, regionKey, false);
                lodRenderers.Add(r1);
                ApplyLodGroup(group, lodRenderers);
            }
        }

        private static bool ContainsMesh(List<Renderer> renderers, Mesh mesh)
        {
            foreach (var r in renderers)
            {
                var f = r != null ? r.GetComponent<MeshFilter>() : null;
                if (f != null && f.sharedMesh == mesh) return true;
            }
            return false;
        }

        private static MeshRenderer CreateLodLevel(Transform parent, string levelName, Mesh mesh, Material material, string layerName, string regionKey, bool addCollider)
        {
            var go = new GameObject(levelName);
            go.transform.SetParent(parent, false);
            var mf = go.AddComponent<MeshFilter>();
            mf.sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            mr.sharedMaterial = material;
            mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            mr.receiveShadows = false;
            int layer = LayerMask.NameToLayer(layerName);
            if (layer >= 0) SetLayerRecursively(go, layer);
            if (addCollider)
            {
                var col = go.AddComponent<MeshCollider>();
                col.sharedMesh = mesh;
                col.convex = false;
                var hit = go.AddComponent<RegionHitTarget>();
                hit.RegionKey = regionKey;
            }
            return mr;
        }

        private static void ApplyLodGroup(LODGroup group, List<Renderer> renderers)
        {
            if (group == null || renderers.Count == 0) return;
            if (renderers.Count == 1)
            {
                group.SetLODs(new[] { new LOD(0.01f, new[] { renderers[0] }) });
                group.RecalculateBounds();
                return;
            }
            var lods = new List<LOD>
            {
                new LOD(0.60f, new[] { renderers[0] }),
                new LOD(0.28f, new[] { renderers[1] }),
            };
            if (renderers.Count >= 3) lods.Add(new LOD(0.08f, new[] { renderers[2] }));
            lods.Add(new LOD(0.01f, new Renderer[0]));
            group.SetLODs(lods.ToArray());
            group.RecalculateBounds();
        }

        private void CreatePickProxy(Transform parent, string regionKey, Mesh mesh)
        {
            if (mesh == null || _pickMats.ContainsKey(regionKey)) return;
            int id = EnsurePickId(regionKey);
            int pickLayer = ResolvePickLayer();
            var go = new GameObject("PickProxy");
            go.transform.SetParent(parent, false);
            var mf = go.AddComponent<MeshFilter>();
            mf.sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            var mat = CreateRuntimeMaterial(RegionIdPickMaterialTemplate, "BrainPlanner/RegionIdPick", "Unlit/Color");
            if (mat.HasProperty("_IdColor")) mat.SetColor("_IdColor", EncodePickColor(id));
            else mat.color = EncodePickColor(id);
            if (mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", HemisphereFilterAsFloat());
            ApplySliceToMaterial(mat, regionKey, ContactStyleShowsFill(ContactDisplayStyle));
            mr.sharedMaterial = mat;
            mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            mr.receiveShadows = false;
            SetLayerRecursively(go, pickLayer);
            _pickMats[regionKey] = mat;
        }

        private int EnsurePickId(string regionKey)
        {
            if (_regionToPickId.TryGetValue(regionKey, out int id)) return id;
            id = _nextPickId++;
            _regionToPickId[regionKey] = id;
            _pickIdToRegion[id] = regionKey;
            return id;
        }

        private void EnsurePickCamera()
        {
            if (_pickCamera != null) return;
            var go = new GameObject("RegionPickCamera");
            go.transform.SetParent(transform, false);
            go.hideFlags = HideFlags.DontSave;
            _pickCamera = go.AddComponent<Camera>();
            _pickCamera.enabled = false;
            _pickCamera.clearFlags = CameraClearFlags.SolidColor;
            _pickCamera.backgroundColor = Color.black;
            _pickCamera.allowHDR = false;
            _pickCamera.allowMSAA = false;
            _pickCamera.cullingMask = 1 << ResolvePickLayer();
        }

        private int ResolvePickLayer()
        {
            int layer = LayerMask.NameToLayer("RegionPick");
            return layer >= 0 ? layer : Mathf.Clamp(FallbackPickLayer, 8, 30);
        }

        private void EnsurePickBuffers(int width, int height)
        {
            int scale = Mathf.Max(1, PickTextureScale);
            int w = Mathf.Max(16, width / scale);
            int h = Mathf.Max(16, height / scale);
            if (_pickRt != null && _pickRt.width == w && _pickRt.height == h) return;
            if (_pickRt != null)
            {
                _pickRt.Release();
                Destroy(_pickRt);
            }
            _pickRt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Linear)
            {
                filterMode = FilterMode.Point,
                wrapMode = TextureWrapMode.Clamp,
                antiAliasing = 1,
            };
            _pickRt.Create();
            if (_pickReadback == null) _pickReadback = new Texture2D(1, 1, TextureFormat.RGBA32, false, true);
        }

        private bool TryPickRegionWithIdBuffer(Vector2 screenPoint, Camera cam, out RegionPickInfo pick)
        {
            pick = default;
            if (_pickIdToRegion.Count == 0) return false;
            EnsurePickCamera();
            EnsurePickBuffers(cam.pixelWidth, cam.pixelHeight);
            if (_pickRt == null || _pickReadback == null || _pickCamera == null) return false;

            _pickCamera.CopyFrom(cam);
            _pickCamera.enabled = false;
            _pickCamera.allowHDR = false;
            _pickCamera.allowMSAA = false;
            _pickCamera.clearFlags = CameraClearFlags.SolidColor;
            _pickCamera.backgroundColor = Color.black;
            _pickCamera.cullingMask = 1 << ResolvePickLayer();
            _pickCamera.targetTexture = _pickRt;
            _pickCamera.Render();

            int scale = Mathf.Max(1, PickTextureScale);
            int px = Mathf.Clamp(Mathf.RoundToInt(screenPoint.x / scale), 0, _pickRt.width - 1);
            int py = Mathf.Clamp(Mathf.RoundToInt(screenPoint.y / scale), 0, _pickRt.height - 1);

            var prev = RenderTexture.active;
            RenderTexture.active = _pickRt;
            _pickReadback.ReadPixels(new Rect(px, py, 1, 1), 0, 0, false);
            _pickReadback.Apply(false, false);
            Color32 c = _pickReadback.GetPixel(0, 0);
            RenderTexture.active = prev;

            int id = DecodePickColor(c);
            if (id <= 0 || !_pickIdToRegion.TryGetValue(id, out string key)) return false;

            if (!TryRaycastForRegion(screenPoint, cam, key, out Vector3 hitPoint, out string hemi))
            {
                var info = TryGetRegionInfo(key);
                hitPoint = info != null ? info.WorldPos : Vector3.zero;
                hemi = hitPoint.x >= 0 ? "right" : "left";
            }

            if (_hemisphereFilter != "all" && hemi != _hemisphereFilter) return false;
            pick = new RegionPickInfo { key = key, hemisphere = hemi, worldPoint = hitPoint };
            return true;
        }

        private bool TryPickRegionByRaycast(Vector2 screenPoint, Camera cam, out RegionPickInfo pick)
        {
            pick = default;
            if (!TryRaycastForRegion(screenPoint, cam, null, out Vector3 hitPoint, out string hemi, out string key)) return false;
            if (_hemisphereFilter != "all" && hemi != _hemisphereFilter) return false;
            pick = new RegionPickInfo { key = key, hemisphere = hemi, worldPoint = hitPoint };
            return true;
        }

        private bool TryRaycastForRegion(Vector2 screenPoint, Camera cam, string expectedRegionKey, out Vector3 hitPoint, out string hemi)
        {
            return TryRaycastForRegion(screenPoint, cam, expectedRegionKey, out hitPoint, out hemi, out _);
        }

        private bool TryRaycastForRegion(Vector2 screenPoint, Camera cam, string expectedRegionKey, out Vector3 hitPoint, out string hemi, out string key)
        {
            hitPoint = Vector3.zero;
            hemi = "left";
            key = null;
            var ray = cam.ScreenPointToRay(screenPoint);
            int mask = GetBrainLayerMask();
            if (mask == 0) mask = ~0;
            var hits = Physics.RaycastAll(ray, 512f, mask);
            if (hits == null || hits.Length == 0) return false;
            System.Array.Sort(hits, (a, b) => a.distance.CompareTo(b.distance));
            for (int i = 0; i < hits.Length; i++)
            {
                var hit = hits[i];
                var target = hit.collider.GetComponent<RegionHitTarget>() ?? hit.collider.GetComponentInParent<RegionHitTarget>();
                if (target == null || string.IsNullOrEmpty(target.RegionKey)) continue;
                if (!string.IsNullOrEmpty(expectedRegionKey) && target.RegionKey != expectedRegionKey) continue;
                key = target.RegionKey;
                hitPoint = hit.point;
                hemi = hit.point.x >= 0f ? "right" : "left";
                return true;
            }
            return false;
        }

        private static Color EncodePickColor(int id)
        {
            return new Color((id & 255) / 255f, ((id >> 8) & 255) / 255f, ((id >> 16) & 255) / 255f, 1f);
        }

        private static int DecodePickColor(Color32 c)
        {
            return c.r | (c.g << 8) | (c.b << 16);
        }

        private void CreateFallbackSphere(string key, RegionInfo info)
        {
            if (_fillObjects.ContainsKey(key)) return;
            var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            go.name = $"Fallback_{key}";
            go.transform.SetParent(transform, false);
            go.transform.localPosition = info.WorldPos;
            go.transform.localScale = Vector3.one * GetRegionRadius(key) * FallbackSphereScale * 2f;
            var mr = go.GetComponent<MeshRenderer>();
            var mat = CreateRuntimeMaterial(FallbackMaterialTemplate, "Universal Render Pipeline/Lit");
            var c = ResolveDisplayColor(info.color, info.category, info.groupKey, key, info.structureId);
            c.a = Mathf.Clamp01(FillOpacity);
            mat.color = c;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c);
            ApplySliceToMaterial(mat, key, true);
            mr.sharedMaterial = mat;
            var target = go.AddComponent<RegionHitTarget>();
            target.RegionKey = key;
            _fillObjects[key] = go;
            _fillMats[key] = mat;
            ApplyVisibilityForKey(key);
            ApplyHemisphereMaterialFilter();
        }

        private void RemoveFill(string key)
        {
            if (_fillObjects.TryGetValue(key, out var go)) { Destroy(go); _fillObjects.Remove(key); }
            if (_fillMats.TryGetValue(key, out var mat)) { Destroy(mat); _fillMats.Remove(key); }
        }

        private void ApplyVisibilityFilters()
        {
            foreach (var key in _outlineObjects.Keys) ApplyVisibilityForKey(key);
            foreach (var key in _fillObjects.Keys) ApplyVisibilityForKey(key);
        }

        private void ApplyHemisphereMaterialFilter()
        {
            float hemi = HemisphereFilterAsFloat();
            foreach (var mat in _outlineMats.Values)
            {
                if (mat != null && mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", hemi);
            }
            foreach (var mat in _fillMats.Values)
            {
                if (mat != null && mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", hemi);
            }
            foreach (var mat in _pickMats.Values)
            {
                if (mat != null && mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", hemi);
            }
        }

        private float HemisphereFilterAsFloat()
        {
            if (_hemisphereFilter == "left") return -1f;
            if (_hemisphereFilter == "right") return 1f;
            return 0f;
        }

        private void ApplyVisibilityForKey(string key)
        {
            bool selected = _selectedKeys.Contains(key);
            bool focusMatch = IsFocusMatch(key, selected);
            bool isContact = IsContactKey(key);
            RegionInfo info = TryGetRegionInfo(key);
            bool mriBoundary = UseMriAppearance && IsMriBoundaryRegion(key, info);
            bool mriContext = UseMriAppearance && IsMriRiskContextRegion(key, info);
            bool baseVisible = (!_selectedOnly || selected) && focusMatch;
            if (!_selectedOnly && UseMriAppearance)
            {
                // MRI appearance should be structurally different from Allen-only appearance:
                // keep boundary/context structures visible as anatomical anchor layers.
                if (mriBoundary || mriContext) baseVisible = true;
                if (!MriShowCcfLabels)
                {
                    baseVisible = selected || isContact;
                }
            }
            bool keepContactOutline =
                isContact &&
                ContactDisplayMode != "standard" &&
                ContactStyleShowsOutline(ContactDisplayStyle);
            bool keepContactFill =
                isContact &&
                ContactDisplayMode != "standard" &&
                ContactStyleShowsFill(ContactDisplayStyle);
            bool keepFocusFill = IsFocusFillKey(key, info, selected);

            bool outlineVisible;
            bool fillVisible;

            switch (ContactDisplayMode)
            {
                case "contact_only":
                    outlineVisible = keepContactOutline;
                    fillVisible = keepContactFill;
                    break;
                case "slice_contact_persist":
                    outlineVisible = baseVisible || keepContactOutline;
                    fillVisible = keepContactFill || selected || (baseVisible && keepFocusFill);
                    break;
                default:
                    outlineVisible = baseVisible;
                    fillVisible = selected || (baseVisible && keepFocusFill);
                    break;
            }

            if (_outlineObjects.TryGetValue(key, out var outline)) outline.SetActive(outlineVisible);
            if (_fillObjects.TryGetValue(key, out var fill)) fill.SetActive(fillVisible);
        }

        private static string NormalizeContactDisplayMode(string mode)
        {
            string raw = (mode ?? string.Empty).Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(raw)) return "standard";
            if (raw == "contact_only" || raw == "contact-only" || raw == "only_contact" || raw == "only")
            {
                return "contact_only";
            }
            if (
                raw == "slice_contact_persist" ||
                raw == "slice-contact-persist" ||
                raw == "slice_contact" ||
                raw == "slice+contact" ||
                raw == "persist" ||
                raw == "contact_persist"
            )
            {
                return "slice_contact_persist";
            }
            return "standard";
        }

        private static string NormalizeContactDisplayStyle(string style)
        {
            string raw = (style ?? string.Empty).Trim().ToLowerInvariant();
            if (raw == "outline") return "outline";
            if (raw == "fill") return "fill";
            return "hybrid";
        }

        private bool IsContactKey(string key)
        {
            return !string.IsNullOrEmpty(key) && _contactKeys.Contains(key);
        }

        private static bool ContactStyleShowsOutline(string style)
        {
            return style != "fill";
        }

        private static bool ContactStyleShowsFill(string style)
        {
            return style != "outline";
        }

        private bool ShouldBypassSliceForContact(string key, bool isFill)
        {
            if (!SliceEnabled || !IsContactKey(key)) return false;
            if (!_contactHemispheres.TryGetValue(key, out var sides) || sides == Vector4.zero) return false;
            if (ContactDisplayMode == "standard") return false;
            if (isFill && !ContactStyleShowsFill(ContactDisplayStyle)) return false;
            if (!isFill && !ContactStyleShowsOutline(ContactDisplayStyle)) return false;
            return true;
        }

        private bool IsFocusMatch(string key, bool selected)
        {
            if (!_clinicalFocusEnabled) return true;
            if (selected) return true;
            bool hasExplicitFocus = _focusCategories.Count > 0 || _focusGroups.Count > 0 || _focusKeys.Count > 0;
            if (!hasExplicitFocus)
            {
                // Keep a small default context when frontend focus payload is not yet available.
                if (_selectedKeys.Count > 0) return false;
                return _defaultContextKeys.Contains(key);
            }
            if (_focusKeys.Contains(key)) return true;
            if (_regionByKey.TryGetValue(key, out var info))
            {
                string cat = NormalizeCategory(info?.category);
                if (!string.IsNullOrEmpty(cat) && _focusCategories.Contains(cat)) return true;
                string group = NormalizeGroup(info?.groupKey);
                if (string.IsNullOrEmpty(group)) group = NormalizeGroup(info?.groupName);
                if (string.IsNullOrEmpty(group)) group = NormalizeGroup(info?.groupNameCn);
                if (!string.IsNullOrEmpty(group) && _focusGroups.Contains(group)) return true;
                return false;
            }
            return false;
        }

        private bool IsFocusFillKey(string key, RegionInfo info, bool selected)
        {
            if (selected) return true;
            if (!_clinicalFocusEnabled || _selectedOnly || string.IsNullOrEmpty(key)) return false;
            if (IsContactKey(key)) return true;
            if (!_focusKeys.Contains(key)) return false;
            if (info == null) info = TryGetRegionInfo(key);
            if (info == null) return true;

            string category = NormalizeCategory(info.category);
            if (category == "pathway" || category == "nerve_fissure") return false;

            string token = string.Join("|", new[]
            {
                info.key ?? key,
                info.category ?? string.Empty,
                info.groupKey ?? string.Empty,
                info.groupName ?? string.Empty,
                info.groupNameCn ?? string.Empty,
                info.displayName ?? string.Empty,
            }).ToLowerInvariant();
            bool isFineObstructor =
                token.Contains("fiber") ||
                token.Contains("tract") ||
                token.Contains("nerve") ||
                token.Contains("sulcus") ||
                token.Contains("fissure") ||
                token.Contains("groove");
            if (isFineObstructor)
            {
                // Fine tracts/sulci/nerves remain hidden unless they are risk context or directly selected/contacted.
                return IsMriRiskContextRegion(key, info);
            }
            return true;
        }

        private bool ShouldKeepFillForKey(string key, bool selected)
        {
            if (selected) return true;
            if (string.IsNullOrEmpty(key)) return false;
            bool contactFill =
                ContactDisplayMode != "standard" &&
                ContactStyleShowsFill(ContactDisplayStyle) &&
                _contactKeys.Contains(key);
            if (contactFill) return true;
            if (!_clinicalFocusEnabled || _selectedOnly) return false;
            if (!_focusKeys.Contains(key)) return false;
            return IsFocusFillKey(key, TryGetRegionInfo(key), false);
        }

        private static string NormalizeCategory(string value)
        {
            string raw = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(raw)) return "atlas";
            if (raw.Contains("visual") || raw == "vis") return "visual";
            if (raw.Contains("hippocampus") || raw.Contains("hippo")) return "hippocampus";
            if (raw.Contains("thalam")) return "thalamus";
            if (raw.Contains("frontal") || raw.Contains("isocortex")) return "frontal";
            if (raw.Contains("midbrain") || raw.Contains("tect")) return "midbrain";
            if (raw.Contains("basal") || raw.Contains("striat") || raw.Contains("ganglia")) return "basal_ganglia";
            return raw;
        }

        private static string NormalizeGroup(string value)
        {
            string raw = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (string.IsNullOrEmpty(raw)) return string.Empty;
            if (raw.Contains("visual") || raw.Contains("视")) return "visual";
            if (raw.Contains("hippocamp") || raw.Contains("海马")) return "hippocampus";
            if (raw.Contains("thalam") || raw.Contains("丘脑")) return "thalamus";
            if (raw.Contains("frontal") || raw.Contains("额叶") || raw.Contains("isocortex")) return "frontal";
            if (raw.Contains("midbrain") || raw.Contains("中脑")) return "midbrain";
            if (raw.Contains("basal") || raw.Contains("striat") || raw.Contains("ganglia") || raw.Contains("基底")) return "basal_ganglia";
            return raw.Replace(" ", "_");
        }

        private static bool IsMriBoundaryRegion(string key, RegionInfo info)
        {
            string token = string.Join("|", new[]
            {
                key ?? string.Empty,
                info?.category ?? string.Empty,
                info?.groupKey ?? string.Empty,
                info?.groupName ?? string.Empty,
                info?.groupNameCn ?? string.Empty,
                info?.displayName ?? string.Empty,
            }).ToLowerInvariant();
            return token.Contains("skull")
                || token.Contains("outer_tissue")
                || token.Contains("outer tissue")
                || token.Contains("cortex_outside")
                || token.Contains("outside_cortex")
                || token.Contains("outside")
                || token.Contains("boundary")
                || token.Contains("scalp")
                || token.Contains("bone")
                || token.Contains("颅骨")
                || token.Contains("外层")
                || token.Contains("脑外");
        }

        private static bool IsMriRiskContextRegion(string key, RegionInfo info)
        {
            string token = string.Join("|", new[]
            {
                key ?? string.Empty,
                info?.category ?? string.Empty,
                info?.groupKey ?? string.Empty,
                info?.groupName ?? string.Empty,
                info?.groupNameCn ?? string.Empty,
                info?.displayName ?? string.Empty,
            }).ToLowerInvariant();
            return token.Contains("ventricle")
                || token.Contains("vessel")
                || token.Contains("sulcus")
                || token.Contains("aq")
                || token.Contains("v3")
                || token.Contains("v4")
                || token.Contains("vs")
                || token.Contains("ivf")
                || token.Contains("chpl")
                || token.Contains("脑室")
                || token.Contains("血管")
                || token.Contains("脑沟");
        }

        private RegionInfo CloneRegionInfo(RegionInfo src)
        {
            if (src == null) return null;
            var sourceColor = src.color;
            if (
                float.IsNaN(sourceColor.r) || float.IsInfinity(sourceColor.r) ||
                float.IsNaN(sourceColor.g) || float.IsInfinity(sourceColor.g) ||
                float.IsNaN(sourceColor.b) || float.IsInfinity(sourceColor.b)
            )
            {
                sourceColor = CategoryFallbackColor(src.category, src.groupKey, src.key, src.structureId);
            }
            sourceColor.a = 1f;
            return new RegionInfo
            {
                key = src.key,
                structureId = src.structureId,
                color = sourceColor,
                ap = src.ap,
                dv = src.dv,
                ml = src.ml,
                category = src.category,
                groupKey = src.groupKey,
                groupName = src.groupName,
                groupNameCn = src.groupNameCn,
                displayName = src.displayName,
                voxelCount = src.voxelCount,
                nSubregions = src.nSubregions,
                volumeAvailabilityKnown = src.volumeAvailabilityKnown,
                presentInVolume = src.presentInVolume,
            };
        }

        private Color ResolveDisplayColor(Color source, string categoryRaw, string groupRaw, string key, int structureId)
        {
            Color resolved = UseMriAppearance
                ? ResolveDisplayColorMri(source, categoryRaw, groupRaw, key, structureId)
                : ResolveDisplayColorStandard(source, categoryRaw, groupRaw, key, structureId);
            if (!EnableColorBlindSafeAdjust)
            {
                resolved.a = 1f;
                return resolved;
            }
            Color fallback = CategoryFallbackColor(categoryRaw, groupRaw, key, structureId);
            fallback.a = 1f;
            float safeStrength = Mathf.Clamp01(ColorBlindSafeStrength);
            float minDelta = Mathf.Clamp(ColorBlindMinLuminanceDelta, 0f, 0.2f);
            return ApplyColorBlindSafeAdjustments(resolved, fallback, safeStrength, minDelta);
        }

        private static Color ResolveDisplayColorStandard(Color source, string categoryRaw, string groupRaw, string key, int structureId)
        {
            Color fallback = CategoryFallbackColor(categoryRaw, groupRaw, key, structureId);
            fallback.a = 1f;
            bool sourceInvalid =
                float.IsNaN(source.r) || float.IsInfinity(source.r) ||
                float.IsNaN(source.g) || float.IsInfinity(source.g) ||
                float.IsNaN(source.b) || float.IsInfinity(source.b) ||
                (source.r <= 0.001f && source.g <= 0.001f && source.b <= 0.001f);
            if (sourceInvalid) return fallback;

            source.a = 1f;
            float sourceEnergy = source.r + source.g + source.b;
            float blend = sourceEnergy > 2.2f ? 0.22f : 0.34f;
            Color mixed = Color.Lerp(fallback, source, blend);

            float redDominance = mixed.r - Mathf.Max(mixed.g, mixed.b);
            bool overlyWarm = redDominance > 0.20f && (mixed.g < 0.50f || mixed.b < 0.50f);
            if (overlyWarm)
            {
                mixed = Color.Lerp(mixed, fallback, 0.82f);
            }

            Color.RGBToHSV(mixed, out float h, out float s, out float v);
            if (h < 0.055f || h > 0.945f) h = Mathf.Repeat(h + 0.12f, 1f);
            s = Mathf.Clamp(s, 0.36f, 0.72f);
            v = Mathf.Clamp(v, 0.78f, 0.97f);
            Color outColor = Color.HSVToRGB(h, s, v);
            outColor.a = 1f;
            return outColor;
        }

        private Color ResolveDisplayColorMri(Color source, string categoryRaw, string groupRaw, string key, int structureId)
        {
            Color neutral = ResolveDisplayColorStandard(source, categoryRaw, groupRaw, key, structureId);
            neutral.a = 1f;

            bool sourceInvalid =
                float.IsNaN(source.r) || float.IsInfinity(source.r) ||
                float.IsNaN(source.g) || float.IsInfinity(source.g) ||
                float.IsNaN(source.b) || float.IsInfinity(source.b) ||
                (source.r <= 0.001f && source.g <= 0.001f && source.b <= 0.001f);
            Color rawColor = sourceInvalid ? neutral : source;
            rawColor.a = 1f;

            float blend = Mathf.Clamp01(MriAppearanceBlend);
            float rawLuminance = rawColor.r * 0.299f + rawColor.g * 0.587f + rawColor.b * 0.114f;
            float neutralLuminance = neutral.r * 0.299f + neutral.g * 0.587f + neutral.b * 0.114f;
            float luminance = Mathf.Clamp01(Mathf.Lerp(rawLuminance, neutralLuminance, 0.62f));
            float coolHueBase = 0.59f; // cool MRI-like tone
            float coolJitter = ((structureId % 23) - 11) * 0.0018f;
            float coolHue = Mathf.Repeat(coolHueBase + coolJitter, 1f);
            float satFloor = Mathf.Clamp01(MriAppearanceSaturationFloor);
            float satCeil = Mathf.Max(satFloor, Mathf.Clamp01(MriAppearanceSaturationCeil));
            float valFloor = Mathf.Clamp01(MriAppearanceValueFloor);
            float valCeil = Mathf.Max(valFloor, Mathf.Clamp01(MriAppearanceValueCeil));
            float sat = Mathf.Lerp(satFloor, satCeil, Mathf.Clamp01(0.16f + (1f - luminance) * 0.42f));
            float val = Mathf.Lerp(valFloor, valCeil, Mathf.Clamp01(0.08f + luminance * 0.92f));
            Color mriTone = Color.HSVToRGB(coolHue, sat, val);
            Color outColor = Color.Lerp(neutral, mriTone, blend);
            outColor.a = 1f;
            return outColor;
        }

        private static float Luminance(Color c)
        {
            return c.r * 0.299f + c.g * 0.587f + c.b * 0.114f;
        }

        private static Color ApplyColorBlindSafeAdjustments(Color input, Color fallback, float strength, float minLuminanceDelta)
        {
            if (strength <= 0.0001f)
            {
                input.a = 1f;
                return input;
            }

            Color.RGBToHSV(input, out float h, out float s, out float v);
            // Red/green confusion zone suppression:
            // move warm red and yellow-green hues toward cyan/blue-safe range.
            if (h < 0.05f || h > 0.95f)
            {
                h = Mathf.Repeat(h + 0.14f * strength, 1f);
            }
            if (h > 0.18f && h < 0.42f)
            {
                h = Mathf.Lerp(h, 0.56f, 0.48f * strength);
            }

            s = Mathf.Clamp(s, 0.34f, 0.78f);
            v = Mathf.Clamp(v, 0.72f, 0.98f);
            Color safe = Color.HSVToRGB(h, s, v);
            float delta = Mathf.Abs(Luminance(safe) - Luminance(fallback));
            if (delta < Mathf.Max(0f, minLuminanceDelta))
            {
                Color.RGBToHSV(fallback, out float fh, out float fs, out float fv);
                float fallbackHueShift = Mathf.Repeat(fh + 0.18f + 0.06f * strength, 1f);
                float fallbackSat = Mathf.Clamp(Mathf.Max(fs, s), 0.44f, 0.82f);
                float fallbackVal = Mathf.Clamp(Mathf.Max(fv, v + 0.06f), 0.74f, 1f);
                safe = Color.HSVToRGB(fallbackHueShift, fallbackSat, fallbackVal);
            }
            safe.a = 1f;
            return safe;
        }

        private static Color CategoryFallbackColor(string categoryRaw, string groupRaw, string key, int structureId)
        {
            string category = NormalizeCategory(categoryRaw);
            string group = NormalizeGroup(groupRaw);
            string token = !string.IsNullOrEmpty(group) ? group : category;
            float baseHue = token switch
            {
                "visual" => 0.56f,
                "hippocampus" => 0.83f,
                "thalamus" => 0.32f,
                "frontal" => 0.10f,
                "midbrain" => 0.70f,
                "basal_ganglia" => 0.16f,
                "atlas" => 0.58f,
                _ => 0.52f,
            };

            uint hash = 2166136261u;
            string seed = $"{key}:{structureId}:{token}";
            for (int i = 0; i < seed.Length; i++)
            {
                hash ^= seed[i];
                hash *= 16777619u;
            }

            float jitter = (((hash & 1023u) / 1023f) - 0.5f) * 0.10f;
            float h = Mathf.Repeat(baseHue + jitter + (structureId % 23) * 0.009f, 1f);
            float s = Mathf.Clamp01(0.62f + (((hash >> 9) & 127u) / 127f) * 0.20f);
            float v = Mathf.Clamp01(0.86f + (((hash >> 16) & 127u) / 127f) * 0.12f);
            Color c = Color.HSVToRGB(h, s, v);
            c.a = 1f;
            return c;
        }

        private static string NormalizeHemisphere(string value)
        {
            string v = (value ?? string.Empty).Trim().ToLowerInvariant();
            if (v == "left" || v == "l") return "left";
            if (v == "right" || v == "r") return "right";
            return "all";
        }

        private static int GetBrainLayerMask()
        {
            int mask = 0;
            int outlineLayer = LayerMask.NameToLayer("BrainOutline");
            int fillLayer = LayerMask.NameToLayer("BrainFill");
            if (outlineLayer >= 0) mask |= 1 << outlineLayer;
            if (fillLayer >= 0) mask |= 1 << fillLayer;
            return mask;
        }

        private static void SetLayerRecursively(GameObject go, int layer)
        {
            go.layer = layer;
            for (int i = 0; i < go.transform.childCount; i++)
            {
                SetLayerRecursively(go.transform.GetChild(i).gameObject, layer);
            }
        }

        private static int ParseSliceAxis(string axis)
        {
            string raw = (axis ?? string.Empty).Trim().ToLowerInvariant();
            if (raw == "x" || raw == "sagittal" || raw == "ml") return 0;
            if (raw == "z" || raw == "coronal" || raw == "ap") return 2;
            return 1; // y / horizontal / dv
        }

        private float GetSliceAxisMin(int axis)
        {
            if (_sliceBoundsValid)
            {
                if (axis == 0) return _sliceBounds.min.x;
                if (axis == 2) return _sliceBounds.min.z;
                return _sliceBounds.min.y;
            }
            if (axis == 1) return SliceYMin;
            return -6f;
        }

        private float GetSliceAxisMax(int axis)
        {
            if (_sliceBoundsValid)
            {
                if (axis == 0) return _sliceBounds.max.x;
                if (axis == 2) return _sliceBounds.max.z;
                return _sliceBounds.max.y;
            }
            if (axis == 1) return SliceYMax;
            return 6f;
        }

        private float SnapSliceCenterToBoundary(int axis, float center, float thresholdMm)
        {
            if (axis < 0 || axis > 2) return center;
            var samples = _sliceBoundarySamples[axis];
            if (samples == null || samples.Count == 0) return center;

            float best = center;
            float bestDist = thresholdMm;
            for (int i = 0; i < samples.Count; i++)
            {
                float v = samples[i];
                float d = Mathf.Abs(v - center);
                if (d <= bestDist)
                {
                    bestDist = d;
                    best = v;
                }
            }
            return best;
        }

        private void ApplySliceToMaterial(Material mat, string key = null, bool isFill = false)
        {
            if (mat == null) return;
            bool contactBypass = ShouldBypassSliceForContact(key, isFill);
            if (mat.HasProperty("_SliceEnabled")) mat.SetFloat("_SliceEnabled", SliceEnabled ? 1f : 0f);
            var sides = Vector4.zero;
            if (!string.IsNullOrEmpty(key)) _contactHemispheres.TryGetValue(key, out sides);
            if (mat.HasProperty("_ContactHemispheres")) mat.SetVector("_ContactHemispheres", sides);
            if (mat.HasProperty("_SliceY")) mat.SetFloat("_SliceY", SliceCenter);
            if (mat.HasProperty("_SliceAxis")) mat.SetFloat("_SliceAxis", SliceAxis);
            if (mat.HasProperty("_SliceCenter")) mat.SetFloat("_SliceCenter", SliceCenter);
            if (mat.HasProperty("_SliceThickness")) mat.SetFloat("_SliceThickness", Mathf.Max(SliceThicknessMm, 0f));
            if (mat.HasProperty("_SliceUseSlab")) mat.SetFloat("_SliceUseSlab", SliceUseSlab ? 1f : 0f);
            if (mat.HasProperty("_ContactOverride")) mat.SetFloat("_ContactOverride", contactBypass ? 1f : 0f);
            if (mat.HasProperty("_ContactStyle"))
            {
                float styleCode = ContactDisplayStyle == "outline" ? 0f : ContactDisplayStyle == "fill" ? 1f : 2f;
                mat.SetFloat("_ContactStyle", styleCode);
            }
        }

        private void ApplySliceToAllMaterials()
        {
            foreach (var kv in _outlineMats) ApplySliceToMaterial(kv.Value, kv.Key, false);
            foreach (var kv in _fillMats) ApplySliceToMaterial(kv.Value, kv.Key, true);
            foreach (var kv in _pickMats) ApplySliceToMaterial(kv.Value, kv.Key, ContactStyleShowsFill(ContactDisplayStyle));
        }

        private void RegisterSliceBoundsForKey(string key, GameObject root)
        {
            if (string.IsNullOrEmpty(key) || root == null) return;
            var renderers = root.GetComponentsInChildren<Renderer>(true);
            if (renderers == null || renderers.Length == 0) return;

            Bounds merged = renderers[0].bounds;
            for (int i = 1; i < renderers.Length; i++) merged.Encapsulate(renderers[i].bounds);
            _sliceBoundsByKey[key] = merged;

            if (_sliceRangeContributorKeys.Contains(key))
            {
                RecomputeSliceRangeFromOutlines();
                return;
            }

            if (!_sliceBoundsValid)
            {
                _sliceBounds = merged;
                _sliceBoundsValid = true;
            }
            else
            {
                _sliceBounds.Encapsulate(merged);
            }
            _sliceRangeContributorKeys.Add(key);
            SliceYMin = _sliceBounds.min.y;
            SliceYMax = _sliceBounds.max.y;
            _sliceBoundarySamples[0].Add(merged.min.x);
            _sliceBoundarySamples[0].Add(merged.center.x);
            _sliceBoundarySamples[0].Add(merged.max.x);
            _sliceBoundarySamples[1].Add(merged.min.y);
            _sliceBoundarySamples[1].Add(merged.center.y);
            _sliceBoundarySamples[1].Add(merged.max.y);
            _sliceBoundarySamples[2].Add(merged.min.z);
            _sliceBoundarySamples[2].Add(merged.center.z);
            _sliceBoundarySamples[2].Add(merged.max.z);

            if (!SliceEnabled)
            {
                SliceCenter = GetSliceAxisMax(SliceAxis);
                SliceY = SliceCenter;
            }
        }

        private void RecomputeSliceRangeFromOutlines()
        {
            _sliceBoundsValid = false;
            _sliceBoundsByKey.Clear();
            for (int i = 0; i < _sliceBoundarySamples.Length; i++)
            {
                _sliceBoundarySamples[i].Clear();
            }
            bool hasBounds = false;
            Bounds merged = default;
            foreach (var kv in _outlineObjects)
            {
                var go = kv.Value;
                if (go == null) continue;
                var renderers = go.GetComponentsInChildren<Renderer>(true);
                if (renderers == null || renderers.Length == 0) continue;
                bool hasItemBounds = false;
                Bounds itemBounds = default;
                for (int i = 0; i < renderers.Length; i++)
                {
                    if (!hasItemBounds)
                    {
                        itemBounds = renderers[i].bounds;
                        hasItemBounds = true;
                    }
                    else
                    {
                        itemBounds.Encapsulate(renderers[i].bounds);
                    }

                    if (!hasBounds)
                    {
                        merged = renderers[i].bounds;
                        hasBounds = true;
                    }
                    else
                    {
                        merged.Encapsulate(renderers[i].bounds);
                    }
                }
                if (hasItemBounds)
                {
                    _sliceBoundsByKey[kv.Key] = itemBounds;
                    _sliceBoundarySamples[0].Add(itemBounds.min.x);
                    _sliceBoundarySamples[0].Add(itemBounds.center.x);
                    _sliceBoundarySamples[0].Add(itemBounds.max.x);
                    _sliceBoundarySamples[1].Add(itemBounds.min.y);
                    _sliceBoundarySamples[1].Add(itemBounds.center.y);
                    _sliceBoundarySamples[1].Add(itemBounds.max.y);
                    _sliceBoundarySamples[2].Add(itemBounds.min.z);
                    _sliceBoundarySamples[2].Add(itemBounds.center.z);
                    _sliceBoundarySamples[2].Add(itemBounds.max.z);
                }
            }
            if (!hasBounds) return;
            _sliceBounds = merged;
            _sliceBoundsValid = true;
            SliceYMin = merged.min.y;
            SliceYMax = merged.max.y;
            if (!SliceEnabled)
            {
                SliceCenter = GetSliceAxisMax(SliceAxis);
                SliceY = SliceCenter;
            }
        }

        private void SetOutlineMaterialState(string key, Material mat, bool selected, bool highlight)
        {
            if (mat == null) return;
            ApplySliceToMaterial(mat, key, false);
            bool focused = IsFocusMatch(key, selected);
            RegionInfo info = TryGetRegionInfo(key);
            bool mriBoundary = UseMriAppearance && IsMriBoundaryRegion(key, info);
            bool mriContext = UseMriAppearance && IsMriRiskContextRegion(key, info);
            bool contactActive =
                IsContactKey(key) &&
                ContactDisplayMode != "standard" &&
                ContactStyleShowsOutline(ContactDisplayStyle);
            float alpha = selected ? OutlineOpacitySelected : OutlineOpacityNormal;
            if (UsePinpointFocusStyle && !selected)
            {
                alpha *= focused ? Mathf.Lerp(NonSelectedOutlineScale, 1f, 0.35f) : NonSelectedOutlineScale * 0.45f;
            }
            if (!selected && _regions.Count >= DenseAtlasThreshold)
            {
                alpha *= DenseAtlasOutlineAttenuation;
            }
            if (contactActive)
            {
                float contactFloor = ContactDisplayMode == "contact_only" ? 0.72f : 0.58f;
                alpha = Mathf.Max(alpha, contactFloor * ContactOutlineBoost);
            }
            if (UseMriAppearance)
            {
                if (mriBoundary) alpha = Mathf.Max(alpha, 0.40f);
                else if (mriContext) alpha = Mathf.Max(alpha, 0.26f);
                else if (!selected) alpha *= 0.84f;
            }
            if (highlight) alpha = Mathf.Max(alpha, 1f);
            SetMaterialAlpha(mat, alpha);
            float rimIntensity = highlight
                ? RimIntensitySelected
                : (selected ? RimIntensitySelected * 0.75f : focused ? RimIntensitySelected * 0.18f : 0f);
            if (contactActive) rimIntensity = Mathf.Max(rimIntensity, RimIntensitySelected * ContactRimBoost * 0.68f);
            if (UseMriAppearance)
            {
                if (mriBoundary) rimIntensity = Mathf.Max(rimIntensity, RimIntensitySelected * 0.88f);
                else if (mriContext) rimIntensity = Mathf.Max(rimIntensity, RimIntensitySelected * 0.56f);
            }
            if (mat.HasProperty("_RimIntensity")) mat.SetFloat("_RimIntensity", rimIntensity);
            float wireWidth = highlight ? 0.0135f : selected ? 0.0105f : (_regions.Count >= DenseAtlasThreshold ? 0.0048f : 0.0072f);
            if (contactActive) wireWidth = Mathf.Max(wireWidth, 0.0118f);
            if (mat.HasProperty("_WireWidth")) mat.SetFloat("_WireWidth", wireWidth);
            float focusScale = highlight ? 1.35f : selected ? 1.18f : focused ? 0.92f : 0.40f;
            if (contactActive) focusScale = Mathf.Max(focusScale, 1.12f);
            if (mat.HasProperty("_FocusedScale")) mat.SetFloat("_FocusedScale", focusScale);
            if (mat.HasProperty("_EmissionColor"))
            {
                Color baseColor = mat.HasProperty("_RegionColor") ? mat.GetColor("_RegionColor") : mat.color;
                Color emission = selected ? baseColor * 0.26f : focused ? baseColor * 0.08f : Color.black;
                if (contactActive)
                {
                    var contactEmission = baseColor * 0.24f;
                    emission = new Color(
                        Mathf.Max(emission.r, contactEmission.r),
                        Mathf.Max(emission.g, contactEmission.g),
                        Mathf.Max(emission.b, contactEmission.b),
                        1f
                    );
                }
                if (highlight) emission = baseColor * 0.52f;
                mat.SetColor("_EmissionColor", emission);
            }
            if (mat.HasProperty("_ContactHighlight")) mat.SetFloat("_ContactHighlight", contactActive ? 1f : 0f);
        }

        private void SetFillMaterialState(string key, bool selected)
        {
            if (!_fillMats.TryGetValue(key, out var mat) || mat == null) return;
            ApplySliceToMaterial(mat, key, true);
            bool focused = IsFocusMatch(key, selected);
            RegionInfo info = TryGetRegionInfo(key);
            bool mriBoundary = UseMriAppearance && IsMriBoundaryRegion(key, info);
            bool mriContext = UseMriAppearance && IsMriRiskContextRegion(key, info);
            bool contactActive =
                IsContactKey(key) &&
                ContactDisplayMode != "standard" &&
                ContactStyleShowsFill(ContactDisplayStyle);
            bool focusFill = IsFocusFillKey(key, info, selected);
            float alphaScale = selected ? 1f : focused ? Mathf.Lerp(NonSelectedFillScale, 1f, 0.30f) : NonSelectedFillScale * 0.38f;
            float alpha = Mathf.Clamp01(FillOpacity * alphaScale);
            if (!selected && focusFill)
            {
                alpha = Mathf.Max(alpha, Mathf.Clamp01(FocusFillOpacityFloor));
                if (mriContext) alpha = Mathf.Max(alpha, Mathf.Clamp01(RiskContextFillOpacityFloor));
            }
            if (contactActive)
            {
                float contactAlpha = Mathf.Clamp01(ContactFillOpacity + (selected ? 0.07f : 0f));
                if (ContactDisplayMode == "contact_only") contactAlpha = Mathf.Clamp01(Mathf.Max(contactAlpha, 0.22f));
                alpha = Mathf.Max(alpha, contactAlpha);
            }
            if (UseMriAppearance)
            {
                if (mriBoundary) alpha = Mathf.Max(alpha, MriBoundaryMinAlpha);
                else if (mriContext) alpha = Mathf.Max(alpha, MriContextMinAlpha);
                else if (!selected && !contactActive) alpha *= 0.72f;
            }
            SetMaterialAlpha(mat, alpha);
            var c = mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor") : mat.color;
            c.a = alpha;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", c);
            mat.color = c;
            float rimIntensity = selected ? 0.50f : focused ? 0.34f : 0.16f;
            if (contactActive) rimIntensity = Mathf.Max(rimIntensity, 0.56f);
            if (mat.HasProperty("_RimIntensity")) mat.SetFloat("_RimIntensity", rimIntensity);
            float emissiveBoost = selected ? SelectedEmissiveBoost : focused ? UnselectedEmissiveBoost : UnselectedEmissiveBoost * 0.45f;
            if (contactActive) emissiveBoost = Mathf.Max(emissiveBoost, UnselectedEmissiveBoost + 0.12f);
            if (UseMriAppearance)
            {
                if (mriBoundary) emissiveBoost = Mathf.Max(emissiveBoost, UnselectedEmissiveBoost + 0.20f);
                else if (mriContext) emissiveBoost = Mathf.Max(emissiveBoost, UnselectedEmissiveBoost + 0.12f);
            }
            if (mat.HasProperty("_EmissiveBoost")) mat.SetFloat("_EmissiveBoost", emissiveBoost);
            float brightness = selected ? SelectedBrightness : focused ? UnselectedBrightness : UnselectedBrightness * 0.78f;
            if (contactActive) brightness = Mathf.Max(brightness, UnselectedBrightness + 0.08f);
            if (UseMriAppearance)
            {
                if (mriBoundary) brightness = Mathf.Max(brightness, MriBoundaryBrightness);
                else if (mriContext) brightness = Mathf.Max(brightness, 1.08f);
                else if (!selected && !contactActive) brightness = Mathf.Min(brightness, MriBackgroundBrightness);
            }
            if (mat.HasProperty("_Brightness")) mat.SetFloat("_Brightness", brightness);
            float saturation = selected ? 1.16f : focused ? 1.04f : 0.88f;
            if (contactActive) saturation = Mathf.Max(saturation, 1.03f);
            if (UseMriAppearance)
            {
                if (mriBoundary) saturation = Mathf.Clamp(saturation, 0.82f, 1.10f);
                else if (mriContext) saturation = Mathf.Clamp(saturation, 0.76f, 1.02f);
                else saturation = Mathf.Clamp(saturation * 0.82f, 0.42f, 0.92f);
            }
            if (mat.HasProperty("_Saturation")) mat.SetFloat("_Saturation", saturation);
            if (mat.HasProperty("_DeRedStrength"))
            {
                float deRed = selected ? 0.30f : contactActive ? 0.42f : focused ? 0.58f : 0.76f;
                if (UseMriAppearance)
                {
                    deRed = mriBoundary ? 0.50f : mriContext ? 0.62f : Mathf.Max(deRed, 0.78f);
                }
                mat.SetFloat("_DeRedStrength", deRed);
            }
            if (mat.HasProperty("_UseWeightedOIT")) mat.SetFloat("_UseWeightedOIT", UseWeightedOit ? 1f : 0f);
            if (mat.HasProperty("_OITWeightScale"))
            {
                float focusedScale = selected ? 1.38f : focused ? 0.90f : 0.48f;
                if (contactActive) focusedScale = Mathf.Max(focusedScale, 1.12f);
                mat.SetFloat("_OITWeightScale", OitWeightScale * focusedScale);
            }
            if (mat.HasProperty("_FocusedScale"))
            {
                float focusScale = selected ? 1.24f : focused ? 0.95f : 0.42f;
                if (contactActive) focusScale = Mathf.Max(focusScale, 1.10f);
                mat.SetFloat("_FocusedScale", focusScale);
            }
            if (mat.HasProperty("_DepthWeightNear")) mat.SetFloat("_DepthWeightNear", OitDepthWeightNear);
            if (mat.HasProperty("_DepthWeightFar")) mat.SetFloat("_DepthWeightFar", OitDepthWeightFar);
            if (mat.HasProperty("_DepthWeightExp")) mat.SetFloat("_DepthWeightExp", OitDepthWeightExp);
            if (mat.HasProperty("_HemisphereFilter")) mat.SetFloat("_HemisphereFilter", HemisphereFilterAsFloat());
            if (mat.HasProperty("_ContactHighlight")) mat.SetFloat("_ContactHighlight", contactActive ? 1f : 0f);
            mat.renderQueue = 3000;
        }

        private void RefreshAppearanceMaterials()
        {
            foreach (var kv in _outlineMats)
            {
                string key = kv.Key;
                Material mat = kv.Value;
                if (mat == null) continue;
                var info = TryGetRegionInfo(key);
                if (info == null) continue;
                Color displayColor = ResolveDisplayColor(info.color, info.category, info.groupKey, key, info.structureId);
                Color outlineColor = displayColor;
                outlineColor.a = mat.color.a;
                mat.color = outlineColor;
                if (mat.HasProperty("_RegionColor")) mat.SetColor("_RegionColor", displayColor);
                SetOutlineMaterialState(key, mat, _selectedKeys.Contains(key), false);
            }

            foreach (var kv in _fillMats)
            {
                string key = kv.Key;
                Material mat = kv.Value;
                if (mat == null) continue;
                var info = TryGetRegionInfo(key);
                if (info == null) continue;
                Color displayColor = ResolveDisplayColor(info.color, info.category, info.groupKey, key, info.structureId);
                float alpha = mat.HasProperty("_BaseColor") ? mat.GetColor("_BaseColor").a : mat.color.a;
                Color fillColor = displayColor;
                fillColor.a = alpha;
                if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", fillColor);
                mat.color = fillColor;
                if (mat.HasProperty("_RimColor"))
                {
                    Color rim = Color.Lerp(displayColor, Color.white, 0.40f);
                    rim.a = 1f;
                    mat.SetColor("_RimColor", rim);
                }
                SetFillMaterialState(key, _selectedKeys.Contains(key));
            }
        }

        private static void SetMaterialAlpha(Material mat, float alpha)
        {
            var c = mat.color;
            c.a = alpha;
            mat.color = c;
            if (mat.HasProperty("_Alpha")) mat.SetFloat("_Alpha", alpha);
            if (mat.HasProperty("_OutlineAlpha")) mat.SetFloat("_OutlineAlpha", alpha);
        }

        private bool ShouldSuppressOutlineByMetadata(string key, RegionInfo info, out string reason)
        {
            reason = string.Empty;
            if (info != null && info.volumeAvailabilityKnown && !info.presentInVolume)
            {
                reason = "not_present_in_current_annotation";
                return true;
            }
            if (!SuppressAggregateRegionOutline || info == null) return false;
            if (_aggregateOutlineGuardKeys.Contains(info.key ?? key))
            {
                reason = "aggregate_parent_outline";
                return true;
            }
            if (info.nSubregions >= AggregateOutlineSubregionThreshold)
            {
                reason = "aggregate_subregions_outline";
                return true;
            }
            if (info.voxelCount >= AggregateOutlineVoxelThreshold)
            {
                reason = "aggregate_volume_outline";
                return true;
            }
            return false;
        }

        private bool ShouldSuppressFillByMetadata(string key, RegionInfo info, out string reason)
        {
            reason = string.Empty;
            if (info != null && info.volumeAvailabilityKnown && !info.presentInVolume)
            {
                reason = "not_present_in_current_annotation";
                return true;
            }
            if (!SuppressAggregateRegionFill || info == null) return false;
            if (_aggregateFillGuardKeys.Contains(info.key ?? key))
            {
                reason = "aggregate_parent";
                return true;
            }
            if (info.nSubregions >= AggregateFillSubregionThreshold)
            {
                reason = "aggregate_subregions";
                return true;
            }
            if (info.voxelCount >= AggregateFillVoxelThreshold)
            {
                reason = "aggregate_volume";
                return true;
            }
            return false;
        }

        private bool ShouldSuppressFillByMesh(string key, Mesh mesh, out string reason)
        {
            reason = string.Empty;
            if (!SuppressAggregateRegionFill || mesh == null) return false;
            var size = mesh.bounds.size;
            float maxExtent = Mathf.Max(size.x, Mathf.Max(size.y, size.z));
            if (maxExtent >= AggregateFillExtentThresholdMm)
            {
                reason = "aggregate_extent";
                return true;
            }
            return false;
        }

        private void ResetRegionCatalogToDefault()
        {
            _regions.Clear();
            _regionByKey.Clear();
            foreach (var r in RegionData.All)
            {
                if (r == null || string.IsNullOrEmpty(r.key)) continue;
                if (_regionByKey.ContainsKey(r.key)) continue;
                var normalized = CloneRegionInfo(r);
                _regions.Add(normalized);
                _regionByKey[normalized.key] = normalized;
            }
        }

        private RegionInfo TryGetRegionInfo(string key)
        {
            if (string.IsNullOrEmpty(key)) return null;
            if (_regionByKey.TryGetValue(key, out var info)) return info;
            return RegionData.Get(key);
        }

        private static float GetRegionRadius(string key)
        {
            var radii = new Dictionary<string, float>
            {
                { "SSp", 1.8f }, { "CP", 1.6f }, { "MOp", 1.3f }, { "CA1", 1.2f }, { "RSP", 1.2f },
                { "MOs", 1.3f }, { "DG", 1.0f }, { "CA3", 0.9f }, { "VISp", 1.0f }, { "ACA", 0.9f },
                { "SCm", 0.9f }, { "MRN", 0.8f }, { "LP", 0.6f }, { "VPM", 0.7f }, { "MD", 0.7f },
                { "GPe", 0.6f }, { "LGd", 0.5f }, { "PO", 0.6f }, { "VPL", 0.6f },
            };
            return radii.TryGetValue(key, out var r) ? r : 0.6f;
        }

        private static Material CreateRuntimeMaterial(Material template, params string[] fallbackShaderNames)
        {
            if (template != null) return new Material(template);
            if (fallbackShaderNames != null)
            {
                for (int i = 0; i < fallbackShaderNames.Length; i++)
                {
                    var s = Shader.Find(fallbackShaderNames[i]);
                    if (s != null) return new Material(s);
                }
            }
            Shader fallback = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard") ?? Shader.Find("Sprites/Default");
            if (fallback == null) throw new System.InvalidOperationException("No fallback shader available.");
            return new Material(fallback);
        }

        private static int OutlinePriority(RegionInfo info)
        {
            string cat = string.IsNullOrEmpty(info?.category) ? "dynamic" : info.category.Trim().ToLowerInvariant();
            if (cat == "visual" || cat == "hippocampus" || cat == "thalamus" || cat == "frontal" || cat == "midbrain" || cat == "basal_ganglia")
                return 0;
            if (cat == "atlas")
                return 1;
            if (cat == "dynamic")
                return 2;
            return 3;
        }

        private int StreamPriority(RegionInfo info)
        {
            if (info == null || string.IsNullOrEmpty(info.key)) return 4;
            if (_selectedKeys.Contains(info.key)) return 0;
            if (_defaultContextKeys.Contains(info.key)) return 1;
            return 2;
        }
    }
}
