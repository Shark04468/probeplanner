using System.Collections.Generic;
using UnityEngine;

namespace BrainPlanner.BrainView
{
    [System.Serializable]
    public class ProbeDepthIntervalData
    {
        public float startDepthMm;
        public float endDepthMm;
        public string key;
        public string label;
        public float contactStrength;
        public float lengthUm;
    }

    [System.Serializable]
    public class ProbeRiskSegmentData
    {
        public float startDepthMm;
        public float endDepthMm;
        public string sourceClass;
        public float minDistanceMm;
        public float thresholdMm;
        public float severity;
    }

    [System.Serializable]
    public class ProbeUncertaintyData
    {
        public float tipSigmaMm;
        public float coneHalfAngleDeg;
        public float[] tipSigmaAxesMm;
        public float entrySigmaMm;
        public float angleSigmaDeg;
        public float depthSigmaMm;
    }

    [System.Serializable]
    public class ProbeData
    {
        public float ap_mm;
        public float ml_mm;
        public float theta_deg;
        public float phi_deg;
        public float spin_deg;
        public float depthUm;
        public float entryDvCorr_mm;
        public string probeType;
        public int shankCount;
        public float shankPitchUm;
        public float radiusUm;
        public string[] coveredRegions;
        public ProbeUncertaintyData uncertainty;
        public float activeStartDepthMm;
        public float activeEndDepthMm;
        public ProbeDepthIntervalData[] contactIntervals;
        public ProbeRiskSegmentData[] riskSegments;
        public float minRiskDistanceMm;
    }

    [System.Serializable]
    public class ProbeDataArray
    {
        public ProbeData[] items;
    }

    public class ProbeSpec
    {
        public int Shanks;
        public float ShankSpacingMm;
        public float DiameterMm;
        public Color Color;

        public static readonly Dictionary<string, ProbeSpec> All = new()
        {
            ["NP2.0-4sh"] = new ProbeSpec { Shanks=4, ShankSpacingMm=0.25f, DiameterMm=0.048f, Color=HexColor("00d4ff") },
            ["NP2.0-1sh"] = new ProbeSpec { Shanks=1, ShankSpacingMm=0f,    DiameterMm=0.065f, Color=HexColor("34d399") },
            ["NP1.0"]     = new ProbeSpec { Shanks=1, ShankSpacingMm=0f,    DiameterMm=0.095f, Color=HexColor("a78bfa") },
            ["custom"]    = new ProbeSpec { Shanks=1, ShankSpacingMm=0f,    DiameterMm=0.080f, Color=HexColor("facc15") },
        };

        private static Color HexColor(string hex)
        {
            ColorUtility.TryParseHtmlString("#" + hex, out Color c);
            return c;
        }

        public static ProbeSpec Resolve(ProbeData probe)
        {
            bool known = All.TryGetValue(probe.probeType ?? string.Empty, out var preset);
            if (!known && probe.shankCount <= 0)
                throw new System.ArgumentException("Probe hardware is missing; cannot infer four shanks from an unknown model");
            preset = preset ?? All["custom"];
            return new ProbeSpec {
                Shanks = probe.shankCount > 0 ? probe.shankCount : preset.Shanks,
                ShankSpacingMm = probe.shankCount > 0 ? probe.shankPitchUm / 1000f : preset.ShankSpacingMm,
                DiameterMm = probe.radiusUm > 0 ? 2f * probe.radiusUm / 1000f : preset.DiameterMm,
                Color = preset.Color,
            };
        }
    }

    public class ProbeRenderer : MonoBehaviour
    {
        [Header("Materials")]
        public Material ShankMaterial;
        public Material DashedLineMaterial;
        public Material EntryDotMaterial;

        [Header("Colors")]
        public Color[] ProbePalette = new Color[]
        {
            new Color(0f,    0.83f, 1f),
            new Color(1f,    0.42f, 0.42f),
            new Color(0.31f, 0.80f, 0.77f),
            new Color(1f,    0.90f, 0.43f),
            new Color(0.66f, 0.90f, 0.81f),
            new Color(1f,    0.83f, 0.71f),
        };

