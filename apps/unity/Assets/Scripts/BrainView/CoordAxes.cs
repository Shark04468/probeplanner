// CoordAxes.cs
// Coordinate axes and Bregma/Lambda markers.

using UnityEngine;

namespace BrainPlanner.BrainView
{
    public class CoordAxes : MonoBehaviour
    {
        [Header("Axis Lines")]
        public float AxisAlpha = 0.22f;
        public float AxisLineWidth = 0.018f;

        [Header("Markers")]
        public float BregmaRadius = 0.14f;
        public float LambdaRadius = 0.10f;

        private const float LambdaAP = -4.2f;

        void Start()
        {
            Build();
        }

        private void Build()
        {
            DrawAxis(
                new Vector3(-6f, 0f, 0f),
                new Vector3(6f, 0f, 0f),
                new Color(1f, 0.27f, 0.40f, AxisAlpha),
                "Axis_ML"
            );

            DrawAxis(
                new Vector3(0f, -9f, 0f),
                new Vector3(0f, 1.5f, 0f),
                new Color(0.27f, 1f, 0.53f, AxisAlpha),
                "Axis_DV"
            );

            DrawAxis(
                new Vector3(0f, 0f, -10f),
                new Vector3(0f, 0f, 7f),
                new Color(0.27f, 0.53f, 1f, AxisAlpha),
                "Axis_AP"
            );

            CreateMarker("Bregma", Vector3.zero, BregmaRadius, new Color(1f, 0.85f, 0f));
            CreateMarker("Lambda", new Vector3(0f, 0f, LambdaAP), LambdaRadius, new Color(1f, 0.55f, 0f));

            CreateLabel("AP", new Vector3(0f, 0.25f, 7f), new Color(0.27f, 0.53f, 1f));
            CreateLabel("ML", new Vector3(6.3f, 0.25f, 0f), new Color(1f, 0.27f, 0.40f));
            CreateLabel("DV", new Vector3(0.3f, 1.8f, 0f), new Color(0.27f, 1f, 0.53f));
            CreateLabel("Bregma", new Vector3(0.2f, 0.3f, 0.3f), new Color(1f, 0.85f, 0f), 0.09f);
            CreateLabel("Lambda", new Vector3(0.2f, 0.3f, LambdaAP + 0.3f), new Color(1f, 0.55f, 0f), 0.09f);
        }

        private void DrawAxis(Vector3 from, Vector3 to, Color color, string name)
        {
            GameObject go = new GameObject(name);
            go.transform.SetParent(transform, false);

            LineRenderer lr = go.AddComponent<LineRenderer>();
            lr.positionCount = 2;
            lr.SetPosition(0, from);
            lr.SetPosition(1, to);
            lr.startWidth = AxisLineWidth;
            lr.endWidth = AxisLineWidth;
            lr.useWorldSpace = false;

            Material mat = new Material(Shader.Find("Universal Render Pipeline/Particles/Unlit"));
            if (mat.shader.name == "Hidden/InternalErrorShader")
                mat = new Material(Shader.Find("Sprites/Default"));

            mat.color = color;
            lr.material = mat;
            lr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            lr.receiveShadows = false;
        }

        private void CreateMarker(string name, Vector3 pos, float radius, Color color)
        {
            GameObject go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            go.name = name;
            go.transform.SetParent(transform, false);
            go.transform.localPosition = pos;
            go.transform.localScale = Vector3.one * radius * 2f;

            Collider col = go.GetComponent<Collider>();
            if (col != null) Destroy(col);

            Material mat = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            mat.color = color;
            mat.SetFloat("_Metallic", 0f);
            mat.SetFloat("_Smoothness", 0.8f);
            go.GetComponent<MeshRenderer>().material = mat;
        }

        private void CreateLabel(string text, Vector3 pos, Color color, float scale = 0.12f)
        {
            GameObject go = new GameObject($"Label_{text}");
            go.transform.SetParent(transform, false);
            go.transform.localPosition = pos;
            go.transform.localScale = Vector3.one * scale;

            TextMesh tm = go.AddComponent<TextMesh>();
            tm.text = text;
            tm.color = new Color(color.r, color.g, color.b, 0.65f);
            tm.fontSize = 64;
            tm.fontStyle = FontStyle.Bold;
            tm.alignment = TextAlignment.Left;
            tm.anchor = TextAnchor.MiddleLeft;

            go.AddComponent<FaceCameraLabel>();
        }
    }

    public class FaceCameraLabel : MonoBehaviour
    {
        private Camera _cam;

        void Start()
        {
            _cam = Camera.main;
        }

        void LateUpdate()
        {
            if (_cam == null) return;
            transform.LookAt(transform.position + _cam.transform.forward);
        }
    }
}
