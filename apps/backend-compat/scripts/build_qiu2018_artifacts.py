#!/usr/bin/env python
"""
Build Qiu2018 backend artifacts from a reconstructed mouse-brain volume.

Outputs:
- annotation.npy / annotation.npz   (required by backend)
- stats.json                        (optional but recommended)
- jacobian.npy / jacobian.npz       (optional, for quality gate)
- region_map.json                   (optional helper map)

This script is designed to match probe-planner backend expectations:
- annotation shape defaults to (528, 320, 456) in (z, y, x) NumPy order
- integer labels, with 0 = background
- stats keys compatible with src/optimizer_server.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import SimpleITK as sitk

try:
    from scipy.ndimage import binary_erosion, distance_transform_edt
except Exception:
    binary_erosion = None
    distance_transform_edt = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_triplet(text: str, cast=float) -> Tuple[float, float, float]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated values, got: {text!r}")
    return (cast(parts[0]), cast(parts[1]), cast(parts[2]))


def _parse_shape(text: str) -> Tuple[int, int, int]:
    z, y, x = _parse_triplet(text, cast=int)
    if z <= 0 or y <= 0 or x <= 0:
        raise ValueError(f"Shape must be positive, got: {(z, y, x)}")
    return (z, y, x)


def _load_image(path: Path) -> sitk.Image:
    if not path.exists():
        raise FileNotFoundError(f"Missing image file: {path}")
    return sitk.ReadImage(str(path))


def _load_label_image(
    path: Path,
    default_spacing_mm_xyz: Tuple[float, float, float],
) -> sitk.Image:
    suffix = path.suffix.lower()
    if suffix in {".npy", ".npz"}:
        if suffix == ".npy":
            arr = np.load(path, allow_pickle=False)
        else:
            pack = np.load(path, allow_pickle=False)
            key = "annotation" if "annotation" in pack.files else (pack.files[0] if pack.files else None)
            if not key:
                raise RuntimeError(f"NPZ has no arrays: {path}")
            arr = pack[key]
        vol = np.asarray(arr, dtype=np.uint32)
        if vol.ndim != 3:
            raise RuntimeError(f"Label npy/npz must be 3D, got shape {tuple(vol.shape)}")
        img = sitk.GetImageFromArray(vol)
        img.SetSpacing(tuple(float(v) for v in default_spacing_mm_xyz))
        img.SetOrigin((0.0, 0.0, 0.0))
        img.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
        return img
    return _load_image(path)


def _make_reference_from_shape(
    expected_shape_zyx: Tuple[int, int, int],
    spacing_mm_xyz: Tuple[float, float, float],
    direction: Sequence[float],
    origin: Sequence[float],
) -> sitk.Image:
    # SimpleITK image size is (x, y, z). NumPy array shape is (z, y, x).
    size_xyz = [int(expected_shape_zyx[2]), int(expected_shape_zyx[1]), int(expected_shape_zyx[0])]
    ref = sitk.Image(size_xyz, sitk.sitkUInt16)
    ref.SetSpacing(tuple(float(v) for v in spacing_mm_xyz))
    if len(direction) == 9:
        ref.SetDirection(tuple(float(v) for v in direction))
    else:
        ref.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    if len(origin) >= 3:
        ref.SetOrigin((float(origin[0]), float(origin[1]), float(origin[2])))
    else:
        ref.SetOrigin((0.0, 0.0, 0.0))
    return ref


def _resample_like(
    image: sitk.Image,
    reference: sitk.Image,
    is_label: bool,
    default_value: float = 0.0,
) -> sitk.Image:
    interp = sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear
    return sitk.Resample(
        image,
        reference,
        sitk.Transform(),
        interp,
        float(default_value),
        image.GetPixelID(),
    )


def _compute_dice(mask_a: np.ndarray, mask_b: np.ndarray) -> Optional[float]:
    a = np.asarray(mask_a, dtype=bool)
    b = np.asarray(mask_b, dtype=bool)
    if a.shape != b.shape:
        return None
    a_sum = int(a.sum())
    b_sum = int(b.sum())
    if a_sum + b_sum == 0:
        return 1.0
    inter = int(np.logical_and(a, b).sum())
    return (2.0 * inter) / float(a_sum + b_sum)


def _surface_distances_mm(
    source_mask: np.ndarray,
    target_mask: np.ndarray,
    spacing_zyx_mm: Tuple[float, float, float],
) -> Optional[np.ndarray]:
    if binary_erosion is None or distance_transform_edt is None:
        return None
    src = np.asarray(source_mask, dtype=bool)
    tgt = np.asarray(target_mask, dtype=bool)
    if src.shape != tgt.shape:
        return None
    if not np.any(src) or not np.any(tgt):
        return None

    src_surface = np.logical_xor(src, binary_erosion(src, border_value=0))
    tgt_surface = np.logical_xor(tgt, binary_erosion(tgt, border_value=0))
    if not np.any(src_surface) or not np.any(tgt_surface):
        return None

    # Distance to the closest target-surface voxel for each source-surface voxel.
    dt = distance_transform_edt(~tgt_surface, sampling=spacing_zyx_mm)
    d = dt[src_surface]
    if d.size == 0:
        return None
    return np.asarray(d, dtype=np.float64)


def _compute_hd95_mm(
    mask_a: np.ndarray,
    mask_b: np.ndarray,
    spacing_zyx_mm: Tuple[float, float, float],
) -> Optional[float]:
    d_ab = _surface_distances_mm(mask_a, mask_b, spacing_zyx_mm)
    d_ba = _surface_distances_mm(mask_b, mask_a, spacing_zyx_mm)
    if d_ab is None or d_ba is None:
        return None
    joined = np.concatenate([d_ab, d_ba], axis=0)
    if joined.size == 0:
        return None
    return float(np.percentile(joined, 95.0))


def _read_landmarks_csv(path: Path) -> np.ndarray:
    rows: List[List[float]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
        # Prefer x,y,z; fallback to AP/ML/DV.
        xyz_keys = []
        for name in ("x", "y", "z"):
            if name in cols:
                xyz_keys.append(cols[name])
        if len(xyz_keys) != 3:
            xyz_keys = []
            for name in ("ap", "ml", "dv"):
                if name in cols:
                    xyz_keys.append(cols[name])
        if len(xyz_keys) != 3:
            raise RuntimeError(
                f"Landmark CSV must contain x,y,z or ap,ml,dv columns: {path}"
            )
        for row in reader:
            try:
                rows.append([float(row[xyz_keys[0]]), float(row[xyz_keys[1]]), float(row[xyz_keys[2]])])
            except Exception:
                continue
    if not rows:
        raise RuntimeError(f"No valid landmark rows parsed: {path}")
    return np.asarray(rows, dtype=np.float64)


def _compute_jacobian_metrics(jacobian_zyx: np.ndarray) -> Dict[str, float]:
    out: Dict[str, float] = {}
    jac = np.asarray(jacobian_zyx, dtype=np.float32)
    if jac.size == 0:
        return out
    finite = np.isfinite(jac)
    if not np.any(finite):
        return out
    vals = jac[finite]
    bad = np.logical_or(vals <= 0.0, np.logical_or(vals < 0.2, vals > 5.0))
    out["jacobianAnomalyRatio"] = float(np.mean(bad))
    out["jacobianStd"] = float(np.std(vals))
    return out


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _derive_uncertainty_from_residual_and_jac(
    residual_mm: Optional[float],
    jac_std: Optional[float],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    residual = float(residual_mm) if residual_mm is not None else None
    jac = float(jac_std) if jac_std is not None else None

    if residual is not None:
        out["entrySigmaMm"] = _clip(max(0.03, residual * 1.2), 0.01, 2.0)
        out["depthSigmaMm"] = _clip(max(0.05, residual * 1.5), 0.01, 5.0)

    base_angle = 2.0
    if residual is not None:
        base_angle += residual * 2.5
    if jac is not None:
        base_angle += jac * 8.0
    out["angleSigmaDeg"] = _clip(base_angle, 0.1, 25.0)

    base_risk = 0.08
    if residual is not None:
        base_risk += residual * 0.5
    if jac is not None:
        base_risk += jac * 0.03
    out["riskDistanceSigmaMm"] = _clip(base_risk, 0.001, 2.0)
    return out


def _load_region_map_json(path: Path) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    out: Dict[str, int] = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            key = str(k).strip()
            if not key:
                continue
            try:
                out[key] = int(v)
            except Exception:
                continue
        return out
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            key = str(row.get("acronym") or row.get("key") or "").strip()
            if not key:
                continue
            try:
                out[key] = int(row.get("id"))
            except Exception:
                continue
    return out


def _load_region_map_csv(path: Path) -> Dict[str, int]:
    out: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
        key_col = cols.get("acronym") or cols.get("key") or cols.get("name")
        id_col = cols.get("id") or cols.get("label") or cols.get("region_id")
        if not key_col or not id_col:
            raise RuntimeError(
                f"Region CSV must contain acronym/key/name and id/label/region_id columns: {path}"
            )
        for row in reader:
            key = str(row.get(key_col, "")).strip()
            if not key:
                continue
            try:
                out[key] = int(row.get(id_col))
            except Exception:
                continue
    return out


def _save_json(path: Path, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Qiu2018 artifacts for probe-planner backend.")
    ap.add_argument("--label-image", required=True, help="Input reconstructed label volume image path")
    ap.add_argument("--output-dir", required=True, help="Output directory for artifacts")
    ap.add_argument("--reference-image", default="", help="Optional reference image to define output grid")
    ap.add_argument("--expected-shape", default="528,320,456", help="Expected NumPy shape z,y,x")
    ap.add_argument("--target-spacing-um", default="25,25,25", help="Target spacing x,y,z in um when no reference")
    ap.add_argument("--allow-shape-mismatch", action="store_true", help="Do not fail on shape mismatch")

    ap.add_argument("--jacobian-image", default="", help="Optional Jacobian determinant image")
    ap.add_argument("--warp-field", default="", help="Optional displacement warp field to derive Jacobian")

    ap.add_argument("--gt-label-image", default="", help="Optional ground-truth/reference label image for Dice/HD95")
    ap.add_argument("--fixed-landmarks-csv", default="", help="Optional fixed landmarks CSV (x,y,z or ap,ml,dv)")
    ap.add_argument("--warped-landmarks-csv", default="", help="Optional warped landmarks CSV (x,y,z or ap,ml,dv)")

    ap.add_argument("--region-map-json", default="", help="Optional prebuilt region map JSON (acronym->id)")
    ap.add_argument("--region-map-csv", default="", help="Optional region map CSV (acronym,id)")
    ap.add_argument("--emit-rid-region-map", action="store_true", help="Emit RID_<id> map if no map is provided")

    args = ap.parse_args()

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_shape = _parse_shape(args.expected_shape)

    spacing_um_xyz = _parse_triplet(args.target_spacing_um, cast=float)
    spacing_mm_xyz = (
        float(spacing_um_xyz[0]) / 1000.0,
        float(spacing_um_xyz[1]) / 1000.0,
        float(spacing_um_xyz[2]) / 1000.0,
    )

    label_path = Path(args.label_image).expanduser().resolve()
    label_img = _load_label_image(label_path, default_spacing_mm_xyz=spacing_mm_xyz)

    if args.reference_image:
        ref_path = Path(args.reference_image).expanduser().resolve()
        ref_img = _load_image(ref_path)
    else:
        ref_img = _make_reference_from_shape(
            expected_shape_zyx=expected_shape,
            spacing_mm_xyz=spacing_mm_xyz,
            direction=label_img.GetDirection(),
            origin=label_img.GetOrigin(),
        )

    label_resampled = _resample_like(label_img, ref_img, is_label=True, default_value=0.0)
    annotation = np.asarray(sitk.GetArrayFromImage(label_resampled), dtype=np.uint32)

    if tuple(annotation.shape) != tuple(expected_shape) and not args.allow_shape_mismatch:
        raise RuntimeError(
            f"Annotation shape mismatch: got {tuple(annotation.shape)}, expected {tuple(expected_shape)}. "
            f"Use --reference-image with planner grid or --allow-shape-mismatch."
        )

    annotation_npy = out_dir / "annotation.npy"
    annotation_npz = out_dir / "annotation.npz"
    np.save(annotation_npy, annotation)
    np.savez_compressed(annotation_npz, annotation=annotation)

    jacobian_arr: Optional[np.ndarray] = None
    if args.jacobian_image or args.warp_field:
        if args.jacobian_image:
            jac_src = _load_image(Path(args.jacobian_image).expanduser().resolve())
        else:
            warp = _load_image(Path(args.warp_field).expanduser().resolve())
            if warp.GetNumberOfComponentsPerPixel() < 3:
                raise RuntimeError("Warp field is not a vector displacement field (need >=3 components per voxel).")
            jac_src = sitk.DisplacementFieldJacobianDeterminant(sitk.Cast(warp, sitk.sitkVectorFloat64))
        jac_resampled = _resample_like(jac_src, ref_img, is_label=False, default_value=1.0)
        jacobian_arr = np.asarray(sitk.GetArrayFromImage(jac_resampled), dtype=np.float32)
        np.save(out_dir / "jacobian.npy", jacobian_arr)
        np.savez_compressed(out_dir / "jacobian.npz", jacobian=jacobian_arr)

    metrics: Dict[str, float] = {}

    if args.gt_label_image:
        gt_img = _load_image(Path(args.gt_label_image).expanduser().resolve())
        gt_res = _resample_like(gt_img, ref_img, is_label=True, default_value=0.0)
        gt = np.asarray(sitk.GetArrayFromImage(gt_res), dtype=np.uint32)

        pred_mask = annotation > 0
        gt_mask = gt > 0
        dice = _compute_dice(pred_mask, gt_mask)
        if dice is not None:
            metrics["dice"] = float(dice)

        spacing_zyx_mm = (
            float(ref_img.GetSpacing()[2]),
            float(ref_img.GetSpacing()[1]),
            float(ref_img.GetSpacing()[0]),
        )
        hd95 = _compute_hd95_mm(pred_mask, gt_mask, spacing_zyx_mm)
        if hd95 is not None:
            metrics["hd95Mm"] = float(hd95)

    tre_mean = None
    tre_rms = None
    if args.fixed_landmarks_csv and args.warped_landmarks_csv:
        fixed_pts = _read_landmarks_csv(Path(args.fixed_landmarks_csv).expanduser().resolve())
        warped_pts = _read_landmarks_csv(Path(args.warped_landmarks_csv).expanduser().resolve())
        n = min(len(fixed_pts), len(warped_pts))
        if n <= 0:
            raise RuntimeError("Landmark CSVs parsed but contain no paired points.")
        if len(fixed_pts) != len(warped_pts):
            print(
                f"[warn] landmark row count mismatch: fixed={len(fixed_pts)} warped={len(warped_pts)}; using first {n} pairs."
            )
        dist = np.linalg.norm(fixed_pts[:n] - warped_pts[:n], axis=1)
        tre_mean = float(np.mean(dist))
        tre_rms = float(np.sqrt(np.mean(np.square(dist))))
        metrics["treMm"] = tre_mean

    if jacobian_arr is not None:
        metrics.update(_compute_jacobian_metrics(jacobian_arr))

    residual_mm = tre_rms
    if residual_mm is not None:
        metrics["residualMm"] = float(residual_mm)

    sigmas = _derive_uncertainty_from_residual_and_jac(
        residual_mm=residual_mm,
        jac_std=metrics.get("jacobianStd"),
    )
    metrics.update(sigmas)

    nonzero_count = int(np.count_nonzero(annotation))
    unique_ids = np.unique(annotation)
    nonzero_ids = [int(x) for x in unique_ids.tolist() if int(x) > 0]

    stats_payload: Dict[str, object] = {
        "createdAt": _utc_now_iso(),
        "source": "qiu2018_artifact_builder",
        "annotationPath": str(annotation_npy),
        "annotationShape": list(annotation.shape),
        "annotationDtype": str(annotation.dtype),
        "nonzeroVoxelCount": nonzero_count,
        "nonzeroLabelCount": int(len(nonzero_ids)),
        "metrics": {
            "dice": metrics.get("dice"),
            "hd95Mm": metrics.get("hd95Mm"),
            "treMm": metrics.get("treMm"),
        },
    }

    # Backend-compatible top-level keys:
    for key in (
        "dice",
        "hd95Mm",
        "treMm",
        "jacobianAnomalyRatio",
        "residualMm",
        "jacobianStd",
        "entrySigmaMm",
        "angleSigmaDeg",
        "depthSigmaMm",
        "riskDistanceSigmaMm",
    ):
        value = metrics.get(key)
        if value is not None:
            stats_payload[key] = round(float(value), 6)

    _save_json(out_dir / "stats.json", stats_payload)

    region_map: Dict[str, int] = {}
    if args.region_map_json:
        region_map.update(_load_region_map_json(Path(args.region_map_json).expanduser().resolve()))
    if args.region_map_csv:
        region_map.update(_load_region_map_csv(Path(args.region_map_csv).expanduser().resolve()))
    if not region_map and args.emit_rid_region_map:
        region_map = {f"RID_{sid}": sid for sid in nonzero_ids}
    if region_map:
        _save_json(out_dir / "region_map.json", region_map)

    result = {
        "ok": True,
        "outputDir": str(out_dir),
        "annotationPath": str(annotation_npy),
        "annotationNpZPath": str(annotation_npz),
        "statsPath": str(out_dir / "stats.json"),
        "jacobianPath": str((out_dir / "jacobian.npy") if jacobian_arr is not None else ""),
        "regionMapPath": str((out_dir / "region_map.json") if region_map else ""),
        "annotationShape": list(annotation.shape),
        "nonzeroLabelCount": len(nonzero_ids),
        "nonzeroVoxelCount": nonzero_count,
        "warnings": [],
    }

    if binary_erosion is None or distance_transform_edt is None:
        result["warnings"].append("scipy not available: HD95 will be skipped.")
    if not args.gt_label_image:
        result["warnings"].append("No --gt-label-image: dice/hd95 not computed.")
    if not (args.fixed_landmarks_csv and args.warped_landmarks_csv):
        result["warnings"].append("No landmark pair CSVs: tre/residual not computed.")
    if jacobian_arr is None:
        result["warnings"].append("No jacobian input: jacobian metrics not computed.")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
