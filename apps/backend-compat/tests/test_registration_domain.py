"""Engineering regression coverage, not independent anatomical validation."""
import itertools
import sys
from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ambmc_true_outer_pipeline as pipeline


def reference(rotated=False, full=False):
    image = sitk.Image([528, 320, 456] if full else [52, 40, 36], sitk.sitkUInt8)
    image.SetSpacing((.025,) * 3 if full else (.025, .03, .04))
    if rotated:
        angle = .41
        image.SetOrigin((4., -3., 2.))
        image.SetDirection((np.cos(angle), -np.sin(angle), 0.,
                            np.sin(angle), np.cos(angle), 0., 0., 0., 1.))
    return image


def constant_displacement(transform):
    parameters = np.asarray(transform.GetParameters()).reshape(3, -1)
    parameters[:] = np.array([.2, -.1, .04])[:, None]
    transform.SetParameters(parameters.ravel().tolist())
    return transform


@pytest.mark.parametrize("rotated", [False, True])
def test_output_initialization_preserves_nonzero_displacement_on_all_faces(rotated):
    image = reference(rotated)
    tx = constant_displacement(pipeline._initialize_output_bspline(image, [3] * 3))
    report = pipeline._check_bspline_output_domain(image, tx)
    assert report["coversOutputVoxelCenters"] is True
    assert report["checkedCorners"] == 8
    assert report["minimumCornerMarginMm"] > 0
    for axis, side in itertools.product(range(3), [0, 1]):
        coordinates = [np.linspace(0, n - 1, 5) for n in image.GetSize()]
        coordinates[axis] = [side * (image.GetSize()[axis] - 1)]
        for index in itertools.product(*coordinates):
            point = image.TransformContinuousIndexToPhysicalPoint(tuple(index))
            np.testing.assert_allclose(np.asarray(tx.TransformPoint(point)) - point,
                                       [.2, -.1, .04], atol=1e-10)


def test_real_ccf_geometry_rejects_legacy_domain_and_accepts_full_domain():
    image = reference(full=True)
    coarse = pipeline._resample_to_spacing(image, (.1,) * 3)
    assert coarse.GetSize() == (132, 80, 114)
    old = sitk.BSplineTransformInitializer(coarse, [5] * 3)
    with pytest.raises(pipeline.RegistrationDomainError, match="every output-grid"):
        pipeline._check_bspline_output_domain(image, old)
    new = constant_displacement(pipeline._initialize_output_bspline(image, [5] * 3))
    assert pipeline._check_bspline_output_domain(image, new)["outputSize"] == [528, 320, 456]
    for index in itertools.product(*[(0., float(n - 1)) for n in image.GetSize()]):
        point = image.TransformContinuousIndexToPhysicalPoint(index)
        np.testing.assert_allclose(np.asarray(new.TransformPoint(point)) - point,
                                   [.2, -.1, .04], atol=1e-10)


def test_serialized_and_wrapped_domain_retains_coverage(tmp_path):
    image = reference(rotated=True)
    tx = constant_displacement(pipeline._initialize_output_bspline(image, [3] * 3))
    path = tmp_path / "bspline.tfm"
    sitk.WriteTransform(tx, str(path))
    loaded = sitk.ReadTransform(str(path))
    wrapped = sitk.CompositeTransform([sitk.CompositeTransform([loaded])])
    assert pipeline._check_bspline_output_domain(image, wrapped)["coversOutputVoxelCenters"]
    point = image.TransformIndexToPhysicalPoint(tuple(n - 1 for n in image.GetSize()))
    np.testing.assert_allclose(wrapped.TransformPoint(point), tx.TransformPoint(point), atol=1e-10)


@pytest.mark.parametrize("kind", ["affine", "empty", "multiple"])
def test_unexpected_optimized_transform_is_rejected(kind):
    image = reference()
    transforms = {"affine": sitk.AffineTransform(3), "empty": sitk.CompositeTransform(3),
                  "multiple": sitk.CompositeTransform([sitk.AffineTransform(3),
                      pipeline._initialize_output_bspline(image, [3] * 3)])}
    with pytest.raises(pipeline.RegistrationDomainError):
        pipeline._check_bspline_output_domain(image, transforms[kind])


