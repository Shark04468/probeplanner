// MeshLoader.cs
// Loads Allen CCFv3 OBJ meshes from Flask with caching, LOD support and coordinate conversion.

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

namespace BrainPlanner.BrainView
{
    public class MeshLoader : MonoBehaviour
    {
        [Header("Flask Server")]
        [Tooltip("Base URL of optimizer_server.py")]
        public string FlaskBase = "http://localhost:5000";

        [Tooltip("Single-request timeout in seconds")]
        public int TimeoutSeconds = 60;

        [Tooltip("Maximum concurrent downloads")]
        public int MaxConcurrent = 4;

        // Allen CCFv3 Bregma in um.
        private const float BregmaAP = 5400f;
        private const float BregmaDV = 440f;
        private const float BregmaML = 5700f;

        private readonly Dictionary<string, MeshLodResult> _meshCache = new();
        private readonly HashSet<string> _loadFailed = new();
        private readonly HashSet<string> _inProgress = new();
        private int _activeConcurrent;

        public struct MeshLodResult
        {
            public Mesh mesh;
            public int requestedLod;
            public int resolvedLod;
            public string cacheSource;
        }

        public void LoadMesh(
            int structureId,
            System.Action<Mesh> onSuccess,
            System.Action<string> onFail = null
        )
        {
            LoadMeshLod(
                structureId,
                0,
                result => onSuccess?.Invoke(result.mesh),
                onFail
            );
        }

        public void LoadMeshLod(
            int structureId,
            int lod,
            System.Action<MeshLodResult> onSuccess,
            System.Action<string> onFail = null
        )
        {
            int requestedLod = Mathf.Max(0, lod);
            string reqKey = CacheKey(structureId, requestedLod);
            if (_meshCache.TryGetValue(reqKey, out MeshLodResult cached))
            {
                onSuccess?.Invoke(new MeshLodResult
                {
                    mesh = cached.mesh,
                    requestedLod = requestedLod,
                    resolvedLod = cached.resolvedLod,
                    cacheSource = "memory",
                });
                return;
            }

            if (_loadFailed.Contains(reqKey))
            {
                onFail?.Invoke("previously failed");
                return;
            }

            StartCoroutine(LoadRoutine(structureId, requestedLod, onSuccess, onFail));
        }

        public void PreloadBatch(IEnumerable<int> ids)
        {
            foreach (int id in ids)
            {
                LoadMesh(id, _ => { }, err => Debug.LogWarning($"[MeshLoader] Preload {id}: {err}"));
            }
        }

        public void LoadMriSurfaceMesh(
            string layer,
            int lod,
            System.Action<MeshLodResult> onSuccess,
            System.Action<string> onFail = null
        )
        {
            string safeLayer = string.IsNullOrWhiteSpace(layer) ? "" : layer.Trim();
            if (string.IsNullOrEmpty(safeLayer))
            {
                onFail?.Invoke("empty MRI surface layer");
                return;
            }
            int requestedLod = Mathf.Max(0, lod);
            string reqKey = MriSurfaceCacheKey(safeLayer, requestedLod);
            if (_meshCache.TryGetValue(reqKey, out MeshLodResult cached))
            {
                onSuccess?.Invoke(new MeshLodResult
                {
                    mesh = cached.mesh,
                    requestedLod = requestedLod,
                    resolvedLod = cached.resolvedLod,
                    cacheSource = "memory",
                });
                return;
            }
            if (_loadFailed.Contains(reqKey))
            {
                onFail?.Invoke("previously failed");
                return;
            }
            StartCoroutine(LoadMriSurfaceRoutine(safeLayer, requestedLod, onSuccess, onFail));
        }

        public bool IsCached(int structureId, int lod = 0) => _meshCache.ContainsKey(CacheKey(structureId, Mathf.Max(0, lod)));
        public bool HasFailed(int structureId, int lod = 0) => _loadFailed.Contains(CacheKey(structureId, Mathf.Max(0, lod)));
        public int CacheCount => _meshCache.Count;

        private IEnumerator LoadRoutine(
            int structureId,
            int requestedLod,
            System.Action<MeshLodResult> onSuccess,
            System.Action<string> onFail
        )
        {
            return LoadRequest(CacheKey(structureId, requestedLod), requestedLod,
                $"{FlaskBase}/api/allen_mesh_lod/{structureId}?lod={requestedLod}",
                $"{FlaskBase}/api/allen_mesh/{structureId}",
                lod => CacheKey(structureId, lod), $"sid={structureId}", onSuccess, onFail);
        }