        [Header("Guides")]
        public float EntryDotRadius = 0.09f;
        public float ExtensionLengthMm = 2.0f;
        public bool ShowExtensionLines = false;
        public bool ShowUncertainty = false;
        public bool ShowActiveSegment = true;
        public bool ShowContactSegments = true;
        public bool ShowRiskSegments = true;
        public bool ShowConflictBands = true;
        public bool ShowSliceIntersection = true;
        [Range(0.1f, 5f)] public float ConflictBandMaxDistanceMm = 1.2f;
        [Range(0.05f, 0.8f)] public float SegmentRadiusScale = 1.1f;
        [Range(0.005f, 0.08f)] public float ConflictBandWidthMm = 0.018f;

        [Header("Slice Intersection")]
        public bool SliceEnabled = false;
        [Range(0, 2)] public int SliceAxis = 1; // 0=ML/x, 1=DV/y, 2=AP/z
        [Range(-16f, 10f)] public float SliceCenter = 0f;
        [Range(0f, 2f)] public float SliceThicknessMm = 0.25f;
        public bool SliceUseSlab = false;
        public Color SliceIntersectionColor = new Color(0.24f, 0.96f, 1f, 1f);

        private readonly List<GameObject> _probeObjects = new();
        private List<ProbeData> _currentProbes = new();
        private int _highlightIndex = -1;
        private readonly List<ProbeSegmentInfo> _probeSegments = new();

        private struct ProbeSegmentInfo
        {
            public int probeIndex;
            public int shankIndex;
            public Vector3 entry;
            public Vector3 tip;
        }

        void Awake()
        {
            // Scene serialization previously enabled this by default, which could leave a large
            // tip-error ellipsoid visible before the frontend sends its explicit visibility state.
            ShowUncertainty = false;
        }

        public void SetProbes(string jsonArray)
        {
            ProbeDataArray wrapper;
            try
            {
                wrapper = JsonUtility.FromJson<ProbeDataArray>("{\"items\":" + jsonArray + "}");
            }
            catch
            {
                Debug.LogError("[ProbeRenderer] JSON parse failed");
                return;
            }
            SetProbes(new List<ProbeData>(wrapper.items ?? new ProbeData[0]));
        }

        public void SetProbes(List<ProbeData> probes)
        {
            _currentProbes = probes;
            Rebuild();
        }

        public void SetHighlight(int index)
        {
            _highlightIndex = index;
            Rebuild();
        }

        public void SetShowExtensionLines(bool show)
        {
            if (ShowExtensionLines == show) return;
            ShowExtensionLines = show;
            Rebuild();
        }

        public void SetShowUncertainty(bool show)
        {
            if (ShowUncertainty == show) return;
            ShowUncertainty = show;
            Rebuild();
        }

        public void SetSlice(string axis, bool enabled, float center, float thicknessMm, bool useSlab)
        {
            int axisIndex = ParseSliceAxis(axis);
            float nextThickness = Mathf.Clamp(thicknessMm, 0f, 2f);
            bool nextUseSlab = useSlab && nextThickness > 0.0005f;
            if (
                SliceEnabled == enabled &&
                SliceAxis == axisIndex &&
                Mathf.Abs(SliceCenter - center) < 1e-4f &&
                Mathf.Abs(SliceThicknessMm - nextThickness) < 1e-4f &&
                SliceUseSlab == nextUseSlab
            )
            {
                return;
            }
            SliceEnabled = enabled;
            SliceAxis = axisIndex;
            SliceCenter = center;
            SliceThicknessMm = nextThickness;
            SliceUseSlab = nextUseSlab;
            Rebuild();
        }

        private void Rebuild()
        {
            foreach (var go in _probeObjects) Destroy(go);
            _probeObjects.Clear();
            _probeSegments.Clear();

            for (int i = 0; i < _currentProbes.Count; i++)
            {
                BuildProbe(_currentProbes[i], i);
            }

            if (ShowConflictBands)
            {
                DrawConflictBands();
            }
        }

