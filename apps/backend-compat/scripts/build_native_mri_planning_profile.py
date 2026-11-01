#!/usr/bin/env python
"""Build a native MRI planning profile from the strict MRI->CCF ANTs baseline.

The registration baseline is kept as the atlas bridge.  This script applies the
inverse image transform chain to bring CCF labels/risk layers back into the
native-oriented MRI grid, where straight physical probe trajectories should be
planned.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Sequence

import numpy as np
import SimpleITK as sitk

try:
    from scipy.ndimage import binary_erosion, distance_transform_edt
except Exception:  # pragma: no cover
    binary_erosion = None
    distance_transform_edt = None


PROJECT_ROOT = workspace_root()
DEFAULT_ANTS_BIN = PROJECT_ROOT / "tools" / "ants" / "ants-2.6.5-windows-2022-X64-VS2019" / "ants-2.6.5" / "bin"
DEFAULT_BASELINE_PROFILE = mri_path("planner_artifacts/mri_allen_ants_shape_head_baseline")
DEFAULT_BASELINE_WORK = mri_path("ants_syn/mri_allen_ants_shape_head_baseline")
DEFAULT_OUT = mri_path("planner_artifacts/native_mri_planning_shape_head")
DEFAULT_WORK = mri_path("ants_syn/native_mri_planning_shape_head")
DEFAULT_FIG_DIR = PROJECT_ROOT / "output" / "pdf" / "figures"

VENTRICLE_ACRONYMS = ("VL", "V3", "AQ", "V4", "V4r")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def artifact(path: Path, *, kind: str, required: bool = True, shape=None) -> dict:
    exists = path.exists()
    return {
        "kind": kind,
        "required": bool(required),
        "path": str(path),
        "exists": bool(exists),
        "shape": list(shape) if shape is not None else None,
        "sha256": sha256_file(path) if exists else "",
    }


def copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def run(exe: Path, args: Sequence[str], *, env: dict, log: Path) -> None:
    cmd = [str(exe), *[str(a) for a in args]]
    print("[RUN]", " ".join(cmd), flush=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("[RUN] " + " ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def ants_env(args) -> dict:
    env = os.environ.copy()
    env["ANTSPATH"] = str(args.ants_bin) + os.sep
    env["PATH"] = str(args.ants_bin) + os.pathsep + env.get("PATH", "")
    env["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = str(args.threads)
    return env


def image_shape_xyz(path: Path) -> list[int]:
    return [int(v) for v in sitk.ReadImage(str(path)).GetSize()]


def npy_mask_from_image(path: Path, out_path: Path) -> np.ndarray:
    img = sitk.ReadImage(str(path))
    arr_xyz = np.transpose((sitk.GetArrayFromImage(img) > 0).astype(np.uint8), (2, 1, 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, arr_xyz, allow_pickle=False)
    return arr_xyz


def write_like(ref: sitk.Image, arr_xyz: np.ndarray, path: Path, pixel_id: int) -> None:
    img = sitk.GetImageFromArray(np.transpose(arr_xyz, (2, 1, 0)))
    img.CopyInformation(ref)
    img = sitk.Cast(img, pixel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(img, str(path), True)


def signed_sdf(mask_path: Path, out_path: Path) -> dict:
    mask = sitk.Cast(sitk.ReadImage(str(mask_path)) > 0, sitk.sitkUInt8)
    sdf = sitk.SignedMaurerDistanceMap(
        mask,
        insideIsPositive=False,
        squaredDistance=False,
        useImageSpacing=True,
    )
    sdf = sitk.Cast(sdf, sitk.sitkFloat32)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(sdf, str(out_path), True)
    stats = sitk.StatisticsImageFilter()
    stats.Execute(sdf)
    return {
        "path": str(out_path),
        "min": float(stats.GetMinimum()),
        "max": float(stats.GetMaximum()),
        "mean": float(stats.GetMean()),
        "sigma": float(stats.GetSigma()),
    }


def edge(mask2d: np.ndarray) -> np.ndarray:
    m = np.asarray(mask2d, dtype=bool)
    if binary_erosion is None:
        return m
    return m ^ binary_erosion(m)


def norm_slice(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros_like(arr, dtype=np.float32)
    vals = arr[finite]
    lo, hi = np.percentile(vals, [1, 99])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def dice(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    denom = int(np.count_nonzero(aa) + np.count_nonzero(bb))
    if denom <= 0:
        return 1.0
    return float(2.0 * np.count_nonzero(aa & bb) / denom)


def hd95_mm(a: np.ndarray, b: np.ndarray, spacing_mm: float, max_voxels: int = 50_000_000) -> float | None:
    if distance_transform_edt is None or binary_erosion is None:
        return None
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    if aa.size > max_voxels:
        stride = int(np.ceil((aa.size / float(max_voxels)) ** (1.0 / 3.0)))
        stride = max(2, stride)
        aa = aa[::stride, ::stride, ::stride]
        bb = bb[::stride, ::stride, ::stride]
        spacing_mm = float(spacing_mm) * float(stride)
    if np.count_nonzero(aa) == 0 or np.count_nonzero(bb) == 0:
        return None
    surf_a = aa ^ binary_erosion(aa)
    surf_b = bb ^ binary_erosion(bb)
    dt_a = distance_transform_edt(~aa).astype(np.float32) * float(spacing_mm)
    dt_b = distance_transform_edt(~bb).astype(np.float32) * float(spacing_mm)
    vals = np.concatenate([dt_b[surf_a], dt_a[surf_b]], axis=0)
    if vals.size == 0:
        return None
    return float(np.percentile(vals, 95.0))


def sdf_stats(path: Path) -> dict:
    sdf = sitk.ReadImage(str(path), sitk.sitkFloat32)
    stats = sitk.StatisticsImageFilter()
    stats.Execute(sdf)
    return {
        "path": str(path),
        "min": float(stats.GetMinimum()),
        "max": float(stats.GetMaximum()),
        "mean": float(stats.GetMean()),
        "sigma": float(stats.GetSigma()),
    }


def load_points(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_points(path: Path, points: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["x", "y", "z"])
        writer.writeheader()
        for row in points:
            writer.writerow({"x": float(row[0]), "y": float(row[1]), "z": float(row[2])})


def points_array(path: Path) -> np.ndarray:
    rows = load_points(path)
    return np.asarray([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows], dtype=np.float64)


def sample_mask_points(mask_path: Path, count: int) -> np.ndarray:
    img = sitk.ReadImage(str(mask_path))
    arr = sitk.GetArrayFromImage(img) > 0
    coords = np.argwhere(arr)
    if coords.size == 0:
        raise RuntimeError(f"Cannot sample points from empty mask: {mask_path}")
    # Deterministic quantile samples across the foreground.
    idx = np.linspace(0, len(coords) - 1, min(count, len(coords))).astype(int)
    out = []
    for z, y, x in coords[idx]:
        out.append(img.TransformIndexToPhysicalPoint((int(x), int(y), int(z))))
    return np.asarray(out, dtype=np.float64)


def roundtrip_points(args, paths: dict, env: dict) -> dict:
    apply_pts = args.ants_bin / "antsApplyTransformsToPoints.exe"
    sampled = sample_mask_points(paths["native_brain_mask"], args.roundtrip_points)
    native_pts = args.work_dir / "roundtrip_native_points.csv"
    ccf_pts = args.work_dir / "roundtrip_points_in_ccf.csv"
    back_pts = args.work_dir / "roundtrip_points_back_native.csv"
    write_points(native_pts, sampled)
    run(
        apply_pts,
        [
            "-d",
            "3",
            "-i",
            str(native_pts),
            "-o",
            str(ccf_pts),
            "-t",
            f"[{paths['affine']},1]",
            "-t",
            str(paths["inverse_warp"]),
        ],
        env=env,
        log=args.work_dir / "logs" / "points_roundtrip.log",
    )
    run(
        apply_pts,
        [
            "-d",
            "3",
            "-i",
            str(ccf_pts),
            "-o",
            str(back_pts),
            "-t",
            str(paths["warp"]),
            "-t",
            str(paths["affine"]),
        ],
        env=env,
        log=args.work_dir / "logs" / "points_roundtrip.log",
    )
    back = points_array(back_pts)
    n = min(len(sampled), len(back))
    residual = np.linalg.norm(sampled[:n] - back[:n], axis=1)
    finite = residual[np.isfinite(residual)]
    return {
        "nativePoints": str(native_pts),
        "ccfPoints": str(ccf_pts),
        "backNativePoints": str(back_pts),
        "count": int(finite.size),
        "meanMm": float(np.mean(finite)) if finite.size else None,
        "p95Mm": float(np.percentile(finite, 95)) if finite.size else None,
        "maxMm": float(np.max(finite)) if finite.size else None,
    }


def apply_inverse_to_native(args, paths: dict, env: dict, src: Path, dst: Path, interpolation: str) -> None:
    apply_tx = args.ants_bin / "antsApplyTransforms.exe"
    run(
        apply_tx,
        [
            "-d",
            "3",
            "-i",
            str(src),
            "-r",
            str(paths["native_mri"]),
            "-o",
            str(dst),
            "-n",
            interpolation,
            "-t",
            f"[{paths['affine']},1]",
            "-t",
            str(paths["inverse_warp"]),
        ],
        env=env,
        log=args.work_dir / "logs" / "apply_inverse_to_native.log",
    )


def build_ventricle_mask(args, paths: dict) -> Path:
    region_map = json.loads(paths["region_map"].read_text(encoding="utf-8"))
    ids = [int(region_map[a]) for a in VENTRICLE_ACRONYMS if a in region_map]
    ann = sitk.ReadImage(str(paths["fixed_annotation"]))
    arr = sitk.GetArrayFromImage(ann)
    mask = np.isin(arr, np.asarray(ids, dtype=arr.dtype)).astype(np.uint8)
    out = args.work_dir / "ccf_ventricle_mask_from_annotation.nii.gz"
    img = sitk.GetImageFromArray(mask)
    img.CopyInformation(ann)
    sitk.WriteImage(sitk.Cast(img, sitk.sitkUInt8), str(out), True)
    return out


def build_qc_figure(args, paths: dict, metrics: dict) -> Path:
    import matplotlib.pyplot as plt

    fig_dir = args.figure_dir
    fig_dir.mkdir(parents=True, exist_ok=True)
    mri = sitk.GetArrayFromImage(sitk.ReadImage(str(paths["native_mri"]))).astype(np.float32)
    moving_brain = sitk.GetArrayFromImage(sitk.ReadImage(str(paths["native_brain_mask"]))) > 0
    moving_head = sitk.GetArrayFromImage(sitk.ReadImage(str(paths["native_head_mask"]))) > 0
    ccf_support = sitk.GetArrayFromImage(sitk.ReadImage(str(paths["ccf_brain_native"]))) > 0
    vent = sitk.GetArrayFromImage(sitk.ReadImage(str(paths["ventricle_native"]))) > 0

    union = moving_head | ccf_support
    coords = np.argwhere(union)
    center = np.median(coords, axis=0).astype(int) if coords.size else np.asarray(mri.shape) // 2
    zc, yc, xc = [int(v) for v in center]

    fig = plt.figure(figsize=(11.5, 6.8), dpi=180)
    gs = fig.add_gridspec(2, 3, height_ratios=[2.3, 1.0])
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    views = [
        (mri[:, :, xc], moving_brain[:, :, xc], moving_head[:, :, xc], ccf_support[:, :, xc], vent[:, :, xc], f"Sagittal x={xc}"),
        (mri[:, yc, :], moving_brain[:, yc, :], moving_head[:, yc, :], ccf_support[:, yc, :], vent[:, yc, :], f"Coronal y={yc}"),
        (mri[zc, :, :], moving_brain[zc, :, :], moving_head[zc, :, :], ccf_support[zc, :, :], vent[zc, :, :], f"Horizontal z={zc}"),
    ]
    for ax, (base, brain, head, ccf, vent_sl, title) in zip(axes, views):
        ax.imshow(norm_slice(base), cmap="gray", origin="lower")
        ax.contour(edge(head), levels=[0.5], colors=["#f2b544"], linewidths=0.75, linestyles="dashed")
        ax.contour(edge(brain), levels=[0.5], colors=["#d05b45"], linewidths=0.75)
        ax.contour(edge(ccf), levels=[0.5], colors=["#21a7c4"], linewidths=0.75)
        if np.any(vent_sl):
            ax.contour(edge(vent_sl), levels=[0.5], colors=["#8b5cf6"], linewidths=0.7)
        ax.set_title(title, fontsize=8)
        ax.set_axis_off()

    axb = fig.add_subplot(gs[1, :])
    labels = ["CCF support vs brain Dice", "CCF support vs head Dice", "Brain HD95 mm", "Head HD95 mm", "Roundtrip p95 mm"]
    vals = [
        metrics["ccfSupportVsNativeBrainDice"],
        metrics["ccfSupportVsNativeHeadDice"],
        metrics["ccfSupportVsNativeBrainHd95Mm"] or 0.0,
        metrics["ccfSupportVsNativeHeadHd95Mm"] or 0.0,
        metrics["inverseConsistencyP95Mm"] or 0.0,
    ]
    bars = axb.bar(labels, vals, color=["#6fb6b2", "#7f8f84", "#b87d6a", "#c7a18e", "#777777"])
    axb.set_ylim(0.0, max(vals + [1.0]) * 1.2)
    axb.set_title("Native-space planning QC metrics", fontsize=9)
    axb.grid(axis="y", alpha=0.25)
    axb.tick_params(axis="x", labelrotation=10, labelsize=8)
    for bar, val in zip(bars, vals):
        axb.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.3f}", ha="center", va="bottom", fontsize=8)

    fig.suptitle(
        "Native MRI planning space: CCF labels/risk masks inverse-warped to MRI grid",
        fontsize=12,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.91,
        "cyan=CCF brain support, red=native MRI brain mask, yellow dashed=native head/foreground, purple=CCF ventricle labels",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    out = fig_dir / "fig_native_mri_planning_space.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build native MRI planning profile from inverse CCF transforms.")
    ap.add_argument("--ants-bin", type=Path, default=DEFAULT_ANTS_BIN)
    ap.add_argument("--baseline-profile", type=Path, default=DEFAULT_BASELINE_PROFILE)
    ap.add_argument("--baseline-work", type=Path, default=DEFAULT_BASELINE_WORK)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    ap.add_argument("--figure-dir", type=Path, default=DEFAULT_FIG_DIR)
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--roundtrip-points", type=int, default=200)
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "registration").mkdir(exist_ok=True)
    env = ants_env(args)

    paths = {
        "native_mri": args.baseline_work / "prepared" / "moving_mri_ail_mm.nii.gz",
        "native_brain_mask": args.baseline_work / "prepared" / "moving_brain_mask_ail.nii.gz",
        "native_head_mask": args.baseline_work / "prepared" / "moving_head_mask_ail.nii.gz",
        "fixed_annotation": args.baseline_work / "prepared" / "fixed_annotation.nii.gz",
        "fixed_brain_mask": args.baseline_work / "prepared" / "fixed_brain_mask.nii.gz",
        "affine": args.baseline_profile / "registration" / "0GenericAffine.mat",
        "warp": args.baseline_profile / "registration" / "1Warp.nii.gz",
        "inverse_warp": args.baseline_profile / "registration" / "1InverseWarp.nii.gz",
        "layered_sdf": args.baseline_profile / "layered_sdf.npz",
        "region_map": args.baseline_profile / "region_map.json",
    }
    for key, p in paths.items():
        if not p.exists():
            raise FileNotFoundError(f"{key}: {p}")

    copied = {
        "native_mri": args.out_dir / "native_mri_ail_mm.nii.gz",
        "native_brain_mask": args.out_dir / "native_brain_mask.nii.gz",
        "native_head_mask": args.out_dir / "native_head_mask.nii.gz",
        "affine": args.out_dir / "registration" / "mri_to_ccf_0GenericAffine.mat",
        "warp": args.out_dir / "registration" / "mri_to_ccf_1Warp.nii.gz",
        "inverse_warp": args.out_dir / "registration" / "mri_to_ccf_1InverseWarp.nii.gz",
    }
    for src_key, dst in copied.items():
        copy_or_link(paths[src_key], dst)

    out_paths = {
        **paths,
        **copied,
        "ccf_annotation_native": args.out_dir / "ccf_annotation_in_native_mri.nii.gz",
        "ccf_brain_native": args.out_dir / "ccf_brain_support_in_native_mri.nii.gz",
        "ventricle_ccf": args.work_dir / "ccf_ventricle_mask_from_annotation.nii.gz",
        "ventricle_native": args.out_dir / "ccf_ventricle_mask_in_native_mri.nii.gz",
        "native_brain_sdf": args.out_dir / "native_brain_sdf_mm.nii.gz",
        "native_head_sdf": args.out_dir / "native_head_sdf_mm.nii.gz",
        "ventricle_sdf": args.out_dir / "ccf_ventricle_sdf_in_native_mri_mm.nii.gz",
    }

    if not args.skip_existing or not out_paths["ccf_annotation_native"].exists():
        apply_inverse_to_native(args, out_paths, env, paths["fixed_annotation"], out_paths["ccf_annotation_native"], "NearestNeighbor")
    if not args.skip_existing or not out_paths["ccf_brain_native"].exists():
        apply_inverse_to_native(args, out_paths, env, paths["fixed_brain_mask"], out_paths["ccf_brain_native"], "NearestNeighbor")
    if not args.skip_existing or not out_paths["ventricle_native"].exists():
        vent_ccf = build_ventricle_mask(args, paths)
        out_paths["ventricle_ccf"] = vent_ccf
        apply_inverse_to_native(args, out_paths, env, vent_ccf, out_paths["ventricle_native"], "NearestNeighbor")

    if args.skip_existing and out_paths["native_brain_sdf"].exists():
        brain_sdf = sdf_stats(out_paths["native_brain_sdf"])
    else:
        brain_sdf = signed_sdf(out_paths["native_brain_mask"], out_paths["native_brain_sdf"])
    if args.skip_existing and out_paths["native_head_sdf"].exists():
        head_sdf = sdf_stats(out_paths["native_head_sdf"])
    else:
        head_sdf = signed_sdf(out_paths["native_head_mask"], out_paths["native_head_sdf"])
    if args.skip_existing and out_paths["ventricle_sdf"].exists():
        vent_sdf = sdf_stats(out_paths["ventricle_sdf"])
    else:
        vent_sdf = signed_sdf(out_paths["ventricle_native"], out_paths["ventricle_sdf"])

    native_shape = image_shape_xyz(out_paths["native_mri"])
    native_spacing = sitk.ReadImage(str(out_paths["native_mri"])).GetSpacing()
    spacing_mm = float(np.mean(native_spacing))

    brain = npy_mask_from_image(out_paths["native_brain_mask"], args.out_dir / "native_brain_mask.npy")
    head = npy_mask_from_image(out_paths["native_head_mask"], args.out_dir / "native_head_mask.npy")
    ccf = npy_mask_from_image(out_paths["ccf_brain_native"], args.out_dir / "ccf_brain_support_in_native_mri.npy")
    vent = npy_mask_from_image(out_paths["ventricle_native"], args.out_dir / "ccf_ventricle_mask_in_native_mri.npy")

    rt = roundtrip_points(args, out_paths, env)
    metrics = {
        "ccfSupportVsNativeBrainDice": dice(ccf, brain),
        "ccfSupportVsNativeHeadDice": dice(ccf, head),
        "ccfSupportVsNativeBrainHd95Mm": hd95_mm(ccf, brain, spacing_mm),
        "ccfSupportVsNativeHeadHd95Mm": hd95_mm(ccf, head, spacing_mm),
        "ccfSupportNativeVoxelCount": int(np.count_nonzero(ccf)),
        "nativeBrainVoxelCount": int(np.count_nonzero(brain)),
        "nativeHeadVoxelCount": int(np.count_nonzero(head)),
        "nativeVentricleVoxelCount": int(np.count_nonzero(vent)),
        "inverseConsistencyMeanMm": rt["meanMm"],
        "inverseConsistencyP95Mm": rt["p95Mm"],
        "inverseConsistencyMaxMm": rt["maxMm"],
    }

    qc = build_qc_figure(args, out_paths, metrics)

    readiness = {
        "status": "blocked_for_public_atlas_prior",
        "reason": (
            "native CCF labels/risk layers are available, but no public atlas ensemble "
            "bregma/lambda/skull-plane prior and leave-one-out TRE calibration are present"
        ),
        "availableNow": [
            "native MRI grid",
            "native moving brain/head masks",
            "inverse-warped CCF annotation",
            "inverse-warped CCF brain support",
            "inverse-warped CCF ventricle labels",
            "native brain/head/ventricle SDFs",
            "roundtrip transform sanity metrics",
        ],
        "missingForHighPrecision": [
            "public MRI+CT/skull-derived atlas ensemble registered to native MRI",
            "native bregma/lambda/skull-plane prior mean and covariance",
            "leave-one-out bregma/lambda TRE on public paired MRI/CT data",
            "planner calibration containing uncertaintyCalibration.riskMarginTreMm",
            "subject-specific micro-CT/skull landmarks for high-confidence deployment",
            "post-hoc probe-track or histology validation",
        ],
    }

    stats = {
        "createdAt": utc_now(),
        "profileId": "native_mri_planning_shape_head",
        "sourceBaselineProfile": str(args.baseline_profile),
        "nativeGrid": {
            "image": str(out_paths["native_mri"]),
            "shape": native_shape,
            "spacingMm": [float(v) for v in native_spacing],
        },
        "transformDirection": {
            "planningGrid": "native MRI",
            "atlasPropagation": "CCF labels/SDF -> native MRI",
            "imageInverseTransforms": [f"[{out_paths['affine']},1]", str(out_paths["inverse_warp"])],
        },
        "metrics": metrics,
        "sdfSummary": {
            "nativeBrain": brain_sdf,
            "nativeHead": head_sdf,
            "ccfVentricleNative": vent_sdf,
        },
        "roundtripPoints": rt,
        "readiness": readiness,
        "qcFigure": str(qc),
    }
    stats_path = args.out_dir / "native_planning_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    readiness_path = args.out_dir / "planning_readiness.json"
    readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = {
        "native_mri": artifact(out_paths["native_mri"], kind="image", shape=native_shape),
        "native_brain_mask": artifact(out_paths["native_brain_mask"], kind="mask", shape=native_shape),
        "native_head_mask": artifact(out_paths["native_head_mask"], kind="mask", shape=native_shape),
        "mri_to_ccf_affine": artifact(out_paths["affine"], kind="transform"),
        "mri_to_ccf_warp": artifact(out_paths["warp"], kind="transform"),
        "mri_to_ccf_inverse_warp": artifact(out_paths["inverse_warp"], kind="transform"),
        "ccf_annotation_native": artifact(out_paths["ccf_annotation_native"], kind="label_image", shape=native_shape),
        "ccf_brain_support_native": artifact(out_paths["ccf_brain_native"], kind="mask", shape=native_shape),
        "ccf_ventricle_mask_native": artifact(out_paths["ventricle_native"], kind="mask", shape=native_shape),
        "native_brain_sdf": artifact(out_paths["native_brain_sdf"], kind="sdf", shape=native_shape),
        "native_head_sdf": artifact(out_paths["native_head_sdf"], kind="sdf", shape=native_shape),
        "ccf_ventricle_sdf_native": artifact(out_paths["ventricle_sdf"], kind="sdf", shape=native_shape),
        "native_brain_mask_npy": artifact(args.out_dir / "native_brain_mask.npy", kind="mask", shape=native_shape),
        "native_head_mask_npy": artifact(args.out_dir / "native_head_mask.npy", kind="mask", shape=native_shape),
        "ccf_brain_support_native_npy": artifact(args.out_dir / "ccf_brain_support_in_native_mri.npy", kind="mask", shape=native_shape),
        "ccf_ventricle_mask_native_npy": artifact(args.out_dir / "ccf_ventricle_mask_in_native_mri.npy", kind="mask", shape=native_shape),
        "stats": artifact(stats_path, kind="json"),
        "planning_readiness": artifact(readiness_path, kind="json"),
        "qc_native_planning": artifact(qc, kind="qc_figure"),
    }
    missing = [k for k, v in artifacts.items() if v["required"] and not v["exists"]]
    hash_missing = [k for k, v in artifacts.items() if v["required"] and not v["sha256"]]
    manifest = {
        "schemaVersion": "probeplanner.native_mri_planning.v1",
        "generatedAt": utc_now(),
        "profileId": "native_mri_planning_shape_head",
        "valid": not missing and not hash_missing,
        "planningReadiness": readiness["status"],
        "nativeShape": native_shape,
        "nativeSpacingMm": [float(v) for v in native_spacing],
        "sameTransformContract": {
            "atlasToNativeImageTransforms": ["inverse(mri_to_ccf_affine)", "mri_to_ccf_inverse_warp"],
            "labelsUseNearestNeighbor": True,
            "sdfUseNativeMaskDistanceMaps": True,
            "noWarpedMriDerivedEvidence": True,
        },
        "artifacts": artifacts,
        "missing": missing,
        "hashMissing": hash_missing,
    }
    manifest_path = args.out_dir / "native_planning_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not manifest["valid"]:
        raise RuntimeError(f"Native planning manifest invalid: missing={missing} hashMissing={hash_missing}")

    print(
        json.dumps(
            {
                "ok": True,
                "profileDir": str(args.out_dir),
                "manifest": str(manifest_path),
                "stats": str(stats_path),
                "qcFigure": str(qc),
                "planningReadiness": readiness["status"],
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