        private IEnumerator LoadRequest(
            string reqKey, int requestedLod, string primaryUrl, string fallbackUrl,
            System.Func<int, string> resolvedKeyForLod, string label,
            System.Action<MeshLodResult> onSuccess, System.Action<string> onFail)
        {
            if (_inProgress.Contains(reqKey))
            {
                yield return new WaitUntil(() => !_inProgress.Contains(reqKey));
                if (_meshCache.TryGetValue(reqKey, out MeshLodResult waitedMesh))
                {
                    onSuccess?.Invoke(new MeshLodResult
                    {
                        mesh = waitedMesh.mesh,
                        requestedLod = requestedLod,
                        resolvedLod = waitedMesh.resolvedLod,
                        cacheSource = "memory",
                    });
                }
                else
                {
                    onFail?.Invoke("Mesh owner failed or was cancelled");
                }

                yield break;
            }

            // Own the key while queued; own the slot through fallback and parsing.
            _inProgress.Add(reqKey);
            bool ownsSlot = false;
            UnityWebRequest req = null;
            try
            {
                // Unity may poll several waiters before resuming them in the same frame.
                yield return new WaitUntil(() =>
                {
                    if (ownsSlot) return true;
                    if (_activeConcurrent >= Mathf.Max(1, MaxConcurrent)) return false;
                    _activeConcurrent++;
                    ownsSlot = true;
                    return true;
                });
                req = UnityWebRequest.Get(primaryUrl);
                req.timeout = TimeoutSeconds;
                yield return req.SendWebRequest();
                bool primarySucceeded = req.result == UnityWebRequest.Result.Success;
                if (!primarySucceeded)
                {
                    req.Dispose();
                    req = UnityWebRequest.Get(fallbackUrl);
                    req.timeout = TimeoutSeconds;
                    yield return req.SendWebRequest();
                    if (req.result != UnityWebRequest.Result.Success)
                    {
                        Debug.LogWarning($"[MeshLoader] {label} lod={requestedLod} failed: {req.error}");
                        _loadFailed.Add(reqKey);
                        onFail?.Invoke(req.error);
                        yield break;
                    }
                }
                if (!TryResolveLod(req.GetResponseHeader("X-Lod"), !primarySucceeded, out int resolvedLod))
                {
                    _loadFailed.Add(reqKey);
                    onFail?.Invoke("Missing or invalid X-Lod on mesh response");
                    yield break;
                }
                string resolvedKey = resolvedKeyForLod(resolvedLod);
                if (!_meshCache.TryGetValue(resolvedKey, out MeshLodResult resolved))
                {
                    Mesh parsed;
                    try { parsed = ParseOBJ(req.downloadHandler.text); }
                    catch (System.Exception ex)
                    {
                        Debug.LogError($"[MeshLoader] {label} parse exception: {ex.Message}");
                        _loadFailed.Add(reqKey);
                        onFail?.Invoke($"parse exception: {ex.Message}");
                        yield break;
                    }
                    if (parsed == null)
                    {
                        _loadFailed.Add(reqKey);
                        onFail?.Invoke("parse failed");
                        yield break;
                    }
                    parsed.name = resolvedKey;
                    resolved = new MeshLodResult { mesh = parsed, resolvedLod = resolvedLod };
                    _meshCache[resolvedKey] = resolved;
                }
                _meshCache[reqKey] = resolved;
                onSuccess?.Invoke(new MeshLodResult
                {
                    mesh = resolved.mesh,
                    requestedLod = requestedLod,
                    resolvedLod = resolvedLod,
                    cacheSource = req.GetResponseHeader("X-Cache-Source") ?? "network",
                });
            }
            finally
            {
                req?.Dispose();
                if (ownsSlot) _activeConcurrent--;
                _inProgress.Remove(reqKey);
            }
        }

        private static string CacheKey(int structureId, int lod)
        {
            return $"{structureId}:{Mathf.Max(0, lod)}";
        }

        public static bool TryResolveLod(string header, bool baseEndpoint, out int lod)
        {
            if (int.TryParse(header, out lod) && lod >= 0) return true;
            lod = baseEndpoint ? 0 : -1;
            return baseEndpoint && string.IsNullOrEmpty(header);
        }

