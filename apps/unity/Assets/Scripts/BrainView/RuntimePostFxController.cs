using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace BrainPlanner.BrainView
{
    /// <summary>
    /// Runtime post-processing bootstrapper for BrainPlanner WebGL.
    /// Ensures URP post-processing is enabled and applies a high-clarity visual preset.
    /// </summary>
    [DisallowMultipleComponent]
    public class RuntimePostFxController : MonoBehaviour
    {
        public enum VisualPreset
        {
            Balanced,
            HighContrast,
            Publication,
            Pinpoint,
            Mri,
        }

        [Header("Bootstrap")]
        public bool EnableRuntimePostFx = true;
        public bool AutoBootstrap = true;
        public bool CreateGlobalVolumeIfMissing = true;
        public string RuntimeVolumeName = "BP_RuntimePostFX";

        [Header("Preset")]
        public VisualPreset DefaultPreset = VisualPreset.Pinpoint;
        [Tooltip("none | fxaa | smaa | taa")]
        public string DefaultAntialiasing = "taa";
        public bool EnableAmbientOcclusion = true;

        [Header("Optional Template")]
        [Tooltip("Optional template profile. Runtime will clone it before applying overrides.")]
        public VolumeProfile ProfileTemplate;

        private Volume _globalVolume;
        private VolumeProfile _runtimeProfile;
        private Camera _camera;
        private UniversalAdditionalCameraData _cameraData;

        private static readonly BindingFlags ReflectionFlags =
            BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        void Start()
        {
            if (!AutoBootstrap) return;
            StartCoroutine(BootstrapNextFrame());
        }

        private IEnumerator BootstrapNextFrame()
        {
            yield return null;
            Bootstrap(null);
        }

        public void Bootstrap(Camera cameraOverride)
        {
            if (!EnableRuntimePostFx) return;

            _camera = cameraOverride != null ? cameraOverride : ResolveCamera();
            if (_camera == null)
            {
                Debug.LogWarning("[PostFX] Camera not found, skipping bootstrap.");
                return;
            }

            _cameraData = _camera.GetComponent<UniversalAdditionalCameraData>();
            if (_cameraData == null)
            {
                _cameraData = _camera.gameObject.AddComponent<UniversalAdditionalCameraData>();
            }
            _cameraData.renderPostProcessing = true;

            EnsureGlobalVolume();
            if (_globalVolume == null || _runtimeProfile == null)
            {
                Debug.LogWarning("[PostFX] Global volume/profile unavailable.");
                return;
            }

            int volumeLayer = _globalVolume.gameObject.layer;
            _cameraData.volumeLayerMask |= 1 << volumeLayer;
            _cameraData.volumeTrigger = _camera.transform;

            ApplyPreset(DefaultPreset);
            SetAntialiasingMode(DefaultAntialiasing);
            SetAmbientOcclusionEnabled(EnableAmbientOcclusion);
            Debug.Log($"[PostFX] Ready preset={DefaultPreset} aa={DefaultAntialiasing} ao={EnableAmbientOcclusion}");
        }

        public void SetPresetByName(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                ApplyPreset(DefaultPreset);
                return;
            }
            string key = value.Trim().ToLowerInvariant();
            if (key == "balanced") ApplyPreset(VisualPreset.Balanced);
            else if (key == "publication" || key == "paper") ApplyPreset(VisualPreset.Publication);
            else if (key == "pinpoint" || key == "planning" || key == "clinical") ApplyPreset(VisualPreset.Pinpoint);
            else if (key == "mri" || key == "mri_hybrid" || key == "hybrid_mri") ApplyPreset(VisualPreset.Mri);
            else ApplyPreset(VisualPreset.HighContrast);
        }

        public void SetPostFxEnabled(bool enabled)
        {
            EnableRuntimePostFx = enabled;
            if (_cameraData != null)
            {
                _cameraData.renderPostProcessing = enabled;
            }
            if (_globalVolume != null)
            {
                _globalVolume.weight = enabled ? 1f : 0f;
                _globalVolume.enabled = true;
            }
        }

        public void SetAntialiasingMode(string mode)
        {
            DefaultAntialiasing = string.IsNullOrWhiteSpace(mode) ? "taa" : mode.Trim().ToLowerInvariant();
            if (_cameraData == null)
            {
                _camera = ResolveCamera();
                _cameraData = _camera != null ? _camera.GetComponent<UniversalAdditionalCameraData>() : null;
            }
            if (_cameraData == null) return;

            _cameraData.renderPostProcessing = true;
            object aaValue = ResolveAntialiasingValue(_cameraData, DefaultAntialiasing);
            if (aaValue != null)
            {
                SetPropertyValue(_cameraData, "antialiasing", aaValue);
            }

            object qualityValue = ResolveEnumValue(_cameraData, "antialiasingQuality", new[] { "High", "3" });
            if (qualityValue != null)
            {
                SetPropertyValue(_cameraData, "antialiasingQuality", qualityValue);
            }
        }

        public void SetAmbientOcclusionEnabled(bool enabled)
        {
            EnableAmbientOcclusion = enabled;
            ApplySsaoRendererFeature(enabled, intensity: 0.55f, radius: 0.28f, directLightStrength: 0.26f);
        }

        public void SetExposureBias(float exposure)
        {
            if (!EnsureProfileReady()) return;
            var color = GetOrAddOverride<ColorAdjustments>();
            if (color == null) return;
            Override(color.postExposure, Mathf.Clamp(exposure, -1.2f, 1.2f));
        }

        public void ApplyPreset(VisualPreset preset)
        {
            if (!EnsureProfileReady()) return;
            if (_cameraData != null) _cameraData.renderPostProcessing = true;

            var tone = GetOrAddOverride<Tonemapping>();
            var color = GetOrAddOverride<ColorAdjustments>();
            var bloom = GetOrAddOverride<Bloom>();
            var vignette = GetOrAddOverride<Vignette>();

            Override(tone.mode, TonemappingMode.ACES);

            switch (preset)
            {
                case VisualPreset.Balanced:
                    Override(color.postExposure, 0.18f);
                    Override(color.contrast, 16f);
                    Override(color.saturation, 8f);
                    Override(bloom.threshold, 1.12f);
                    Override(bloom.intensity, 0.17f);
                    Override(bloom.scatter, 0.64f);
                    Override(vignette.intensity, 0.10f);
                    Override(vignette.smoothness, 0.34f);
                    ApplySsaoRendererFeature(EnableAmbientOcclusion, intensity: 0.45f, radius: 0.22f, directLightStrength: 0.24f);
                    break;
                case VisualPreset.Publication:
                    Override(color.postExposure, 0.52f);
                    Override(color.contrast, 32f);
                    Override(color.saturation, 18f);
                    Override(bloom.threshold, 0.94f);
                    Override(bloom.intensity, 0.32f);
                    Override(bloom.scatter, 0.71f);
                    Override(vignette.intensity, 0.06f);
                    Override(vignette.smoothness, 0.24f);
                    ApplySsaoRendererFeature(EnableAmbientOcclusion, intensity: 0.68f, radius: 0.33f, directLightStrength: 0.32f);
                    break;
                case VisualPreset.Pinpoint:
                    // Pinpoint-style: prioritize region separability and depth readability.
                    Override(color.postExposure, 0.34f);
                    Override(color.contrast, 16f);
                    Override(color.saturation, 11f);
                    Override(bloom.threshold, 1.12f);
                    Override(bloom.intensity, 0.11f);
                    Override(bloom.scatter, 0.52f);
                    Override(vignette.intensity, 0.03f);
                    Override(vignette.smoothness, 0.18f);
                    ApplySsaoRendererFeature(EnableAmbientOcclusion, intensity: 0.58f, radius: 0.30f, directLightStrength: 0.30f);
                    break;
                case VisualPreset.Mri:
                    // MRI-hybrid style: higher exposure, lower saturation, stronger depth cues.
                    Override(color.postExposure, 0.68f);
                    Override(color.contrast, 20f);
                    Override(color.saturation, -22f);
                    Override(bloom.threshold, 1.22f);
                    Override(bloom.intensity, 0.08f);
                    Override(bloom.scatter, 0.42f);
                    Override(vignette.intensity, 0.04f);
                    Override(vignette.smoothness, 0.20f);
                    ApplySsaoRendererFeature(EnableAmbientOcclusion, intensity: 0.64f, radius: 0.34f, directLightStrength: 0.34f);
                    break;
                case VisualPreset.HighContrast:
                default:
                    Override(color.postExposure, 0.34f);
                    Override(color.contrast, 26f);
                    Override(color.saturation, 13f);
                    Override(bloom.threshold, 1.0f);
                    Override(bloom.intensity, 0.24f);
                    Override(bloom.scatter, 0.68f);
                    Override(vignette.intensity, 0.08f);
                    Override(vignette.smoothness, 0.30f);
                    ApplySsaoRendererFeature(EnableAmbientOcclusion, intensity: 0.56f, radius: 0.28f, directLightStrength: 0.26f);
                    break;
            }

            DefaultPreset = preset;
        }

        private bool EnsureProfileReady()
        {
            if (_globalVolume == null || _runtimeProfile == null)
            {
                Bootstrap(null);
            }
            return _runtimeProfile != null;
        }

        private void EnsureGlobalVolume()
        {
            if (_globalVolume == null)
            {
                _globalVolume = FindGlobalVolume();
            }

            if (_globalVolume == null && CreateGlobalVolumeIfMissing)
            {
                var go = new GameObject(RuntimeVolumeName);
                go.transform.SetParent(transform, false);
                go.layer = 0;
                _globalVolume = go.AddComponent<Volume>();
                _globalVolume.isGlobal = true;
                _globalVolume.priority = 60f;
                _globalVolume.weight = 1f;
            }

            if (_globalVolume == null) return;

            if (_runtimeProfile == null)
            {
                VolumeProfile source = ProfileTemplate != null ? ProfileTemplate : _globalVolume.sharedProfile;
                _runtimeProfile = source != null ? Instantiate(source) : ScriptableObject.CreateInstance<VolumeProfile>();
                _runtimeProfile.name = "BP_RuntimeProfile_Instance";
                _globalVolume.sharedProfile = _runtimeProfile;
            }
        }

        private Volume FindGlobalVolume()
        {
            var volumes = FindObjectsOfType<Volume>(true);
            for (int i = 0; i < volumes.Length; i++)
            {
                var v = volumes[i];
                if (v != null && v.isGlobal)
                {
                    return v;
                }
            }
            return null;
        }

        private Camera ResolveCamera()
        {
            if (_camera != null) return _camera;
            _camera = Camera.main;
            if (_camera == null) _camera = FindObjectOfType<Camera>();
            return _camera;
        }

        private T GetOrAddOverride<T>() where T : VolumeComponent
        {
            if (_runtimeProfile == null) return null;
            if (!_runtimeProfile.TryGet(out T component))
            {
                component = _runtimeProfile.Add<T>(true);
            }
            if (component != null)
            {
                component.active = true;
            }
            return component;
        }

        private static void Override<T>(VolumeParameter<T> parameter, T value)
        {
            if (parameter == null) return;
            parameter.overrideState = true;
            parameter.value = value;
        }

        private void ApplySsaoRendererFeature(bool enabled, float intensity, float radius, float directLightStrength)
        {
            if (_cameraData == null) return;
            var renderer = _cameraData.scriptableRenderer;
            if (renderer == null) return;

            IList features = ResolveRendererFeatureList(renderer);
            if (features == null) return;

            for (int i = 0; i < features.Count; i++)
            {
                var feature = features[i] as ScriptableRendererFeature;
                if (feature == null) continue;
                string typeName = feature.GetType().Name.ToLowerInvariant();
                if (!typeName.Contains("ambientocclusion") && !typeName.Contains("ssao")) continue;

                feature.SetActive(enabled);
                TrySetSsaoSettings(feature, intensity, radius, directLightStrength);
            }
        }

        private static IList ResolveRendererFeatureList(ScriptableRenderer renderer)
        {
            if (renderer == null) return null;
            var property = renderer.GetType().GetProperty("rendererFeatures", ReflectionFlags);
            if (property != null)
            {
                return property.GetValue(renderer) as IList;
            }
            var field = renderer.GetType().GetField("m_RendererFeatures", ReflectionFlags);
            if (field != null)
            {
                return field.GetValue(renderer) as IList;
            }
            return null;
        }

        private static void TrySetSsaoSettings(ScriptableRendererFeature feature, float intensity, float radius, float directLightStrength)
        {
            if (feature == null) return;

            var settingsField = feature.GetType().GetField("m_Settings", ReflectionFlags);
            if (settingsField == null) return;
            object settings = settingsField.GetValue(feature);
            if (settings == null) return;

            SetNumericField(settings, "Intensity", intensity);
            SetNumericField(settings, "Radius", radius);
            SetNumericField(settings, "DirectLightingStrength", directLightStrength);
            settingsField.SetValue(feature, settings);
        }

        private static bool SetNumericField(object target, string fieldName, float value)
        {
            if (target == null) return false;
            var field = target.GetType().GetField(fieldName, ReflectionFlags);
            if (field == null) return false;
            if (field.FieldType == typeof(float))
            {
                field.SetValue(target, value);
                return true;
            }
            if (field.FieldType == typeof(int))
            {
                field.SetValue(target, Mathf.RoundToInt(value));
                return true;
            }
            return false;
        }

        private static object ResolveAntialiasingValue(UniversalAdditionalCameraData camData, string mode)
        {
            string key = (mode ?? string.Empty).Trim().ToLowerInvariant();
            string[] candidates = key switch
            {
                "none" => new[] { "None" },
                "fxaa" => new[] { "FastApproximateAntialiasing", "FXAA" },
                "smaa" => new[] { "SubpixelMorphologicalAntiAliasing", "SMAA" },
                "taa" => new[] { "TemporalAntiAliasing", "TAA", "FastApproximateAntialiasing" },
                _ => new[] { "TemporalAntiAliasing", "TAA", "FastApproximateAntialiasing" },
            };
            object value = ResolveEnumValue(camData, "antialiasing", candidates);
            if (value != null) return value;
            return ResolveEnumValue(camData, "antialiasing", new[] { "FastApproximateAntialiasing", "None" });
        }

        private static object ResolveEnumValue(object owner, string propertyName, string[] candidates)
        {
            if (owner == null) return null;
            var prop = owner.GetType().GetProperty(propertyName, ReflectionFlags);
            if (prop == null || !prop.PropertyType.IsEnum) return null;
            var enumType = prop.PropertyType;
            var names = Enum.GetNames(enumType);

            for (int c = 0; c < candidates.Length; c++)
            {
                string token = candidates[c];
                for (int i = 0; i < names.Length; i++)
                {
                    if (string.Equals(names[i], token, StringComparison.OrdinalIgnoreCase))
                    {
                        return Enum.Parse(enumType, names[i], true);
                    }
                }
            }

            for (int c = 0; c < candidates.Length; c++)
            {
                string token = candidates[c].ToLowerInvariant();
                for (int i = 0; i < names.Length; i++)
                {
                    if (names[i].ToLowerInvariant().Contains(token))
                    {
                        return Enum.Parse(enumType, names[i], true);
                    }
                }
            }
            return null;
        }

        private static void SetPropertyValue(object owner, string propertyName, object value)
        {
            if (owner == null || value == null) return;
            var prop = owner.GetType().GetProperty(propertyName, ReflectionFlags);
            if (prop == null || !prop.CanWrite) return;
            prop.SetValue(owner, value);
        }
    }
}
