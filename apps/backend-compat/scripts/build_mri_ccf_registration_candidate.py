from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk
from scipy.ndimage import distance_transform_edt

from build_ambmc_true_outer_pipeline import (
    _binary_close_fill,
    _canonical_index_space,
    _largest_component,
    _normalize_percentile,
    _normalize_spacing_units_mm,
    _register_ambmc_to_ccf,
)
from path_defaults import mri_path


def _parse_int_triplet(text: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated integers, got {text!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _parse_float_levels(text: str) -> tuple[float, ...]:
    return tuple(float(p.strip()) for p in str(text).split(",") if p.strip())


def _must_exist(path: Path, name: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Missing {name}: {path}")
    return path


def _to_xyz_array(img: sitk.Image, dtype: Any = np.uint8) -> np.ndarray:
    arr_zyx = sitk.GetArrayFromImage(img).astype(dtype, copy=False)
    return np.transpose(arr_zyx, (2, 1, 0))


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a) > 0
    bb = np.asarray(b) > 0
    denom = int(aa.sum()) + int(bb.sum())
    if denom == 0:
        return 1.0
    return float(2.0 * np.logical_and(aa, bb).sum() / denom)


def _hd95_mm(a: np.ndarray, b: np.ndarray, voxel_mm: float) -> float | None:
    aa = np.asarray(a) > 0
    bb = np.asarray(b) > 0
    if int(aa.sum()) == 0 or int(bb.sum()) == 0:
        return None
    da = distance_transform_edt(~bb) * voxel_mm
    db = distance_transform_edt(~aa) * voxel_mm
    sa = aa & (distance_transform_edt(aa) <= 1.1)
    sb = bb & (distance_transform_edt(bb) <= 1.1)
    vals = np.concatenate([da[sa], db[sb]])
    if vals.size == 0:
        return None
    return float(np.percentile(vals, 95.0))


def _intensity_summary(img: sitk.Image) -> dict[str, float]:
    arr = sitk.GetArrayViewFromImage(img).astype(np.float32)
    vals = arr[np.isfinite(arr)]
    return {
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "p99": float(np.percentile(vals, 99.0)),
        "p999": float(np.percentile(vals, 99.9)),
    }


def main() -> int:
    default_ambmc = mri_path(
        "raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/"
        "ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii"
    )
    default_ccf_template = mri_path("raw/allen_ccf/average_template_25.nrrd")
    default_ccf_annotation = mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")

    ap = argparse.ArgumentParser(description="Build a registration-only AMBMC-to-CCF candidate.")
    ap.add_argument("--ambmc-image", default=str(default_ambmc))
    ap.add_argument("--ccf-template", default=str(default_ccf_template))
    ap.add_argument("--ccf-annotation", default=str(default_ccf_annotation))
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--dataset-profile", required=True)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--skip-deformable", action="store_true")
    ap.add_argument("--deformable-mesh", default="4,4,4")
    ap.add_argument("--deformable-learning-rate", type=float, default=0.08)
    ap.add_argument("--deformable-iterations", type=int, default=80)
    ap.add_argument("--deformable-sampling", type=float, default=0.08)
    ap.add_argument("--deformable-smoothing-sigmas", default="5,3,2")
    ap.add_argument("--registration-spacing-mm", type=float, default=0.20)
    args = ap.parse_args()

    out_dir = Path(args.output_dir).resolve()
    reg_dir = out_dir / "registration"
    qc_dir = out_dir / "quality_eval"
    reg_dir.mkdir(parents=True, exist_ok=True)
    qc_dir.mkdir(parents=True, exist_ok=True)

    ambmc_image = _must_exist(Path(args.ambmc_image).resolve(), "ambmc-image")
    ccf_template = _must_exist(Path(args.ccf_template).resolve(), "ccf-template")
    ccf_annotation = _must_exist(Path(args.ccf_annotation).resolve(), "ccf-annotation")

    fixed = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(str(ccf_template)), sitk.sitkFloat32)))
    moving = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(str(ambmc_image)), sitk.sitkFloat32)))
    ann = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(str(ccf_annotation)), sitk.sitkUInt32)))
    ann = sitk.Resample(ann, fixed, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt32)

    fixed_brain_mask = sitk.Cast(ann > 0, sitk.sitkUInt8)
    moving_brain_mask = _binary_close_fill(
        _largest_component(sitk.OtsuThreshold(_normalize_percentile(moving), 0, 1, 200)),
        radius=2,
    )

    reg_result = _register_ambmc_to_ccf(
        fixed,
        moving,
        fixed_brain_mask,
        moving_brain_mask,
        quick=bool(args.quick),
        skip_deformable=bool(args.skip_deformable),
        deformable_mesh=_parse_int_triplet(args.deformable_mesh),
        deformable_learning_rate=float(args.deformable_learning_rate),
        deformable_iterations=int(args.deformable_iterations),
        deformable_sampling=float(args.deformable_sampling),
        deformable_smoothing_sigmas=_parse_float_levels(args.deformable_smoothing_sigmas),
        registration_spacing_mm=float(args.registration_spacing_mm),
    )

    mri_in_ccf = sitk.Resample(moving, fixed, reg_result.transform, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    moved_brain_mask = sitk.Resample(moving_brain_mask, fixed_brain_mask, reg_result.transform, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)

    sitk.WriteImage(mri_in_ccf, str(reg_dir / "ambmc_in_ccf.nii.gz"))
    try:
        sitk.WriteTransform(reg_result.transform, str(reg_dir / "ambmc_to_ccf.tfm"))
    except Exception as ex:
        reg_result.summary["transformWriteError"] = str(ex)

    gt_mask = _to_xyz_array(fixed_brain_mask, np.uint8)
    pred_mask = _to_xyz_array(moved_brain_mask, np.uint8)
    np.save(qc_dir / "gt_mask.npy", gt_mask)
    np.save(qc_dir / "pred_mask.npy", pred_mask)

    metrics = {
        "dice": _dice(pred_mask, gt_mask),
        "hd95Mm": _hd95_mm(pred_mask, gt_mask, voxel_mm=0.025),
        "treMm": None,
        "jacobianAnomalyRatio": None,
    }
    stats = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": "build_mri_ccf_registration_candidate",
        "datasetProfile": str(args.dataset_profile),
        "annotationShape": list(gt_mask.shape),
        "voxelSpacingMm": [0.025, 0.025, 0.025],
        "metrics": metrics,
        "registration": reg_result.summary,
        "intensity": _intensity_summary(mri_in_ccf),
        "paths": {
            "mriInCcf": str(reg_dir / "ambmc_in_ccf.nii.gz"),
            "gtMask": str(qc_dir / "gt_mask.npy"),
            "predMask": str(qc_dir / "pred_mask.npy"),
        },
    }
    (reg_dir / "registration_summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "outputDir": str(out_dir), "metrics": metrics, "intensity": stats["intensity"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