        private void BuildProbe(ProbeData probe, int idx)
        {
            bool isHighlight = idx == _highlightIndex;

            string ptype = probe.probeType ?? "custom";
            ProbeSpec spec = ProbeSpec.Resolve(probe);

            Color color = isHighlight ? Color.white : ProbePalette[idx % ProbePalette.Length];
            Vector3 entryPos = new Vector3(probe.ml_mm, -probe.entryDvCorr_mm, probe.ap_mm);

            Vector3 dir = CcfCoordinates.ProbeDirection(probe.theta_deg, probe.phi_deg);

            float depthMm = probe.depthUm / 1000f;

            var root = new GameObject($"Probe_{idx}_{ptype}");
            root.transform.SetParent(transform, false);
            _probeObjects.Add(root);

            for (int s = 0; s < spec.Shanks; s++)
            {
                float offset = spec.Shanks > 1
                    ? (s - (spec.Shanks - 1) / 2f) * spec.ShankSpacingMm
                    : 0f;

                Vector3 shankEntry = entryPos + CcfCoordinates.ProbeLateralAxis(dir, probe.spin_deg) * offset;
                Vector3 shankTip = shankEntry + dir * depthMm;
                Vector3 mid = (shankEntry + shankTip) * 0.5f;
                float length = Vector3.Distance(shankEntry, shankTip);
                _probeSegments.Add(new ProbeSegmentInfo
                {
                    probeIndex = idx,
                    shankIndex = s,
                    entry = shankEntry,
                    tip = shankTip
                });

                var shank = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                shank.name = $"Shank_{s}";
                shank.transform.SetParent(root.transform, false);
                shank.transform.localPosition = mid;
                shank.transform.localScale = new Vector3(spec.DiameterMm, length * 0.5f, spec.DiameterMm);
                shank.transform.rotation = Quaternion.FromToRotation(Vector3.up, dir);

                var shankMat = new Material(ShankMaterial ?? CreateBasicMat(color));
                shankMat.color = color;
                if (shankMat.HasProperty("_EmissionColor"))
                {
                    shankMat.SetColor("_EmissionColor", color * (isHighlight ? 2.5f : 1.2f));
                    shankMat.EnableKeyword("_EMISSION");
                }
                shank.GetComponent<MeshRenderer>().material = shankMat;

                var dot = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                dot.name = "EntryDot";
                dot.transform.SetParent(root.transform, false);
                dot.transform.localPosition = shankEntry;
                dot.transform.localScale = Vector3.one * EntryDotRadius * 2f;
                dot.GetComponent<MeshRenderer>().material = CreateBasicMat(color);

                if (ShowExtensionLines)
                {
                    Vector3 extStart = shankEntry - dir * ExtensionLengthMm;
                    DrawDashedLine(root.transform, extStart, shankEntry, color);
                }

                if (ShowUncertainty)
                {
                    DrawUncertainty(root.transform, shankEntry, shankTip, dir, probe.uncertainty, color);
                }

                if (ShowActiveSegment)
                {
                    DrawActiveSegment(root.transform, shankEntry, dir, depthMm, spec.DiameterMm, probe, color);
                }
                if (ShowContactSegments)
                {
                    DrawContactSegments(root.transform, shankEntry, dir, depthMm, spec.DiameterMm, probe);
                }
                if (ShowRiskSegments)
                {
                    DrawRiskSegments(root.transform, shankEntry, dir, depthMm, spec.DiameterMm, probe);
                }
                DrawSliceIntersection(root.transform, shankEntry, dir, depthMm, spec.DiameterMm);
            }

            if (isHighlight || _currentProbes.Count <= 8)
            {
                var labelGO = new GameObject("Label");
                labelGO.transform.SetParent(root.transform, false);
                labelGO.transform.localPosition = entryPos + Vector3.up * 0.3f;
                labelGO.transform.localScale = Vector3.one * 0.12f;

                var tm = labelGO.AddComponent<TextMesh>();
                tm.text = $"P{idx + 1}";
                tm.color = color;
                tm.fontSize = 48;
                tm.alignment = TextAlignment.Center;
                tm.anchor = TextAnchor.MiddleCenter;
                labelGO.AddComponent<FaceCameraLabel>();
            }
        }

        private static float ClampDepth(float depthMm, float maxDepthMm)
        {
            if (float.IsNaN(depthMm) || float.IsInfinity(depthMm)) return 0f;
            return Mathf.Clamp(depthMm, 0f, Mathf.Max(0f, maxDepthMm));
        }

