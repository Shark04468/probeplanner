#!/usr/bin/env python
"""Build a real Jacobian determinant volume from a deformable warp field.

Preferred path uses SimpleITK's DisplacementFieldJacobianDeterminant.
Fallback path supports NIfTI vector fields readable by nibabel, with vectors in
the last dimension and displacement in millimetres.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _parse_shape(text: str | None):
    if not text:
        return None
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated shape values, got: {text!r}")
    return tuple(int(p) for p in parts)


def _metric(v):
    try:
        x = float(v)
    except Exception:
        return None
    return x if np.isfinite(x) else None


def _summary(jac: np.ndarray) -> dict:
    finite = np.isfinite(jac)
    if not np.any(finite):
        return {"valid": False}
    vals = jac[finite].astype(np.float32, copy=False)
    bad = (vals <= 0.0) | (vals < 0.2) | (vals > 5.0)
    return {
        "valid": True,
        "jacobianValueType": "determinant_not_log",
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "fractionLt0": float(np.mean(vals < 0.0)),
        "fractionLt02": float(np.mean(vals < 0.2)),
        "fractionGt5": float(np.mean(vals > 5.0)),
        "jacobianAnomalyRatio": float(np.mean(bad)),
        "realJacobianEvidence": bool(np.std(vals) > 1e-6 and (np.max(vals) - np.min(vals)) > 1e-6),
        "overfitWarning": bool(np.mean(bad) > 0.03),
    }


def _stats_annotation_shape(stats_path: Path | None):
    if not stats_path or not stats_path.exists():
        return None
    try:
        data = json.loads(stats_path.read_text(encoding="utf-8"))
        shape = data.get("annotationShape")
        if isinstance(shape, list) and len(shape) == 3:
            return tuple(int(x) for x in shape)
    except Exception:
        return None
    return None


def _conform_to_expected_shape(jac: np.ndarray, expected_shape):
    if not expected_shape:
        return jac, "as_read"
    expected_shape = tuple(int(x) for x in expected_shape)
    if tuple(jac.shape) == expected_shape:
        return jac, "as_read"
    reversed_shape = tuple(reversed(expected_shape))
    if tuple(jac.shape) == reversed_shape:
        return np.transpose(jac, (2, 1, 0)), "transposed_zyx_to_xyz"
    raise ValueError(
        f"Jacobian shape {tuple(jac.shape)} does not match expected {expected_shape} "
        f"or reversed {reversed_shape}."
    )


def _compute_with_sitk(warp_path: Path) -> np.ndarray:
    import SimpleITK as sitk  # type: ignore

    field = sitk.ReadImage(str(warp_path), sitk.sitkVectorFloat64)
    jac_img = sitk.DisplacementFieldJacobianDeterminant(field)
    return sitk.GetArrayFromImage(jac_img).astype(np.float32, copy=False)


def _compute_with_nibabel(warp_path: Path) -> np.ndarray:
    import nibabel as nib  # type: ignore

    img = nib.load(str(warp_path))
    data = np.asarray(img.get_fdata(dtype=np.float32))
    if data.ndim != 4 or data.shape[-1] < 3:
        raise ValueError("Fallback expects a 4D vector field with last dimension >= 3.")
    spacing = np.sqrt((img.affine[:3, :3] ** 2).sum(axis=0))
    spacing = np.where(spacing > 1e-9, spacing, 1.0)
    u = data[..., :3]
    grads = []
    for c in range(3):
        grads.append(np.gradient(u[..., c], *spacing, edge_order=1))
    shape = u.shape[:3]
    jac = np.empty(shape, dtype=np.float32)
    eye = np.eye(3, dtype=np.float32)
    # Vectorized determinant of I + grad(u). Axis order follows voxel axes.
    j00 = eye[0, 0] + grads[0][0]
    j01 = eye[0, 1] + grads[0][1]
    j02 = eye[0, 2] + grads[0][2]
    j10 = eye[1, 0] + grads[1][0]
    j11 = eye[1, 1] + grads[1][1]
    j12 = eye[1, 2] + grads[1][2]
    j20 = eye[2, 0] + grads[2][0]
    j21 = eye[2, 1] + grads[2][1]
    j22 = eye[2, 2] + grads[2][2]
    jac[...] = (
        j00 * (j11 * j22 - j12 * j21)
        - j01 * (j10 * j22 - j12 * j20)
        + j02 * (j10 * j21 - j11 * j20)
    )
    return jac


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warp-image", required=True, help="ANTs/SyN warp field, e.g. subj_to_template_Warp.nii.gz")
    ap.add_argument("--output-jacobian", required=True, help="Output .npy or .npz path")
    ap.add_argument("--stats", default="", help="Optional stats.json to update with Jacobian metrics")
    ap.add_argument(
        "--expected-shape",
        default="",
        help="Expected backend array shape, e.g. 528,320,456. If SITK returns z,y,x, it is transposed.",
    )
    args = ap.parse_args()

    warp_path = Path(args.warp_image).expanduser()
    out_path = Path(args.output_jacobian).expanduser()
    if not warp_path.exists():
        raise FileNotFoundError(f"Warp image not found: {warp_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        jac = _compute_with_sitk(warp_path)
        method = "SimpleITK.DisplacementFieldJacobianDeterminant"
    except Exception as sitk_exc:
        jac = _compute_with_nibabel(warp_path)
        method = f"nibabel_gradient_fallback_after_sitk_error:{sitk_exc.__class__.__name__}"

    stats_path = Path(args.stats).expanduser() if args.stats else None
    expected_shape = _parse_shape(args.expected_shape) or _stats_annotation_shape(stats_path)
    jac, shapeMode = _conform_to_expected_shape(jac, expected_shape)

    if out_path.suffix.lower() == ".npz":
        np.savez_compressed(out_path, jacobian=jac.astype(np.float32, copy=False))
    else:
        np.save(out_path, jac.astype(np.float32, copy=False))
    info = _summary(jac)
    info["method"] = method
    info["shapeMode"] = shapeMode
    info["shape"] = [int(x) for x in jac.shape]
    info["warpImage"] = str(warp_path.resolve())
    info["outputJacobian"] = str(out_path.resolve())

    if stats_path and stats_path.exists():
        data = json.loads(stats_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        metrics["jacobianAnomalyRatio"] = _metric(info.get("jacobianAnomalyRatio"))
        metrics["jacobianStd"] = _metric(info.get("std"))
        data["metrics"] = metrics
        data["jacobianAnomalyRatio"] = metrics["jacobianAnomalyRatio"]
        data["jacobianStd"] = metrics["jacobianStd"]
        data["jacobianPath"] = str(out_path.resolve())
        data["jacobianBuilder"] = info
        stats_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
