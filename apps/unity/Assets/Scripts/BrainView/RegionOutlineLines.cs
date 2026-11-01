// RegionOutlineLines.cs
// 用 LineRenderer 绘制 Mesh Edges（比 RegionOutline Shader 更清晰）
// 每个脑区轮廓 = 一个 GameObject + 多条 LineRenderer 线段
// 
// 使用时机：当 RegionOutline.shader 效果不佳时替换使用
// RegionManager 可在 Inspector 里切换 UseLineRendererOutline 开关

using System.Collections.Generic;
using UnityEngine;

namespace BrainPlanner.BrainView
{
    public class RegionOutlineLines : MonoBehaviour
    {
        [Header("线条参数")]
        public float LineWidthNormal   = 0.012f;
        public float LineWidthSelected = 0.022f;
        public float AlphaNormal       = 0.22f;
        public float AlphaSelected     = 0.80f;

        // 角度阈值：只绘制两面夹角大于此值的边（减少噪声线）
        public float EdgeAngleThreshold = 20f;

        private string _regionKey;
        private Color  _regionColor;
        private bool   _isSelected;

        private readonly List<GameObject> _lineObjects = new();
        private Material _lineMat;

        // ── 初始化 ────────────────────────────────────────────────────────────
        public void Initialize(string key, Color color, Mesh mesh)
        {
            _regionKey   = key;
            _regionColor = color;

            _lineMat = new Material(Shader.Find("Universal Render Pipeline/Particles/Unlit"));
            if (_lineMat.shader.name.Contains("Error"))
                _lineMat = new Material(Shader.Find("Sprites/Default"));

            BuildEdgeLines(mesh);
            ApplyStyle();
        }

        // ── 选中/取消选中 ─────────────────────────────────────────────────────
        public void SetSelected(bool selected)
        {
            _isSelected = selected;
            ApplyStyle();
        }

        // ── 构建边线 ──────────────────────────────────────────────────────────
        private void BuildEdgeLines(Mesh mesh)
        {
            var edges = ExtractEdges(mesh);

            // 将所有边合并成一个 LineRenderer（BatchedLines 模式）
            // 每条边 = 两个点 + 一个分隔符（移动到下一段）
            // 使用多段 LineRenderer 避免连线错误
            const int maxPerLR = 512;   // 每个 LineRenderer 最多 512 条边

            for (int batch = 0; batch * maxPerLR < edges.Count; batch++)
            {
                int start = batch * maxPerLR;
                int end   = Mathf.Min(start + maxPerLR, edges.Count);

                var go = new GameObject($"EdgeLines_{batch}");
                go.transform.SetParent(transform, false);
                _lineObjects.Add(go);

                var lr = go.AddComponent<LineRenderer>();
                lr.useWorldSpace = false;
                lr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                lr.receiveShadows    = false;

                // 每条边 2 个点 + 退化点分隔（让线段不相连）
                var positions = new List<Vector3>();
                for (int i = start; i < end; i++)
                {
                    positions.Add(edges[i].Item1);
                    positions.Add(edges[i].Item2);
                    // 退化点（与前一点重合，使线段断开）
                    if (i < end - 1) positions.Add(edges[i].Item2);
                }

                lr.positionCount = positions.Count;
                lr.SetPositions(positions.ToArray());

                var mat = new Material(_lineMat);
                lr.material     = mat;
                lr.startWidth   = LineWidthNormal;
                lr.endWidth     = LineWidthNormal;
            }
        }

        // ── 提取显著边（仅保留夹角大于阈值的边）────────────────────────────
        private List<(Vector3, Vector3)> ExtractEdges(Mesh mesh)
        {
            var vertices  = mesh.vertices;
            var triangles = mesh.triangles;
            var normals   = mesh.normals;

            // 构建边 → 相邻三角形法线字典
            var edgeNormals = new Dictionary<(int, int), List<Vector3>>();

            for (int t = 0; t < triangles.Length; t += 3)
            {
                int a = triangles[t], b = triangles[t+1], c = triangles[t+2];

                // 三角形法线（面法线）
                Vector3 faceNormal = Vector3.Cross(
                    vertices[b] - vertices[a],
                    vertices[c] - vertices[a]
                ).normalized;

                AddEdgeNormal(edgeNormals, a, b, faceNormal);
                AddEdgeNormal(edgeNormals, b, c, faceNormal);
                AddEdgeNormal(edgeNormals, c, a, faceNormal);
            }

            float cosThreshold = Mathf.Cos(EdgeAngleThreshold * Mathf.Deg2Rad);
            var result = new List<(Vector3, Vector3)>();

            foreach (var kvp in edgeNormals)
            {
                var norms = kvp.Value;
                bool isFeatureEdge;

                if (norms.Count == 1)
                {
                    // 边界边（只属于一个三角形）→ 始终显示
                    isFeatureEdge = true;
                }
                else
                {
                    // 检查两面夹角
                    float dot = Vector3.Dot(norms[0], norms[norms.Count - 1]);
                    isFeatureEdge = dot < cosThreshold;
                }

                if (isFeatureEdge)
                {
                    result.Add((vertices[kvp.Key.Item1], vertices[kvp.Key.Item2]));
                }
            }

            return result;
        }

        private static void AddEdgeNormal(
            Dictionary<(int,int), List<Vector3>> dict,
            int a, int b, Vector3 normal)
        {
            var key = a < b ? (a, b) : (b, a);
            if (!dict.TryGetValue(key, out var list))
            {
                list = new List<Vector3>(2);
                dict[key] = list;
            }
            list.Add(normal);
        }

        // ── 应用样式 ──────────────────────────────────────────────────────────
        private void ApplyStyle()
        {
            float alpha = _isSelected ? AlphaSelected : AlphaNormal;
            float width = _isSelected ? LineWidthSelected : LineWidthNormal;
            Color c     = new Color(_regionColor.r, _regionColor.g, _regionColor.b, alpha);

            foreach (var go in _lineObjects)
            {
                var lr = go.GetComponent<LineRenderer>();
                if (lr == null) continue;

                lr.startWidth   = width;
                lr.endWidth     = width;
                lr.material.color = c;
                // 同时更新颜色渐变（LineRenderer 的颜色通道）
                lr.colorGradient = new Gradient
                {
                    colorKeys = new[] { new GradientColorKey(c, 0), new GradientColorKey(c, 1) },
                    alphaKeys = new[] { new GradientAlphaKey(alpha, 0), new GradientAlphaKey(alpha, 1) },
                };
            }
        }
    }
}
