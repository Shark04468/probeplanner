#!/usr/bin/env python
"""Validate MRI->CCF registration artifacts against common ANTs/SyN pitfalls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root

import numpy as np
import SimpleITK as sitk


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ok(name: str, passed: bool, detail: str, severity: str = "error") -> dict:
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}


def _read_image(path: Path):
    img = sitk.ReadImage(str(path))
    arr = sitk.GetArrayFromImage(img)
    return img, arr


def _finite_sample(arr: np.ndarray, stride: int = 8) -> np.ndarray:
    sample = np.asarray(arr[::stride, ::stride, ::stride], dtype=np.float32).ravel()
    return sample[np.isfinite(sample)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepared-dir", default=str(mri_path("ants_syn/ambmc_to_ccf/prepared")))
    ap.add_argument("--profile-dir", default=str(mri_path("planner_artifacts/ambmc_true_outer_full_jacobian")))
    ap.add_argument("--max-jacobian-anomaly", type=float, default=0.03)
    ap.add_argument("--min-mask-fraction", type=float, default=0.03)
    ap.add_argument("--max-mask-fraction", type=float, default=0.80)
    args = ap.parse_args()

    prepared = Path(args.prepared_dir).expanduser()
    profile = Path(args.profile_dir).expanduser()
    metadata_path = prepared / "metadata.json"
    stats_path = profile / "stats.json"
    jac_path = profile / "jacobian.npy"

    checks = []
    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata.json not found: {metadata_path}")
    metadata = _load_json(metadata_path)
    inputs = metadata.get("inputs") if isinstance(metadata.get("inputs"), dict) else {}
    outputs = metadata.get("outputs") if isinstance(metadata.get("outputs"), dict) else {}
    spacing_meta = metadata.get("spacingPitfallHandled") if isinstance(metadata.get("spacingPitfallHandled"), dict) else {}
    interp_meta = metadata.get("interpolationPolicy") if isinstance(metadata.get("interpolationPolicy"), dict) else {}

    fixed_in = str(inputs.get("fixedImage") or "").lower()
    moving_in = str(inputs.get("movingImage") or "").lower()
    checks.append(
        _ok(
            "fixed_is_ccf",
            ("ccf" in fixed_in or "allen" in fixed_in or "average_template" in fixed_in),
            str(inputs.get("fixedImage") or ""),
        )
    )
    checks.append(
        _ok(
            "moving_is_ambmc_mri",
            ("ambmc" in moving_in or "mri" in moving_in),
            str(inputs.get("movingImage") or ""),
        )
    )

    fixed_img, fixed_arr = _read_image(Path(outputs["fixedImage"]))
    moving_img, moving_arr = _read_image(Path(outputs["movingImage"]))
    fixed_spacing = tuple(float(x) for x in fixed_img.GetSpacing())
    moving_spacing = tuple(float(x) for x in moving_img.GetSpacing())
    checks.append(
        _ok(
            "fixed_spacing_mm",
            all(abs(x - 0.025) < 1e-5 for x in fixed_spacing),
            f"fixed spacing={fixed_spacing}; original={spacing_meta.get('fixedOriginalSpacing')}",
        )
    )
    checks.append(
        _ok(
            "moving_spacing_mm",
            all(0.001 <= x <= 0.2 for x in moving_spacing),
            f"moving spacing={moving_spacing}; original={spacing_meta.get('movingOriginalSpacing')}",
        )
    )
    checks.append(
        _ok(
            "interpolation_policy_declared",
            interp_meta.get("intensityImages") == "Linear" and interp_meta.get("annotationAndMasks") == "NearestNeighbor",
            json.dumps(interp_meta, ensure_ascii=False),
        )
    )

    for key in ("fixedMask", "movingMask"):
        img, arr = _read_image(Path(outputs[key]))
        vals = np.unique(arr)
        frac = float(np.count_nonzero(arr) / max(1, arr.size))
        checks.append(
            _ok(
                f"{key}_binary_nearest_neighbor_safe",
                set(vals.tolist()).issubset({0, 1}) and img.GetPixelID() in (sitk.sitkUInt8, sitk.sitkUInt16, sitk.sitkUInt32),
                f"unique={vals[:10].tolist()}, pixelType={img.GetPixelIDTypeAsString()}",
            )
        )
        checks.append(
            _ok(
                f"{key}_fraction_reasonable",
                args.min_mask_fraction <= frac <= args.max_mask_fraction,
                f"fraction={frac:.6f}, nonzero={int(np.count_nonzero(arr))}, total={int(arr.size)}",
                severity="warning",
            )
        )

    if not stats_path.exists():
        raise FileNotFoundError(f"stats.json not found: {stats_path}")
    stats = _load_json(stats_path)
    builder = stats.get("jacobianBuilder") if isinstance(stats.get("jacobianBuilder"), dict) else {}
    checks.append(
        _ok(
            "jacobian_is_determinant_not_log",
            builder.get("jacobianValueType") == "determinant_not_log"
            or "DisplacementFieldJacobianDeterminant" in str(builder.get("method") or ""),
            f"valueType={builder.get('jacobianValueType')}; method={builder.get('method')}",
        )
    )
    checks.append(
        _ok(
            "real_jacobian_not_identity",
            bool(builder.get("realJacobianEvidence")) and float(stats.get("jacobianStd") or 0.0) > 1e-6,
            f"real={builder.get('realJacobianEvidence')}; std={stats.get('jacobianStd')}",
        )
    )

    if not jac_path.exists():
        raise FileNotFoundError(f"jacobian.npy not found: {jac_path}")
    jac = np.load(jac_path, mmap_mode="r")
    sample = _finite_sample(jac)
    anomaly = float(np.mean((sample <= 0.0) | (sample < 0.2) | (sample > 5.0)))
    checks.append(
        _ok(
            "jacobian_overfit_gate",
            anomaly <= args.max_jacobian_anomaly,
            f"sampleAnomaly={anomaly:.6f}; max={args.max_jacobian_anomaly:.6f}; "
            f"min={float(np.min(sample)):.6f}; p50={float(np.percentile(sample, 50)):.6f}; "
            f"p99={float(np.percentile(sample, 99)):.6f}; maxVal={float(np.max(sample)):.6f}",
        )
    )

    failed = [c for c in checks if not c["passed"] and c["severity"] == "error"]
    warnings = [c for c in checks if not c["passed"] and c["severity"] != "error"]
    report = {
        "ok": not failed,
        "failedCount": len(failed),
        "warningCount": len(warnings),
        "checks": checks,
        "recommendation": (
            "Do not use this deformable profile for strict planning until failed checks are fixed."
            if failed
            else "Pitfall checks passed."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
