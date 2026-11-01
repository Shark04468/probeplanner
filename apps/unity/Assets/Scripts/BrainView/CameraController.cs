using UnityEngine;

namespace BrainPlanner.BrainView
{
    /// <summary>
    /// Orbit camera controller used by the 3D brain view.
    /// Exposes pose updates so React anchors can follow the true Unity camera.
    /// </summary>
    public class CameraController : MonoBehaviour
    {
        [Header("Target")]
        public Vector3 PivotPoint = new Vector3(0f, -2.8f, -0.8f);

        [Header("Initial View")]
        public float InitialDistance = 18f;
        public float InitialTheta = 0.55f; // rad
        public float InitialPhi = 1.25f;   // rad

        [Header("Limits")]
        public float MinDistance = 4f;
        public float MaxDistance = 55f;
        public float MinPhi = 0.05f;
        public float MaxPhi = Mathf.PI - 0.05f;

        [Header("Speed")]
        public float RotateSpeed = 0.007f;
        public float ZoomSpeed = 0.02f;
        public float TouchRotateSpeed = 0.005f;
        public float TouchZoomSpeed = 0.01f;

        [Header("Smoothing")]
        [Range(0f, 1f)] public float Damping = 0.12f;

        [Header("Interaction")]
        public bool CameraLocked = false;

        public event System.Action<float, float, float> OnPoseChanged;

        private float _theta;
        private float _phi;
        private float _distance;

        private float _targetTheta;
        private float _targetPhi;
        private float _targetDistance;

        private bool _isDragging;
        private Vector2 _lastMousePos;
        private float _lastPinchDist;
        private bool _isPinching;

        private Vector3 _lastPose = new Vector3(float.NaN, float.NaN, float.NaN);
        private Camera _camera;
        private float _baseVerticalFov;
        private float _lastFov = float.NaN;

        public Camera ControlledCamera => _camera;

        void Start()
        {
            _camera = GetComponent<Camera>();
            _baseVerticalFov = _camera != null ? _camera.fieldOfView : 38f;
            _theta = _targetTheta = InitialTheta;
            _phi = _targetPhi = InitialPhi;
            _distance = _targetDistance = InitialDistance;
            UpdatePosition(forceEmit: true);
        }

        void Update()
        {
            HandleMouse();
            HandleTouch();
            SmoothUpdate();
        }

        private void HandleMouse()
        {
            if (CameraLocked)
            {
                _isDragging = false;
                return;
            }

            if (Input.GetMouseButtonDown(0))
            {
                _isDragging = true;
                _lastMousePos = Input.mousePosition;
            }

            if (Input.GetMouseButtonUp(0))
            {
                _isDragging = false;
            }

            if (_isDragging)
            {
                Vector2 delta = (Vector2)Input.mousePosition - _lastMousePos;
                _lastMousePos = Input.mousePosition;
                _targetTheta -= delta.x * RotateSpeed;
                _targetPhi = Mathf.Clamp(_targetPhi + delta.y * RotateSpeed, MinPhi, MaxPhi);
            }

            float scroll = Input.GetAxis("Mouse ScrollWheel");
            if (Mathf.Abs(scroll) > 0.001f)
            {
                _targetDistance = Mathf.Clamp(_targetDistance - scroll * 8f, MinDistance, MaxDistance);
            }
        }

        private void HandleTouch()
        {
            if (CameraLocked)
            {
                _isPinching = false;
                return;
            }

            if (Input.touchCount == 1)
            {
                var touch = Input.GetTouch(0);
                if (touch.phase == TouchPhase.Moved)
                {
                    _targetTheta -= touch.deltaPosition.x * TouchRotateSpeed;
                    _targetPhi = Mathf.Clamp(_targetPhi + touch.deltaPosition.y * TouchRotateSpeed, MinPhi, MaxPhi);
                }

                _isPinching = false;
            }
            else if (Input.touchCount == 2)
            {
                var t0 = Input.GetTouch(0);
                var t1 = Input.GetTouch(1);
                float pinchDist = Vector2.Distance(t0.position, t1.position);

                if (!_isPinching)
                {
                    _lastPinchDist = pinchDist;
                    _isPinching = true;
                }
                else
                {
                    float delta = _lastPinchDist - pinchDist;
                    _targetDistance = Mathf.Clamp(
                        _targetDistance + delta * TouchZoomSpeed,
                        MinDistance,
                        MaxDistance
                    );
                    _lastPinchDist = pinchDist;
                }
            }
            else
            {
                _isPinching = false;
            }
        }

