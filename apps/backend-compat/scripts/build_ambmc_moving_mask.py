#!/usr/bin/env python
"""Build a cleaner AMBMC moving brain mask for native ANTs/SyN.

The default Otsu mask in prepare_ants_syn_inputs.py is intentionally simple.
For AMBMC it can include elongated background/edge artifacts, which then pull
SyN and inflate the Jacobian anomaly ratio.  This script tries several
threshold/morphology candidates, scores them by plausible mouse-brain geometry,
and writes the best mask plus a QC report.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Iterable

import numpy as np
import SimpleITK as sitk

try:
    from scipy import ndimage as ndi  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    ndi = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spacing_to_mm(spacing: Iterable[float]) -> tuple[float, float, float]:
    vals = tuple(float(v) for v in spacing)
    if max(vals) > 1.0:
        return tuple(v / 1000.0 for v in vals)
    return vals


def _normalize(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.float32, copy=False)
    finite = np.isfinite(a)
    if not np.any(finite):
        return np.zeros_like(a, dtype=np.float32)
    vals = a[finite]
    lo, hi = np.percentile(vals, [1.0, 99.5])
    if hi <= lo + 1e-6:
        return np.zeros_like(a, dtype=np.float32)
    out = (a - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0).astype(np.float32, copy=False)


def _sitk_like(mask_arr_zyx: np.ndarray, ref: sitk.Image) -> sitk.Image:
    img = sitk.GetImageFromArray(mask_arr_zyx.astype(np.uint8, copy=False))
    img.CopyInformation(ref)
    return img


def _largest_component(mask: sitk.Image) -> sitk.Image:
    cc = sitk.ConnectedComponent(sitk.Cast(mask > 0, sitk.sitkUInt8))
    rel = sitk.RelabelComponent(cc, sortByObjectSize=True)
    return sitk.Cast(rel == 1, sitk.sitkUInt8)


def _cleanup(mask: sitk.Image, radius: int) -> sitk.Image:
    out = sitk.Cast(mask > 0, sitk.sitkUInt8)
    if radius > 0:
        out = sitk.BinaryMorphologicalClosing(out, [radius, radius, radius])
        out = sitk.BinaryMorphologicalOpening(out, [max(1, radius // 2)] * 3)
    out = sitk.BinaryFillhole(out)
    out = _largest_component(out)
    out = sitk.BinaryFillhole(out)
    return sitk.Cast(out > 0, sitk.sitkUInt8)


def _bbox_metrics(mask_arr_zyx: np.ndarray, spacing_xyz: tuple[float, float, float]) -> dict:
    idx = np.argwhere(mask_arr_zyx > 0)
    total = int(mask_arr_zyx.size)
    count = int(idx.shape[0])
    if count == 0:
        return {
            "voxelCount": 0,
            "voxelFraction": 0.0,
            "bboxVoxZyx": None,
            "bboxMmZyx": None,
            "bboxVolumeMm3": 0.0,
            "maskVolumeMm3": 0.0,
            "fillRatio": 0.0,
            "centerDistanceNorm": 1e9,
        }
    mins = idx.min(axis=0)
    maxs = idx.max(axis=0)
    lengths_zyx_vox = maxs - mins + 1
    spacing_zyx = np.asarray(tuple(reversed(spacing_xyz)), dtype=np.float64)
    lengths_zyx_mm = lengths_zyx_vox.astype(np.float64) * spacing_zyx
    bbox_vol = float(np.prod(lengths_zyx_mm))
    voxel_vol = float(np.prod(spacing_xyz))
    mask_vol = float(count * voxel_vol)
    center = (mins + maxs) / 2.0
    img_center = (np.asarray(mask_arr_zyx.shape) - 1.0) / 2.0
    center_dist = float(np.linalg.norm((center - img_center) / np.maximum(1.0, np.asarray(mask_arr_zyx.shape))))
    return {
        "voxelCount": count,
        "voxelFraction": float(count / max(1, total)),
        "bboxVoxZyx": [[int(mins[i]), int(maxs[i])] for i in range(3)],
        "bboxMmZyx": [float(x) for x in lengths_zyx_mm],
        "bboxVolumeMm3": bbox_vol,
        "maskVolumeMm3": mask_vol,
        "fillRatio": float(mask_vol / max(1e-9, bbox_vol)),
        "centerDistanceNorm": center_dist,
    }


def _score(metrics: dict) -> float:
    if metrics["voxelCount"] <= 0:
        return 1e9
    lengths = np.asarray(metrics["bboxMmZyx"], dtype=np.float64)
    sorted_lengths = np.sort(lengths)
    frac = float(metrics["voxelFraction"])
    fill = float(metrics["fillRatio"])
    longest = float(sorted_lengths[-1])
    shortest = float(sorted_lengths[0])
    # A mouse brain mask should not occupy the full 19.7 mm padded AMBMC axis.
    score = 0.0
    # AMBMC has a large padded field of view.  A plausible brain mask is much
    # smaller than the FOV but should not collapse to only high-intensity cores.
    score += abs(frac - 0.16) * 8.0
    score += max(0.0, frac - 0.35) * 120.0
    score += max(0.0, 0.03 - frac) * 80.0
    score += max(0.0, longest - 16.5) * 2.5
    score += max(0.0, 5.0 - shortest) * 1.5
    score += max(0.0, 0.20 - fill) * 3.0
    score += metrics["centerDistanceNorm"] * 2.0
    return float(score)


def _largest_component_array(mask: np.ndarray) -> np.ndarray:
    if ndi is None:
        img = sitk.GetImageFromArray(mask.astype(np.uint8, copy=False))
        out = _largest_component(img)
        return sitk.GetArrayFromImage(out) > 0
    lab, n = ndi.label(mask)
    if n <= 0:
        return np.zeros_like(mask, dtype=bool)
    counts = np.bincount(lab.ravel())
    counts[0] = 0
    return lab == int(np.argmax(counts))


def _cleanup_array(mask: np.ndarray, radius: int) -> np.ndarray:
    mask = mask.astype(bool, copy=False)
    if ndi is None:
        img = sitk.GetImageFromArray(mask.astype(np.uint8, copy=False))
        return sitk.GetArrayFromImage(_cleanup(img, radius=radius)) > 0
    if radius > 0:
        struct = ndi.generate_binary_structure(3, 1)
        mask = ndi.binary_closing(mask, structure=struct, iterations=radius)
        mask = ndi.binary_opening(mask, structure=struct, iterations=max(1, radius // 2))
    mask = ndi.binary_fill_holes(mask)
    mask = _largest_component_array(mask)
    mask = ndi.binary_fill_holes(mask)
    return mask.astype(bool, copy=False)


def _bbox(mask: np.ndarray):
    idx = np.argwhere(mask > 0)
    if idx.size == 0:
        return None
    return idx.min(axis=0), idx.max(axis=0)


def _crop_slices_from_sample_bbox(mask: np.ndarray, stride: int, full_shape: tuple[int, int, int], margin: int):
    box = _bbox(mask)
    if box is None:
        return tuple(slice(0, s) for s in full_shape)
    mins, maxs = box
    lo = np.maximum(0, mins * stride - margin)
    hi = np.minimum(np.asarray(full_shape), (maxs + 1) * stride + margin)
    return tuple(slice(int(lo[i]), int(hi[i])) for i in range(3))


def main() -> int:
    default_moving = mri_path(
        "raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/"
        "ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii"
    )
    ap = argparse.ArgumentParser(description="Build clean AMBMC moving mask for ANTs/SyN.")
    ap.add_argument("--moving-image", default=default_moving)
    ap.add_argument("--output-mask", default=str(mri_path("ants_syn/ambmc_to_ccf/moving_masks/ambmc_moving_brain_mask_clean.nii.gz")))
    ap.add_argument("--qc-json", default=str(mri_path("ants_syn/ambmc_to_ccf/moving_masks/moving_mask_qc.json")))
    ap.add_argument("--radius", type=int, default=2)
    ap.add_argument("--sample-stride", type=int, default=4, help="Downsample stride for candidate scoring.")
    ap.add_argument("--roi-margin-vox", type=int, default=64, help="High-resolution ROI margin around the selected low-res mask.")
    args = ap.parse_args()

    moving_path = Path(args.moving_image).expanduser().resolve()
    out_mask = Path(args.output_mask).expanduser().resolve()
    qc_path = Path(args.qc_json).expanduser().resolve()
    if not moving_path.exists():
        raise FileNotFoundError(f"Moving image not found: {moving_path}")
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    qc_path.parent.mkdir(parents=True, exist_ok=True)

    moving = sitk.Cast(sitk.ReadImage(str(moving_path)), sitk.sitkFloat32)
    moving.SetSpacing(_spacing_to_mm(moving.GetSpacing()))
    arr = sitk.GetArrayFromImage(moving)
    spacing_xyz = tuple(float(x) for x in moving.GetSpacing())

    stride = max(1, int(args.sample_stride))
    sample_raw = arr[::stride, ::stride, ::stride]
    sample_norm = _normalize(sample_raw)
    full_lo = float(np.percentile(sample_raw[np.isfinite(sample_raw)], 1.0))
    full_hi = float(np.percentile(sample_raw[np.isfinite(sample_raw)], 99.5))
    if full_hi <= full_lo + 1e-6:
        raise RuntimeError("Moving image has near-constant intensity; cannot build a mask.")
    sample_spacing_xyz = tuple(float(x) * stride for x in spacing_xyz)
    vals = sample_norm[np.isfinite(sample_norm)]
    nonzero_vals = vals[vals > 1e-5]
    if nonzero_vals.size > 1000:
        vals = nonzero_vals

    rows = []
    best = None
    candidate_specs: list[tuple[str, str, float]] = []
    for q in (10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85):
        candidate_specs.append((f"p{q}_gt", "gt", float(np.percentile(vals, q))))
    for q in (10, 15, 20, 25, 30, 35, 40):
        candidate_specs.append((f"p{q}_lt", "lt", float(np.percentile(vals, q))))

    for name, op, thr in candidate_specs:
        for radius in (1, 2):
            try:
                raw = sample_norm >= thr if op == "gt" else sample_norm <= thr
                clean_arr = _cleanup_array(raw, radius=radius)
                metrics = _bbox_metrics(clean_arr, sample_spacing_xyz)
                row = {"name": name, "op": op, "thresholdNorm": thr, "cleanupRadius": int(radius), **metrics}
                row["score"] = _score(metrics)
                rows.append(row)
                if best is None or row["score"] < best[0]["score"]:
                    best = (row, clean_arr)
            except Exception as ex:
                rows.append({"name": name, "op": op, "thresholdNorm": thr, "cleanupRadius": int(radius), "error": str(ex), "score": 1e9})

    if best is None:
        raise RuntimeError("No valid moving mask candidate was generated.")
    best_row, best_sample_mask = best

    # Rebuild only the selected candidate at full resolution, inside an expanded
    # ROI.  This keeps memory/time bounded while preserving high-res boundaries.
    roi_slices = _crop_slices_from_sample_bbox(
        best_sample_mask,
        stride=stride,
        full_shape=tuple(int(x) for x in arr.shape),
        margin=int(args.roi_margin_vox),
    )
    roi = arr[roi_slices].astype(np.float32, copy=False)
    roi_norm = np.clip((roi - full_lo) / (full_hi - full_lo), 0.0, 1.0)
    thr = float(best_row["thresholdNorm"])
    op = str(best_row["op"])
    raw_roi = roi_norm >= thr if op == "gt" else roi_norm <= thr
    clean_roi = _cleanup_array(raw_roi, radius=int(best_row["cleanupRadius"]))
    full_mask = np.zeros(arr.shape, dtype=np.uint8)
    full_mask[roi_slices] = clean_roi.astype(np.uint8, copy=False)
    best_row["highResRoiZyx"] = [[int(s.start), int(s.stop)] for s in roi_slices]
    best_row["highResMetrics"] = _bbox_metrics(full_mask > 0, spacing_xyz)

    out_img = sitk.GetImageFromArray(full_mask)
    out_img.CopyInformation(moving)
    sitk.WriteImage(out_img, str(out_mask))

    qc = {
        "createdAt": _utc_now_iso(),
        "movingImage": str(moving_path),
        "outputMask": str(out_mask),
        "spacingMmXyz": [float(x) for x in spacing_xyz],
        "imageSizeXyz": [int(x) for x in moving.GetSize()],
        "selected": best_row,
        "candidates": sorted(rows, key=lambda x: float(x.get("score", 1e9)))[:20],
        "warnings": [
            "This is an automatic mask. If SyN Jacobian remains overfit, replace it with a manually curated AMBMC brain mask.",
            "Reject candidates whose longest bbox axis approaches the full padded AMBMC field of view.",
        ],
    }
    qc_path.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "selected": best_row, "outputMask": str(out_mask), "qcJson": str(qc_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