        private static string MriSurfaceCacheKey(string layer, int lod)
        {
            return $"mri_surface:{layer}:{Mathf.Max(0, lod)}";
        }

        private IEnumerator LoadMriSurfaceRoutine(
            string layer,
            int requestedLod,
            System.Action<MeshLodResult> onSuccess,
            System.Action<string> onFail
        )
        {
            string escapedLayer = UnityWebRequest.EscapeURL(layer);
            return LoadRequest(MriSurfaceCacheKey(layer, requestedLod), requestedLod,
                $"{FlaskBase}/api/mri_surface_mesh_lod/{escapedLayer}?lod={requestedLod}",
                $"{FlaskBase}/api/mri_surface_mesh/{escapedLayer}",
                lod => MriSurfaceCacheKey(layer, lod), $"MRI layer={layer}", onSuccess, onFail);
        }

        // Allen OBJ vertex format: v AP_um DV_um ML_um
        // Unity coords: x=(ML-5700)/1000, y=-(DV-440)/1000, z=-(AP-5400)/1000
        private Mesh ParseOBJ(string text)
        {
            List<Vector3> verts = new List<Vector3>(4096);
            List<int> tris = new List<int>(8192);
            List<Vector3> sourceNormals = new List<Vector3>(4096);
            List<int> triVertexNormalRefs = new List<int>(8192); // packed pairs: [vi0,ni0, vi1,ni1, ...]

            int start = 0;
            int len = text.Length;
            while (start < len)
            {
                int end = text.IndexOf('\n', start);
                if (end < 0) end = len;

                if (start < len - 1)
                {
                    char c0 = text[start];
                    char c1 = start + 1 < len ? text[start + 1] : '\0';
                    char c2 = start + 2 < len ? text[start + 2] : '\0';

                    if (c0 == 'v' && c1 == ' ')
                        ParseVertex(text, start + 2, end, verts);
                    else if (c0 == 'v' && c1 == 'n' && c2 == ' ')
                        ParseNormal(text, start + 3, end, sourceNormals);
                    else if (c0 == 'f' && c1 == ' ')
                        ParseFace(text, start + 2, end, verts.Count, sourceNormals.Count, tris, triVertexNormalRefs);
                }

                start = end + 1;
            }

            if (verts.Count == 0 || tris.Count == 0)
                return null;

            AlignWindingWithNormals(verts, tris, sourceNormals, triVertexNormalRefs);
            Mesh mesh = new Mesh
            {
                indexFormat = UnityEngine.Rendering.IndexFormat.UInt32,
                name = "AllenMesh",
            };
            mesh.SetVertices(verts);
            mesh.SetTriangles(tris, 0);

            Vector3[] normals = BuildVertexNormals(verts, tris, sourceNormals, triVertexNormalRefs);
            if (normals != null && normals.Length == verts.Count)
            {
                mesh.normals = normals;
            }
            else
            {
                // Keep a safe fallback path in case malformed OBJ content bypasses normal reconstruction.
                mesh.RecalculateNormals();
            }

            mesh.RecalculateBounds();
            return mesh;
        }

        private static void AlignWindingWithNormals(List<Vector3> verts, List<int> tris,
            List<Vector3> sourceNormals, List<int> normalRefs)
        {
            // The CCF mapping reflects handedness. Allen OBJ and marching-cubes OBJ
            // use different source winding, so preserve their supplied outward normals.
            // Flip a whole file, never individual faces: local normal noise must not tear topology.
            bool reverse = true; // Standard OBJ without normals: compensate the reflection.
            if (sourceNormals.Count > 0 && normalRefs.Count > 0)
            {
                var supplied = new Vector3[verts.Count];
                for (int i = 0; i + 1 < normalRefs.Count; i += 2)
                    supplied[normalRefs[i]] += sourceNormals[normalRefs[i + 1]];
                for (int i = 0; i < supplied.Length; i++) supplied[i].Normalize();
                double agreement = 0;
                double magnitude = 0;
                for (int i = 0; i + 2 < tris.Count; i += 3)
                {
                    int a = tris[i], b = tris[i + 1], c = tris[i + 2];
                    if (supplied[a] == Vector3.zero || supplied[b] == Vector3.zero || supplied[c] == Vector3.zero) continue;
                    var face = Vector3.Cross(verts[b] - verts[a], verts[c] - verts[a]);
                    float projection = Vector3.Dot(face, supplied[a] + supplied[b] + supplied[c]);
                    agreement += projection;
                    magnitude += System.Math.Abs(projection);
                }
                if (magnitude > 1e-12) reverse = agreement < 0;
            }
            if (!reverse) return;
            for (int i = 0; i + 2 < tris.Count; i += 3)
            {
                int second = tris[i + 1]; tris[i + 1] = tris[i + 2]; tris[i + 2] = second;
            }
        }

