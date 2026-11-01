using System.Collections;
using System.Globalization;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using BrainPlanner;
using BrainPlanner.BrainView;
using BrainPlanner.Data;
using RegionInfo = BrainPlanner.Data.RegionInfo;
using UnityEngine;
using UnityEngine.Networking;

namespace BrainPlanner.Bridge
{
    /// <summary>
    /// React <-> Unity bridge for WebGL builds.
    /// </summary>
    public class ReactBridge : MonoBehaviour
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void BP_NotifyReact(string eventName, string jsonData);

        [DllImport("__Internal")]
        private static extern void BP_LogToConsole(string message);
#endif

        private RegionManager _regionMgr;
        private ProbeRenderer _probeRenderer;
        private CameraController _cameraController;
        private BrainShell _brainShell;
        private MriAppearanceLayerController _mriAppearanceLayerController;
        private AtlasSectionController _atlasSectionController;
        private CoordAxes _coordAxes;
        private RuntimePostFxController _postFxController;
        private BrainController _controller;
        private bool _catalogRefreshRunning;
        private int _lastCatalogCount;
        private string _lastCatalogSource = "default";
        private Dictionary<string, System.Action<string>> _commands;

        // Unity SendMessage broadcasts to every component on the target object.
        // A unique entry point prevents typed subsystem setters from being invoked too.
        public void DispatchCommand(string json)
        {
            try
            {
                var command = JsonUtility.FromJson<BridgeCommand>(json);
                if (command == null || !CommandHandlers.TryGetValue(command.method ?? "", out var handler))
                    throw new System.ArgumentException("Unsupported bridge command.");
                handler(command.param ?? "");
                command.ts = System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                command.status = "dispatched";
                // Asset completion is reported separately through fill/stream events.
                SendToReact("stateAck", JsonUtility.ToJson(command));
            }
            catch (System.Exception ex)
            {
                Debug.LogError($"[ReactBridge] Command dispatch failed: {ex}");
                SendToReact("error", ex.Message);
            }
        }

        public IReadOnlyDictionary<string, System.Action<string>> CommandHandlers
        {
            get
            {
                if (_commands != null) return _commands;
                _commands = new Dictionary<string, System.Action<string>>
                {
                    { nameof(SetStateSnapshot), SetStateSnapshot },
                    { nameof(SetSelectedRegions), SetSelectedRegions },
                    { nameof(SetProbes), SetProbes },
                    { nameof(SetContactProfile), SetContactProfile },
                    { nameof(SetOpacity), SetOpacity },
                    { nameof(SetHighlightProbe), SetHighlightProbe },
                    { nameof(SetRegionHighlight), SetRegionHighlight },
                    { nameof(SetSelectedOnly), SetSelectedOnly },
                    { nameof(SetClinicalFocus), SetClinicalFocus },
                    { nameof(FocusRegion), FocusRegion },
                    { nameof(SetHemisphereFilter), SetHemisphereFilter },
                    { nameof(SetShellVisible), SetShellVisible },
                    { nameof(SetAxesVisible), SetAxesVisible },
                    { nameof(SetProbeGuideVisible), SetProbeGuideVisible },
                    { nameof(SetProbeUncertaintyVisible), SetProbeUncertaintyVisible },
                    { nameof(SetWeightedOit), SetWeightedOit },
                    { nameof(SetInteractionQuality), SetInteractionQuality },
                    { nameof(SetMriAppearance), SetMriAppearance },
                    { nameof(SetCoronalSlice), SetCoronalSlice },
                    { nameof(SetSlice), SetSlice },
                    { nameof(SetPostFxPreset), SetPostFxPreset },
                    { nameof(SetAntiAliasingMode), SetAntiAliasingMode },
                    { nameof(SetPostFxEnabled), SetPostFxEnabled },
                    { nameof(SetAmbientOcclusionEnabled), SetAmbientOcclusionEnabled },
                    { nameof(SetEnvironmentLighting), SetEnvironmentLighting },
                    { nameof(SetView), SetView },
                    { nameof(SetCameraPreset), SetCameraPreset },
                    { nameof(SetCameraView), SetCameraView },
                    { nameof(SetCameraAngle), SetCameraAngle },
                    { nameof(SetCameraLocked), SetCameraLocked },
                    { nameof(OnCanvasPointer), OnCanvasPointer },
                    { nameof(RequestScreenshot), RequestScreenshot },
                };
                return _commands;
            }
        }

        [System.Serializable]
        private class BridgeCommand
        {
            public string method;
            public string param;
            public int stateVersion;
            public long sentAt;
            public int payloadBytes;
            public bool largePayload;
            public long ts;
            public string status;
        }