        private void DrawActiveSegment(
            Transform parent,
            Vector3 entry,
            Vector3 dir,
            float depthMm,
            float diameterMm,
            ProbeData probe,
            Color baseColor)
        {
            float start = ClampDepth(probe.activeStartDepthMm, depthMm);
            float end = ClampDepth(probe.activeEndDepthMm, depthMm);
            if (end <= start + 1e-4f)
            {
                start = depthMm * 0.30f;
                end = depthMm;
            }
            if (end <= start + 1e-4f) return;

            Color activeColor = Color.Lerp(baseColor, new Color(0.35f, 1f, 0.92f, 1f), 0.45f);
            activeColor.a = 0.72f;
            BuildSegmentCylinder(
                parent,
                "ActiveRecordingSegment",
                entry + dir * start,
                entry + dir * end,
                Mathf.Max(0.008f, diameterMm * SegmentRadiusScale * 0.62f),
                activeColor
            );
        }

        private void DrawContactSegments(
            Transform parent,
            Vector3 entry,
            Vector3 dir,
            float depthMm,
            float diameterMm,
            ProbeData probe)
        {
            if (probe.contactIntervals == null || probe.contactIntervals.Length == 0) return;
            int limit = Mathf.Min(8, probe.contactIntervals.Length);
            for (int i = 0; i < limit; i++)
            {
                var seg = probe.contactIntervals[i];
                if (seg == null) continue;
                float start = ClampDepth(seg.startDepthMm, depthMm);
                float end = ClampDepth(seg.endDepthMm, depthMm);
                if (end <= start + 1e-4f) continue;

                float strength = Mathf.Clamp01(seg.contactStrength);
                Color c = Color.Lerp(new Color(0.12f, 0.78f, 1f, 1f), new Color(0.98f, 0.95f, 0.25f, 1f), strength);
                c.a = 0.68f;
                BuildSegmentCylinder(
                    parent,
                    $"ContactSeg_{i}",
                    entry + dir * start,
                    entry + dir * end,
                    Mathf.Max(0.007f, diameterMm * SegmentRadiusScale * 0.54f),
                    c
                );
            }
        }

        private void DrawRiskSegments(
            Transform parent,
            Vector3 entry,
            Vector3 dir,
            float depthMm,
            float diameterMm,
            ProbeData probe)
        {
            if (probe.riskSegments == null || probe.riskSegments.Length == 0) return;
            int limit = Mathf.Min(3, probe.riskSegments.Length);
            for (int i = 0; i < limit; i++)
            {
                var seg = probe.riskSegments[i];
                if (seg == null) continue;
                float start = ClampDepth(seg.startDepthMm, depthMm);
                float end = ClampDepth(seg.endDepthMm, depthMm);
                if (end <= start + 1e-4f) continue;

                float sev = seg.severity;
                if (float.IsNaN(sev) || float.IsInfinity(sev)) sev = Mathf.Max(0f, seg.thresholdMm - seg.minDistanceMm);
                sev = Mathf.Clamp01(sev / 0.45f);

                Color classColor = RiskColorByClass(seg.sourceClass);
                Color alert = Color.Lerp(classColor, new Color(1f, 0.12f, 0.12f, 1f), Mathf.Clamp01(0.45f + sev * 0.5f));
                alert.a = 0.84f;
                BuildSegmentCylinder(
                    parent,
                    $"RiskSeg_{i}",
                    entry + dir * start,
                    entry + dir * end,
                    Mathf.Max(0.009f, diameterMm * SegmentRadiusScale * 0.70f),
                    alert
                );
            }
        }

        private static Color RiskColorByClass(string clsRaw)
        {
            string cls = (clsRaw ?? string.Empty).Trim().ToLowerInvariant();
            if (cls.Contains("vent")) return new Color(1f, 0.58f, 0.18f, 1f);
            if (cls.Contains("vessel")) return new Color(1f, 0.28f, 0.28f, 1f);
            if (cls.Contains("sulcus")) return new Color(0.86f, 0.62f, 1f, 1f);
            if (cls.Contains("skull")) return new Color(1f, 0.44f, 0.18f, 1f);
            if (cls.Contains("cortex")) return new Color(1f, 0.72f, 0.20f, 1f);
            return new Color(1f, 0.36f, 0.22f, 1f);
        }

