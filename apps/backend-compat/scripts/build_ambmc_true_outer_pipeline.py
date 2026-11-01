#!/usr/bin/env python
"""
Build MRI+CCF hybrid artifacts with AMBMC -> CCF registration and
MRI-derived outer-layer masks (skull / outer tissue).

Outputs:
  - registration_summary.json
  - ambmc_in_ccf.nii.gz
  - ambmc_to_ccf_displacement.nii.gz (when deformable registration succeeds)
  - jacobian.npy / jacobian.npz (derived from displacement field)
  - head_mask.nii.gz
  - skull_mask.nii.gz
  - outer_tissue_mask.nii.gz
  - annotation.npy / stats.json / layered_sdf.npz (via build_mri_ccf_hybrid_artifacts.py)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Dict, List, Tuple

import numpy as np
import SimpleITK as sitk


def _normalize_spacing_units_mm(img: sitk.Image) -> sitk.Image:
    # Allen CCF nrrd often stores spacing as "25,25,25" while this means um.
    # If any axis spacing is > 1.0, we treat it as um and convert to mm.
    spacing = tuple(float(v) for v in img.GetSpacing())
    if max(spacing) > 1.0:
        out = sitk.Image(img)
        out.SetSpacing(tuple(v / 1000.0 for v in spacing))
        return out
    return img


def _canonical_index_space(img: sitk.Image) -> sitk.Image:
    # Build a direction/origin-neutral copy for robust cross-atlas registration.
    arr = sitk.GetArrayFromImage(img)
    out = sitk.GetImageFromArray(arr)
    spacing = tuple(float(v) for v in img.GetSpacing())
    if max(spacing) > 1.0:
        spacing = tuple(v / 1000.0 for v in spacing)
    out.SetSpacing(spacing)
    out.SetOrigin((0.0, 0.0, 0.0))
    out.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    return out


def _resample_to_spacing(img: sitk.Image, spacing_xyz: Tuple[float, float, float], is_label: bool = False) -> sitk.Image:
    in_spacing = np.array(img.GetSpacing(), dtype=np.float64)
    in_size = np.array(img.GetSize(), dtype=np.int64)
    out_spacing = np.array(spacing_xyz, dtype=np.float64)
    out_size = np.maximum(1, np.round(in_size * (in_spacing / out_spacing))).astype(np.int64).tolist()
    return sitk.Resample(
        img,
        out_size,
        sitk.Transform(),
        sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear,
        img.GetOrigin(),
        tuple(out_spacing.tolist()),
        img.GetDirection(),
        0,
        img.GetPixelID(),
    )


def _normalize_percentile(img: sitk.Image, p_low: float = 1.0, p_high: float = 99.0) -> sitk.Image:
    arr = sitk.GetArrayViewFromImage(img).astype(np.float32)
    lo = float(np.percentile(arr, p_low))
    hi = float(np.percentile(arr, p_high))
    if hi <= lo + 1e-6:
        return sitk.Cast(img, sitk.sitkFloat32)
    out = sitk.Clamp((sitk.Cast(img, sitk.sitkFloat32) - lo) / (hi - lo), sitk.sitkFloat32, 0.0, 1.0)
    return out


def _largest_component(mask: sitk.Image) -> sitk.Image:
    cc = sitk.ConnectedComponent(sitk.Cast(mask > 0, sitk.sitkUInt8))
    rel = sitk.RelabelComponent(cc, sortByObjectSize=True)
    return sitk.Cast(rel == 1, sitk.sitkUInt8)


def _binary_close_fill(mask: sitk.Image, radius: int = 2) -> sitk.Image:
    out = sitk.BinaryMorphologicalClosing(mask, [radius, radius, radius])
    out = sitk.BinaryFillhole(out)
    return sitk.Cast(out > 0, sitk.sitkUInt8)


@dataclass
class RegistrationResult:
    transform: sitk.Transform
    summary: Dict[str, float]


def _register_ambmc_to_ccf(
    fixed_full: sitk.Image,
    moving_full: sitk.Image,
    fixed_mask_full: sitk.Image,
    moving_mask_full: sitk.Image,
    quick: bool,
    skip_deformable: bool,
    deformable_mesh: Tuple[int, int, int],
    deformable_learning_rate: float,
    deformable_iterations: int,
    deformable_sampling: float,
    deformable_smoothing_sigmas: Tuple[float, ...],
    registration_spacing_mm: float = 0.10,
) -> RegistrationResult:
    # Register on a coarse grid, then apply transform on full-resolution grid.
    reg_spacing = (float(registration_spacing_mm),) * 3
    fixed_small = _resample_to_spacing(fixed_full, reg_spacing, is_label=False)
    moving_small = _resample_to_spacing(moving_full, reg_spacing, is_label=False)
    fixed_mask_small = sitk.Cast(_resample_to_spacing(fixed_mask_full, reg_spacing, is_label=True) > 0, sitk.sitkUInt8)
    moving_mask_small = sitk.Cast(_resample_to_spacing(moving_mask_full, reg_spacing, is_label=True) > 0, sitk.sitkUInt8)
    fixed_small = _normalize_percentile(fixed_small)
    moving_small = _normalize_percentile(moving_small)

    def _center_point(img: sitk.Image) -> Tuple[float, float, float]:
        cidx = [0.5 * (float(s) - 1.0) for s in img.GetSize()]
        return img.TransformContinuousIndexToPhysicalPoint(cidx)

    def _mask_centroid(mask_img: sitk.Image, fallback_img: sitk.Image) -> Tuple[float, float, float]:
        stats = sitk.LabelShapeStatisticsImageFilter()
        stats.Execute(sitk.Cast(mask_img > 0, sitk.sitkUInt8))
        if stats.HasLabel(1):
            c = stats.GetCentroid(1)
            return float(c[0]), float(c[1]), float(c[2])
        return _center_point(fallback_img)

    fixed_center = np.array(_mask_centroid(fixed_mask_small, fixed_small), dtype=np.float64)
    moving_center = np.array(_mask_centroid(moving_mask_small, moving_small), dtype=np.float64)
    base_offset = tuple((fixed_center - moving_center).tolist())
    init = sitk.TranslationTransform(3)
    init.SetOffset(base_offset)

    if quick:
        return RegistrationResult(
            transform=init,
            summary={
                "quickTranslationOnly": True,
                "initialCenterOffset": list(base_offset),
                "usedAffine": False,
                "affineError": None,
                "affineMetric": None,
                "deformableMetric": None,
                "usedDeformable": False,
            },
        )

    # Step-1: rigid alignment on binary masks for robust overlap.
    used_rigid = True
    rigid_error = None
    rigid_metric = None
    rigid_init = sitk.Euler3DTransform()
    rigid_init.SetTranslation(base_offset)
    try:
        reg_rigid = sitk.ImageRegistrationMethod()
        reg_rigid.SetMetricAsMeanSquares()
        reg_rigid.SetInterpolator(sitk.sitkLinear)
        reg_rigid.SetShrinkFactorsPerLevel([4, 2, 1])
        reg_rigid.SetSmoothingSigmasPerLevel([2, 1, 0])
        reg_rigid.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
        reg_rigid.SetOptimizerAsGradientDescentLineSearch(
            learningRate=0.5,
            numberOfIterations=180,
            convergenceMinimumValue=1e-7,
            convergenceWindowSize=10,
        )
        reg_rigid.SetOptimizerScalesFromPhysicalShift()
        reg_rigid.SetInitialTransform(rigid_init, inPlace=False)
        rigid_tx = reg_rigid.Execute(
            sitk.Cast(fixed_mask_small, sitk.sitkFloat32),
            sitk.Cast(moving_mask_small, sitk.sitkFloat32),
        )
        rigid_metric = float(reg_rigid.GetMetricValue())
    except Exception as ex:
        rigid_tx = init
        rigid_metric = None
        used_rigid = False
        rigid_error = str(ex)

    # Step-2: affine refinement on MRI intensity with fixed/moving brain masks.
    reg_aff = sitk.ImageRegistrationMethod()
    reg_aff.SetMetricAsMattesMutualInformation(numberOfHistogramBins=64)
    reg_aff.SetMetricSamplingStrategy(reg_aff.RANDOM)
    reg_aff.SetMetricSamplingPercentage(0.25, seed=42)
    reg_aff.SetMetricFixedMask(sitk.Cast(fixed_mask_small > 0, sitk.sitkUInt8))
    reg_aff.SetMetricMovingMask(sitk.Cast(moving_mask_small > 0, sitk.sitkUInt8))
    reg_aff.SetInterpolator(sitk.sitkLinear)
    reg_aff.SetShrinkFactorsPerLevel([8, 4, 2, 1])
    reg_aff.SetSmoothingSigmasPerLevel([3, 2, 1, 0])
    reg_aff.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    reg_aff.SetOptimizerAsGradientDescentLineSearch(
        learningRate=1.0,
        numberOfIterations=320,
        convergenceMinimumValue=1e-6,
        convergenceWindowSize=10,
    )
    reg_aff.SetOptimizerScalesFromPhysicalShift()
    reg_aff.SetMovingInitialTransform(rigid_tx)
    reg_aff.SetInitialTransform(sitk.AffineTransform(3), inPlace=False)

    used_affine = True
    affine_error = None
    affine_mode = "intensity_mi"
    try:
        aff_delta_tx = reg_aff.Execute(fixed_small, moving_small)
        aff_metric = float(reg_aff.GetMetricValue())
        aff_tx = sitk.CompositeTransform(3)
        aff_tx.AddTransform(rigid_tx)
        aff_tx.AddTransform(aff_delta_tx)
    except Exception as ex:
        # Fallback-1: affine on binary masks (more stable when intensity MI fails).
        primary_err = str(ex)
        try:
            reg_aff_mask = sitk.ImageRegistrationMethod()
            reg_aff_mask.SetMetricAsMeanSquares()
            reg_aff_mask.SetInterpolator(sitk.sitkLinear)
            reg_aff_mask.SetShrinkFactorsPerLevel([4, 2, 1])
            reg_aff_mask.SetSmoothingSigmasPerLevel([2, 1, 0])
            reg_aff_mask.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
            reg_aff_mask.SetOptimizerAsGradientDescentLineSearch(
                learningRate=0.4,
                numberOfIterations=200,
                convergenceMinimumValue=1e-7,
                convergenceWindowSize=10,
            )
            reg_aff_mask.SetOptimizerScalesFromPhysicalShift()
            reg_aff_mask.SetMovingInitialTransform(rigid_tx)
            reg_aff_mask.SetInitialTransform(sitk.AffineTransform(3), inPlace=False)
            aff_delta_tx = reg_aff_mask.Execute(
                sitk.Cast(fixed_mask_small, sitk.sitkFloat32),
                sitk.Cast(moving_mask_small, sitk.sitkFloat32),
            )
            aff_metric = float(reg_aff_mask.GetMetricValue())
            aff_tx = sitk.CompositeTransform(3)
            aff_tx.AddTransform(rigid_tx)
            aff_tx.AddTransform(aff_delta_tx)
            affine_mode = "mask_mse"
            affine_error = None
            used_affine = True
        except Exception as ex2:
            # Fallback-2: rigid-only.
            aff_tx = rigid_tx
            aff_metric = None
            used_affine = False
            affine_mode = "rigid_only"
            affine_error = f"{primary_err} | mask_fallback={str(ex2)}"

    final_tx: sitk.Transform = aff_tx
    bspline_metric = None
    used_deformable = False

    if not skip_deformable:
        try:
            mesh = [int(x) for x in deformable_mesh]
            bspline = sitk.BSplineTransformInitializer(image1=fixed_small, transformDomainMeshSize=mesh, order=3)
            reg_def = sitk.ImageRegistrationMethod()
            reg_def.SetMetricAsANTSNeighborhoodCorrelation(4)
            reg_def.SetMetricSamplingStrategy(reg_def.RANDOM)
            reg_def.SetMetricSamplingPercentage(float(deformable_sampling), seed=43)
            reg_def.SetInterpolator(sitk.sitkLinear)
            reg_def.SetShrinkFactorsPerLevel([4, 2, 1])
            reg_def.SetSmoothingSigmasPerLevel([float(x) for x in deformable_smoothing_sigmas])
            reg_def.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
            reg_def.SetOptimizerAsGradientDescentLineSearch(
                learningRate=float(deformable_learning_rate),
                numberOfIterations=int(deformable_iterations),
                convergenceMinimumValue=1e-6,
                convergenceWindowSize=8,
            )
            reg_def.SetOptimizerScalesFromPhysicalShift()
            reg_def.SetMovingInitialTransform(aff_tx)
            reg_def.SetInitialTransform(bspline, inPlace=False)
            bspline_tx = reg_def.Execute(fixed_small, moving_small)
            bspline_metric = float(reg_def.GetMetricValue())
            comp = sitk.CompositeTransform(3)
            comp.AddTransform(aff_tx)
            comp.AddTransform(bspline_tx)
            final_tx = comp
            used_deformable = True
        except Exception:
            # Keep affine-only if deformable registration fails.
            used_deformable = False

    summary = {
        "initialCenterOffset": list(base_offset),
        "usedRigid": used_rigid,
        "rigidError": rigid_error,
        "rigidMetric": rigid_metric,
        "usedAffine": used_affine,
        "affineError": affine_error,
        "affineMode": affine_mode,
        "affineMetric": aff_metric,
        "deformableMetric": bspline_metric,
        "usedDeformable": used_deformable,
        "deformableMesh": [int(x) for x in deformable_mesh],
        "deformableLearningRate": float(deformable_learning_rate),
        "deformableIterations": int(deformable_iterations),
        "deformableSampling": float(deformable_sampling),
        "deformableSmoothingSigmas": [float(x) for x in deformable_smoothing_sigmas],
        "registrationSpacingMm": float(registration_spacing_mm),
    }
    return RegistrationResult(transform=final_tx, summary=summary)


def _segment_outer_layers(
    mri_in_ccf: sitk.Image,
    brain_mask: sitk.Image,
) -> Tuple[sitk.Image, sitk.Image, sitk.Image]:
    norm = _normalize_percentile(mri_in_ccf)
    head = sitk.OtsuThreshold(norm, 0, 1, 200)
    head = _largest_component(head)
    head = _binary_close_fill(head, radius=2)

    brain = sitk.Cast(brain_mask > 0, sitk.sitkUInt8)
    outer = sitk.Cast((head > 0) & (brain == 0), sitk.sitkUInt8)
    outer = _largest_component(outer) if int(sitk.GetArrayViewFromImage(outer).sum()) > 0 else outer
    outer = _binary_close_fill(outer, radius=1)

    eroded = sitk.BinaryErode(head, [2, 2, 2])
    shell = sitk.Cast((head > 0) & (eroded == 0), sitk.sitkUInt8)

    arr = sitk.GetArrayFromImage(norm).astype(np.float32)
    shell_arr = sitk.GetArrayFromImage(shell) > 0
    brain_arr = sitk.GetArrayFromImage(brain) > 0
    skull_arr = np.zeros_like(shell_arr, dtype=bool)
    if np.any(shell_arr):
        vals = arr[shell_arr]
        thr = float(np.quantile(vals, 0.35))
        skull_arr = shell_arr & (arr <= thr) & (~brain_arr)
    if int(skull_arr.sum()) < 1000:
        skull_arr = shell_arr & (~brain_arr)

    skull = sitk.GetImageFromArray(skull_arr.astype(np.uint8))
    skull.CopyInformation(head)
    skull = _binary_close_fill(skull, radius=1)

    # Fallback if outer becomes too thin after morphology.
    if int(sitk.GetArrayViewFromImage(outer).sum()) < 1000:
        outer = sitk.Cast((sitk.BinaryDilate(brain, [5, 5, 5]) > 0) & (brain == 0), sitk.sitkUInt8)

    return head, skull, outer


def _must_exist(path: Path, name: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Missing {name}: {path}")
    return path


def _parse_int_triplet(text: str) -> Tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated integers, got {text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _parse_float_levels(text: str) -> Tuple[float, ...]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated smoothing levels, got {text!r}")
    return tuple(float(p) for p in parts)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    default_ambmc = mri_path("raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii")
    default_ccf_template = mri_path("raw/allen_ccf/average_template_25.nrrd")
    default_ccf_annotation = mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")
    default_label_npy = project_root / ".probeplanner" / "mri_ccf_hybrid_seed" / "annotation.npy"
    default_region_map = project_root / ".probeplanner" / "mri_ccf_hybrid_seed" / "region_map.json"

    ap = argparse.ArgumentParser(description="AMBMC -> CCF registration and true outer-layer mask generation.")
    ap.add_argument("--project-root", default=str(project_root))
    ap.add_argument("--ambmc-image", default=str(default_ambmc))
    ap.add_argument("--ccf-template", default=str(default_ccf_template))
    ap.add_argument("--ccf-annotation", default=str(default_ccf_annotation))
    ap.add_argument("--label-npy", default=str(default_label_npy))
    ap.add_argument("--region-map-json", default=str(default_region_map))
    ap.add_argument("--output-dir", default=str(mri_path("planner_artifacts/ambmc_true_outer")))
    ap.add_argument("--dataset-profile", default="ambmc_true_outer")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--skip-deformable", action="store_true")
    ap.add_argument("--deformable-mesh", default="8,8,8", help="BSpline mesh size; lower values reduce folding risk.")
    ap.add_argument("--deformable-learning-rate", type=float, default=0.5)
    ap.add_argument("--deformable-iterations", type=int, default=180)
    ap.add_argument("--deformable-sampling", type=float, default=0.20)
    ap.add_argument("--deformable-smoothing-sigmas", default="2,1,0")
    ap.add_argument("--registration-spacing-mm", type=float, default=0.10)
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    out_dir = Path(args.output_dir).resolve()
    reg_dir = out_dir / "registration"
    reg_dir.mkdir(parents=True, exist_ok=True)

    ambmc_image = _must_exist(Path(args.ambmc_image).resolve(), "ambmc-image")
    ccf_template = _must_exist(Path(args.ccf_template).resolve(), "ccf-template")
    ccf_annotation = _must_exist(Path(args.ccf_annotation).resolve(), "ccf-annotation")
    label_npy = _must_exist(Path(args.label_npy).resolve(), "label-npy")
    region_map = _must_exist(Path(args.region_map_json).resolve(), "region-map-json")

    fixed = sitk.Cast(sitk.ReadImage(str(ccf_template)), sitk.sitkFloat32)
    moving = sitk.Cast(sitk.ReadImage(str(ambmc_image)), sitk.sitkFloat32)
    ann = sitk.Cast(sitk.ReadImage(str(ccf_annotation)), sitk.sitkUInt32)

    fixed = _canonical_index_space(_normalize_spacing_units_mm(fixed))
    moving = _canonical_index_space(_normalize_spacing_units_mm(moving))
    ann = _canonical_index_space(_normalize_spacing_units_mm(ann))

    ann = sitk.Resample(ann, fixed, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt32)
    brain_mask = sitk.Cast(ann > 0, sitk.sitkUInt8)
    moving_mask_guess = _largest_component(sitk.OtsuThreshold(_normalize_percentile(moving), 0, 1, 200))
    moving_mask_guess = _binary_close_fill(moving_mask_guess, radius=2)

    reg_result = _register_ambmc_to_ccf(
        fixed,
        moving,
        brain_mask,
        moving_mask_guess,
        quick=args.quick,
        skip_deformable=args.skip_deformable,
        deformable_mesh=_parse_int_triplet(args.deformable_mesh),
        deformable_learning_rate=float(args.deformable_learning_rate),
        deformable_iterations=int(args.deformable_iterations),
        deformable_sampling=float(args.deformable_sampling),
        deformable_smoothing_sigmas=_parse_float_levels(args.deformable_smoothing_sigmas),
        registration_spacing_mm=float(args.registration_spacing_mm),
    )
    mri_in_ccf = sitk.Resample(moving, fixed, reg_result.transform, sitk.sitkLinear, 0.0, sitk.sitkFloat32)

    head_mask, skull_mask, outer_mask = _segment_outer_layers(mri_in_ccf, brain_mask)

    mri_ccf_path = reg_dir / "ambmc_in_ccf.nii.gz"
    warp_field_path = reg_dir / "ambmc_to_ccf_displacement.nii.gz"
    head_path = reg_dir / "head_mask.nii.gz"
    skull_path = reg_dir / "skull_mask.nii.gz"
    outer_path = reg_dir / "outer_tissue_mask.nii.gz"
    skull_npy_path = reg_dir / "skull_mask.npy"
    outer_npy_path = reg_dir / "outer_tissue_mask.npy"
    sitk.WriteImage(mri_in_ccf, str(mri_ccf_path))
    sitk.WriteImage(head_mask, str(head_path))
    sitk.WriteImage(skull_mask, str(skull_path))
    sitk.WriteImage(outer_mask, str(outer_path))
    warp_field_written = False
    if bool(reg_result.summary.get("usedDeformable")):
        try:
            displacement = sitk.TransformToDisplacementField(
                reg_result.transform,
                sitk.sitkVectorFloat32,
                fixed.GetSize(),
                fixed.GetOrigin(),
                fixed.GetSpacing(),
                fixed.GetDirection(),
            )
            sitk.WriteImage(displacement, str(warp_field_path))
            warp_field_written = True
        except Exception as ex:
            reg_result.summary["warpFieldError"] = str(ex)
    try:
        sitk.WriteTransform(reg_result.transform, str(reg_dir / "ambmc_to_ccf.tfm"))
    except Exception:
        # Composite transform may not always serialize in all SITK builds.
        pass

    # Convert z,y,x -> x,y,z for backend array convention (528,320,456).
    skull_arr = sitk.GetArrayFromImage(skull_mask).astype(np.uint8)
    outer_arr = sitk.GetArrayFromImage(outer_mask).astype(np.uint8)
    np.save(skull_npy_path, np.transpose(skull_arr, (2, 1, 0)))
    np.save(outer_npy_path, np.transpose(outer_arr, (2, 1, 0)))

    summary = {
        "ambmcImage": str(ambmc_image),
        "ccfTemplate": str(ccf_template),
        "ccfAnnotation": str(ccf_annotation),
        "quickMode": bool(args.quick),
        "skipDeformable": bool(args.skip_deformable),
        "registration": reg_result.summary,
        "warpFieldPath": str(warp_field_path) if warp_field_written else None,
        "voxelCountHead": int(sitk.GetArrayViewFromImage(head_mask).sum()),
        "voxelCountSkull": int(sitk.GetArrayViewFromImage(skull_mask).sum()),
        "voxelCountOuterTissue": int(sitk.GetArrayViewFromImage(outer_mask).sum()),
    }
    (reg_dir / "registration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    build_script = project_root / "scripts" / "build_mri_ccf_hybrid_artifacts.py"
    validate_script = project_root / "scripts" / "validate_mri_ccf_inputs.py"

    build_cmd: List[str] = [
        sys.executable,
        str(build_script),
        "--label-image",
        str(label_npy),
        "--region-map-json",
        str(region_map),
        "--output-dir",
        str(out_dir),
        "--expected-shape",
        "528,320,456",
        "--target-spacing-um",
        "25,25,25",
        "--dataset-profile",
        str(args.dataset_profile),
        "--skull-mask-image",
        str(skull_npy_path),
        "--outer-tissue-mask-image",
        str(outer_npy_path),
    ]
    if warp_field_written:
        build_cmd.extend(["--warp-field", str(warp_field_path)])
    subprocess.run(build_cmd, check=True)

    validate_cmd: List[str] = [
        sys.executable,
        str(validate_script),
        "--annotation",
        str(out_dir / "annotation.npy"),
        "--region-map",
        str(out_dir / "region_map.json"),
        "--stats",
        str(out_dir / "stats.json"),
        "--skull-mask",
        str(out_dir / "skull_mask.npy"),
        "--outer-tissue-mask",
        str(out_dir / "outer_tissue_mask.npy"),
        "--layered-sdf",
        str(out_dir / "layered_sdf.npz"),
        "--expected-shape",
        "528,320,456",
    ]
    if (out_dir / "jacobian.npy").exists():
        validate_cmd.extend(["--jacobian", str(out_dir / "jacobian.npy")])
    subprocess.run(validate_cmd, check=True)

    out_summary = {
        "ok": True,
        "outputDir": str(out_dir),
        "registrationDir": str(reg_dir),
        "ambmcInCcfPath": str(mri_ccf_path),
        "warpFieldPath": str(warp_field_path) if warp_field_written else "",
        "headMaskPath": str(head_path),
        "skullMaskPath": str(out_dir / "skull_mask.npy"),
        "outerTissueMaskPath": str(out_dir / "outer_tissue_mask.npy"),
        "skullMaskRawPath": str(skull_npy_path),
        "outerTissueMaskRawPath": str(outer_npy_path),
        "layeredSdfPath": str(out_dir / "layered_sdf.npz"),
        "statsPath": str(out_dir / "stats.json"),
        "jacobianPath": str(out_dir / "jacobian.npy") if (out_dir / "jacobian.npy").exists() else "",
        "registrationSummaryPath": str(reg_dir / "registration_summary.json"),
    }
    print(json.dumps(out_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
