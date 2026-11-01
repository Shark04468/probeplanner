using System;
using BrainPlanner.BrainView;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace BrainPlanner.Editor
{
    public static class AtlasSectionChecks
    {
        public static void BuildAndVerify()
        {
            PaperBuildChecks.PrepareRuntimeMaterials();
            Run();
            MeshOrientationPixelChecks.Run();
            ContactShaderPixelChecks.Run();
            WebGLBatchBuild.BuildWebGL();
        }

        private static int _checks;
        private static void Require(bool valid, string name)
        {
            if (!valid) throw new InvalidOperationException("Atlas section check failed: " + name);
            _checks++;
        }

        public static void Run()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                throw new InvalidOperationException("Section checks require a graphics device");
            _checks = 0;
            var target = new RenderTexture(128, 128, 24, RenderTextureFormat.ARGB32);
            var readback = new Texture2D(128, 128, TextureFormat.RGBA32, false);
            var material = new Material(Shader.Find("BrainPlanner/AtlasSection"));
            var previous = RenderTexture.active;
            string pipeline = Shader.globalRenderPipeline;
            bool asyncCompilation = ShaderUtil.allowAsyncCompilation;
            target.Create();
            try
            {
                Shader.globalRenderPipeline = "UniversalPipeline";
                ShaderUtil.allowAsyncCompilation = false;
                ShaderUtil.CompilePass(material, 0, true);
                Require(material.shader.isSupported && !ShaderUtil.ShaderHasError(material.shader), "real section shader compiles");
                foreach (string axis in new[] { "x", "y", "z" })
                {
                    var data = Fixture(axis);
                    byte[] bytes = AtlasSectionController.Validate(data, axis, 0, "mri_only", "");
                    Require(bytes.Length == 16, "actual raw RGBA size");
                    var mesh = AtlasSectionController.MakeMesh(data);
                    var texture = AtlasSectionController.MakeTexture(data, bytes);
                    try
                    {
                        material.SetTexture("_MainTex", texture);
                        foreach (bool reverse in new[] { false, true })
                        {
                            Render(mesh, data, material, target, readback, reverse);
                            Require(Is(readback.GetPixel(38, 38), Color.red), axis + " row0/col0");
                            Require(Is(readback.GetPixel(90, 38), Color.green), axis + " row0/col1");
                            Require(Is(readback.GetPixel(38, 90), Color.blue), axis + " row1/col0");
                            Require(Is(readback.GetPixel(90, 90), new Color(1, 1, 0)), axis + " row1/col1");
                        }
                        material.SetFloat("_HemisphereFilter", 1);
                        Render(mesh, data, material, target, readback, false);
                        if (axis != "x") Require(Is(readback.GetPixel(38, 38), Color.black), axis + " left half excluded");
                        material.SetFloat("_HemisphereFilter", 0);
                        data.rgbaBase64 = Convert.ToBase64String(new byte[16]);
                        var empty = AtlasSectionController.MakeTexture(data, new byte[16]);
                        material.SetTexture("_MainTex", empty);
                        Render(mesh, data, material, target, readback, false);
                        Require(Is(readback.GetPixel(90, 90), Color.black), axis + " outside-data pixels do not fill a rectangular cap");
                        UnityEngine.Object.DestroyImmediate(empty);
                    }
                    finally { UnityEngine.Object.DestroyImmediate(mesh); UnityEngine.Object.DestroyImmediate(texture); }
                }
                var slab = AtlasSectionController.PlaneCoordinates(-2, true, .3f);
                Require(slab.Length == 2 && Mathf.Abs(slab[0] + 2.15f) < 1e-6 && Mathf.Abs(slab[1] + 1.85f) < 1e-6,
                    "slab has both true bounding sections");
                Require(AtlasSectionController.PlaneCoordinates(-2, false, .3f).Length == 1, "below has one section");
                foreach (string defect in new[] { "axis", "coordinate", "hash", "pixels", "frame", "oversize" })
                {
                    var data = Fixture("z");
                    if (defect == "axis") data.axis = "x";
                    if (defect == "coordinate") data.planeWorldMm = .1f;
                    if (defect == "hash") data.sourceHashes.warped_mri = "";
                    if (defect == "pixels") data.rgbaBase64 = "";
                    if (defect == "frame") data.origin[2] = .1f;
                    if (defect == "oversize") data.width = 100000;
                    bool rejected = false;
                    try { AtlasSectionController.Validate(data, "z", 0, "mri_only", ""); }
                    catch (ArgumentException) { rejected = true; }
                    Require(rejected, "reject " + defect);
                }
                CheckStreamingSliceReplay();
                Debug.Log($"[AtlasSectionChecks] {_checks} section contract/pixel checks passed on {SystemInfo.graphicsDeviceType}");
            }
            finally
            {
                RenderTexture.active = previous;
                Shader.globalRenderPipeline = pipeline;
                ShaderUtil.allowAsyncCompilation = asyncCompilation;
                UnityEngine.Object.DestroyImmediate(target);
                UnityEngine.Object.DestroyImmediate(readback);
                UnityEngine.Object.DestroyImmediate(material);
            }
        }

        private static bool Is(Color actual, Color expected)
        {
            return Mathf.Abs(actual.r - expected.r) < .05f && Mathf.Abs(actual.g - expected.g) < .05f && Mathf.Abs(actual.b - expected.b) < .05f;
        }

        private static void CheckStreamingSliceReplay()
        {
            var root = new GameObject("StreamingSliceReplayTest");
            try
            {
                var regions = root.AddComponent<RegionManager>();
                var flags = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
                typeof(RegionManager).GetField("_sliceBoundsValid", flags).SetValue(regions, true);
                typeof(RegionManager).GetField("_sliceBounds", flags).SetValue(regions, new Bounds(Vector3.zero, Vector3.one * 4));
                regions.SetSlice("x", true, .25f, .3f, false, false, 0);
                Require(Mathf.Abs(regions.SliceCenter + 1) < 1e-6, "initial partial-bound plane");
                int changes = 0;
                regions.OnSliceChanged += () => changes++;
                typeof(RegionManager).GetField("_sliceBounds", flags).SetValue(regions, new Bounds(Vector3.zero, Vector3.one * 12));
                regions.FinalizeSliceRange();
                Require(regions.SliceBoundsFinalized && Mathf.Abs(regions.SliceCenter + 3) < 1e-6,
                    "replay normalized slice against complete bounds");
                Require(changes == 1, "final slice notifies probes and anatomical layers");
                regions.FinalizeSliceRange();
                Require(Mathf.Abs(regions.SliceCenter + 3) < 1e-6, "final replay is position-idempotent");
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        private static AtlasSectionController.SectionData Fixture(string axis)
        {
            return new AtlasSectionController.SectionData {
                schemaVersion = "probeplanner.atlas_section.v1", pixelFormat = "RGBA32", rowOrder = "origin_to_v",
                width = 2, height = 2, axis = axis, mode = "mri_only", planeWorldMm = 0,
                manifestSha256 = new string('a', 64),
                sourceHashes = new AtlasSectionController.SourceHashes { warped_mri = new string('b', 64), annotation = new string('c', 64) },
                origin = axis == "x" ? new[] { 0f, -.8f, -.8f } : axis == "y" ? new[] { -.8f, 0f, -.8f } : new[] { -.8f, -.8f, 0f },
                u = axis == "x" ? new[] { 0f, 0f, 1.6f } : new[] { 1.6f, 0f, 0f },
                v = axis == "y" ? new[] { 0f, 0f, 1.6f } : new[] { 0f, 1.6f, 0f },
                rgbaBase64 = Convert.ToBase64String(new byte[] { 255,0,0,255, 0,255,0,255, 0,0,255,255, 255,255,0,255 }),
            };
        }

        private static void Render(Mesh mesh, AtlasSectionController.SectionData data, Material material,
            RenderTexture target, Texture2D readback, bool reverse)
        {
            var u = new Vector3(data.u[0], data.u[1], data.u[2]).normalized;
            var v = new Vector3(data.v[0], data.v[1], data.v[2]).normalized;
            var normal = Vector3.Cross(u, v) * (reverse ? -1 : 1);
            var view = Matrix4x4.identity;
            view.SetRow(0, new Vector4(u.x, u.y, u.z, 0));
            view.SetRow(1, new Vector4(v.x, v.y, v.z, 0));
            view.SetRow(2, new Vector4(normal.x, normal.y, normal.z, -5));
            RenderTexture.active = target;
            GL.Clear(true, true, Color.black);
            GL.PushMatrix();
            try
            {
                GL.LoadProjectionMatrix(Matrix4x4.Ortho(-1, 1, -1, 1, .1f, 10));
                GL.modelview = view;
                if (!material.SetPass(0)) throw new InvalidOperationException("Cannot bind section shader");
                Graphics.DrawMeshNow(mesh, Matrix4x4.identity);
            }
            finally { GL.PopMatrix(); }
            readback.ReadPixels(new Rect(0, 0, 128, 128), 0, 0);
            readback.Apply();
        }
    }
}