def test_two_dimensional_reference_rejected():
    with pytest.raises(pipeline.RegistrationDomainError, match="3D output"):
        pipeline._check_bspline_output_domain(sitk.Image([10, 10], sitk.sitkUInt8), sitk.Transform(3, sitk.sitkIdentity))


def run_entry(monkeypatch, mode="normal", **options):
    image = reference()
    fixed = sitk.Cast(image + 1, sitk.sitkFloat32)
    calls = []

    class RegistrationDouble:
        RANDOM = 2

        def __init__(self):
            self.initial = None
            calls.append(self)

        def SetInitialTransform(self, transform, inPlace=False):
            self.initial = transform

        def Execute(self, fixed_small, moving_small):
            if self.initial.GetName() == "BSplineTransform":
                if mode == "optimizer_failure":
                    raise RuntimeError("synthetic optimizer failure")
                if mode == "truncated_result":
                    return sitk.BSplineTransformInitializer(fixed_small, [3] * 3)
                return sitk.CompositeTransform([self.initial])
            return sitk.CompositeTransform([self.initial])

        def GetMetricValue(self):
            return .125

        def __getattr__(self, name):
            if name.startswith("Set") or name == "SmoothingSigmasAreSpecifiedInPhysicalUnitsOn":
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    monkeypatch.setattr(pipeline.sitk, "ImageRegistrationMethod", RegistrationDouble)
    kwargs = dict(quick=False, skip_deformable=False, deformable_mesh=(3, 3, 3),
                  deformable_learning_rate=.1, deformable_iterations=2,
                  deformable_sampling=.2, deformable_smoothing_sigmas=(2., 1., 0.),
                  registration_spacing_mm=.1)
    kwargs.update(options)
    result = pipeline._register_ambmc_to_ccf(fixed, fixed, image + 1, image + 1, **kwargs)
    return result, calls


def test_shared_entry_uses_full_geometry_and_records_coverage(monkeypatch):
    result, calls = run_entry(monkeypatch)
    assert len(calls) == 3
    assert result.summary["usedDeformable"] is True
    assert result.summary["deformableError"] is None
    assert result.summary["deformableDomain"]["outputSize"] == [52, 40, 36]


def test_truncated_optimized_domain_cannot_fall_back_silently(monkeypatch):
    with pytest.raises(pipeline.RegistrationDomainError, match="every output-grid"):
        run_entry(monkeypatch, mode="truncated_result")


def test_bad_initializer_cannot_fall_back_silently(monkeypatch):
    initializer = pipeline.sitk.BSplineTransformInitializer

    def truncate(image, mesh, order=3):
        return initializer(pipeline._resample_to_spacing(image, (.1,) * 3), mesh, order=order)

    monkeypatch.setattr(pipeline.sitk, "BSplineTransformInitializer", truncate)
    with pytest.raises(pipeline.RegistrationDomainError, match="every output-grid"):
        run_entry(monkeypatch)


def test_optimizer_failure_keeps_existing_fallback_but_records_reason(monkeypatch):
    result, _ = run_entry(monkeypatch, mode="optimizer_failure")
    assert result.summary["usedDeformable"] is False
    assert result.summary["deformableDomain"] is None
    assert result.summary["deformableError"] == "synthetic optimizer failure"


@pytest.mark.parametrize("options", [{"quick": True}, {"skip_deformable": True}])
def test_nondeformable_modes_do_not_initialize_bspline(monkeypatch, options):
    def forbidden(*args, **kwargs):
        raise AssertionError("B-spline should not be initialized")

    monkeypatch.setattr(pipeline, "_initialize_output_bspline", forbidden)
    result, _ = run_entry(monkeypatch, **options)
    assert result.summary["usedDeformable"] is False
    assert result.summary["deformableDomain"] is None