        private static int ParseSliceAxis(string axis)
        {
            string a = (axis ?? string.Empty).Trim().ToLowerInvariant();
            if (a == "x" || a == "ml") return 0;
            if (a == "z" || a == "ap") return 2;
            return 1;
        }

        private float AxisValue(Vector3 p)
        {
            if (SliceAxis == 0) return p.x;
            if (SliceAxis == 2) return p.z;
            return p.y;
        }

        private void DrawSliceIntersection(
            Transform parent,
            Vector3 entry,
            Vector3 dir,
            float depthMm,
            float diameterMm)
        {
            if (!ShowSliceIntersection || !SliceEnabled || depthMm <= 1e-4f) return;
            float denom = AxisValue(dir);
            float startDepth = 0f;
            float endDepth = depthMm;

            if (Mathf.Abs(denom) < 1e-6f)
            {
                float delta = Mathf.Abs(AxisValue(entry) - SliceCenter);
                if (!SliceUseSlab || delta > Mathf.Max(0.0005f, SliceThicknessMm * 0.5f)) return;
                startDepth = 0f;
                endDepth = depthMm;
            }
            else if (SliceUseSlab)
            {
                float half = Mathf.Max(0.0005f, SliceThicknessMm * 0.5f);
                float t0 = (SliceCenter - half - AxisValue(entry)) / denom;
                float t1 = (SliceCenter + half - AxisValue(entry)) / denom;
                startDepth = Mathf.Clamp(Mathf.Min(t0, t1), 0f, depthMm);
                endDepth = Mathf.Clamp(Mathf.Max(t0, t1), 0f, depthMm);
                if (endDepth <= startDepth + 1e-4f) return;
            }
            else
            {
                float t = (SliceCenter - AxisValue(entry)) / denom;
                if (t < 0f || t > depthMm) return;
                startDepth = Mathf.Clamp(t - 0.035f, 0f, depthMm);
                endDepth = Mathf.Clamp(t + 0.035f, 0f, depthMm);
                if (endDepth <= startDepth + 1e-4f)
                {
                    startDepth = Mathf.Clamp(t, 0f, depthMm);
                    endDepth = startDepth;
                }
            }

            Color c = SliceIntersectionColor;
            c.a = SliceUseSlab ? 0.86f : 1f;
            Vector3 a = entry + dir * startDepth;
            Vector3 b = entry + dir * endDepth;
            if (Vector3.Distance(a, b) > 1e-4f)
            {
                BuildSegmentCylinder(
                    parent,
                    "SliceProbeIntersection",
                    a,
                    b,
                    Mathf.Max(0.014f, diameterMm * SegmentRadiusScale * 0.85f),
                    c
                );
                return;
            }

            var marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            marker.name = "SliceProbeIntersection";
            marker.transform.SetParent(parent, false);
            marker.transform.localPosition = a;
            marker.transform.localScale = Vector3.one * Mathf.Max(0.08f, diameterMm * 2.4f);
            marker.GetComponent<MeshRenderer>().material = CreateTransparentMat(c);
            var col = marker.GetComponent<Collider>();
            if (col != null) Destroy(col);
        }

        private GameObject BuildSegmentCylinder(
            Transform parent,
            string name,
            Vector3 start,
            Vector3 end,
            float radiusMm,
            Color color)
        {
            float len = Vector3.Distance(start, end);
            if (len <= 1e-4f) return null;
            var go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            go.name = name;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = (start + end) * 0.5f;
            go.transform.localScale = new Vector3(radiusMm, len * 0.5f, radiusMm);
            go.transform.rotation = Quaternion.FromToRotation(Vector3.up, (end - start).normalized);
            var mr = go.GetComponent<MeshRenderer>();
            mr.sharedMaterial = CreateTransparentMat(color);
            var col = go.GetComponent<Collider>();
            if (col != null) Destroy(col);
            return go;
        }

