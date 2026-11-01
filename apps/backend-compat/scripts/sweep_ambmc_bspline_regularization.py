#!/usr/bin/env python
"""Coarse QC sweep for AMBMC->CCF BSpline regularization.

This script is intentionally cheap: it does not build MRI artifacts or SDFs.
It only evaluates registration candidates on a coarse grid and reports:
mask Dice, HD95, and Jacobian determinant anomaly ratio.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Tuple

import numpy as np
import SimpleITK as sitk

from build_ambmc_true_outer_pipeline import (
    _binary_close_fill,
    _canonical_index_space,
    _largest_component,
    _normalize_percentile,
    _normalize_spacing_units_mm,
    _register_ambmc_to_ccf,
    _resample_to_spacing,
)


def _parse_triplet(text: str, cast=float) -> Tuple:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated values, got {text!r}")
    return tuple(cast(p) for p in parts)


def _dice(a: sitk.Image, b: sitk.Image) -> float:
    aa = sitk.GetArrayViewFromImage(a) > 0
    bb = sitk.GetArrayViewFromImage(b) > 0
    denom = int(np.count_nonzero(aa) + np.count_nonzero(bb))
    if denom <= 0:
        return 1.0
    return float(2.0 * np.count_nonzero(aa & bb) / denom)


def _hd95_mm(a: sitk.Image, b: sitk.Image) -> float | None:
    from scipy.ndimage import distance_transform_edt

    aa = sitk.GetArrayViewFromImage(a) > 0
    bb = sitk.GetArrayViewFromImage(b) > 0
    if np.count_nonzero(aa) == 0 or np.count_nonzero(bb) == 0:
        return None
    spacing = float(np.mean(a.GetSpacing()))
    da = distance_transform_edt(~bb) * spacing
    db = distance_transform_edt(~aa) * spacing
    sa = aa & (distance_transform_edt(aa) <= 1.1)
    sb = bb & (distance_transform_edt(bb) <= 1.1)
    vals = np.concatenate([da[sa], db[sb]])
    if vals.size == 0:
        return None
    return float(np.percentile(vals, 95.0))


def _jacobian_summary(transform: sitk.Transform, fixed: sitk.Image) -> dict:
    disp = sitk.TransformToDisplacementField(
        transform,
        sitk.sitkVectorFloat32,
        fixed.GetSize(),
        fixed.GetOrigin(),
        fixed.GetSpacing(),
        fixed.GetDirection(),
    )
    jac = sitk.DisplacementFieldJacobianDeterminant(disp)
    arr = sitk.GetArrayViewFromImage(jac).astype(np.float32)
    vals = arr[np.isfinite(arr)]
    bad = (vals <= 0.0) | (vals < 0.2) | (vals > 5.0)
    return {
        "jacobianValueType": "determinant_not_log",
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "fractionLt0": float(np.mean(vals < 0.0)),
        "fractionLt02": float(np.mean(vals < 0.2)),
        "fractionGt5": float(np.mean(vals > 5.0)),
        "jacobianAnomalyRatio": float(np.mean(bad)),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--ambmc-image", default=str(mri_path("raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii")))
    ap.add_argument("--ccf-template", default=str(mri_path("raw/allen_ccf/average_template_25.nrrd")))
    ap.add_argument("--ccf-annotation", default=str(mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")))
    ap.add_argument("--output-json", default=str(mri_path("planner_artifacts/bspline_regularization_sweep.json")))
    ap.add_argument("--spacing-mm", type=float, default=0.20)
    ap.add_argument(
        "--candidates",
        default=(
            "4,4,4|0.20|80|0.08|4,2,1;"
            "4,4,4|0.25|120|0.12|3,2,1;"
            "5,5,5|0.25|120|0.12|3,2,1;"
            "6,6,6|0.30|140|0.15|3,2,1"
        ),
        help="Semicolon-separated mesh|lr|iters|sampling|sigmas candidate list.",
    )
    args = ap.parse_args()

    fixed = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(args.ccf_template), sitk.sitkFloat32)))
    moving = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(args.ambmc_image), sitk.sitkFloat32)))
    ann = _canonical_index_space(_normalize_spacing_units_mm(sitk.Cast(sitk.ReadImage(args.ccf_annotation), sitk.sitkUInt32)))
    ann = sitk.Resample(ann, fixed, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt32)
    fixed_mask = _binary_close_fill(sitk.Cast(ann > 0, sitk.sitkUInt8), radius=1)
    moving_mask = _binary_close_fill(
        _largest_component(sitk.OtsuThreshold(_normalize_percentile(moving), 0, 1, 200)),
        radius=2,
    )

    spacing = (float(args.spacing_mm),) * 3
    fixed_qc = _resample_to_spacing(fixed, spacing, is_label=False)
    moving_qc = _resample_to_spacing(moving, spacing, is_label=False)
    fixed_mask_qc = sitk.Cast(_resample_to_spacing(fixed_mask, spacing, is_label=True) > 0, sitk.sitkUInt8)
    moving_mask_qc = sitk.Cast(_resample_to_spacing(moving_mask, spacing, is_label=True) > 0, sitk.sitkUInt8)

    results = []
    for raw in [x.strip() for x in str(args.candidates).split(";") if x.strip()]:
        mesh_s, lr_s, iter_s, sampling_s, sigmas_s = [p.strip() for p in raw.split("|")]
        start = time.time()
        try:
            reg = _register_ambmc_to_ccf(
                fixed_qc,
                moving_qc,
                fixed_mask_qc,
                moving_mask_qc,
                quick=False,
                skip_deformable=False,
                deformable_mesh=_parse_triplet(mesh_s, int),
                deformable_learning_rate=float(lr_s),
                deformable_iterations=int(iter_s),
                deformable_sampling=float(sampling_s),
                deformable_smoothing_sigmas=_parse_triplet(sigmas_s, float),
                registration_spacing_mm=float(args.spacing_mm),
            )
            moved_mask = sitk.Resample(moving_mask_qc, fixed_mask_qc, reg.transform, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
            jac = _jacobian_summary(reg.transform, fixed_qc)
            rec = {
                "ok": bool(jac["jacobianAnomalyRatio"] <= 0.03),
                "candidate": raw,
                "mesh": list(_parse_triplet(mesh_s, int)),
                "learningRate": float(lr_s),
                "iterations": int(iter_s),
                "sampling": float(sampling_s),
                "smoothingSigmas": list(_parse_triplet(sigmas_s, float)),
                "dice": _dice(fixed_mask_qc, moved_mask),
                "hd95Mm": _hd95_mm(moved_mask, fixed_mask_qc),
                "jacobian": jac,
                "registration": reg.summary,
                "elapsedSec": round(time.time() - start, 3),
            }
        except Exception as ex:
            rec = {"ok": False, "candidate": raw, "error": str(ex), "elapsedSec": round(time.time() - start, 3)}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    out = {
        "spacingMm": float(args.spacing_mm),
        "targetJacobianAnomalyRatio": 0.03,
        "results": results,
    }
    out_path = Path(args.output_json).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    best = sorted(
        [r for r in results if r.get("ok") and "dice" in r],
        key=lambda r: (float(r.get("dice", 0.0)), -float(r.get("jacobian", {}).get("jacobianAnomalyRatio", 1.0))),
        reverse=True,
    )
    print(json.dumps({"ok": True, "outputJson": str(out_path), "best": best[:3]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