        void Start()
        {
            try
            {
                _regionMgr = GetComponent<RegionManager>() ?? gameObject.AddComponent<RegionManager>();
                _regionMgr.OnSliceChanged += SyncProbeSliceToRegion;
                _probeRenderer = GetComponent<ProbeRenderer>() ?? gameObject.AddComponent<ProbeRenderer>();
                _cameraController = FindObjectOfType<CameraController>();
                _controller = GetComponent<BrainController>();
                _brainShell = _controller != null ? _controller.brainShell : FindObjectOfType<BrainShell>();
                if (_brainShell != null)
                {
                    _brainShell.Initialize(GetComponent<MeshLoader>());
                    _brainShell.OnSurfaceState = state => SendToReact("surfaceState", JsonUtility.ToJson(state));
                }
                _mriAppearanceLayerController = _controller != null
                    ? (_controller.mriAppearanceLayerController ?? GetComponent<MriAppearanceLayerController>())
                    : GetComponent<MriAppearanceLayerController>();
                if (_mriAppearanceLayerController == null)
                {
                    _mriAppearanceLayerController = gameObject.AddComponent<MriAppearanceLayerController>();
                }
                _mriAppearanceLayerController.OnSurfaceState = state =>
                    SendToReact("surfaceState", JsonUtility.ToJson(state));
                _atlasSectionController = GetComponent<AtlasSectionController>() ?? gameObject.AddComponent<AtlasSectionController>();
                _atlasSectionController.OnSectionState = state =>
                    SendToReact("sectionState", JsonUtility.ToJson(state));
                _coordAxes = _controller != null ? _controller.coordAxes : FindObjectOfType<CoordAxes>();
                _postFxController = _controller != null
                    ? (_controller.postFxController ?? GetComponent<RuntimePostFxController>())
                    : GetComponent<RuntimePostFxController>();
                if (_postFxController == null)
                {
                    _postFxController = gameObject.AddComponent<RuntimePostFxController>();
                }

                _regionMgr.OnOutlineProgress = (loaded, total) =>
                {
                    float pct = total > 0 ? (float)loaded / total : 0f;
                    SendToReact(
                        "outlineProgress",
                        $"{{\"loaded\":{loaded},\"failed\":{_regionMgr.OutlineFailed},\"excluded\":{_regionMgr.OutlineExcluded},\"total\":{total},\"pct\":{pct.ToString("F4", CultureInfo.InvariantCulture)}}}"
                    );
                };
                _regionMgr.OnStreamStage = (stage, progress, detail, loaded, total) =>
                {
                    float safeProgress = Mathf.Clamp01(progress);
                    SendToReact(
                        "streamStage",
                        $"{{\"stage\":\"{EscapeForJson(stage)}\",\"progress\":{safeProgress.ToString("F4", CultureInfo.InvariantCulture)},\"detail\":\"{EscapeForJson(detail)}\",\"loaded\":{loaded},\"failed\":{_regionMgr.OutlineFailed},\"excluded\":{_regionMgr.OutlineExcluded},\"total\":{total}}}"
                    );
                };

                _regionMgr.OnFillLoaded = key => SendToReact("fillLoaded", $"{{\"key\":\"{EscapeForJson(key)}\"}}");
                _regionMgr.OnFillFailed = key => SendToReact("fillFailed", $"{{\"key\":\"{EscapeForJson(key)}\"}}");
                _regionMgr.OnFillSuppressed = (key, reason) =>
                    SendToReact(
                        "fillSuppressed",
                        $"{{\"key\":\"{EscapeForJson(key)}\",\"reason\":\"{EscapeForJson(reason)}\"}}"
                    );

                if (_cameraController != null)
                {
                    _cameraController.OnPoseChanged += HandleCameraPoseChanged;
                }

                StartCoroutine(BootstrapRegionCatalogAndLoadOutlines());

                // Defaults: keep helper geometry hidden unless explicitly enabled from UI.
                SetShellVisible("false");
                SetAxesVisible("false");
                SetProbeGuideVisible("false");
                SetClinicalFocus("true");
                SetPostFxPreset("pinpoint");
                SetAntiAliasingMode("taa");
                SetAmbientOcclusionEnabled("true");
                SetEnvironmentLighting("0.62");
                SetMriAppearance("{\"enabled\":false,\"showOuterBoundary\":false,\"showSkull\":false,\"showBrainSurface\":false,\"showCcfLabels\":true,\"overlayMode\":\"ccf_only\"}");
            }
            catch (System.Exception ex)
            {
                SendToReact("error", $"ReactBridge.Start failed: {EscapeForJson(ex.Message)}");
                Debug.LogError($"[ReactBridge] Start failed: {ex}");
            }
        }

        void OnDestroy()
        {
            if (_cameraController != null)
            {
                _cameraController.OnPoseChanged -= HandleCameraPoseChanged;
            }
        }

        private IEnumerator BootstrapRegionCatalogAndLoadOutlines()
        {
            bool usedDynamicCatalog = false;
            int regionCount = RegionData.All.Count;
            string sourceTag = "default";

            var meshLoader = GetComponent<MeshLoader>();
            string baseUrl = meshLoader != null ? meshLoader.FlaskBase : "http://localhost:5000";
            string root = TrimTrailingSlash(baseUrl);
            string[] requestUrls = {
                $"{root}/api/regions_unity?scope=all",
                $"{root}/api/regions?scope=all",
                $"{root}/api/regions?scope=planner",
            };

            for (int u = 0; u < requestUrls.Length && !usedDynamicCatalog; u++)
            {
                string requestUrl = requestUrls[u];
                for (int attempt = 0; attempt < 3 && !usedDynamicCatalog; attempt++)
                {
                    using (var req = UnityWebRequest.Get(requestUrl))
                    {
                        req.timeout = 12 + attempt * 8;
                        yield return req.SendWebRequest();

#if UNITY_2020_2_OR_NEWER
                        bool ok = req.result == UnityWebRequest.Result.Success;
#else
                        bool ok = !(req.isNetworkError || req.isHttpError);
#endif
                        if (!ok || string.IsNullOrEmpty(req.downloadHandler?.text))
                        {
                            if (attempt == 2)
                            {
                                Debug.LogWarning(
                                    $"[ReactBridge] region catalog request failed url={requestUrl} code={req.responseCode} err={req.error}"
                                );
                            }
                            continue;
                        }
                        string resolvedSource =
                            requestUrl.Contains("regions_unity")
                                ? "backend_unity_all"
                                : requestUrl.Contains("scope=all")
                                    ? "backend_all"
                                    : "backend_planner";
                        if (TryApplyCatalogPayload(req.downloadHandler.text, resolvedSource, out int appliedCount))
                        {
                            usedDynamicCatalog = true;
                            regionCount = appliedCount;
                            sourceTag = _lastCatalogSource;
                            break;
                        }
                    }
                }
            }

            _lastCatalogCount = regionCount;
            _lastCatalogSource = sourceTag;
            if (usedDynamicCatalog)
            {
                _regionMgr.LoadAllOutlines();
                EmitReady(regionCount, sourceTag);
            }
            else
            {
                _regionMgr.ClearLoadedMeshes();
                regionCount = 0;
                _lastCatalogCount = 0;
                _lastCatalogSource = "backend_unavailable";
                SendToReact(
                    "error",
                    "Region catalog unavailable from backend. Waiting for backend and atlas readiness before loading outlines."
                );
                EmitReady(0, _lastCatalogSource);
            }
            StartCoroutine(RefreshCatalogAfterAtlasReady(root, regionCount));
            _cameraController?.ForceEmitPose();
        }

