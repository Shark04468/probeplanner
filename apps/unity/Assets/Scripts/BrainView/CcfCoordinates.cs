using UnityEngine;

namespace BrainPlanner.BrainView
{
    public static class CcfCoordinates
    {
        public static Vector3 FromMicrometres(Vector3 ccf)
        {
            return new Vector3((ccf.z - 5700f) / 1000f,
                -(ccf.y - 440f) / 1000f, -(ccf.x - 5400f) / 1000f);
        }

        public static Vector3 ProbeDirection(float thetaDegrees, float phiDegrees)
        {
            float theta = thetaDegrees * Mathf.Deg2Rad;
            float phi = phiDegrees * Mathf.Deg2Rad;
            return new Vector3(Mathf.Sin(theta) * Mathf.Sin(phi),
                -Mathf.Cos(theta), -Mathf.Sin(theta) * Mathf.Cos(phi)).normalized;
        }

        public static Vector3 ProbeLateralAxis(Vector3 direction, float spinDegrees = 0f)
        {
            var dir = direction.normalized;
            var lateral = Vector3.right - dir * Vector3.Dot(Vector3.right, dir);
            if (lateral.sqrMagnitude < 1e-12f) lateral = Vector3.Cross(dir, Vector3.up);
            lateral = lateral.sqrMagnitude < 1e-12f ? Vector3.back : lateral.normalized;
            // The CCF -> Unity mapping reverses handedness, hence the negative angle.
            return spinDegrees == 0f ? lateral : Quaternion.AngleAxis(-spinDegrees % 360f, dir) * lateral;
        }
    }
}