        private void ParseVertex(string text, int start, int end, List<Vector3> verts)
        {
            if (!TryParseFloat(text, ref start, end, out float ap)) return;
            if (!TryParseFloat(text, ref start, end, out float dv)) return;
            if (!TryParseFloat(text, ref start, end, out float ml)) return;

            verts.Add(CcfCoordinates.FromMicrometres(new Vector3(ap, dv, ml)));
        }

        private void ParseNormal(string text, int start, int end, List<Vector3> normals)
        {
            if (!TryParseFloat(text, ref start, end, out float nap)) return;
            if (!TryParseFloat(text, ref start, end, out float ndv)) return;
            if (!TryParseFloat(text, ref start, end, out float nml)) return;

            // Keep normal-space transform aligned with position transform:
            // [AP, DV, ML] -> [ML, -DV, -AP]
            var n = new Vector3(nml, -ndv, -nap);
            if (n.sqrMagnitude > 1e-10f) n.Normalize();
            normals.Add(n);
        }

        private void ParseFace(
            string text,
            int start,
            int end,
            int vertCount,
            int normalCount,
            List<int> tris,
            List<int> triVertexNormalRefs
        )
        {
            List<int> vertexIndices = new List<int>(8);
            List<int> normalIndices = new List<int>(8);
            while (start < end)
            {
                while (start < end && (text[start] == ' ' || text[start] == '\t')) start++;
                if (start >= end) break;

                int tokenEnd = start;
                while (tokenEnd < end && text[tokenEnd] != ' ' && text[tokenEnd] != '\t') tokenEnd++;
                if (TryParseFaceToken(text, start, tokenEnd, vertCount, normalCount, out int vi, out int ni))
                {
                    vertexIndices.Add(vi);
                    normalIndices.Add(ni);
                }
                start = tokenEnd;
            }

            for (int i = 1; i < vertexIndices.Count - 1; i++)
            {
                int i0 = vertexIndices[0];
                int i1 = vertexIndices[i];
                int i2 = vertexIndices[i + 1];
                if (!IsValidTriIndex(i0, vertCount) || !IsValidTriIndex(i1, vertCount) || !IsValidTriIndex(i2, vertCount))
                    continue;

                tris.Add(i0);
                tris.Add(i1);
                tris.Add(i2);

                int n0 = normalIndices[0];
                int n1 = normalIndices[i];
                int n2 = normalIndices[i + 1];
                if (n0 >= 0 && n1 >= 0 && n2 >= 0)
                {
                    triVertexNormalRefs.Add(i0); triVertexNormalRefs.Add(n0);
                    triVertexNormalRefs.Add(i1); triVertexNormalRefs.Add(n1);
                    triVertexNormalRefs.Add(i2); triVertexNormalRefs.Add(n2);
                }
            }
        }

        private static bool IsValidTriIndex(int idx, int count)
        {
            return idx >= 0 && idx < count;
        }

        private static bool TryParseFaceToken(
            string s,
            int start,
            int end,
            int vertCount,
            int normalCount,
            out int vertexIndex,
            out int normalIndex
        )
        {
            vertexIndex = -1;
            normalIndex = -1;
            if (start >= end) return false;

            int slash1 = s.IndexOf('/', start, end - start);
            int vEnd = slash1 >= 0 ? slash1 : end;
            if (!TryParseSignedIntSlice(s, start, vEnd, out int rawVi)) return false;
            vertexIndex = ResolveObjIndex(rawVi, vertCount);
            if (vertexIndex < 0) return false;

            if (slash1 < 0) return true;

            int slash2 = s.IndexOf('/', slash1 + 1, end - (slash1 + 1));
            if (slash2 >= 0)
            {
                int nStart = slash2 + 1;
                if (nStart < end && TryParseSignedIntSlice(s, nStart, end, out int rawNi))
                {
                    normalIndex = ResolveObjIndex(rawNi, normalCount);
                }
            }
            return true;
        }

