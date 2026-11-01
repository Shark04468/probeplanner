// Editor/BuildScript.cs
// Unity 命令行构建入口（供 deploy.sh 调用）
// 用法：Unity -executeMethod BrainPlanner.Editor.BuildScript.BuildWebGL -buildOutput /path/to/output

#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using System;
using System.Linq;

namespace BrainPlanner.Editor
{
    public static class BuildScript
    {
        public static void BuildWebGL()
        {
            // 从命令行参数读取输出路径
            string outputPath = GetArg("-buildOutput") ?? "WebGL-Build";

            Debug.Log($"[BuildScript] 开始构建 WebGL → {outputPath}");

            var scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();

            if (scenes.Length == 0)
            {
                // 自动添加 BrainPlanner 场景
                scenes = new[] { "Assets/Scenes/BrainPlanner.unity" };
            }

            var options = new BuildPlayerOptions
            {
                scenes          = scenes,
                locationPathName = outputPath,
                target           = BuildTarget.WebGL,
                options          = BuildOptions.None,
            };

            var report = BuildPipeline.BuildPlayer(options);
            var summary = report.summary;

            if (summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded)
            {
                Debug.Log($"[BuildScript] ✅ 构建成功！大小：{summary.totalSize / 1024 / 1024:F1} MB");
                EditorApplication.Exit(0);
            }
            else
            {
                Debug.LogError($"[BuildScript] ❌ 构建失败：{summary.result}");
                EditorApplication.Exit(1);
            }
        }

        private static string GetArg(string name)
        {
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == name) return args[i + 1];
            return null;
        }
    }
}
#endif
