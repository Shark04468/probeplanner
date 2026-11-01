using System;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace BrainPlanner.Editor
{
    public static class MeshOrientationPixelChecks
    {
        public static void Run()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                throw new InvalidOperationException("Orientation pixel checks require a graphics device");
            var root = new GameObject("ObjPixelContractTest");
            var primitive = GameObject.CreatePrimitive(PrimitiveType.Quad);
            var reference = primitive.GetComponent<MeshFilter>().sharedMesh;
            UnityEngine.Object.DestroyImmediate(primitive);
            var target = new RenderTexture(128, 128, 24, RenderTextureFormat.ARGB32);
            var pixels = new Texture2D(128, 128, TextureFormat.RGBA32, false);
            var material = new Material(Shader.Find("BrainPlanner/AnatomicalSurface"));
            var previous = RenderTexture.active;
            string pipeline = Shader.globalRenderPipeline;
            bool asynchronous = ShaderUtil.allowAsyncCompilation;
            target.Create();
            try
            {
                Shader.globalRenderPipeline = "UniversalPipeline";
                ShaderUtil.allowAsyncCompilation = false;
                ShaderUtil.CompilePass(material, 0, true);
                material.SetColor("_BaseColor", Color.white);
                var loader = root.AddComponent<BrainView.MeshLoader>();
                var parse = typeof(BrainView.MeshLoader).GetMethod("ParseOBJ", BindingFlags.Instance | BindingFlags.NonPublic);
                int visible = Render(reference, material, target, pixels, false);
                bool reverseView = visible == 0;
                visible = Render(reference, material, target, pixels, reverseView);
                if (visible < 100) throw new InvalidOperationException("Reference front face is blank");
                int checks = 0;
                foreach (string convention in new[] { "standard_obj", "marching_cubes", "without_normals" })
                {
                    var text = ToCcfObj(reference, convention != "marching_cubes", convention != "without_normals");
                    var parsed = (Mesh)parse.Invoke(loader, new object[] { text });
                    try
                    {
                        int front = Render(parsed, material, target, pixels, reverseView);
                        int back = Render(parsed, material, target, pixels, !reverseView);
                        Debug.Log($"[MeshOrientationPixelChecks] {convention}: front={front}, back={back}, reference={visible}");
                        if (front != visible || back != 0)
                            throw new InvalidOperationException("OBJ surface is rendered inside out: " + convention);
                        checks += 2;
                    }
                    finally { UnityEngine.Object.DestroyImmediate(parsed); }
                }
                Debug.Log($"[MeshOrientationPixelChecks] {checks} front/back pixel checks passed on {SystemInfo.graphicsDeviceType}");
            }
            finally
            {
                Shader.globalRenderPipeline = pipeline;
                ShaderUtil.allowAsyncCompilation = asynchronous;
                RenderTexture.active = previous;
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(material);
                UnityEngine.Object.DestroyImmediate(target);
                UnityEngine.Object.DestroyImmediate(pixels);
            }
        }

        private static string ToCcfObj(Mesh mesh, bool reverse, bool includeNormals)
        {
            var text = new StringBuilder();
            foreach (var v in mesh.vertices)
                text.AppendFormat(CultureInfo.InvariantCulture, "v {0:R} {1:R} {2:R}\n", 5400f - v.z * 1000f, 440f - v.y * 1000f, 5700f + v.x * 1000f);
            if (includeNormals)
                foreach (var n in mesh.normals)
                    text.AppendFormat(CultureInfo.InvariantCulture, "vn {0:R} {1:R} {2:R}\n", -n.z, -n.y, n.x);
            var t = mesh.triangles;
            for (int i = 0; i < t.Length; i += 3)
            {
                var indices = new[] { t[i] + 1, t[i + (reverse ? 2 : 1)] + 1, t[i + (reverse ? 1 : 2)] + 1 };
                text.Append("f " + string.Join(" ", indices.Select(n => includeNormals ? n + "//" + n : n.ToString())) + "\n");
            }
            return text.ToString();
        }

        private static int Render(Mesh mesh, Material material, RenderTexture target, Texture2D pixels, bool reverse)
        {
            RenderTexture.active = target;
            GL.Clear(true, true, Color.black);
            GL.PushMatrix();
            try
            {
                GL.LoadProjectionMatrix(Matrix4x4.Ortho(-1, 1, -1, 1, 0.1f, 10));
                GL.modelview = Matrix4x4.Translate(new Vector3(0, 0, -5));
                if (!material.SetPass(0)) throw new InvalidOperationException("Cannot bind anatomical surface shader");
                Graphics.DrawMeshNow(mesh, Matrix4x4.Rotate(Quaternion.Euler(0, reverse ? 180 : 0, 0)));
            }
            finally { GL.PopMatrix(); }
            pixels.ReadPixels(new Rect(0, 0, 128, 128), 0, 0);
            pixels.Apply();
            return pixels.GetPixels32().Count(p => Mathf.Max(p.r, Mathf.Max(p.g, p.b)) > 12);
        }
    }
}