        private void SmoothUpdate()
        {
            float t = 1f - Mathf.Pow(Damping, Time.deltaTime * 60f);
            _theta = Mathf.LerpAngle(_theta * Mathf.Rad2Deg, _targetTheta * Mathf.Rad2Deg, t) * Mathf.Deg2Rad;
            _phi = Mathf.Lerp(_phi, _targetPhi, t);
            _distance = Mathf.Lerp(_distance, _targetDistance, t);
            UpdatePosition(forceEmit: false);
        }

        private void UpdatePosition(bool forceEmit)
        {
            if (_camera != null)
                _camera.fieldOfView = FitVerticalFov(_baseVerticalFov, _camera.aspect);
            float sinPhi = Mathf.Sin(_phi);
            float cosPhi = Mathf.Cos(_phi);

            transform.position = PivotPoint + new Vector3(
                _distance * sinPhi * Mathf.Cos(_theta),
                _distance * cosPhi,
                _distance * sinPhi * Mathf.Sin(_theta)
            );
            transform.LookAt(PivotPoint);
            EmitPoseIfChanged(forceEmit);
        }

        private void EmitPoseIfChanged(bool force)
        {
            if (OnPoseChanged == null) return;

            var pose = new Vector3(_theta, _phi, _distance);
            if (!force)
            {
                if (
                    Mathf.Abs(pose.x - _lastPose.x) < 0.0001f &&
                    Mathf.Abs(pose.y - _lastPose.y) < 0.0001f &&
                    Mathf.Abs(pose.z - _lastPose.z) < 0.0005f &&
                    _camera != null && Mathf.Abs(_camera.fieldOfView - _lastFov) < 0.001f
                )
                {
                    return;
                }
            }

            _lastPose = pose;
            _lastFov = _camera != null ? _camera.fieldOfView : _baseVerticalFov;
            OnPoseChanged?.Invoke(_theta, _phi, _distance);
        }

        public static float FitVerticalFov(float baseFov, float aspect)
        {
            // Retain the landscape horizontal field on narrow canvases without moving anatomy or the orbit pivot.
            float scale = Mathf.Max(1f, 1.5f / Mathf.Max(0.1f, aspect));
            return Mathf.Clamp(2f * Mathf.Atan(Mathf.Tan(baseFov * Mathf.Deg2Rad * 0.5f) * scale)
                * Mathf.Rad2Deg, 1f, 170f);
        }

        public void AnimateTo(float theta, float phi, float distance)
        {
            _targetTheta = theta;
            _targetPhi = Mathf.Clamp(phi, MinPhi, MaxPhi);
            _targetDistance = Mathf.Clamp(distance, MinDistance, MaxDistance);
        }

        public void FocusPoint(Vector3 point, float distance = -1f)
        {
            PivotPoint = point;
            if (!float.IsNaN(distance) && !float.IsInfinity(distance) && distance > 0f)
            {
                _targetDistance = Mathf.Clamp(distance, MinDistance, MaxDistance);
            }
            else
            {
                _targetDistance = Mathf.Clamp(Mathf.Max(_targetDistance, 9.5f), MinDistance, MaxDistance);
            }
            UpdatePosition(forceEmit: true);
        }

        public void SetLocked(bool locked)
        {
            CameraLocked = locked;
            _isDragging = false;
            _isPinching = false;
        }

        public bool IsLocked() => CameraLocked;

        public void SetView(string viewName)
        {
            switch ((viewName ?? string.Empty).ToLowerInvariant())
            {
                case "top":
                    AnimateTo(0f, 0.05f, InitialDistance);
                    break;
                case "side":
                    AnimateTo(0f, Mathf.PI * 0.5f, InitialDistance);
                    break;
                case "front":
                    AnimateTo(Mathf.PI * 0.5f, Mathf.PI * 0.5f, InitialDistance);
                    break;
                case "iso":
                default:
                    AnimateTo(0.55f, 1.25f, InitialDistance);
                    break;
            }
        }

        public Vector3 GetCurrentEuler() =>
            new Vector3(_theta * Mathf.Rad2Deg, _phi * Mathf.Rad2Deg, _distance);

        public Vector3 GetPose() => new Vector3(_theta, _phi, _distance);

        public void ForceEmitPose() => EmitPoseIfChanged(force: true);
    }
}