        private static int ResolveObjIndex(int rawIndex, int count)
        {
            if (count <= 0 || rawIndex == 0) return -1;
            if (rawIndex > 0) return rawIndex - 1;
            int resolved = count + rawIndex; // negative OBJ indices are relative to current count
            return resolved >= 0 && resolved < count ? resolved : -1;
        }

        private static bool TryParseSignedIntSlice(string s, int start, int end, out int result)
        {
            result = 0;
            if (start >= end) return false;
            return int.TryParse(
                s.Substring(start, end - start),
                System.Globalization.NumberStyles.Integer,
                System.Globalization.CultureInfo.InvariantCulture,
                out result
            );
        }

        private static Vector3[] BuildVertexNormals(
            List<Vector3> verts,
            List<int> tris,
            List<Vector3> sourceNormals,
            List<int> triVertexNormalRefs
        )
        {
            int vCount = verts.Count;
            if (vCount == 0) return null;

            var accum = new Vector3[vCount];
            var counts = new int[vCount];

            if (sourceNormals != null && sourceNormals.Count > 0 && triVertexNormalRefs != null && triVertexNormalRefs.Count >= 2)
            {
                for (int i = 0; i + 1 < triVertexNormalRefs.Count; i += 2)
                {
                    int vi = triVertexNormalRefs[i];
                    int ni = triVertexNormalRefs[i + 1];
                    if (vi < 0 || vi >= vCount || ni < 0 || ni >= sourceNormals.Count) continue;
                    accum[vi] += sourceNormals[ni];
                    counts[vi]++;
                }
            }

            // Fill missing normals from triangle geometry to avoid dark/invalid patches.
            for (int i = 0; i + 2 < tris.Count; i += 3)
            {
                int ia = tris[i];
                int ib = tris[i + 1];
                int ic = tris[i + 2];
                if (ia < 0 || ib < 0 || ic < 0 || ia >= vCount || ib >= vCount || ic >= vCount) continue;

                Vector3 e1 = verts[ib] - verts[ia];
                Vector3 e2 = verts[ic] - verts[ia];
                Vector3 face = Vector3.Cross(e1, e2);
                float mag = face.magnitude;
                if (mag < 1e-8f) continue;
                face /= mag;

                if (counts[ia] <= 0) accum[ia] += face;
                if (counts[ib] <= 0) accum[ib] += face;
                if (counts[ic] <= 0) accum[ic] += face;
            }

            for (int i = 0; i < vCount; i++)
            {
                if (accum[i].sqrMagnitude < 1e-10f)
                {
                    accum[i] = Vector3.up;
                }
                else
                {
                    accum[i].Normalize();
                }
            }
            return accum;
        }

        private static bool TryParseFloat(string s, ref int pos, int end, out float result)
        {
            while (pos < end && (s[pos] == ' ' || s[pos] == '\t')) pos++;
            int numStart = pos;
            bool hasDot = false;
            bool hasE = false;

            if (pos < end && (s[pos] == '-' || s[pos] == '+')) pos++;
            while (pos < end)
            {
                char c = s[pos];
                if (c >= '0' && c <= '9') pos++;
                else if (c == '.' && !hasDot) { hasDot = true; pos++; }
                else if ((c == 'e' || c == 'E') && !hasE)
                {
                    hasE = true;
                    pos++;
                    if (pos < end && (s[pos] == '+' || s[pos] == '-')) pos++;
                }
                else break;
            }

            if (pos == numStart)
            {
                result = 0f;
                return false;
            }

            return float.TryParse(
                s.Substring(numStart, pos - numStart),
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out result
            );
        }

        private static bool TryParseInt(string s, ref int pos, int end, out int result)
        {
            while (pos < end && (s[pos] == ' ' || s[pos] == '\t')) pos++;
            int numStart = pos;
            if (pos < end && s[pos] == '-') pos++;
            while (pos < end && s[pos] >= '0' && s[pos] <= '9') pos++;

            if (pos == numStart)
            {
                result = 0;
                return false;
            }

            return int.TryParse(
                s.Substring(numStart, pos - numStart),
                System.Globalization.NumberStyles.Integer,
                System.Globalization.CultureInfo.InvariantCulture,
                out result
            );
        }
    }
}