        private bool TryApplyCatalogPayload(string payload, string sourceTag, out int regionCount)
        {
            regionCount = _regionMgr != null ? _regionMgr.RegionCount : 0;
            if (_regionMgr == null || string.IsNullOrEmpty(payload)) return false;
            try
            {
                var parsed = JsonUtility.FromJson<RegionCatalogResponse>(payload);
                var mapped = ConvertRegionCatalog(parsed);
                if (mapped.Count <= 0) return false;
                _regionMgr.SetRegionCatalog(mapped);
                regionCount = _regionMgr.RegionCount;
                _lastCatalogCount = regionCount;
                _lastCatalogSource = sourceTag;
                return true;
            }
            catch (System.Exception ex)
            {
                Debug.LogWarning($"[ReactBridge] catalog parse failed ({sourceTag}): {ex.Message}");
                return false;
            }
        }

        private void EmitReady(int regions, string catalog)
        {
            SendToReact("ready", $"{{\"version\":\"1.2\",\"regions\":{regions},\"catalog\":\"{EscapeForJson(catalog)}\"}}");
        }

        private IEnumerator RefreshCatalogAfterAtlasReady(string root, int initialCount)
        {
            if (_catalogRefreshRunning) yield break;
            _catalogRefreshRunning = true;

            int knownCount = Mathf.Max(initialCount, _lastCatalogCount);
            const int refreshThreshold = 2;
            for (int attempt = 0; attempt < 90; attempt++)
            {
                using (var req = UnityWebRequest.Get($"{root}/api/status?includeRegions=0"))
                {
                    req.timeout = 8;
                    yield return req.SendWebRequest();

#if UNITY_2020_2_OR_NEWER
                    bool ok = req.result == UnityWebRequest.Result.Success;
#else
                    bool ok = !(req.isNetworkError || req.isHttpError);
#endif
                    if (!ok || string.IsNullOrEmpty(req.downloadHandler?.text))
                    {
                        yield return new WaitForSeconds(2f);
                        continue;
                    }

                    StatusSnapshot status = null;
                    try
                    {
                        status = JsonUtility.FromJson<StatusSnapshot>(req.downloadHandler.text);
                    }
                    catch
                    {
                        status = null;
                    }
                    if (status == null)
                    {
                        yield return new WaitForSeconds(2f);
                        continue;
                    }

                    int backendCount = Mathf.Max(
                        status.regionCount,
                        Mathf.Max(status.availableRegionCount, Mathf.Max(status.plannerRegionCount, status.indexedRegionCount))
                    );
                    bool shouldRefresh = backendCount > knownCount + refreshThreshold;
                    if (!shouldRefresh && status.atlasLoaded && knownCount <= 64 && backendCount > knownCount)
                    {
                        shouldRefresh = true;
                    }
                    if (shouldRefresh)
                    {
                        using (var r2 = UnityWebRequest.Get($"{root}/api/regions_unity?scope=all"))
                        {
                            r2.timeout = 18;
                            yield return r2.SendWebRequest();
#if UNITY_2020_2_OR_NEWER
                            bool ok2 = r2.result == UnityWebRequest.Result.Success;
#else
                            bool ok2 = !(r2.isNetworkError || r2.isHttpError);
#endif
                            if (ok2 && TryApplyCatalogPayload(r2.downloadHandler?.text, "backend_unity_all_refresh", out int updatedCount))
                            {
                                _regionMgr.LoadAllOutlines();
                                knownCount = Mathf.Max(knownCount, updatedCount);
                                EmitReady(updatedCount, _lastCatalogSource);
                            }
                        }
                    }

                    if (status.atlasLoaded && backendCount <= knownCount + 4) break;
                }
                yield return new WaitForSeconds(2f);
            }

            _catalogRefreshRunning = false;
        }

        private static string TrimTrailingSlash(string url)
        {
            if (string.IsNullOrEmpty(url)) return "http://localhost:5000";
            return url.TrimEnd('/');
        }

        private static List<RegionInfo> ConvertRegionCatalog(RegionCatalogResponse response)
        {
            var outList = new List<RegionInfo>();
            if (response?.regions == null) return outList;
            var seen = new HashSet<string>();
            foreach (var item in response.regions)
            {
                if (item == null || string.IsNullOrEmpty(item.key) || item.structureId <= 0) continue;
                if (!seen.Add(item.key)) continue;
                string category = string.IsNullOrEmpty(item.category) ? "dynamic" : item.category;
                outList.Add(new RegionInfo
                {
                    key = item.key,
                    structureId = item.structureId,
                    ap = item.ap,
                    dv = item.dv,
                    ml = item.ml,
                    category = category,
                    groupKey = string.IsNullOrEmpty(item.groupKey) ? string.Empty : item.groupKey,
                    groupName = string.IsNullOrEmpty(item.groupName) ? string.Empty : item.groupName,
                    groupNameCn = string.IsNullOrEmpty(item.groupNameCn) ? string.Empty : item.groupNameCn,
                    displayName = string.IsNullOrEmpty(item.name) ? item.key : item.name,
                    color = ResolveCatalogColor(item, category),
                    voxelCount = Mathf.Max(0, item.voxelCount),
                    nSubregions = Mathf.Max(0, item.nSubregions),
                    volumeAvailabilityKnown = item.volumeAvailabilityKnown,
                    presentInVolume = item.presentInVolume,
                });
            }
            return outList;
        }

        private static Color ResolveCatalogColor(RegionCatalogItem item, string category)
        {
            if (item != null && !string.IsNullOrEmpty(item.key))
            {
                // Keep curated core regions visually stable.
                var core = RegionData.Get(item.key);
                if (core != null)
                {
                    var cc = core.color;
                    cc.a = 1f;
                    return cc;
                }
            }

            Color fallback = CategoryColor(category, item?.structureId ?? 0, item?.key ?? string.Empty);
            if (item != null)
            {
                if (!string.IsNullOrEmpty(item.colorHex))
                {
                    var parsed = ParseColor(item.colorHex);
                    return HarmonizeCatalogColor(parsed, fallback, category);
                }
                if (item.colorR >= 0f && item.colorG >= 0f && item.colorB >= 0f)
                {
                    var rgb = new Color(
                        Mathf.Clamp01(item.colorR),
                        Mathf.Clamp01(item.colorG),
                        Mathf.Clamp01(item.colorB),
                        1f
                    );
                    return HarmonizeCatalogColor(rgb, fallback, category);
                }
            }
            return fallback;
        }