        private void DrawConflictBands()
        {
            if (_probeSegments.Count <= 1) return;
            const int maxBands = 28;
            int count = 0;
            for (int i = 0; i < _probeSegments.Count; i++)
            {
                for (int j = i + 1; j < _probeSegments.Count; j++)
                {
                    if (count >= maxBands) return;
                    var a = _probeSegments[i];
                    var b = _probeSegments[j];
                    if (a.probeIndex == b.probeIndex) continue;

                    float dist = ClosestDistanceBetweenSegments(a.entry, a.tip, b.entry, b.tip, out var pa, out var pb);
                    if (dist > ConflictBandMaxDistanceMm) continue;
                    float t = Mathf.Clamp01(dist / Mathf.Max(0.05f, ConflictBandMaxDistanceMm));
                    Color c = Color.Lerp(new Color(1f, 0.12f, 0.12f, 0.94f), new Color(1f, 0.92f, 0.22f, 0.84f), t);
                    DrawBandLine(pa, pb, c);
                    count++;
                }
            }
        }

        private void DrawBandLine(Vector3 from, Vector3 to, Color color)
        {
            if (Vector3.Distance(from, to) <= 1e-4f) return;
            var go = new GameObject("ProbeConflictBand");
            go.transform.SetParent(transform, false);
            var lr = go.AddComponent<LineRenderer>();
            lr.positionCount = 2;
            lr.SetPosition(0, from);
            lr.SetPosition(1, to);
            lr.useWorldSpace = false;
            lr.startWidth = ConflictBandWidthMm;
            lr.endWidth = ConflictBandWidthMm;
            lr.material = CreateTransparentMat(color);
            _probeObjects.Add(go);
        }

        private static float ClosestDistanceBetweenSegments(
            Vector3 p1, Vector3 q1, Vector3 p2, Vector3 q2,
            out Vector3 c1, out Vector3 c2)
        {
            Vector3 d1 = q1 - p1;
            Vector3 d2 = q2 - p2;
            Vector3 r = p1 - p2;
            float a = Vector3.Dot(d1, d1);
            float e = Vector3.Dot(d2, d2);
            float f = Vector3.Dot(d2, r);

            float s;
            float t;
            const float eps = 1e-6f;

            if (a <= eps && e <= eps)
            {
                c1 = p1;
                c2 = p2;
                return Vector3.Distance(c1, c2);
            }

            if (a <= eps)
            {
                s = 0f;
                t = Mathf.Clamp01(f / e);
            }
            else
            {
                float c = Vector3.Dot(d1, r);
                if (e <= eps)
                {
                    t = 0f;
                    s = Mathf.Clamp01(-c / a);
                }
                else
                {
                    float b = Vector3.Dot(d1, d2);
                    float denom = a * e - b * b;
                    if (denom != 0f)
                    {
                        s = Mathf.Clamp01((b * f - c * e) / denom);
                    }
                    else
                    {
                        s = 0f;
                    }

                    float tNom = b * s + f;
                    if (tNom < 0f)
                    {
                        t = 0f;
                        s = Mathf.Clamp01(-c / a);
                    }
                    else if (tNom > e)
                    {
                        t = 1f;
                        s = Mathf.Clamp01((b - c) / a);
                    }
                    else
                    {
                        t = tNom / e;
                    }
                }
            }

            c1 = p1 + d1 * s;
            c2 = p2 + d2 * t;
            return Vector3.Distance(c1, c2);
        }

