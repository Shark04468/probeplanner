using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using UnityEngine;

namespace BrainPlanner.Editor
{
    public static class MeshOrientationAudit
    {
        [Serializable] private class Input { public string label; public string path; }
        [Serializable] private class Contract { public string outputPath; public Input[] files; }
        [Serializable] private class Row
        {
            public string label;
            public string sha256;
            public string vertexSha256;
            public string normalSha256;
            public string indexSha256;
            public int vertices;
            public int faces;
            public int aligned;
            public int opposed;
            public int ambiguous;
            public float opposedFraction;
            public Vector3 min;
            public Vector3 max;
        }
        [Serializable] private class Report { public List<Row> meshes = new List<Row>(); }

        public static void Run()
        {
            var args = Environment.GetCommandLineArgs();
            int flag = Array.IndexOf(args, "-orientationManifest");
            if (flag < 0 || flag + 1 >= args.Length) throw new ArgumentException("Missing orientation manifest");
            var contract = JsonUtility.FromJson<Contract>(File.ReadAllText(args[flag + 1]));
            if (File.Exists(contract.outputPath)) throw new IOException("Preserve existing audit output");
            var report = new Report();
            var root = new GameObject("MeshOrientationAudit");
            try
            {
                var loader = root.AddComponent<BrainView.MeshLoader>();
                var parse = typeof(BrainView.MeshLoader).GetMethod("ParseOBJ", BindingFlags.Instance | BindingFlags.NonPublic);
                foreach (var input in contract.files)
                {
                    byte[] bytes = File.ReadAllBytes(input.path);
                    var row = new Row { label = input.label };
                    using (var sha = SHA256.Create()) row.sha256 = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                    var mesh = (Mesh)parse.Invoke(loader, new object[] { System.Text.Encoding.UTF8.GetString(bytes) });
                    if (mesh == null) throw new InvalidDataException("Empty mesh: " + input.label);
                    try
                    {
                        var vertices = mesh.vertices; var indices = mesh.triangles; var normals = mesh.normals;
                        row.vertexSha256 = HashVectors(vertices);
                        row.normalSha256 = HashVectors(normals);
                        row.indexSha256 = HashArray(indices);
                        row.vertices = vertices.Length; row.faces = indices.Length / 3;
                        row.min = mesh.bounds.min; row.max = mesh.bounds.max;
                        for (int i = 0; i < indices.Length; i += 3)
                        {
                            int a = indices[i], b = indices[i + 1], c = indices[i + 2];
                            var face = Vector3.Cross(vertices[b] - vertices[a], vertices[c] - vertices[a]);
                            var supplied = normals[a] + normals[b] + normals[c];
                            float agreement = Vector3.Dot(face.normalized, supplied.normalized);
                            if (agreement > 0.001f) row.aligned++;
                            else if (agreement < -0.001f) row.opposed++;
                            else row.ambiguous++;
                        }
                        row.opposedFraction = (float)row.opposed / row.faces;
                        report.meshes.Add(row);
                        Debug.Log($"[MeshOrientationAudit] {row.label}: aligned={row.aligned} opposed={row.opposed} ambiguous={row.ambiguous}");
                    }
                    finally { UnityEngine.Object.DestroyImmediate(mesh); }
                }
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
            File.WriteAllText(contract.outputPath, JsonUtility.ToJson(report, true));
        }

        public static void VerifyAndBuild()
        {
            Run();
            MeshOrientationPixelChecks.Run();
            ContactShaderPixelChecks.Run();
            WebGLBatchBuild.BuildWebGL();
        }

        private static string HashVectors(Vector3[] values)
        {
            var packed = new float[values.Length * 3];
            for (int i = 0; i < values.Length; i++)
            {
                packed[3 * i] = values[i].x; packed[3 * i + 1] = values[i].y; packed[3 * i + 2] = values[i].z;
            }
            return HashArray(packed);
        }

        private static string HashArray(Array values)
        {
            var bytes = new byte[Buffer.ByteLength(values)];
            Buffer.BlockCopy(values, 0, bytes, 0, bytes.Length);
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
    }
}