        private static Color HarmonizeCatalogColor(Color atlasColor, Color fallback, string categoryRaw)
        {
            atlasColor.a = 1f;
            fallback.a = 1f;

            string category = (categoryRaw ?? string.Empty).Trim().ToLowerInvariant();
            // Allen ontology rgb_triplet is informative but often too warm/dense for full-brain transparent stacks.
            // Blend toward a deterministic palette so regions remain distinguishable under OIT.
            float atlasWeight = category switch
            {
                "visual" => 0.34f,
                "hippocampus" => 0.32f,
                "thalamus" => 0.30f,
                "frontal" => 0.28f,
                "midbrain" => 0.26f,
                "basal_ganglia" => 0.26f,
                "atlas" => 0.18f,
                "dynamic" => 0.16f,
                _ => 0.22f,
            };
            Color mixed = Color.Lerp(fallback, atlasColor, atlasWeight);

            // Guard against "all-red" collapse in dense overlays.
            float redDominance = mixed.r - Mathf.Max(mixed.g, mixed.b);
            bool overlyWarm = redDominance > 0.21f && (mixed.g < 0.50f || mixed.b < 0.50f);
            if (overlyWarm)
            {
                mixed = Color.Lerp(mixed, fallback, 0.84f);
            }

            Color.RGBToHSV(mixed, out float h, out float s, out float v);
            if (h < 0.055f || h > 0.945f)
            {
                h = Mathf.Repeat(h + 0.12f, 1f);
            }
            s = Mathf.Clamp(s, 0.36f, 0.72f);
            v = Mathf.Clamp(v, 0.78f, 0.97f);
            var outColor = Color.HSVToRGB(h, s, v);
            outColor.a = 1f;
            return outColor;
        }

        private static Color CategoryColor(string categoryRaw, int structureId, string key)
        {
            // Keep curated/core regions visually consistent with legacy colors.
            var core = RegionData.Get(key);
            if (core != null)
            {
                var cc = core.color;
                cc.a = 1f;
                return cc;
            }

            string category = (categoryRaw ?? string.Empty).Trim().ToLowerInvariant();
            float categoryHueBias = category switch
            {
                "visual" => 0.54f,
                "hippocampus" => 0.83f,
                "thalamus" => 0.33f,
                "frontal" => 0.09f,
                "midbrain" => 0.72f,
                "basal_ganglia" => 0.15f,
                "atlas" => 0.58f,
                "dynamic" => 0.51f,
                _ => 0.62f,
            };

            uint hash = 2166136261u;
            string seed = $"{key}:{structureId}:{category}";
            for (int i = 0; i < seed.Length; i++)
            {
                hash ^= seed[i];
                hash *= 16777619u;
            }

            // Pinpoint-style palette: category-centric hue with bounded jitter and consistently high value.
            float jitter = (((hash & 1023u) / 1023f) - 0.5f) * 0.11f;
            float h = Mathf.Repeat(categoryHueBias + jitter + (structureId % 29) * 0.008f, 1f);
            float s = Mathf.Clamp01(0.68f + (((hash >> 10) & 127u) / 127f) * 0.18f);
            float v = Mathf.Clamp01(0.90f + (((hash >> 17) & 127u) / 127f) * 0.09f);
            if (category == "atlas" || category == "dynamic")
            {
                s = Mathf.Clamp01(0.56f + (((hash >> 10) & 127u) / 127f) * 0.16f);
                v = Mathf.Clamp01(0.92f + (((hash >> 17) & 127u) / 127f) * 0.07f);
            }
            var outColor = Color.HSVToRGB(h, s, v);
            outColor.a = 1f;
            return outColor;
        }

        private static Color ParseColor(string hex)
        {
            if (ColorUtility.TryParseHtmlString(hex, out var c))
            {
                c.a = 1f;
                return c;
            }
            return new Color(0.35f, 0.6f, 0.85f, 1f);
        }

        public void SetSelectedRegions(string jsonArray)
        {
            try
            {
                if (_regionMgr == null)
                {
                    _regionMgr = GetComponent<RegionManager>() ?? gameObject.AddComponent<RegionManager>();
                }
                string payload = (jsonArray ?? string.Empty).Trim();
                if (string.IsNullOrEmpty(payload) || payload == "null" || payload == "undefined")
                {
                    payload = "[]";
                }

                StringArrayWrapper wrapper = null;
                try
                {
                    wrapper = JsonUtility.FromJson<StringArrayWrapper>("{\"items\":" + payload + "}");
                }
                catch
                {
                    wrapper = null;
                }

                var raw = wrapper?.items ?? System.Array.Empty<string>();
                var dedup = new HashSet<string>();
                var cleaned = new List<string>(raw.Length);
                foreach (var item in raw)
                {
                    if (string.IsNullOrEmpty(item)) continue;
                    string key = item.Trim();
                    if (key.Length == 0) continue;
                    if (!dedup.Add(key)) continue;
                    cleaned.Add(key);
                }
                _regionMgr.SetSelected(cleaned);
                SendToReact("selectionUpdated", $"{{\"count\":{cleaned.Count}}}");
            }
            catch (System.Exception ex)
            {
                Debug.LogWarning($"[ReactBridge] SetSelectedRegions degraded: {ex.Message}");
                try
                {
                    _regionMgr?.SetSelected(System.Array.Empty<string>());
                    SendToReact("selectionUpdated", "{\"count\":0}");
                }
                catch
                {
                    SendToReact("error", $"SetSelectedRegions failed: {EscapeForJson(ex.Message)}");
                }
            }
        }

        public void SetProbes(string jsonArray)
        {
            try
            {
                _probeRenderer.SetProbes(jsonArray);
                SendToReact("probesUpdated", "{}");
            }
            catch (System.Exception ex)
            {
                SendToReact("error", $"SetProbes failed: {EscapeForJson(ex.Message)}");
                Debug.LogError($"[ReactBridge] SetProbes failed: {ex}");
            }
        }

