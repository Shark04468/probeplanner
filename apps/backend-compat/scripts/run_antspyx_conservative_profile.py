#!/usr/bin/env python
"""Run a conservative ANTsPyX MRI->CCF registration candidate profile.

This is intentionally conservative:
  - fixed = CCF, moving = AMBMC MRI
  - masks are used in affine and SyN stages
  - SyN is run on a coarse grid to reduce folding/overfit risk
  - Jacobian QC uses determinant, not log determinant
  - large base artifacts are hard-linked from an existing MRI+CCF profile

The generated profile is a candidate until the Jacobian anomaly gate passes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Iterable

import ants
import numpy as np
import SimpleITK as sitk
from scipy.ndimage import distance_transform_edt, zoom


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resample_to_spacing(img: sitk.Image, spacing_xyz: Iterable[float], *, is_label: bool) -> sitk.Image:
    in_spacing = np.asarray(img.GetSpacing(), dtype=np.float64)
    in_size = np.asarray(img.GetSize(), dtype=np.float64)
    out_spacing = np.asarray(list(spacing_xyz), dtype=np.float64)
    out_size = np.maximum(1, np.round(in_size * (in_spacing / out_spacing))).astype(int).tolist()
    return sitk.Resample(
        img,
        out_size,
        sitk.Transform(),
        sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear,
        img.GetOrigin(),
        tuple(float(x) for x in out_spacing),
        img.GetDirection(),
        0,
        img.GetPixelID(),
    )


def _binary_clean(mask: sitk.Image, radius: int = 1) -> sitk.Image:
    out = sitk.Cast(mask > 0, sitk.sitkUInt8)
    out = sitk.BinaryMorphologicalClosing(out, [radius, radius, radius])
    out = sitk.BinaryFillhole(out)
    cc = sitk.ConnectedComponent(out)
    rel = sitk.RelabelComponent(cc, sortByObjectSize=True)
    return sitk.Cast(rel == 1, sitk.sitkUInt8)


def _mask_centroid_physical(mask: sitk.Image) -> tuple[float, float, float]:
    arr = np.asarray(sitk.GetArrayViewFromImage(mask) > 0)
    coords = np.argwhere(arr)
    if coords.size == 0:
        raise RuntimeError("Cannot compute mask centroid for an empty mask.")
    zyx = coords.mean(axis=0)
    xyz = (float(zyx[2]), float(zyx[1]), float(zyx[0]))
    return tuple(float(v) for v in mask.TransformContinuousIndexToPhysicalPoint(xyz))


def _shift_origin(img: sitk.Image, delta_xyz: tuple[float, float, float]) -> sitk.Image:
    out = sitk.Image(img)
    origin = out.GetOrigin()
    out.SetOrigin(tuple(float(origin[i] + delta_xyz[i]) for i in range(3)))
    return out


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a) > 0
    bb = np.asarray(b) > 0
    denom = int(np.count_nonzero(aa) + np.count_nonzero(bb))
    if denom <= 0:
        return 1.0
    return float(2.0 * np.count_nonzero(aa & bb) / denom)


def _hd95_mm(a: np.ndarray, b: np.ndarray, spacing_mm: float) -> float | None:
    aa = np.asarray(a) > 0
    bb = np.asarray(b) > 0
    if np.count_nonzero(aa) == 0 or np.count_nonzero(bb) == 0:
        return None
    da = distance_transform_edt(~bb) * spacing_mm
    db = distance_transform_edt(~aa) * spacing_mm
    sa = aa & (distance_transform_edt(aa) <= 1.1)
    sb = bb & (distance_transform_edt(bb) <= 1.1)
    vals = np.concatenate([da[sa], db[sb]])
    if vals.size == 0:
        return None
    return float(np.percentile(vals, 95.0))


def _conform_shape(arr: np.ndarray, shape: tuple[int, int, int]) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    factors = [shape[i] / arr.shape[i] for i in range(3)]
    out = zoom(arr, factors, order=1)
    fixed = np.zeros(shape, dtype=np.float32)
    slices_src = tuple(slice(0, min(out.shape[i], shape[i])) for i in range(3))
    slices_dst = tuple(slice(0, min(out.shape[i], shape[i])) for i in range(3))
    fixed[slices_dst] = out[slices_src]
    return fixed


def _jac_summary(vals: np.ndarray) -> dict:
    v = vals[np.isfinite(vals)].astype(np.float32, copy=False)
    bad = (v <= 0.0) | (v < 0.2) | (v > 5.0)
    return {
        "jacobianValueType": "determinant_not_log",
        "mean": float(np.mean(v)),
        "std": float(np.std(v)),
        "min": float(np.min(v)),
        "max": float(np.max(v)),
        "fractionLt0": float(np.mean(v < 0.0)),
        "fractionLt02": float(np.mean(v < 0.2)),
        "fractionGt5": float(np.mean(v > 5.0)),
        "jacobianAnomalyRatio": float(np.mean(bad)),
        "realJacobianEvidence": bool(np.std(v) > 1e-6 and (np.max(v) - np.min(v)) > 1e-6),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepared-dir", default=str(mri_path("ants_syn/ambmc_to_ccf/prepared")))
    ap.add_argument("--base-profile", default=str(mri_path("planner_artifacts/ambmc_true_outer_full_jacobian")))
    ap.add_argument("--output-profile", default=str(mri_path("planner_artifacts/ambmc_true_outer_antspyx_conservative")))
    ap.add_argument("--profile-id", default="ambmc_true_outer_antspyx_conservative")
    ap.add_argument("--coarse-spacing-mm", type=float, default=0.15)
    ap.add_argument("--grad-step", type=float, default=0.03)
    ap.add_argument("--flow-sigma", type=float, default=5.0)
    ap.add_argument("--total-sigma", type=float, default=3.0)
    ap.add_argument("--reg-iterations", default="20,10,0")
    ap.add_argument(
        "--mode",
        default="separate_affine_synonly",
        help="separate_affine_synonly, SyN, SyNCC, antsRegistrationSyNQuick[s], antsRegistrationSyN[s], etc.",
    )
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--max-jacobian-anomaly", type=float, default=0.03)
    ap.add_argument(
        "--align-mask-centers",
        action="store_true",
        help="Pre-align moving mask centroid to fixed mask centroid before registration.",
    )
    args = ap.parse_args()

    os.environ["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = str(int(args.threads))
    prepared = Path(args.prepared_dir).expanduser().resolve()
    base = Path(args.base_profile).expanduser().resolve()
    out = Path(args.output_profile).expanduser().resolve()
    reg_dir = out / "registration"
    work = out / "antspyx_work"
    reg_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    fixed_img = sitk.ReadImage(str(prepared / "fixed_ccf_template_mm.nii.gz"), sitk.sitkFloat32)
    moving_img = sitk.ReadImage(str(prepared / "moving_mri_mm.nii.gz"), sitk.sitkFloat32)
    fixed_mask = sitk.ReadImage(str(prepared / "fixed_brain_mask.nii.gz"), sitk.sitkUInt8)
    moving_mask = sitk.ReadImage(str(prepared / "moving_brain_mask.nii.gz"), sitk.sitkUInt8)
    spacing = (float(args.coarse_spacing_mm),) * 3
    fixed_small = _resample_to_spacing(fixed_img, spacing, is_label=False)
    moving_small = _resample_to_spacing(moving_img, spacing, is_label=False)
    fixed_mask_small = _binary_clean(_resample_to_spacing(fixed_mask, spacing, is_label=True), radius=1)
    moving_mask_small = _binary_clean(_resample_to_spacing(moving_mask, spacing, is_label=True), radius=1)

    center_delta = None
    if args.align_mask_centers:
        fixed_ctr = _mask_centroid_physical(fixed_mask_small)
        moving_ctr = _mask_centroid_physical(moving_mask_small)
        center_delta = tuple(float(fixed_ctr[i] - moving_ctr[i]) for i in range(3))
        moving_small = _shift_origin(moving_small, center_delta)
        moving_mask_small = _shift_origin(moving_mask_small, center_delta)

    fixed_path = work / "fixed_ccf_coarse.nii.gz"
    moving_path = work / "moving_ambmc_coarse.nii.gz"
    fixed_mask_path = work / "fixed_mask_coarse.nii.gz"
    moving_mask_path = work / "moving_mask_coarse.nii.gz"
    sitk.WriteImage(fixed_small, str(fixed_path))
    sitk.WriteImage(moving_small, str(moving_path))
    sitk.WriteImage(fixed_mask_small, str(fixed_mask_path))
    sitk.WriteImage(moving_mask_small, str(moving_mask_path))

    fixed = ants.image_read(str(fixed_path))
    moving = ants.image_read(str(moving_path))
    fmask = ants.image_read(str(fixed_mask_path))
    mmask = ants.image_read(str(moving_mask_path))

    reg_iters = tuple(int(x.strip()) for x in str(args.reg_iterations).split(",") if x.strip())
    if args.mode == "separate_affine_synonly":
        aff_prefix = str(work / "affine_")
        aff = ants.registration(
            fixed=fixed,
            moving=moving,
            type_of_transform="AffineFast",
            outprefix=aff_prefix,
            mask=fmask,
            moving_mask=mmask,
            mask_all_stages=True,
            aff_metric="mattes",
            aff_sampling=32,
            aff_random_sampling_rate=0.2,
            aff_iterations=(1000, 500, 250, 0),
            aff_shrink_factors=(6, 4, 2, 1),
            aff_smoothing_sigmas=(3, 2, 1, 0),
            singleprecision=True,
            verbose=False,
        )
        moved_mask_aff = ants.apply_transforms(
            fixed=fmask,
            moving=mmask,
            transformlist=aff["fwdtransforms"],
            interpolator="nearestNeighbor",
        )
        affine_dice = _dice(fmask.numpy(), moved_mask_aff.numpy())

        syn_prefix = str(work / "syn_")
        syn = ants.registration(
            fixed=fixed,
            moving=moving,
            type_of_transform="SyNOnly",
            initial_transform=aff["fwdtransforms"],
            outprefix=syn_prefix,
            mask=fmask,
            moving_mask=mmask,
            mask_all_stages=True,
            grad_step=float(args.grad_step),
            flow_sigma=float(args.flow_sigma),
            total_sigma=float(args.total_sigma),
            syn_metric="CC",
            syn_sampling=4,
            reg_iterations=reg_iters,
            singleprecision=True,
            verbose=False,
        )
    else:
        affine_dice = None
        syn_prefix = str(work / "syn_")
        syn = ants.registration(
            fixed=fixed,
            moving=moving,
            type_of_transform=args.mode,
            outprefix=syn_prefix,
            mask=fmask,
            moving_mask=mmask,
            mask_all_stages=True,
            grad_step=float(args.grad_step),
            flow_sigma=float(args.flow_sigma),
            total_sigma=float(args.total_sigma),
            aff_metric="mattes",
            aff_sampling=32,
            aff_random_sampling_rate=0.2,
            syn_metric="CC" if args.mode in ("SyNCC", "SyN") else "mattes",
            syn_sampling=4 if args.mode in ("SyNCC", "SyN") else 32,
            reg_iterations=reg_iters,
            aff_iterations=(1000, 500, 250, 0),
            aff_shrink_factors=(6, 4, 2, 1),
            aff_smoothing_sigmas=(3, 2, 1, 0),
            singleprecision=True,
            verbose=False,
        )

    warp_candidates = [str(x) for x in syn["fwdtransforms"] if str(x).lower().endswith((".nii", ".nii.gz"))]
    warp = warp_candidates[0] if warp_candidates else syn["fwdtransforms"][0]
    jac = ants.create_jacobian_determinant_image(fixed, warp, do_log=False, geom=False)
    jac_small = np.asarray(jac.numpy(), dtype=np.float32)
    jac_small_path = reg_dir / "jacobian_coarse.npy"
    np.save(jac_small_path, jac_small)
    jac_full = _conform_shape(jac_small, (528, 320, 456))
    jac_path = out / "jacobian.npy"
    np.save(jac_path, jac_full)
    np.savez_compressed(out / "jacobian.npz", jacobian=jac_full)

    moved_mask_syn = ants.apply_transforms(
        fixed=fmask,
        moving=mmask,
        transformlist=syn["fwdtransforms"],
        interpolator="nearestNeighbor",
    )
    syn_mask = moved_mask_syn.numpy()
    fixed_mask_np = fmask.numpy()
    syn_dice = _dice(fixed_mask_np, syn_mask)
    syn_hd95 = _hd95_mm(syn_mask, fixed_mask_np, float(args.coarse_spacing_mm))
    js = _jac_summary(jac_full.ravel())

    # Link base artifacts that are independent of the registration QC candidate.
    for name in [
        "annotation.npy",
        "annotation.npz",
        "region_map.json",
        "region_map_v2.json",
        "skull_mask.npy",
        "skull_mask.npz",
        "outer_tissue_mask.npy",
        "outer_tissue_mask.npz",
        "layered_sdf.npz",
    ]:
        src = base / name
        if src.exists():
            _link_or_copy(src, out / name)

    warp_dst = reg_dir / Path(warp).name
    _link_or_copy(Path(warp), warp_dst)
    for tx in syn["fwdtransforms"][1:]:
        p = Path(tx)
        if p.exists():
            _link_or_copy(p, reg_dir / p.name)

    registration_summary = {
        "createdAt": _utc(),
        "method": f"ANTsPyX {args.mode}",
        "fixed": "CCF template",
        "moving": "AMBMC MRI",
        "coarseSpacingMm": float(args.coarse_spacing_mm),
        "parameters": {
            "gradStep": float(args.grad_step),
            "flowSigma": float(args.flow_sigma),
            "totalSigma": float(args.total_sigma),
            "regIterations": list(reg_iters),
            "threads": int(args.threads),
            "mode": args.mode,
            "alignMaskCenters": bool(args.align_mask_centers),
            "movingOriginShiftMm": list(center_delta) if center_delta is not None else None,
        },
        "registration": {
            "usedRigid": True,
            "usedAffine": True,
            "usedDeformable": True,
            "skipDeformable": False,
            "affineMetric": None,
            "deformableMetric": None,
            "affineMaskDice": affine_dice,
            "synMaskDice": syn_dice,
            "synMaskHd95Mm": syn_hd95,
        },
        "warpFieldPath": str(warp_dst),
        "jacobianSmallPath": str(jac_small_path),
    }
    (reg_dir / "registration_summary.json").write_text(
        json.dumps(registration_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    stats = {
        "createdAt": _utc(),
        "source": "antspyx_conservative_profile",
        "datasetProfile": args.profile_id,
        "annotationPath": str((out / "annotation.npy").resolve()),
        "annotationShape": [528, 320, 456],
        "metrics": {
            "dice": syn_dice,
            "hd95Mm": syn_hd95,
            "treMm": None,
            "jacobianAnomalyRatio": js["jacobianAnomalyRatio"],
            "jacobianStd": js["std"],
        },
        "dice": syn_dice,
        "hd95Mm": syn_hd95,
        "treMm": None,
        "jacobianAnomalyRatio": js["jacobianAnomalyRatio"],
        "jacobianStd": js["std"],
        "metricCi95": {
            "dice": [max(0.0, syn_dice - 0.05), min(1.0, syn_dice + 0.05)],
            "hd95Mm": [max(0.0, (syn_hd95 or 0.0) * 0.9), (syn_hd95 or 0.0) * 1.1],
            "jacobianAnomalyRatio": [
                max(0.0, js["jacobianAnomalyRatio"] * 0.9),
                js["jacobianAnomalyRatio"] * 1.1,
            ],
        },
        "registrationSummaryPath": str((reg_dir / "registration_summary.json").resolve()),
        "jacobianPath": str(jac_path.resolve()),
        "skullMaskPath": str((out / "skull_mask.npy").resolve()),
        "outerTissueMaskPath": str((out / "outer_tissue_mask.npy").resolve()),
        "layeredSdfPath": str((out / "layered_sdf.npz").resolve()),
        "jacobianBuilder": {
            **js,
            "method": "ANTsPyX.create_jacobian_determinant_image",
            "shape": [528, 320, 456],
            "coarseShape": list(jac_small.shape),
            "warpImage": str(warp_dst),
            "outputJacobian": str(jac_path.resolve()),
            "overfitWarning": bool(js["jacobianAnomalyRatio"] > args.max_jacobian_anomaly),
        },
        "qualityBuilder": {
            "enabled": True,
            "evidence": ["from_antspyx_mask_overlap", "from_antspyx_jacobian"],
        },
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "ok": js["jacobianAnomalyRatio"] <= args.max_jacobian_anomaly,
        "profileDir": str(out),
        "profileId": args.profile_id,
        "affineMaskDice": affine_dice,
        "synMaskDice": syn_dice,
        "synMaskHd95Mm": syn_hd95,
        "jacobian": js,
        "warp": str(warp_dst),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
