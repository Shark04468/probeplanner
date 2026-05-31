#!/usr/bin/env python
"""Prepare ANTs/SyN-ready MRI and CCF inputs.

This script exists to avoid the common registration pitfalls in this project:

* Allen CCF NRRD spacing may be stored as 25/50/100 while the unit is um.
  ANTs expects millimetres for these mouse templates, so values > 1 are
  converted from um to mm.
* CCF and AMBMC images can carry incompatible origins/directions.  The planner
  uses atlas index space, so by default we write direction/origin-neutral copies.
* Fixed and moving masks are generated and validated before SyN is run.
* All outputs and geometry decisions are recorded in metadata.json.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Iterable, Tuple

import numpy as np
import SimpleITK as sitk


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spacing_to_mm(spacing: Iterable[float]) -> Tuple[float, float, float]:
    vals = tuple(float(v) for v in spacing)
    if max(vals) > 1.0:
        return tuple(v / 1000.0 for v in vals)
    return vals


def _canonical_index_space(img: sitk.Image, *, force: bool) -> sitk.Image:
    out = sitk.Image(img)
    out.SetSpacing(_spacing_to_mm(out.GetSpacing()))
    if not force:
        return out
    arr = sitk.GetArrayFromImage(out)
    canon = sitk.GetImageFromArray(arr)
    canon.SetSpacing(_spacing_to_mm(out.GetSpacing()))
    canon.SetOrigin((0.0, 0.0, 0.0))
    canon.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    return canon


def _normalize_percentile(img: sitk.Image, p_low: float = 1.0, p_high: float = 99.0) -> sitk.Image:
    arr = sitk.GetArrayViewFromImage(img).astype(np.float32)
    lo = float(np.percentile(arr, p_low))
    hi = float(np.percentile(arr, p_high))
    cast = sitk.Cast(img, sitk.sitkFloat32)
    if hi <= lo + 1e-6:
        return cast
    return sitk.Clamp((cast - lo) / (hi - lo), sitk.sitkFloat32, 0.0, 1.0)


def _largest_component(mask: sitk.Image) -> sitk.Image:
    cc = sitk.ConnectedComponent(sitk.Cast(mask > 0, sitk.sitkUInt8))
    rel = sitk.RelabelComponent(cc, sortByObjectSize=True)
    return sitk.Cast(rel == 1, sitk.sitkUInt8)


def _binary_close_fill(mask: sitk.Image, radius: int = 2) -> sitk.Image:
    out = sitk.BinaryMorphologicalClosing(sitk.Cast(mask > 0, sitk.sitkUInt8), [radius, radius, radius])
    out = sitk.BinaryFillhole(out)
    return sitk.Cast(out > 0, sitk.sitkUInt8)


def _resample_to_reference(img: sitk.Image, reference: sitk.Image, *, is_label: bool, default_value=0) -> sitk.Image:
    return sitk.Resample(
        img,
        reference,
        sitk.Transform(),
        sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear,
        default_value,
        img.GetPixelID(),
    )


def _image_info(img: sitk.Image) -> dict:
    return {
        "size": [int(x) for x in img.GetSize()],
        "spacingMm": [float(x) for x in img.GetSpacing()],
        "origin": [float(x) for x in img.GetOrigin()],
        "direction": [float(x) for x in img.GetDirection()],
    }


def _mask_count(mask: sitk.Image) -> int:
    return int(np.asarray(sitk.GetArrayViewFromImage(mask) > 0).sum())


def main() -> int:
    default_fixed = mri_path("raw/allen_ccf/average_template_25.nrrd")
    default_ann = mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")
    default_moving = mri_path(
        "raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/"
        "ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii"
    )
    ap = argparse.ArgumentParser(description="Prepare ANTs/SyN fixed/moving images and masks.")
    ap.add_argument("--fixed-image", default=str(default_fixed), help="CCF template image.")
    ap.add_argument("--fixed-annotation", default=str(default_ann), help="CCF annotation used to build fixed mask.")
    ap.add_argument("--moving-image", default=str(default_moving), help="MRI/AMBMC moving image.")
    ap.add_argument("--moving-mask", default="", help="Optional moving brain/head mask.")
    ap.add_argument("--output-dir", default=str(mri_path("ants_syn/ambmc_to_ccf/prepared")))
    ap.add_argument("--keep-geometry", action="store_true", help="Keep original origins/directions instead of index-space canonicalization.")
    ap.add_argument("--n4", action="store_true", help="Run N4 bias correction on moving image before writing.")
    args = ap.parse_args()

    fixed_path = Path(args.fixed_image).expanduser().resolve()
    ann_path = Path(args.fixed_annotation).expanduser().resolve()
    moving_path = Path(args.moving_image).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for p, label in [(fixed_path, "fixed-image"), (ann_path, "fixed-annotation"), (moving_path, "moving-image")]:
        if not p.exists():
            raise FileNotFoundError(f"Missing {label}: {p}")

    canonical = not bool(args.keep_geometry)
    fixed_raw = sitk.Cast(sitk.ReadImage(str(fixed_path)), sitk.sitkFloat32)
    ann_raw = sitk.Cast(sitk.ReadImage(str(ann_path)), sitk.sitkUInt32)
    moving_raw = sitk.Cast(sitk.ReadImage(str(moving_path)), sitk.sitkFloat32)

    fixed = _canonical_index_space(fixed_raw, force=canonical)
    ann = _canonical_index_space(ann_raw, force=canonical)
    moving = _canonical_index_space(moving_raw, force=canonical)
    ann = _resample_to_reference(ann, fixed, is_label=True, default_value=0)
    fixed_mask = sitk.Cast(ann > 0, sitk.sitkUInt8)
    fixed_mask = _binary_close_fill(fixed_mask, radius=1)

    moving_norm = _normalize_percentile(moving)
    if args.n4:
        try:
            rough_mask = _largest_component(sitk.OtsuThreshold(moving_norm, 0, 1, 200))
            moving_norm = sitk.N4BiasFieldCorrection(moving_norm, rough_mask)
            moving_norm = _normalize_percentile(moving_norm)
        except Exception as ex:
            raise RuntimeError(f"N4 failed; rerun without --n4 or inspect moving image: {ex}") from ex

    if args.moving_mask:
        moving_mask_raw = sitk.ReadImage(str(Path(args.moving_mask).expanduser().resolve()))
        moving_mask = _canonical_index_space(sitk.Cast(moving_mask_raw, sitk.sitkUInt8), force=canonical)
        moving_mask = _resample_to_reference(moving_mask, moving, is_label=True, default_value=0)
    else:
        moving_mask = _largest_component(sitk.OtsuThreshold(moving_norm, 0, 1, 200))
        moving_mask = _binary_close_fill(moving_mask, radius=2)

    fixed_count = _mask_count(fixed_mask)
    moving_count = _mask_count(moving_mask)
    if fixed_count < 1000:
        raise RuntimeError(f"Fixed mask is too small ({fixed_count} voxels). Check CCF annotation and spacing.")
    if moving_count < 1000:
        raise RuntimeError(f"Moving mask is too small ({moving_count} voxels). Check MRI image or provide --moving-mask.")

    fixed_out = out_dir / "fixed_ccf_template_mm.nii.gz"
    moving_out = out_dir / "moving_mri_mm.nii.gz"
    fixed_mask_out = out_dir / "fixed_brain_mask.nii.gz"
    moving_mask_out = out_dir / "moving_brain_mask.nii.gz"
    sitk.WriteImage(_normalize_percentile(fixed), str(fixed_out))
    sitk.WriteImage(moving_norm, str(moving_out))
    sitk.WriteImage(fixed_mask, str(fixed_mask_out))
    sitk.WriteImage(sitk.Cast(moving_mask > 0, sitk.sitkUInt8), str(moving_mask_out))

    metadata = {
        "createdAt": _utc_now_iso(),
        "canonicalIndexSpace": canonical,
        "registrationDirection": {
            "fixed": "CCF template",
            "moving": "AMBMC MRI",
            "intendedTransform": "AMBMC MRI -> CCF",
        },
        "interpolationPolicy": {
            "intensityImages": "Linear",
            "annotationAndMasks": "NearestNeighbor",
        },
        "spacingPitfallHandled": {
            "fixedOriginalSpacing": [float(x) for x in fixed_raw.GetSpacing()],
            "movingOriginalSpacing": [float(x) for x in moving_raw.GetSpacing()],
            "rule": "spacing > 1.0 is treated as um and converted to mm",
        },
        "inputs": {
            "fixedImage": str(fixed_path),
            "fixedAnnotation": str(ann_path),
            "movingImage": str(moving_path),
            "movingMask": str(Path(args.moving_mask).expanduser().resolve()) if args.moving_mask else "",
        },
        "outputs": {
            "fixedImage": str(fixed_out),
            "movingImage": str(moving_out),
            "fixedMask": str(fixed_mask_out),
            "movingMask": str(moving_mask_out),
        },
        "geometry": {
            "fixed": _image_info(fixed),
            "moving": _image_info(moving),
        },
        "maskVoxelCounts": {
            "fixed": fixed_count,
            "moving": moving_count,
        },
        "maskVoxelFractions": {
            "fixed": float(fixed_count / max(1, np.prod(fixed.GetSize()))),
            "moving": float(moving_count / max(1, np.prod(moving.GetSize()))),
        },
        "warnings": [
            "If fixed/moving orientation is intentionally physical-space based, rerun with --keep-geometry.",
            "Do not run downstream profile activation until Jacobian std is non-zero and quality metrics are filled.",
        ],
    }
    meta_path = out_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