        public void SetContactProfile(string json)
        {
            if (_regionMgr == null)
            {
                _regionMgr = GetComponent<RegionManager>() ?? gameObject.AddComponent<RegionManager>();
            }
            if (_regionMgr == null) return;
            string payloadRaw = (json ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(payloadRaw) || payloadRaw == "null" || payloadRaw == "undefined")
            {
                _regionMgr.SetContactProfile("standard", "hybrid", 0f, System.Array.Empty<string>());
                return;
            }
            if (!payloadRaw.StartsWith("{"))
            {
                _regionMgr.SetContactProfile("standard", "hybrid", 0f, System.Array.Empty<string>());
                return;
            }
            try
            {
                var payload = JsonUtility.FromJson<ContactProfileData>(payloadRaw);
                if (payload == null)
                {
                    _regionMgr.SetContactProfile("standard", "hybrid", 0f, System.Array.Empty<string>());
                    return;
                }
                _regionMgr.SetContactProfile(
                    payload.mode,
                    payload.style,
                    payload.minLengthUm,
                    payload.regions ?? System.Array.Empty<string>(),
                    payload.regionHemispheres
                );
            }
            catch (System.Exception ex)
            {
                Debug.LogWarning($"[ReactBridge] SetContactProfile ignored: {ex}");
                // Contact overlays are explanatory UI only. They must never block mesh loading or interaction.
                try
                {
                    _regionMgr.SetContactProfile("standard", "hybrid", 0f, System.Array.Empty<string>());
                }
                catch (System.Exception clearEx)
                {
                    Debug.LogWarning($"[ReactBridge] Clearing contact profile also failed: {clearEx}");
                }
            }
        }

        public void SetOpacity(string valueStr)
        {
            if (
                float.TryParse(
                    valueStr,
                    NumberStyles.Float,
                    CultureInfo.InvariantCulture,
                    out float value
                )
            )
            {
                _regionMgr.SetGlobalOpacity(value);
            }
        }

        public void SetHighlightProbe(string indexStr)
        {
            if (int.TryParse(indexStr, out int idx))
            {
                _probeRenderer.SetHighlight(idx);
            }
        }

        public void SetRegionHighlight(string json)
        {
            try
            {
                var data = JsonUtility.FromJson<RegionHighlightData>(json);
                if (data == null || string.IsNullOrEmpty(data.key)) return;
                _regionMgr.HighlightRegion(data.key, data.highlight);
            }
            catch
            {
                // Ignore malformed payload.
            }
        }

        public void SetSelectedOnly(string value)
        {
            _regionMgr.SetSelectedOnly(ParseBool(value));
        }

        public void SetClinicalFocus(string value)
        {
            if (_regionMgr == null) return;
            string trimmed = (value ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(trimmed))
            {
                _regionMgr.SetClinicalFocus(true, null);
                return;
            }
            if (!trimmed.StartsWith("{"))
            {
                _regionMgr.SetClinicalFocus(ParseBool(trimmed), null);
                return;
            }
            try
            {
                var payload = JsonUtility.FromJson<ClinicalFocusData>(trimmed);
                if (payload == null)
                {
                    _regionMgr.SetClinicalFocus(true, null);
                    return;
                }
                _regionMgr.SetClinicalFocus(payload.enabled, payload.categories, payload.groups, payload.keys);
            }
            catch (System.Exception ex)
            {
                SendToReact("error", $"SetClinicalFocus failed: {EscapeForJson(ex.Message)}");
            }
        }

        public void FocusRegion(string value)
        {
            if (_regionMgr == null || _cameraController == null) return;
            string key = string.Empty;
            float desiredDistance = float.NaN;
            string payload = (value ?? string.Empty).Trim();

            if (string.IsNullOrEmpty(payload)) return;
            if (!payload.StartsWith("{"))
            {
                key = payload;
            }
            else
            {
                try
                {
                    var req = JsonUtility.FromJson<FocusRegionData>(payload);
                    if (req != null)
                    {
                        key = (req.key ?? string.Empty).Trim();
                        desiredDistance = req.distance;
                    }
                }
                catch
                {
                    // Fallthrough: ignore malformed focus payload.
                }
            }

            if (string.IsNullOrEmpty(key)) return;
            if (!_regionMgr.TryGetRegionWorldPoint(key, out var point, out _)) return;

            _cameraController.FocusPoint(point, desiredDistance);
            string ack =
                "{" +
                $"\"key\":\"{EscapeForJson(key)}\"," +
                $"\"x\":{point.x.ToString("F4", CultureInfo.InvariantCulture)}," +
                $"\"y\":{point.y.ToString("F4", CultureInfo.InvariantCulture)}," +
                $"\"z\":{point.z.ToString("F4", CultureInfo.InvariantCulture)}" +
                "}";
            SendToReact("focusRegionAck", ack);
        }

        public void SetHemisphereFilter(string value)
        {
            _regionMgr.SetHemisphereFilter(value);
            _mriAppearanceLayerController?.SyncClipping(_regionMgr);
            _brainShell?.SyncClipping(_regionMgr);
        }

        public void SetShellVisible(string value)
        {
            bool visible = ParseBool(value);
            if (_brainShell != null)
            {
                _brainShell.SetVisible(visible);
                _brainShell.SyncClipping(_regionMgr);
            }
        }

        public void SetAxesVisible(string value)
        {
            bool visible = ParseBool(value);
            if (_coordAxes != null)
            {
                _coordAxes.gameObject.SetActive(visible);
            }
        }

        public void SetProbeGuideVisible(string value)
        {
            _probeRenderer?.SetShowExtensionLines(ParseBool(value));
        }

        public void SetProbeUncertaintyVisible(string value)
        {
            _probeRenderer?.SetShowUncertainty(ParseBool(value));
        }

        public void SetWeightedOit(string value)
        {
            if (_regionMgr == null) return;
            if (string.IsNullOrEmpty(value))
            {
                _regionMgr.SetWeightedOit(true);
                return;
            }

            string trimmed = value.Trim();
            if (trimmed.StartsWith("{"))
            {
                try
                {
                    var payload = JsonUtility.FromJson<WeightedOitData>(trimmed);
                    if (payload != null)
                    {
                        float weight = payload.weightScale > 0f ? payload.weightScale : -1f;
                        _regionMgr.SetWeightedOit(payload.enabled, weight);
                        return;
                    }
                }
                catch
                {
                    // Fallback to bool parse below for malformed payload.
                }
            }

            if (float.TryParse(trimmed, NumberStyles.Float, CultureInfo.InvariantCulture, out float scaleOnly))
            {
                _regionMgr.SetWeightedOit(true, Mathf.Max(0.05f, scaleOnly));
                return;
            }

            _regionMgr.SetWeightedOit(ParseBool(trimmed));
        }

