using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BrainPlanner.Editor
{
    [InitializeOnLoad]
    public static class RegionLodPlayModeChecks
    {
        private const string Pending = "ProbePlanner.RegionLodPlayMode.Pending";

        static RegionLodPlayModeChecks()
        {
            EditorApplication.playModeStateChanged += state => {
                if (state != PlayModeStateChange.EnteredPlayMode || !SessionState.GetBool(Pending, false)) return;
                SessionState.SetBool(Pending, false);
                new GameObject("LodPlayModeDriver").AddComponent<RegionLodPlayModeDriver>();
            };
        }

        public static void Run()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            SessionState.SetBool(Pending, true);
            EditorApplication.EnterPlaymode();
        }
    }
}
