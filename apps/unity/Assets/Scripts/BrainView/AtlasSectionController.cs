using System;
using System.Collections;
using System.Globalization;
using UnityEngine;
using UnityEngine.Networking;

namespace BrainPlanner.BrainView
{
    // Volume-backed cut surfaces share the anatomical scene and its actual clip plane.
    public class AtlasSectionController : MonoBehaviour
    {
        [Serializable]
        public class SourceHashes { public string warped_mri; public string annotation; }
        [Serializable]
        public class SectionData
        {
            public string schemaVersion, rgbaBase64, pixelFormat, rowOrder, axis, mode, manifestSha256;
            public int width, height, labelIndex;
            public float[] origin, u, v;
            public float planeWorldMm, sampleIndex;
            public SourceHashes sourceHashes;
        }
        [Serializable]
        public class SectionState
        {
            public string key = "mri_section";
            public string status, axis, mode, manifestSha256, error, requestKey;
            public bool visible;
            public int requestedPlanes, loadedPlanes, generation;
            public float center;
        }
        public Action<SectionState> OnSectionState;
        private RegionManager _regions;
        private MeshLoader _loader;
        private readonly GameObject[] _planes = new GameObject[2];
        private readonly Texture2D[] _textures = new Texture2D[2];
        private readonly Mesh[] _meshes = new Mesh[2];
        private Material _material;
        private bool _enabled, _running;
        private string _mode = "mri_ccf_overlay", _signature = "", _requestKey = "";
        private int _generation;
        private float _dueTime;
        private UnityWebRequest _request;

        public void Configure(bool enabled, string mode)
        {
            _enabled = enabled;
            _mode = mode == "ccf_only" || mode == "mri_only" ? mode : "mri_ccf_overlay";
            Refresh();
        }

        public void SetRequestKey(string key)
        {
            _requestKey = key ?? "";
            Refresh();
        }

        private void Awake()
        {
            _regions = GetComponent<RegionManager>();
            _loader = GetComponent<MeshLoader>();
        }

        private void LateUpdate()
        {
            Refresh();
            if (_enabled && _regions != null && _regions.SliceEnabled && _regions.SliceBoundsFinalized && !_running &&
                _dueTime > 0 && Time.unscaledTime >= _dueTime)
            {
                _dueTime = 0;
                StartCoroutine(Load(_generation));
            }
        }

        private void Refresh()
        {
            if (_regions == null) _regions = GetComponent<RegionManager>();
            if (_loader == null) _loader = GetComponent<MeshLoader>();
            bool active = _enabled && _regions != null && _regions.SliceEnabled;
            string signature = active ? string.Join("|", _mode, _regions.SliceAxis,
                _regions.SliceCenter.ToString("R", CultureInfo.InvariantCulture), _regions.SliceUseSlab,
                _regions.SliceThicknessMm.ToString("R", CultureInfo.InvariantCulture),
                _regions.HemisphereFilter, _requestKey, _loader != null ? _loader.FlaskBase : "") : "off|" + _requestKey;
            if (signature == _signature) return;
            _signature = signature;
            _generation++;
            _request?.Abort();
            HidePlanes();
            _dueTime = active ? Time.unscaledTime + .12f : 0;
            Emit(active ? "loading" : "idle", 0, "", "");
        }

        private void Emit(string status, int loaded, string hash, string error)
        {
            OnSectionState?.Invoke(new SectionState {
                status = status, visible = status == "loaded", loadedPlanes = loaded,
                requestedPlanes = _enabled && _regions != null && _regions.SliceEnabled
                    ? (_regions.SliceUseSlab ? 2 : 1) : 0,
                axis = AxisName(_regions != null ? _regions.SliceAxis : 1), mode = _mode,
                center = _regions != null ? _regions.SliceCenter : 0,
                generation = _generation, manifestSha256 = hash, error = error, requestKey = _requestKey,
            });
        }

        public static string AxisName(int axis) => axis == 0 ? "x" : axis == 2 ? "z" : "y";

        public static float[] PlaneCoordinates(float center, bool slab, float thickness)
        {
            return slab ? new[] { center - thickness * .5f, center + thickness * .5f } : new[] { center };
        }

        private IEnumerator Load(int generation)
        {
            _running = true;
            try
            {
                if (_loader == null) { Emit("failed", 0, "", "Section backend unavailable"); yield break; }
                string axis = AxisName(_regions.SliceAxis), mode = _mode;
                float[] coordinates = PlaneCoordinates(_regions.SliceCenter, _regions.SliceUseSlab, _regions.SliceThicknessMm);
                string hash = "";
                for (int i = 0; i < coordinates.Length; i++)
                {
                    string url = _loader.FlaskBase.TrimEnd('/') + "/api/mri/section?axis=" + axis +
                        "&coordinate=" + coordinates[i].ToString("R", CultureInfo.InvariantCulture) + "&mode=" + mode;
                    using (var request = UnityWebRequest.Get(url))
                    {
                        _request = request;
                        request.timeout = 120;
                        yield return request.SendWebRequest();
                        _request = null;
                        if (generation != _generation) yield break;
                        if (request.result != UnityWebRequest.Result.Success)
                        {
                            Emit("failed", 0, "", "Section request failed (HTTP " + request.responseCode + ")");
                            yield break;
                        }
                        if (!TryInstall(i, request.downloadHandler.text, axis, coordinates[i], mode, hash, out string currentHash, out string error))
                        {
                            Emit("failed", 0, "", error);
                            yield break;
                        }
                        hash = currentHash;
                    }
                }
                if (generation != _generation) yield break;
                float hemisphere = _regions.HemisphereFilter == "left" ? -1f : _regions.HemisphereFilter == "right" ? 1f : 0f;
                _material.SetFloat("_HemisphereFilter", hemisphere);
                for (int i = 0; i < coordinates.Length; i++) _planes[i].SetActive(true);
                Emit("loaded", coordinates.Length, hash, "");
            }
            finally { _request = null; _running = false; }
        }