        public void SetInteractionQuality(string value)
        {
            string mode = string.IsNullOrWhiteSpace(value) ? "high" : value.Trim();
            _regionMgr?.SetInteractionQuality(mode);
            _mriAppearanceLayerController?.SetInteractionQuality(mode);
        }

        public void SetMriAppearance(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                _regionMgr?.SetMriAppearance(false);
                _mriAppearanceLayerController?.Apply(false, false, false, false, true, "ccf_only");
                _atlasSectionController?.Configure(false, "ccf_only");
                _brainShell?.SetSuppressed(false);
                return;
            }
            string trimmed = value.Trim();
            if (trimmed.StartsWith("{"))
            {
                try
                {
                    var payload = JsonUtility.FromJson<MriAppearanceData>(trimmed);
                    if (payload != null)
                    {
                        string overlayMode = string.IsNullOrWhiteSpace(payload.overlayMode)
                            ? (payload.enabled ? "mri_ccf_overlay" : "ccf_only")
                            : payload.overlayMode;
                        _regionMgr?.SetMriAppearance(
                            payload.enabled,
                            payload.showOuterBoundary,
                            payload.showSkull,
                            payload.showBrainSurface,
                            payload.showCcfLabels,
                            overlayMode
                        );
                        _mriAppearanceLayerController?.Apply(
                            MriAppearanceLayerController.ShouldRenderNativeSurfaces(payload.enabled, payload.surfaceRenderer),
                            payload.showOuterBoundary,
                            payload.showSkull,
                            payload.showBrainSurface,
                            payload.showCcfLabels,
                            overlayMode
                        );
                        _mriAppearanceLayerController?.SyncClipping(_regionMgr);
                        _atlasSectionController?.Configure(payload.enabled, overlayMode);
                        _brainShell?.SetSuppressed(payload.enabled && payload.showBrainSurface);
                        return;
                    }
                }
                catch
                {
                    // fallback to bool parse below
                }
            }
            bool enabled = ParseBool(trimmed);
            _regionMgr?.SetMriAppearance(enabled);
            _mriAppearanceLayerController?.Apply(enabled, enabled, enabled, enabled, true, enabled ? "mri_ccf_overlay" : "ccf_only");
            _atlasSectionController?.Configure(enabled, enabled ? "mri_ccf_overlay" : "ccf_only");
            _brainShell?.SetSuppressed(enabled);
            _mriAppearanceLayerController?.SyncClipping(_regionMgr);
        }

        private void SyncProbeSliceToRegion()
        {
            if (_regionMgr == null) return;
            _mriAppearanceLayerController?.SyncClipping(_regionMgr);
            _brainShell?.SyncClipping(_regionMgr);
            string axis = _regionMgr.SliceAxis == 0 ? "x" : (_regionMgr.SliceAxis == 2 ? "z" : "y");
            _probeRenderer?.SetSlice(
                axis,
                _regionMgr.SliceEnabled,
                _regionMgr.SliceCenter,
                _regionMgr.SliceThicknessMm,
                _regionMgr.SliceUseSlab
            );
        }

        public void SetCoronalSlice(string value)
        {
            // Backward compatibility alias.
            if (_regionMgr == null) return;
            bool enabled = false;
            float normalized = 1f;
            string trimmed = (value ?? string.Empty).Trim();
            if (string.IsNullOrEmpty(trimmed))
            {
                _regionMgr.SetSlice("y", false, 1f, 0.25f, false, false, 0.18f);
                SyncProbeSliceToRegion();
                return;
            }
            if (trimmed.StartsWith("{"))
            {
                try
                {
                    var payload = JsonUtility.FromJson<CoronalSliceData>(trimmed);
                    if (payload != null)
                    {
                        enabled = payload.enabled;
                        normalized = payload.normalized;
                        if (float.IsNaN(normalized) || float.IsInfinity(normalized)) normalized = payload.value;
                        if (float.IsNaN(normalized) || float.IsInfinity(normalized)) normalized = 1f;
                        _regionMgr.SetSlice("y", enabled, Mathf.Clamp01(normalized), payload.thicknessMm, payload.useSlab, payload.snapToBoundary, payload.snapThresholdMm);
                        SyncProbeSliceToRegion();
                        return;
                    }
                }
                catch
                {
                    // Fallback to primitive parse below.
                }
            }
            if (float.TryParse(trimmed, NumberStyles.Float, CultureInfo.InvariantCulture, out float parsed))
            {
                normalized = Mathf.Clamp01(parsed);
                enabled = normalized < 0.9995f;
                _regionMgr.SetSlice("y", enabled, normalized, 0.25f, false, false, 0.18f);
                SyncProbeSliceToRegion();
                return;
            }
            enabled = ParseBool(trimmed);
            _regionMgr.SetSlice("y", enabled, enabled ? 0.65f : 1f, 0.25f, false, false, 0.18f);
            SyncProbeSliceToRegion();
        }

        public void SetSlice(string value)
        {
            if (_regionMgr == null) return;
            if (string.IsNullOrWhiteSpace(value))
            {
                _regionMgr.SetSlice("y", false, 1f, 0.25f, false, false, 0.18f);
                SyncProbeSliceToRegion();
                return;
            }

            string trimmed = value.Trim();
            if (!trimmed.StartsWith("{"))
            {
                SetCoronalSlice(trimmed);
                return;
            }

            try
            {
                var payload = JsonUtility.FromJson<SliceData>(trimmed);
                if (payload == null)
                {
                    _regionMgr.SetSlice("y", false, 1f, 0.25f, false, false, 0.18f);
                    SyncProbeSliceToRegion();
                    return;
                }

                float normalized = payload.normalized;
                if (float.IsNaN(normalized) || float.IsInfinity(normalized)) normalized = payload.value;
                if (float.IsNaN(normalized) || float.IsInfinity(normalized)) normalized = 1f;
                bool enabled = payload.enabled || normalized < 0.9995f;
                float thicknessMm = (float.IsNaN(payload.thicknessMm) || float.IsInfinity(payload.thicknessMm))
                    ? 0.25f
                    : payload.thicknessMm;
                float snapThresholdMm = (float.IsNaN(payload.snapThresholdMm) || float.IsInfinity(payload.snapThresholdMm))
                    ? 0.18f
                    : payload.snapThresholdMm;
                _regionMgr.SetSlice(
                    payload.axis,
                    enabled,
                    Mathf.Clamp01(normalized),
                    Mathf.Clamp(thicknessMm, 0f, 2f),
                    payload.useSlab,
                    payload.snapToBoundary,
                    Mathf.Clamp(snapThresholdMm, 0f, 2f)
                );
                _atlasSectionController?.SetRequestKey(payload.requestKey);
                SyncProbeSliceToRegion();
            }
            catch (System.Exception ex)
            {
                SendToReact("error", $"SetSlice failed: {EscapeForJson(ex.Message)}");
            }
        }

        public void SetPostFxPreset(string value)
        {
            _postFxController?.SetPresetByName(value);
        }

        public void SetAntiAliasingMode(string value)
        {
            _postFxController?.SetAntialiasingMode(value);
        }

        public void SetPostFxEnabled(string value)
        {
            _postFxController?.SetPostFxEnabled(ParseBool(value));
        }

        public void SetAmbientOcclusionEnabled(string value)
        {
            _postFxController?.SetAmbientOcclusionEnabled(ParseBool(value));
        }

        public void SetEnvironmentLighting(string value)
        {
            if (_controller == null)
            {
                _controller = GetComponent<BrainController>();
            }
            if (_controller == null) return;
            float normalized = 0.62f;
            if (!float.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out normalized))
            {
                normalized = 0.62f;
            }
            _controller.SetEnvironmentLighting(normalized);
        }

        // Compatibility alias used by frontend snapshot transport.
        public void SetView(string viewName)
        {
            SetCameraView(viewName);
        }

        // Legacy compatibility alias: some frontend snapshots still call SetCameraPreset.
        public void SetCameraPreset(string viewName)
        {
            SetCameraView(viewName);
        }

        // Lightweight snapshot hook: frontend also sends granular commands afterwards.
        public void SetStateSnapshot(string json)
        {
            if (!string.IsNullOrWhiteSpace(json))
            {
                try
                {
                    var snapshot = JsonUtility.FromJson<StateSnapshotData>(json);
                    if (snapshot != null && snapshot.contactProfile != null)
                    {
                        var contact = snapshot.contactProfile;
                        _regionMgr?.SetContactProfile(
                            contact.mode,
                            contact.style,
                            contact.minLengthUm,
                            contact.regions ?? System.Array.Empty<string>(),
                            contact.regionHemispheres
                        );
                    }
                    if (snapshot != null && snapshot.mriAppearance != null)
                    {
                        string overlayMode = string.IsNullOrWhiteSpace(snapshot.mriAppearance.overlayMode)
                            ? (snapshot.mriAppearance.enabled ? "mri_ccf_overlay" : "ccf_only")
                            : snapshot.mriAppearance.overlayMode;
                        _regionMgr?.SetMriAppearance(
                            snapshot.mriAppearance.enabled,
                            snapshot.mriAppearance.showOuterBoundary,
                            snapshot.mriAppearance.showSkull,
                            snapshot.mriAppearance.showBrainSurface,
                            snapshot.mriAppearance.showCcfLabels,
                            overlayMode
                        );
                        _mriAppearanceLayerController?.Apply(
                            MriAppearanceLayerController.ShouldRenderNativeSurfaces(snapshot.mriAppearance.enabled, snapshot.mriAppearance.surfaceRenderer),
                            snapshot.mriAppearance.showOuterBoundary,
                            snapshot.mriAppearance.showSkull,
                            snapshot.mriAppearance.showBrainSurface,
                            snapshot.mriAppearance.showCcfLabels,
                            overlayMode
                        );
                        _mriAppearanceLayerController?.SyncClipping(_regionMgr);
                        _brainShell?.SetSuppressed(snapshot.mriAppearance.enabled && snapshot.mriAppearance.showBrainSurface);
                        _atlasSectionController?.Configure(snapshot.mriAppearance.enabled, overlayMode);
                    }
                }
                catch
                {
                    // Ignore malformed snapshot payload; granular commands still follow.
                }
            }
            SendToReact(
                "snapshotApplied",
                $"{{\"ok\":true,\"ts\":{System.DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}}}"
            );
        }

        public void Heartbeat(string payload)
        {
            string safe = string.IsNullOrEmpty(payload) ? "{}" : payload;
            SendToReact("heartbeatAck", safe);
        }

        public void SetCameraView(string viewName)
        {
            _cameraController?.SetView(viewName);
            _cameraController?.ForceEmitPose();
        }

        public void SetCameraAngle(string json)
        {
            try
            {
                var data = JsonUtility.FromJson<CameraAngleData>(json);
                if (data == null) return;
                _cameraController?.AnimateTo(data.theta, data.phi, data.distance);
                _cameraController?.ForceEmitPose();
            }
            catch
            {
                // Ignore malformed payload.
            }
        }

        public void SetCameraLocked(string value)
        {
            bool locked = ParseBool(value);
            _cameraController?.SetLocked(locked);
            _cameraController?.ForceEmitPose();
        }

        /// <summary>
        /// Called by JS host on click/tap. Performs native Unity raycast picking.
        /// </summary>
        public void OnCanvasPointer(string json)
        {
            try
            {
                var data = JsonUtility.FromJson<CanvasPointerData>(json);
                if (data == null) return;
                if (!string.IsNullOrEmpty(data.type) && data.type != "click") return;

                Camera cam = _cameraController != null ? _cameraController.ControlledCamera : Camera.main;
                if (cam == null) return;

                float width = data.width > 0f ? data.width : Screen.width;
                float height = data.height > 0f ? data.height : Screen.height;
                if (width <= 1f || height <= 1f) return;

                float sx = (data.x / width) * Screen.width;
                float sy = (1f - (data.y / height)) * Screen.height;
                if (_regionMgr.TryPickRegionFromScreenPoint(new Vector2(sx, sy), cam, out var pick))
                {
                    float ap = pick.worldPoint.z;
                    float dv = -pick.worldPoint.y;
                    float ml = pick.worldPoint.x;
                    string payload =
                        "{" +
                        $"\"key\":\"{EscapeForJson(pick.key)}\"," +
                        $"\"hemisphere\":\"{EscapeForJson(pick.hemisphere)}\"," +
                        $"\"world\":{{\"x\":{pick.worldPoint.x.ToString("F4", CultureInfo.InvariantCulture)},\"y\":{pick.worldPoint.y.ToString("F4", CultureInfo.InvariantCulture)},\"z\":{pick.worldPoint.z.ToString("F4", CultureInfo.InvariantCulture)}}}," +
                        $"\"coords\":{{\"ap\":{ap.ToString("F4", CultureInfo.InvariantCulture)},\"dv\":{dv.ToString("F4", CultureInfo.InvariantCulture)},\"ml\":{ml.ToString("F4", CultureInfo.InvariantCulture)}}}" +
                        "}";
                    SendToReact("regionClicked", payload);
                }
            }
            catch (System.Exception ex)
            {
                SendToReact("error", $"OnCanvasPointer failed: {EscapeForJson(ex.Message)}");
            }
        }

        public void RequestScreenshot(string _)
        {
            StartCoroutine(CaptureScreenshot());
        }

        private void HandleCameraPoseChanged(float theta, float phi, float distance)
        {
            var pivot = _cameraController.PivotPoint;
            var camera = _cameraController.ControlledCamera;
            string payload =
                "{" +
                $"\"theta\":{theta.ToString("F6", CultureInfo.InvariantCulture)}," +
                $"\"phi\":{phi.ToString("F6", CultureInfo.InvariantCulture)}," +
                $"\"distance\":{distance.ToString("F6", CultureInfo.InvariantCulture)}," +
                $"\"pivot\":{JsonUtility.ToJson(pivot)}," +
                $"\"fov\":{(camera != null ? camera.fieldOfView : 38f).ToString("F6", CultureInfo.InvariantCulture)}" +
                "}";
            SendToReact("cameraPose", payload);
        }

        private static bool ParseBool(string value)
        {
            string v = (value ?? string.Empty).Trim().ToLowerInvariant();
            return v == "1" || v == "true" || v == "yes" || v == "on";
        }

        private void SendToReact(string eventName, string jsonData)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            BP_NotifyReact(eventName, jsonData);
