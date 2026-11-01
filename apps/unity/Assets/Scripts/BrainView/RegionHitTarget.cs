using UnityEngine;

namespace BrainPlanner.BrainView
{
    /// <summary>
    /// Marks a renderer/collider as belonging to a logical planner region.
    /// </summary>
    public class RegionHitTarget : MonoBehaviour
    {
        [SerializeField] private string regionKey = string.Empty;

        public string RegionKey
        {
            get => regionKey;
            set => regionKey = value ?? string.Empty;
        }
    }
}