        private void DrawUncertainty(Transform parent, Vector3 entry, Vector3 tip, Vector3 dir, ProbeUncertaintyData uncertainty, Color baseColor)
        {
            if (uncertainty == null) return;
            float tipSigma = Mathf.Max(0f, uncertainty.tipSigmaMm);
            float coneHalf = Mathf.Max(0f, uncertainty.coneHalfAngleDeg);

            if (tipSigma <= 0.001f && coneHalf <= 0.01f) return;

            Color uncColor = Color.Lerp(baseColor, new Color(1f, 0.83f, 0.2f), 0.65f);
            uncColor.a = 0.22f;

            if (coneHalf > 0.01f)
            {
                float length = Mathf.Max(0.15f, Vector3.Distance(entry, tip));
                float radius = Mathf.Tan(coneHalf * Mathf.Deg2Rad) * length;
                var cone = new GameObject("UncertaintyCone");
                cone.transform.SetParent(parent, false);
                cone.transform.position = entry;
                cone.transform.rotation = Quaternion.FromToRotation(Vector3.up, dir);
                var mf = cone.AddComponent<MeshFilter>();
                mf.sharedMesh = BuildConeMesh(length, radius, 24);
                var mr = cone.AddComponent<MeshRenderer>();
                mr.sharedMaterial = CreateTransparentMat(uncColor);
            }

            if (tipSigma > 0.001f)
            {
                float sx = tipSigma;
                float sy = tipSigma;
                float sz = tipSigma;
                if (uncertainty.tipSigmaAxesMm != null && uncertainty.tipSigmaAxesMm.Length >= 3)
                {
                    sx = Mathf.Max(0.001f, uncertainty.tipSigmaAxesMm[0]);
                    sy = Mathf.Max(0.001f, uncertainty.tipSigmaAxesMm[1]);
                    sz = Mathf.Max(0.001f, uncertainty.tipSigmaAxesMm[2]);
                }

                var ellipsoid = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                ellipsoid.name = "TipErrorEllipsoid";
                ellipsoid.transform.SetParent(parent, false);
                ellipsoid.transform.position = tip;
                ellipsoid.transform.localScale = new Vector3(sx * 2f, sy * 2f, sz * 2f);
                ellipsoid.GetComponent<MeshRenderer>().material = CreateTransparentMat(uncColor);
                var col = ellipsoid.GetComponent<Collider>();
                if (col != null) Destroy(col);
            }
        }

        private static Mesh BuildConeMesh(float length, float radius, int segments)
        {
            segments = Mathf.Clamp(segments, 8, 64);
            var vertices = new List<Vector3>(segments + 2);
            var triangles = new List<int>(segments * 6);

            vertices.Add(new Vector3(0f, length, 0f));
            vertices.Add(Vector3.zero);

            for (int i = 0; i < segments; i++)
            {
                float t = i / (float)segments * Mathf.PI * 2f;
                vertices.Add(new Vector3(Mathf.Cos(t) * radius, 0f, Mathf.Sin(t) * radius));
            }

            for (int i = 0; i < segments; i++)
            {
                int current = 2 + i;
                int next = 2 + ((i + 1) % segments);

                triangles.Add(0);
                triangles.Add(next);
                triangles.Add(current);

                triangles.Add(1);
                triangles.Add(current);
                triangles.Add(next);
            }

            var mesh = new Mesh { name = "ProbeUncertaintyCone" };
            mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
            mesh.SetVertices(vertices);
            mesh.SetTriangles(triangles, 0);
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        private void DrawDashedLine(Transform parent, Vector3 from, Vector3 to, Color color)
        {
            float dashLen = 0.18f;
            float gapLen = 0.12f;
            float total = Vector3.Distance(from, to);
            Vector3 dir = (to - from).normalized;
            float pos = 0f;
            int segIdx = 0;

            while (pos < total)
            {
                float segEnd = Mathf.Min(pos + dashLen, total);
                Vector3 p0 = from + dir * pos;
                Vector3 p1 = from + dir * segEnd;

                var go = new GameObject($"Dash_{segIdx}");
                go.transform.SetParent(parent, false);
                var lr = go.AddComponent<LineRenderer>();
                lr.positionCount = 2;
                lr.SetPosition(0, p0);
                lr.SetPosition(1, p1);
                lr.startWidth = 0.015f;
                lr.endWidth = 0.015f;
                lr.useWorldSpace = false;

                var mat = new Material(DashedLineMaterial ?? CreateBasicMat(color));
                var c = color;
                c.a = 0.5f;
                mat.color = c;
                lr.material = mat;

                pos += dashLen + gapLen;
                segIdx++;
            }
        }

        private static Material CreateBasicMat(Color color)
        {
            var mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            mat.color = color;
            return mat;
        }

        private static Material CreateTransparentMat(Color color)
        {
            var mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            mat.SetFloat("_Surface", 1f);
            mat.SetFloat("_Blend", 0f);
            mat.SetFloat("_ZWrite", 0f);
            mat.SetFloat("_Cull", 0f);
            mat.renderQueue = 3050;
            mat.color = color;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            return mat;
        }
    }
}
