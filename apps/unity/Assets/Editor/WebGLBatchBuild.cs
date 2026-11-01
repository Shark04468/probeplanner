using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace BrainPlanner.Editor
{
    public static class WebGLBatchBuild
    {
        private const string OutputFolderName = "WebGL-Build";

        [MenuItem("BrainPlanner/Build/WebGL (Batch Compatible)")]
        public static void BuildWebGL()
        {
            PaperBuildChecks.PrepareRuntimeMaterials();
            PaperBuildChecks.Run();
            UnityEditor.SceneManagement.EditorSceneManager.OpenScene("Assets/Scenes/BrainPlanner.unity");
            PaperBuildChecks.ValidateScene();
            string projectRoot = Directory.GetParent(Application.dataPath)?.FullName
                ?? throw new InvalidOperationException("Unable to resolve project root.");
            string outputRoot = Path.Combine(projectRoot, OutputFolderName);
            string outputBuildDir = Path.Combine(outputRoot, "Build");

            if (!Directory.Exists(outputRoot))
            {
                Directory.CreateDirectory(outputRoot);
            }

            var scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();
            if (scenes.Length == 0)
            {
                const string fallbackScene = "Assets/Scenes/BrainPlanner.unity";
                if (!File.Exists(Path.Combine(projectRoot, fallbackScene.Replace('/', Path.DirectorySeparatorChar))))
                {
                    throw new InvalidOperationException("No enabled scenes found and fallback scene Assets/Scenes/BrainPlanner.unity is missing.");
                }
                scenes = new[] { fallbackScene };
                Debug.Log("[WebGLBatchBuild] BuildSettings has no enabled scenes. Fallback to Assets/Scenes/BrainPlanner.unity");
            }

            var options = new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = outputRoot,
                target = BuildTarget.WebGL,
                options = BuildOptions.None,
            };

            Debug.Log($"[WebGLBatchBuild] Building WebGL to: {outputRoot}");
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result != BuildResult.Succeeded)
            {
                throw new InvalidOperationException(
                    $"WebGL build failed. Result={report.summary.result}, errors={report.summary.totalErrors}"
                );
            }

            Debug.Log($"[WebGLBatchBuild] Build succeeded. Output: {outputBuildDir}");
        }
    }
}
