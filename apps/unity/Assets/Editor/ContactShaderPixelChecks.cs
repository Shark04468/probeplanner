using System;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace BrainPlanner.Editor
{
    public static class ContactShaderPixelChecks
    {
        public static void Run()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                throw new InvalidOperationException("Pixel checks require a real graphics device, not -nographics.");
            var primitive = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            var mesh = primitive.GetComponent<MeshFilter>().sharedMesh;
            UnityEngine.Object.DestroyImmediate(primitive);
            var target = new RenderTexture(256, 128, 24, RenderTextureFormat.ARGB32);
            var pixels = new Texture2D(256, 128, TextureFormat.RGBA32, false);
            target.Create();
            int checks = 0;
            var previous = RenderTexture.active;
            bool asyncCompilation = ShaderUtil.allowAsyncCompilation;
            ShaderUtil.allowAsyncCompilation = false;
            string previousPipeline = Shader.globalRenderPipeline;
            Shader.globalRenderPipeline = "UniversalPipeline";
            try
            {
                foreach (string name in new[] { "RegionFill", "RegionOutline", "RegionIdPick" })
                {
                    var material = new Material(Shader.Find("BrainPlanner/" + name));
                    try
                    {
                        ShaderUtil.CompilePass(material, 0, true);
                        Debug.Log($"[ContactShaderPixelChecks] {name}: pass={material.GetPassName(0)} compiled={ShaderUtil.IsPassCompiled(material, 0)}");
                        if (!material.shader.isSupported || ShaderUtil.ShaderHasError(material.shader))
                            throw new InvalidOperationException("Unsupported/error shader: " + name);
                        if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", Color.white);
                        if (material.HasProperty("_RegionColor")) material.SetColor("_RegionColor", Color.white);
                        if (material.HasProperty("_IdColor")) material.SetColor("_IdColor", Color.white);
                        if (material.HasProperty("_Alpha")) material.SetFloat("_Alpha", 1);
                        if (material.HasProperty("_OutlineAlpha")) material.SetFloat("_OutlineAlpha", 1);
                        if (material.HasProperty("_UseWeightedOIT")) material.SetFloat("_UseWeightedOIT", 0);
                        material.SetFloat("_ContactOverride", 1);
                        material.SetFloat("_SliceCenter", -2);
                        material.SetFloat("_SliceThickness", 0.25f);
                        material.SetFloat("_SliceEnabled", 0);
                        var baseline = Render(material, mesh, target, pixels);
                        Verify(baseline.x > 20 && baseline.y > 20, name + " baseline is visible on both sides");
                        checks++;
                        material.SetFloat("_SliceEnabled", 1);
                        foreach (int axis in new[] { 0, 1, 2 })
                        foreach (int slab in new[] { 0, 1 })
                        foreach (var sides in new[] { Vector4.zero, new Vector4(1, 0, 0, 0),
                            new Vector4(0, 1, 0, 0), new Vector4(1, 1, 0, 0), new Vector4(0, 0, 1, 0) })
                        {
                            material.SetFloat("_SliceAxis", axis);
                            material.SetFloat("_SliceUseSlab", slab);
                            material.SetVector("_ContactHemispheres", sides);
                            var counts = Render(material, mesh, target, pixels);
                            Verify((counts.x > 20) == (sides.x > 0) && (counts.y > 20) == (sides.y > 0),
                                $"{name} axis={axis} slab={slab} sides={sides} actual={counts}");
                            Verify((sides.x > 0 || counts.x == 0) && (sides.y > 0 || counts.y == 0),
                                name + " removed hemisphere has zero visible pixels");
                            checks += 2;
                        }
                        material.SetVector("_ContactHemispheres", new Vector4(1, 1, 0, 0));
                        material.SetFloat("_HemisphereFilter", 1);
                        var filtered = Render(material, mesh, target, pixels);
                        Verify(filtered.x == 0 && filtered.y > 20, name + " global hemisphere filter still wins");
                        checks++;
                        material.SetFloat("_HemisphereFilter", 0);
                        material.SetFloat("_ContactOverride", 0);
                        var disabled = Render(material, mesh, target, pixels);
                        Verify(disabled == Vector2Int.zero, name + " standard mode clips both sides");
                        checks++;
                    }
                    finally { UnityEngine.Object.DestroyImmediate(material); }
                }
            }
            finally
            {
                var args = Environment.GetCommandLineArgs();
                int outputFlag = Array.IndexOf(args, "-contactPixelOutput");
                if (outputFlag >= 0 && outputFlag + 1 < args.Length)
                    System.IO.File.WriteAllBytes(args[outputFlag + 1], pixels.EncodeToPNG());
                ShaderUtil.allowAsyncCompilation = asyncCompilation;
                Shader.globalRenderPipeline = previousPipeline;
                RenderTexture.active = previous;
                UnityEngine.Object.DestroyImmediate(target);
                UnityEngine.Object.DestroyImmediate(pixels);
            }
            Debug.Log($"[ContactShaderPixelChecks] {checks} pixel checks passed on {SystemInfo.graphicsDeviceType}.");
        }

        private static Vector2Int Render(Material material, Mesh mesh, RenderTexture target, Texture2D pixels)
        {
            RenderTexture.active = target;
            GL.Clear(true, true, Color.black);
            GL.PushMatrix();
            try
            {
                GL.LoadProjectionMatrix(Matrix4x4.Ortho(-2, 2, -1, 1, 0.1f, 10));
                GL.modelview = Matrix4x4.Translate(new Vector3(0, 0, -5));
                Shader.SetGlobalVector("_WorldSpaceCameraPos", new Vector4(0, 0, 5, 1));
                if (!material.SetPass(0)) throw new InvalidOperationException("Cannot bind " + material.shader.name);
                if (ShaderUtil.ShaderHasError(material.shader))
                    throw new InvalidOperationException(string.Join("\n", ShaderUtil.GetShaderMessages(material.shader)
                        .Select(m => m.message + " at " + m.file + ":" + m.line)));
                foreach (float x in new[] { -1f, 1f })
                    Graphics.DrawMeshNow(mesh, Matrix4x4.TRS(new Vector3(x, 0, 0), Quaternion.identity, Vector3.one));
            }
            finally { GL.PopMatrix(); }
            pixels.ReadPixels(new Rect(0, 0, 256, 128), 0, 0);
            pixels.Apply();
            var data = pixels.GetPixels32();
            var counts = Vector2Int.zero;
            for (int i = 0; i < data.Length; i++)
            {
                if (data[i].r > 240 && data[i].g < 15 && data[i].b > 240)
                    throw new InvalidOperationException("Shader error pixels: " + material.shader.name + "\n"
                        + string.Join("\n", ShaderUtil.GetShaderMessages(material.shader).Select(m => m.message)));
                if (Mathf.Max(data[i].r, Mathf.Max(data[i].g, data[i].b)) <= 12) continue;
                if (i % 256 < 128) counts.x++; else counts.y++;
            }
            return counts;
        }

        private static void Verify(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException("Contact shader pixel check failed: " + message);
        }
    }
}