#else
            Debug.Log($"[Bridge->React] {eventName}: {jsonData}");
#endif
        }

        private static string EscapeForJson(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return string.Empty;
            return raw.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        private System.Collections.IEnumerator CaptureScreenshot()
        {
            yield return new WaitForEndOfFrame();
            var tex = ScreenCapture.CaptureScreenshotAsTexture();
            byte[] png = tex.EncodeToPNG();
            string base64 = System.Convert.ToBase64String(png);
            Destroy(tex);
            SendToReact("screenshot", $"{{\"data\":\"data:image/png;base64,{base64}\"}}");
        }

        [System.Serializable]
        private class StringArrayWrapper
        {
            public string[] items;
        }

        [System.Serializable]
        private class RegionHighlightData
        {
            public string key;
            public bool highlight;
        }

        [System.Serializable]
        private class CameraAngleData
        {
            public float theta;
            public float phi;
            public float distance;
        }

        [System.Serializable]
        private class WeightedOitData
        {
            public bool enabled = true;
            public float weightScale = 1f;
        }

        [System.Serializable]
        private class ClinicalFocusData
        {
            public bool enabled = true;
            public string[] categories;
            public string[] groups;
            public string[] keys;
        }

        [System.Serializable]
        private class FocusRegionData
        {
            public string key;
            public float distance = -1f;
        }

        [System.Serializable]
        private class CoronalSliceData
        {
            public bool enabled = false;
            public float normalized = 1f;
            public float value = 1f;
            public float thicknessMm = 0.25f;
            public bool useSlab = false;
            public bool snapToBoundary = false;
            public float snapThresholdMm = 0.18f;
        }

        [System.Serializable]
        private class SliceData
        {
            public string requestKey;
            public string axis = "y";
            public bool enabled = false;
            public float normalized = 1f;
            public float value = 1f;
            public float thicknessMm = 0.25f;
            public bool useSlab = false;
            public bool snapToBoundary = false;
            public float snapThresholdMm = 0.18f;
        }

        [System.Serializable]
        private class ContactProfileData
        {
            public string mode = "standard";
            public string style = "hybrid";
            public float minLengthUm = 0f;
            public string[] regions;
            public RegionManager.ContactRegionHemispheres[] regionHemispheres;
        }

        [System.Serializable]
        private class MriAppearanceData
        {
            public bool enabled = false;
            public string surfaceRenderer = "unity";
            public bool showOuterBoundary = true;
            public bool showSkull = true;
            public bool showBrainSurface = true;
            public bool showCcfLabels = true;
            public string overlayMode = "mri_ccf_overlay";
        }

        [System.Serializable]
        private class StateSnapshotData
        {
            public ContactProfileData contactProfile;
            public MriAppearanceData mriAppearance;
        }

        [System.Serializable]
        private class CanvasPointerData
        {
            public string type;
            public float x;
            public float y;
            public float width;
            public float height;
        }

        [System.Serializable]
        private class RegionCatalogResponse
        {
            public RegionCatalogItem[] regions;
        }

        [System.Serializable]
        private class RegionCatalogItem
        {
            public string key;
            public int structureId;
            public float ap;
            public float dv;
            public float ml;
            public string category;
            public string name;
            public string colorHex;
            public float colorR = -1f;
            public float colorG = -1f;
            public float colorB = -1f;
            public string groupKey;
            public string groupName;
            public string groupNameCn;
            public int voxelCount;
            public int nSubregions;
            public bool volumeAvailabilityKnown;
            public bool presentInVolume;
        }

        [System.Serializable]
        private class StatusSnapshot
        {
            public bool atlasLoaded;
            public int regionCount;
            public int availableRegionCount;
            public int plannerRegionCount;
            public int indexedRegionCount;
        }
    }
}