        private bool TryInstall(int slot, string json, string axis, float coordinate, string mode,
            string expectedHash, out string hash, out string error)
        {
            hash = ""; error = "";
            try
            {
                var data = JsonUtility.FromJson<SectionData>(json);
                byte[] bytes = Validate(data, axis, coordinate, mode, expectedHash);
                if (_material == null)
                {
                    var shader = Shader.Find("BrainPlanner/AtlasSection");
                    if (shader == null) throw new InvalidOperationException("Missing atlas section shader");
                    _material = new Material(shader);
                }
                if (_planes[slot] == null)
                {
                    _planes[slot] = new GameObject("AtlasSection_" + slot);
                    _planes[slot].transform.SetParent(transform, false);
                    _planes[slot].AddComponent<MeshFilter>();
                    var renderer = _planes[slot].AddComponent<MeshRenderer>();
                    renderer.sharedMaterial = _material;
                    renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                    renderer.receiveShadows = false;
                }
                _planes[slot].SetActive(false);
                if (_textures[slot] != null) Destroy(_textures[slot]);
                if (_meshes[slot] != null) Destroy(_meshes[slot]);
                _textures[slot] = MakeTexture(data, bytes);
                _meshes[slot] = MakeMesh(data);
                _planes[slot].GetComponent<MeshFilter>().sharedMesh = _meshes[slot];
                var properties = new MaterialPropertyBlock();
                properties.SetTexture("_MainTex", _textures[slot]);
                _planes[slot].GetComponent<MeshRenderer>().SetPropertyBlock(properties);
                hash = data.manifestSha256;
                return true;
            }
            catch (Exception ex) { error = ex.Message; HidePlanes(); return false; }
        }

        public static byte[] Validate(SectionData data, string axis, float coordinate, string mode, string expectedHash)
        {
            if (data == null || data.schemaVersion != "probeplanner.atlas_section.v1" || data.pixelFormat != "RGBA32" ||
                data.rowOrder != "origin_to_v" || data.axis != axis || data.mode != mode ||
                !Finite(data.planeWorldMm) || Mathf.Abs(data.planeWorldMm - coordinate) > 0.00001f ||
                data.width < 1 || data.height < 1 || data.width > 2048 || data.height > 2048 ||
                !IsHash(data.manifestSha256) || data.sourceHashes == null ||
                !IsHash(data.sourceHashes.warped_mri) || !IsHash(data.sourceHashes.annotation) ||
                (!string.IsNullOrEmpty(expectedHash) && expectedHash != data.manifestSha256))
                throw new ArgumentException("Invalid or mismatched section response");
            var origin = Vector(data.origin); var u = Vector(data.u); var v = Vector(data.v);
            int component = axis == "x" ? 0 : axis == "y" ? 1 : 2;
            if (Mathf.Abs(origin[component] - coordinate) > .00001f || Mathf.Abs(u[component]) > .00001f ||
                Mathf.Abs(v[component]) > .00001f || Vector3.Cross(u, v).sqrMagnitude < 1e-10f)
                throw new ArgumentException("Section frame does not lie on clipping plane");
            byte[] bytes = Convert.FromBase64String(data.rgbaBase64 ?? "");
            if (bytes.Length != data.width * data.height * 4) throw new ArgumentException("Section pixel length mismatch");
            return bytes;
        }

        private static bool IsHash(string value) => value != null && System.Text.RegularExpressions.Regex.IsMatch(value, "^[a-f0-9]{64}$");
        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        private static Vector3 Vector(float[] values)
        {
            if (values == null || values.Length != 3 || !Finite(values[0]) || !Finite(values[1]) || !Finite(values[2]))
                throw new ArgumentException("Invalid section frame");
            return new Vector3(values[0], values[1], values[2]);
        }

        public static Texture2D MakeTexture(SectionData data, byte[] bytes)
        {
            var texture = new Texture2D(data.width, data.height, TextureFormat.RGBA32, false);
            texture.filterMode = FilterMode.Point;
            texture.wrapMode = TextureWrapMode.Clamp;
            texture.LoadRawTextureData(bytes);
            texture.Apply(false, true);
            return texture;
        }

        public static Mesh MakeMesh(SectionData data)
        {
            Vector3 origin = Vector(data.origin), u = Vector(data.u), v = Vector(data.v);
            var mesh = new Mesh { name = "VerifiedAtlasSection" };
            mesh.vertices = new[] { origin, origin + u, origin + u + v, origin + v };
            mesh.uv = new[] { Vector2.zero, Vector2.right, Vector2.one, Vector2.up };
            mesh.triangles = new[] { 0, 1, 2, 0, 2, 3 };
            mesh.RecalculateBounds();
            return mesh;
        }

        private void HidePlanes()
        {
            foreach (var plane in _planes) if (plane != null) plane.SetActive(false);
        }

        private void OnDestroy()
        {
            _request?.Abort();
            foreach (var texture in _textures) if (texture != null) Destroy(texture);
            foreach (var mesh in _meshes) if (mesh != null) Destroy(mesh);
            if (_material != null) Destroy(_material);
        }
    }
}
