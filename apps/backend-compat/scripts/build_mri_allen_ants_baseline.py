#!/usr/bin/env python
"""Build a strict AMBMC MRI -> Allen CCF ANTs baseline profile.

This is the reproducible replacement for the earlier mixed-artifact MRI/CCF
profiles.  It follows the MIRACL-style contract but uses local Windows ANTs:
reorient moving MRI/masks first, then register fixed=Allen CCF and moving=MRI,
then apply the same transform chain to masks and point landmarks.
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
from typing import Iterable, Sequence

import numpy as np
import SimpleITK as sitk

try:
    from scipy.ndimage import binary_erosion, distance_transform_edt
except Exception:  # pragma: no cover
    binary_erosion = None
    distance_transform_edt = None


DEFAULT_PROJECT_ROOT = backend_root()
DEFAULT_ANTS_BIN = ants_bin()
DEFAULT_FIXED = mri_path("raw/allen_ccf/average_template_25.nrrd")
DEFAULT_ANN = mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")
DEFAULT_MOVING = mri_path(
    "raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/"
    "ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii"
)
DEFAULT_MOVING_MASK = mri_path("ants_syn/ambmc_to_ccf/moving_masks/ambmc_moving_brain_mask_clean.nii.gz")
DEFAULT_WORK_DIR = mri_path("ants_syn/mri_allen_ants_shape_head_baseline")
DEFAULT_PROFILE_DIR = mri_path("planner_artifacts/mri_allen_ants_shape_head_baseline")
DEFAULT_LEGACY_PROFILE = mri_path("planner_artifacts/ambmc_true_outer_full")
DEFAULT_FIG_DIR = workspace_root() / "output" / "pdf" / "figures"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spacing_to_mm(spacing: Iterable[float]) -> tuple[float, float, float]:
    vals = tuple(float(v) for v in spacing)
    if max(vals) > 1.0:
        return tuple(v / 1000.0 for v in vals)
    return vals


def _as_index_grid(img: sitk.Image, *, pixel_id: int | None = None) -> sitk.Image:
    out = sitk.Image(img)
    if pixel_id is not None:
        out = sitk.Cast(out, pixel_id)
    out.SetSpacing(_spacing_to_mm(out.GetSpacing()))
    out.SetOrigin((0.0, 0.0, 0.0))
    out.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    return out


def _rescale_float(img: sitk.Image) -> sitk.Image:
    return sitk.RescaleIntensity(sitk.Cast(img, sitk.sitkFloat32), 0.0, 1.0)


def _largest_component(mask: sitk.Image) -> sitk.Image:
    cc = sitk.ConnectedComponent(sitk.Cast(mask > 0, sitk.sitkUInt8))
    rel = sitk.RelabelComponent(cc, sortByObjectSize=True)
    return sitk.Cast(rel == 1, sitk.sitkUInt8)


def _safe_close(mask: sitk.Image, radius: int) -> sitk.Image:
    out = sitk.Cast(mask > 0, sitk.sitkUInt8)
    if radius > 0:
        out = sitk.BinaryMorphologicalClosing(out, [radius, radius, radius])
    return sitk.Cast(out > 0, sitk.sitkUInt8)


def _image_info(img: sitk.Image) -> dict:
    return {
        "size": [int(x) for x in img.GetSize()],
        "spacingMm": [float(x) for x in img.GetSpacing()],
        "origin": [float(x) for x in img.GetOrigin()],
        "direction": [float(x) for x in img.GetDirection()],
        "orientation": sitk.DICOMOrientImageFilter_GetOrientationFromDirectionCosines(img.GetDirection()),
    }


def _mask_voxels(mask: sitk.Image) -> int:
    return int(np.count_nonzero(sitk.GetArrayViewFromImage(mask) > 0))


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _write_landmarks_from_mask(mask: sitk.Image, path: Path, *, prefix: str) -> list[dict]:
    stats = sitk.LabelShapeStatisticsImageFilter()
    stats.Execute(sitk.Cast(mask > 0, sitk.sitkUInt8))
    if not stats.HasLabel(1):
        raise RuntimeError(f"Cannot build landmarks from empty mask: {path}")
    bbox = stats.GetBoundingBox(1)
    start = np.asarray(bbox[:3], dtype=np.float64)
    size = np.asarray(bbox[3:], dtype=np.float64)
    fractions = [0.2, 0.35, 0.5, 0.65, 0.8]
    rows: list[dict] = []
    for fx in fractions:
        for fy in (0.3, 0.5, 0.7):
            for fz in fractions:
                idx = start + (size - 1.0) * np.asarray([fx, fy, fz], dtype=np.float64)
                point = mask.TransformContinuousIndexToPhysicalPoint([float(v) for v in idx])
                rows.append(
                    {
                        "name": f"{prefix}_{len(rows):03d}",
                        "x": float(point[0]),
                        "y": float(point[1]),
                        "z": float(point[2]),
                    }
                )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "x", "y", "z"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _load_points(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _point_array(rows: Sequence[dict]) -> np.ndarray:
    return np.asarray([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows], dtype=np.float64)


def _write_points_only_csv(src: Path, dst: Path) -> list[dict]:
    rows = _load_points(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["x", "y", "z"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"x": row["x"], "y": row["y"], "z": row["z"]})
    return rows


def _reattach_point_names(src_rows: Sequence[dict], transformed_points: Path, dst: Path) -> None:
    tx_rows = _load_points(transformed_points)
    n = min(len(src_rows), len(tx_rows))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "x", "y", "z"])
        writer.writeheader()
        for src, tx in zip(src_rows[:n], tx_rows[:n]):
            writer.writerow(
                {
                    "name": src.get("name", ""),
                    "x": tx.get("x", ""),
                    "y": tx.get("y", ""),
                    "z": tx.get("z", ""),
                }
            )


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
    denom = int(np.count_nonzero(aa) + np.count_nonzero(bb))
    if denom <= 0:
        return 1.0
    return float(2.0 * np.count_nonzero(aa & bb) / denom)


def _hd95_mm(a: np.ndarray, b: np.ndarray, spacing_mm: float = 0.025) -> float | None:
    if distance_transform_edt is None or binary_erosion is None:
        return None
    aa = np.asarray(a, dtype=bool)
    bb = np.asarray(b, dtype=bool)
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


def _save_npy_mask(img_path: Path, out_path: Path) -> np.ndarray:
    img = sitk.ReadImage(str(img_path))
    arr_zyx = (sitk.GetArrayFromImage(img) > 0).astype(np.uint8)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, arr_xyz, allow_pickle=False)
    return arr_xyz


def _load_mask_xyz(img_path: Path) -> np.ndarray:
    return np.transpose((sitk.GetArrayFromImage(sitk.ReadImage(str(img_path))) > 0), (2, 1, 0))


def _run(exe: Path, args: Sequence[str], *, env: dict, log_path: Path | None = None) -> None:
    cmd = [str(exe), *[str(a) for a in args]]
    print("[RUN]", " ".join(cmd), flush=True)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write("[RUN] " + " ".join(cmd) + "\n")
            log.flush()
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, env=env)
    else:
        proc = subprocess.run(cmd, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def prepare_inputs(args) -> dict:
    prepared = args.work_dir / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)

    fixed_raw = sitk.ReadImage(str(args.fixed_image))
    ann_raw = sitk.ReadImage(str(args.fixed_annotation))
    moving_raw = sitk.ReadImage(str(args.moving_image))
    moving_brain_raw = sitk.ReadImage(str(args.moving_brain_mask))

    fixed = _as_index_grid(fixed_raw, pixel_id=sitk.sitkFloat32)
    fixed_norm = _rescale_float(fixed)
    ann = _as_index_grid(ann_raw, pixel_id=sitk.sitkUInt32)
    fixed_mask = _safe_close(sitk.Cast(ann > 0, sitk.sitkUInt8), 1)

    moving_oriented = sitk.DICOMOrient(moving_raw, args.orient_code)
    moving = _as_index_grid(moving_oriented, pixel_id=sitk.sitkFloat32)
    moving_norm = _rescale_float(moving)
    moving_brain = _as_index_grid(sitk.DICOMOrient(moving_brain_raw, args.orient_code), pixel_id=sitk.sitkUInt8)
    moving_brain = _safe_close(_largest_component(moving_brain), 1)

    minmax = sitk.MinimumMaximumImageFilter()
    minmax.Execute(moving)
    bg = float(minmax.GetMinimum())
    moving_head = sitk.Cast(moving > (bg + 1e-3), sitk.sitkUInt8)

    paths = {
        "fixed_image": prepared / "fixed_ccf_template_mm.nii.gz",
        "fixed_annotation": prepared / "fixed_annotation.nii.gz",
        "fixed_mask": prepared / "fixed_brain_mask.nii.gz",
        "moving_image": prepared / f"moving_mri_{args.orient_code.lower()}_mm.nii.gz",
        "moving_brain_mask": prepared / f"moving_brain_mask_{args.orient_code.lower()}.nii.gz",
        "moving_head_mask": prepared / f"moving_head_mask_{args.orient_code.lower()}.nii.gz",
        "fixed_landmarks": prepared / "fixed_landmarks.csv",
        "moving_landmarks": prepared / "moving_landmarks.csv",
        "metadata": prepared / "metadata.json",
    }
    sitk.WriteImage(fixed_norm, str(paths["fixed_image"]))
    sitk.WriteImage(ann, str(paths["fixed_annotation"]))
    sitk.WriteImage(fixed_mask, str(paths["fixed_mask"]))
    sitk.WriteImage(moving_norm, str(paths["moving_image"]))
    sitk.WriteImage(moving_brain, str(paths["moving_brain_mask"]))
    sitk.WriteImage(moving_head, str(paths["moving_head_mask"]))
    fixed_lm = _write_landmarks_from_mask(fixed_mask, paths["fixed_landmarks"], prefix="fixed")
    moving_lm = _write_landmarks_from_mask(moving_brain, paths["moving_landmarks"], prefix="moving")

    metadata = {
        "createdAt": _utc_now_iso(),
        "route": "MIRACL mri_allen_nifty style equivalent ANTs pipeline",
        "fixed": "Allen CCF average_template_25",
        "moving": "AMBMC MRI",
        "fixedGridConvention": "local Allen NRRD index axes treated as AP,DV,ML",
        "movingOriginalOrientation": sitk.DICOMOrientImageFilter_GetOrientationFromDirectionCosines(
            moving_raw.GetDirection()
        ),
        "selectedMovingOrientCode": args.orient_code,
        "orientationScreen": str(args.orientation_screen) if args.orientation_screen else "",
        "inputs": {
            "fixedImage": str(args.fixed_image),
            "fixedAnnotation": str(args.fixed_annotation),
            "movingImage": str(args.moving_image),
            "movingBrainMask": str(args.moving_brain_mask),
        },
        "outputs": {k: str(v) for k, v in paths.items()},
        "geometry": {
            "fixedImage": _image_info(fixed_norm),
            "movingImage": _image_info(moving_norm),
            "fixedMaskVoxels": _mask_voxels(fixed_mask),
            "movingBrainMaskVoxels": _mask_voxels(moving_brain),
            "movingHeadMaskVoxels": _mask_voxels(moving_head),
        },
        "landmarks": {
            "type": "same-run geometric mask landmarks, not manual anatomical ground truth",
            "fixedCount": len(fixed_lm),
            "movingCount": len(moving_lm),
        },
    }
    paths["metadata"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": {k: str(v) for k, v in paths.items()}}, ensure_ascii=False, indent=2), flush=True)
    return {k: str(v) for k, v in paths.items()}


def ants_env(args) -> dict:
    env = dict(os.environ)
    env["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = str(args.threads)
    env["ANTSPATH"] = str(args.ants_bin) + os.sep
    env["PATH"] = str(args.ants_bin) + os.pathsep + env.get("PATH", "")
    return env


def run_registration(args, prepared: dict) -> dict:
    reg_dir = args.work_dir / "registration"
    reg_dir.mkdir(parents=True, exist_ok=True)
    env = ants_env(args)
    ants_reg = args.ants_bin / "antsRegistration.exe"
    apply_tx = args.ants_bin / "antsApplyTransforms.exe"
    apply_pts = args.ants_bin / "antsApplyTransformsToPoints.exe"
    jac_exe = args.ants_bin / "CreateJacobianDeterminantImage.exe"
    for exe in (ants_reg, apply_tx, apply_pts, jac_exe):
        if not exe.exists():
            raise FileNotFoundError(f"Missing ANTs executable: {exe}")

    prefix = reg_dir / "mri_to_ccf_"
    log = reg_dir / "ants_registration.log"
    if not args.skip_registration:
        syn = f"SyN[{args.syn_gradient_step},{args.syn_update_sigma},{args.syn_total_sigma}]"
        moving_reg_mask = (
            prepared["moving_head_mask"]
            if args.registration_moving_mask == "head"
            else prepared["moving_brain_mask"]
        )
        intensity_weight = max(0.0, 1.0 - float(args.shape_metric_weight))
        shape_weight = max(0.0, float(args.shape_metric_weight))

        def stage_metrics(kind: str) -> list[str]:
            if kind == "cc":
                items = ["--metric", f"CC[{prepared['fixed_image']},{prepared['moving_image']},{intensity_weight},4]"]
            else:
                items = [
                    "--metric",
                    f"MI[{prepared['fixed_image']},{prepared['moving_image']},{intensity_weight},32,Regular,0.25]",
                ]
            if shape_weight > 0.0:
                items.extend(["--metric", f"MeanSquares[{prepared['fixed_mask']},{moving_reg_mask},{shape_weight},0]"])
            return items

        reg_args = [
            "--dimensionality",
            "3",
            "--float",
            "1",
            "--collapse-output-transforms",
            "1",
            "--output",
            f"[{prefix},{prefix}Warped.nii.gz]",
            "--interpolation",
            "Linear",
            "--use-histogram-matching",
            "1",
            "--winsorize-image-intensities",
            "[0.005,0.995]",
            "--initial-moving-transform",
            f"[{prepared['fixed_image']},{prepared['moving_image']},1]",
            "--masks",
            f"[{prepared['fixed_mask']},{moving_reg_mask}]",
            "--transform",
            "Rigid[0.1]",
            *stage_metrics("mi"),
            "--convergence",
            "[600x300x150x60,1e-6,10]",
            "--shrink-factors",
            "8x4x2x1",
            "--smoothing-sigmas",
            "3x2x1x0vox",
            "--transform",
            "Affine[0.1]",
            *stage_metrics("mi"),
            "--convergence",
            "[600x300x150x60,1e-6,10]",
            "--shrink-factors",
            "8x4x2x1",
            "--smoothing-sigmas",
            "3x2x1x0vox",
            "--transform",
            syn,
            *stage_metrics("cc"),
            "--convergence",
            f"[{args.syn_iterations},1e-6,10]",
            "--shrink-factors",
            args.syn_shrink_factors,
            "--smoothing-sigmas",
            args.syn_smoothing_sigmas,
            "--verbose",
            "1",
        ]
        _run(ants_reg, reg_args, env=env, log_path=log)

    raw_affine = Path(str(prefix) + "0GenericAffine.mat")
    raw_warp = Path(str(prefix) + "1Warp.nii.gz")
    raw_inverse = Path(str(prefix) + "1InverseWarp.nii.gz")
    raw_warped = Path(str(prefix) + "Warped.nii.gz")
    for p in (raw_affine, raw_warp, raw_inverse, raw_warped):
        if not p.exists():
            raise FileNotFoundError(f"Expected ANTs registration output missing: {p}")

    affine = reg_dir / "0GenericAffine.mat"
    warp = reg_dir / "1Warp.nii.gz"
    inverse = reg_dir / "1InverseWarp.nii.gz"
    warped_mri = reg_dir / "warped_mri_in_ccf.nii.gz"
    _copy_or_link(raw_affine, affine)
    _copy_or_link(raw_warp, warp)
    _copy_or_link(raw_inverse, inverse)

    forward = ["-t", str(warp), "-t", str(affine)]
    _run(
        apply_tx,
        [
            "-d",
            "3",
            "-i",
            prepared["moving_image"],
            "-r",
            prepared["fixed_image"],
            "-o",
            str(warped_mri),
            "-n",
            "Linear",
            *forward,
        ],
        env=env,
        log_path=reg_dir / "ants_apply.log",
    )
    transformed_brain = reg_dir / "transformed_moving_brain_mask.nii.gz"
    transformed_head = reg_dir / "transformed_moving_head_mask.nii.gz"
    for src, dst in (
        (prepared["moving_brain_mask"], transformed_brain),
        (prepared["moving_head_mask"], transformed_head),
    ):
        _run(
            apply_tx,
            [
                "-d",
                "3",
                "-i",
                src,
                "-r",
                prepared["fixed_image"],
                "-o",
                str(dst),
                "-n",
                "NearestNeighbor",
                *forward,
            ],
            env=env,
            log_path=reg_dir / "ants_apply.log",
        )

    native_jac = reg_dir / "jacobian_det_native.nii.gz"
    _run(jac_exe, ["3", str(warp), str(native_jac), "0", "0"], env=env, log_path=reg_dir / "jacobian.log")

    # ANTs point transforms use the inverse transform list for moving->fixed
    # point coordinates because image resampling is a pullback operation.
    points_in = reg_dir / "moving_landmarks_points_only.csv"
    points_tmp = reg_dir / "transformed_moving_landmarks_points_only.csv"
    source_landmark_rows = _write_points_only_csv(Path(prepared["moving_landmarks"]), points_in)
    transformed_landmarks = reg_dir / "transformed_moving_landmarks.csv"
    _run(
        apply_pts,
        [
            "-d",
            "3",
            "-i",
            str(points_in),
            "-o",
            str(points_tmp),
            "-t",
            f"[{affine},1]",
            "-t",
            str(inverse),
        ],
        env=env,
        log_path=reg_dir / "ants_points.log",
    )
    _reattach_point_names(source_landmark_rows, points_tmp, transformed_landmarks)

    return {
        "registration_dir": str(reg_dir),
        "affine": str(affine),
        "warp": str(warp),
        "inverse_warp": str(inverse),
        "warped_mri": str(warped_mri),
        "transformed_brain_mask": str(transformed_brain),
        "transformed_head_mask": str(transformed_head),
        "native_jacobian": str(native_jac),
        "transformed_landmarks": str(transformed_landmarks),
        "ants_log": str(log),
    }


def _artifact_meta(path: Path, *, kind: str, required: bool = True, shape=None) -> dict:
    exists = path.exists()
    return {
        "kind": kind,
        "required": bool(required),
        "path": str(path),
        "exists": bool(exists),
        "shape": list(shape) if shape is not None else None,
        "sha256": _sha256_file(path) if exists else "",
    }


def _image_shape_xyz(path: Path) -> list[int]:
    return [int(v) for v in sitk.ReadImage(str(path)).GetSize()]


def _geometry_tuple(img: sitk.Image) -> tuple:
    return (
        tuple(int(v) for v in img.GetSize()),
        tuple(round(float(v), 9) for v in img.GetSpacing()),
        tuple(round(float(v), 9) for v in img.GetOrigin()),
        tuple(round(float(v), 9) for v in img.GetDirection()),
    )


def _jacobian_to_array(
    native_jac: Path,
    out_path: Path,
    reference_image: Path,
    ccf_grid_jac: Path,
) -> tuple[np.ndarray, dict]:
    img = sitk.ReadImage(str(native_jac), sitk.sitkFloat32)
    ref = sitk.ReadImage(str(reference_image), sitk.sitkFloat32)
    raw_shape = [int(v) for v in img.GetSize()]
    resampled = _geometry_tuple(img) != _geometry_tuple(ref)
    if resampled:
        img_ccf = sitk.Resample(
            img,
            ref,
            sitk.Transform(3, sitk.sitkIdentity),
            sitk.sitkLinear,
            1.0,
            sitk.sitkFloat32,
        )
    else:
        img_ccf = sitk.Image(img)
    ccf_grid_jac.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(img_ccf, str(ccf_grid_jac), True)
    arr_xyz = np.transpose(sitk.GetArrayFromImage(img_ccf).astype(np.float32), (2, 1, 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, arr_xyz, allow_pickle=False)
    finite = np.isfinite(arr_xyz)
    vals = arr_xyz[finite]
    bad = (vals <= 0.0) | (vals < 0.2) | (vals > 5.0)
    info = {
        "path": str(out_path),
        "nativeRawPath": str(native_jac),
        "ccfGridPath": str(ccf_grid_jac),
        "nativeRawShape": raw_shape,
        "shape": [int(x) for x in arr_xyz.shape],
        "dtype": str(arr_xyz.dtype),
        "resampledToReferenceGrid": bool(resampled),
        "referenceImage": str(reference_image),
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "mean": float(np.mean(vals)),
        "std": float(np.std(vals)),
        "anomalyRatio": float(np.mean(bad)),
        "fractionLt0": float(np.mean(vals < 0.0)),
        "fractionLt02": float(np.mean(vals < 0.2)),
        "fractionGt5": float(np.mean(vals > 5.0)),
        "realJacobianEvidence": bool(np.std(vals) > 1e-6 and (np.max(vals) - np.min(vals)) > 1e-6),
    }
    return arr_xyz, info


def build_profile_and_stats(args, prepared: dict, reg: dict) -> dict:
    profile = args.profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "registration").mkdir(exist_ok=True)
    quality = profile / "quality_eval"
    quality.mkdir(exist_ok=True)

    # Stable CCF artifacts reused from the atlas-derived profile.
    for name in ("annotation.npy", "region_map.json", "region_map_v2.json", "layered_sdf.npz"):
        src = args.legacy_profile / name
        if src.exists():
            _copy_or_link(src, profile / name)

    fixed_shape = _image_shape_xyz(Path(prepared["fixed_image"]))

    for src_key, dst_name in (
        ("warped_mri", "warped_mri_in_ccf.nii.gz"),
        ("warped_mri", "ambmc_in_ccf.nii.gz"),
        ("affine", "0GenericAffine.mat"),
        ("warp", "1Warp.nii.gz"),
        ("inverse_warp", "1InverseWarp.nii.gz"),
        ("transformed_brain_mask", "transformed_moving_brain_mask.nii.gz"),
        ("transformed_head_mask", "transformed_moving_head_mask.nii.gz"),
        ("native_jacobian", "jacobian_det_native_raw.nii.gz"),
    ):
        _copy_or_link(Path(reg[src_key]), profile / "registration" / dst_name)

    fixed_mask = _load_mask_xyz(Path(prepared["fixed_mask"]))
    transformed_brain = _save_npy_mask(Path(reg["transformed_brain_mask"]), profile / "transformed_moving_brain_mask.npy")
    transformed_head = _save_npy_mask(Path(reg["transformed_head_mask"]), profile / "transformed_moving_head_mask.npy")
    np.save(profile / "skull_mask.npy", transformed_brain.astype(np.uint8), allow_pickle=False)
    np.save(profile / "outer_tissue_mask.npy", transformed_head.astype(np.uint8), allow_pickle=False)
    np.save(quality / "gt_mask.npy", fixed_mask.astype(np.uint8), allow_pickle=False)
    np.save(quality / "pred_mask.npy", transformed_brain.astype(np.uint8), allow_pickle=False)

    jac, jac_info = _jacobian_to_array(
        Path(reg["native_jacobian"]),
        profile / "jacobian.npy",
        Path(prepared["fixed_image"]),
        profile / "registration" / "jacobian_det_ccf_grid.nii.gz",
    )
    fixed_lm = _load_points(Path(prepared["fixed_landmarks"]))
    moved_lm = _load_points(Path(reg["transformed_landmarks"]))
    n = min(len(fixed_lm), len(moved_lm))
    fixed_pts = _point_array(fixed_lm[:n])
    moved_pts = _point_array(moved_lm[:n])
    finite_points = np.all(np.isfinite(fixed_pts), axis=1) & np.all(np.isfinite(moved_pts), axis=1)
    if n == 0 or not np.all(finite_points):
        bad_count = int(n - np.count_nonzero(finite_points))
        raise RuntimeError(f"Invalid transformed landmark coordinates: count={n}, nonfinite={bad_count}")
    residuals = fixed_pts - moved_pts
    residual_mm = np.linalg.norm(residuals, axis=1)
    shutil.copy2(prepared["fixed_landmarks"], quality / "fixed_landmarks.csv")
    shutil.copy2(prepared["moving_landmarks"], quality / "moving_landmarks.csv")
    shutil.copy2(reg["transformed_landmarks"], quality / "transformed_moving_landmarks.csv")

    dice = _dice(transformed_brain, fixed_mask)
    hd95 = _hd95_mm(transformed_brain, fixed_mask, spacing_mm=0.025)
    tre = float(np.mean(residual_mm)) if residual_mm.size else None
    metrics = {
        "dice": float(dice),
        "hd95Mm": hd95,
        "treMm": tre,
        "jacobianAnomalyRatio": float(jac_info["anomalyRatio"]),
        "jacobianStd": float(jac_info["std"]),
    }
    stats = {
        "createdAt": _utc_now_iso(),
        "source": args.profile_id,
        "profileId": args.profile_id,
        "datasetProfile": args.profile_id,
        "annotationPath": str(profile / "annotation.npy"),
        "annotationShape": fixed_shape,
        "metrics": metrics,
        "dice": metrics["dice"],
        "hd95Mm": metrics["hd95Mm"],
        "treMm": metrics["treMm"],
        "jacobianAnomalyRatio": metrics["jacobianAnomalyRatio"],
        "jacobianStd": metrics["jacobianStd"],
        "metricEvidence": {
            "dice": "fixed_brain_mask_vs_same_transform_moving_brain_mask",
            "hd95Mm": "fixed_brain_mask_vs_same_transform_moving_brain_mask",
            "treMm": "same_transform_geometric_mask_landmarks",
            "jacobianAnomalyRatio": "CreateJacobianDeterminantImage_from_same_warp",
            "jacobianStd": "CreateJacobianDeterminantImage_from_same_warp",
        },
        "landmarkResidualSummaryMm": {
            "count": int(residual_mm.size),
            "mean": float(np.mean(residual_mm)) if residual_mm.size else None,
            "median": float(np.median(residual_mm)) if residual_mm.size else None,
            "p95": float(np.percentile(residual_mm, 95)) if residual_mm.size else None,
            "max": float(np.max(residual_mm)) if residual_mm.size else None,
        },
        "jacobianSummary": jac_info,
        "registrationSummaryPath": str(profile / "registration" / "registration_summary.json"),
        "warpedMriPath": str(profile / "registration" / "warped_mri_in_ccf.nii.gz"),
        "transformedMovingBrainMaskPath": str(profile / "registration" / "transformed_moving_brain_mask.nii.gz"),
        "transformedMovingHeadMaskPath": str(profile / "registration" / "transformed_moving_head_mask.nii.gz"),
        "jacobianPath": str(profile / "jacobian.npy"),
        "jacobianCcfGridPath": str(profile / "registration" / "jacobian_det_ccf_grid.nii.gz"),
        "jacobianNativeRawPath": str(profile / "registration" / "jacobian_det_native_raw.nii.gz"),
        "skullMaskPath": str(profile / "skull_mask.npy"),
        "outerTissueMaskPath": str(profile / "outer_tissue_mask.npy"),
        "layeredSdfPath": str(profile / "layered_sdf.npz"),
        "notes": [
            "skull_mask.npy stores transformed moving brain mask for legacy backend compatibility",
            "outer_tissue_mask.npy stores transformed moving head/foreground mask for legacy backend compatibility",
            "No mask was segmented from warped MRI as registration evidence",
        ],
    }
    stats_path = profile / "stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    reg_summary = {
        "createdAt": _utc_now_iso(),
        "fixed": str(args.fixed_image),
        "moving": str(args.moving_image),
        "fixedPrepared": prepared["fixed_image"],
        "movingPrepared": prepared["moving_image"],
        "fixedMask": prepared["fixed_mask"],
        "movingBrainMask": prepared["moving_brain_mask"],
        "movingHeadMask": prepared["moving_head_mask"],
        "registrationMovingMask": args.registration_moving_mask,
        "shapeMetricWeight": float(args.shape_metric_weight),
        "selectedMovingOrientCode": args.orient_code,
        "orientationScreen": str(args.orientation_screen) if args.orientation_screen else "",
        "registrationDirection": {"fixed": "Allen CCF", "moving": "AMBMC MRI", "transform": "moving_to_fixed"},
        "registrationConfig": {
            "movingMaskForRegistration": args.registration_moving_mask,
            "shapeMetricWeight": float(args.shape_metric_weight),
            "intensityMetricWeight": max(0.0, 1.0 - float(args.shape_metric_weight)),
            "synIterations": args.syn_iterations,
            "synShrinkFactors": args.syn_shrink_factors,
            "synSmoothingSigmas": args.syn_smoothing_sigmas,
        },
        "transforms": {
            "forwardImageTransforms": [str(profile / "registration" / "1Warp.nii.gz"), str(profile / "registration" / "0GenericAffine.mat")],
            "inverseWarp": str(profile / "registration" / "1InverseWarp.nii.gz"),
            "pointMovingToFixedTransforms": [f"[{profile / 'registration' / '0GenericAffine.mat'},1]", str(profile / "registration" / "1InverseWarp.nii.gz")],
        },
        "outputs": {
            "warpedMri": str(profile / "registration" / "warped_mri_in_ccf.nii.gz"),
            "transformedMovingBrainMask": str(profile / "registration" / "transformed_moving_brain_mask.nii.gz"),
            "transformedMovingHeadMask": str(profile / "registration" / "transformed_moving_head_mask.nii.gz"),
            "jacobian": str(profile / "jacobian.npy"),
            "jacobianCcfGrid": str(profile / "registration" / "jacobian_det_ccf_grid.nii.gz"),
            "jacobianNativeRaw": str(profile / "registration" / "jacobian_det_native_raw.nii.gz"),
            "stats": str(stats_path),
        },
        "metrics": metrics,
    }
    (profile / "registration" / "registration_summary.json").write_text(
        json.dumps(reg_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "stats": str(stats_path),
        "registration_summary": str(profile / "registration" / "registration_summary.json"),
        "jacobian": str(profile / "jacobian.npy"),
        "jacobian_ccf_grid": str(profile / "registration" / "jacobian_det_ccf_grid.nii.gz"),
        "jacobian_native_raw": str(profile / "registration" / "jacobian_det_native_raw.nii.gz"),
        "fixed_shape": fixed_shape,
    }


def _norm_slice(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not np.any(finite):
        return np.zeros_like(arr, dtype=np.float32)
    vals = arr[finite]
    lo, hi = np.percentile(vals, [1, 99])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def _edge(mask2d: np.ndarray) -> np.ndarray:
    m = np.asarray(mask2d, dtype=bool)
    if binary_erosion is None:
        return m
    return m ^ binary_erosion(m)


def generate_qc_figures(args, prepared: dict, reg: dict, profile_info: dict) -> dict:
    import matplotlib.pyplot as plt

    fig_dir = args.figure_dir
    fig_dir.mkdir(parents=True, exist_ok=True)
    fixed = sitk.GetArrayFromImage(sitk.ReadImage(prepared["fixed_image"])).astype(np.float32)  # z,y,x
    warped = sitk.GetArrayFromImage(sitk.ReadImage(reg["warped_mri"])).astype(np.float32)
    fixed_mask = sitk.GetArrayFromImage(sitk.ReadImage(prepared["fixed_mask"])) > 0
    moving_mask = sitk.GetArrayFromImage(sitk.ReadImage(reg["transformed_brain_mask"])) > 0
    moving_head = sitk.GetArrayFromImage(sitk.ReadImage(reg["transformed_head_mask"])) > 0
    jac_path = profile_info.get("jacobian_ccf_grid") or reg["native_jacobian"]
    jac = sitk.GetArrayFromImage(sitk.ReadImage(str(jac_path))).astype(np.float32)
    union = fixed_mask | moving_mask
    coords = np.argwhere(union)
    center = np.median(coords, axis=0).astype(int) if coords.size else np.asarray(fixed.shape) // 2
    zc, yc, xc = [int(x) for x in center]
    z_slices = np.linspace(max(0, zc - 120), min(fixed.shape[0] - 1, zc + 120), 5).astype(int)
    y_slices = np.linspace(max(0, yc - 90), min(fixed.shape[1] - 1, yc + 90), 5).astype(int)
    x_slices = np.linspace(max(0, xc - 140), min(fixed.shape[2] - 1, xc + 140), 5).astype(int)

    def overlay(ax, base, fmask, mmask, title, hmask=None):
        ax.imshow(_norm_slice(base), cmap="gray", origin="lower")
        fe = _edge(fmask)
        me = _edge(mmask)
        he = _edge(hmask) if hmask is not None else None
        ax.contour(fe, levels=[0.5], colors=["#21a7c4"], linewidths=0.7)
        if he is not None:
            ax.contour(he, levels=[0.5], colors=["#f2b544"], linewidths=0.55, linestyles="dashed")
        ax.contour(me, levels=[0.5], colors=["#d05b45"], linewidths=0.7)
        ax.set_title(title, fontsize=8)
        ax.set_axis_off()

    qc_multi = fig_dir / "mri_allen_ants_qc_multislice_montage.png"
    fig, axes = plt.subplots(3, 5, figsize=(12, 6), dpi=180)
    for i, x in enumerate(x_slices):
        overlay(axes[0, i], warped[:, :, x], fixed_mask[:, :, x], moving_mask[:, :, x], f"Sagittal x={x}", moving_head[:, :, x])
    for i, y in enumerate(y_slices):
        overlay(axes[1, i], warped[:, y, :], fixed_mask[:, y, :], moving_mask[:, y, :], f"Coronal y={y}", moving_head[:, y, :])
    for i, z in enumerate(z_slices):
        overlay(axes[2, i], warped[z, :, :], fixed_mask[z, :, :], moving_mask[z, :, :], f"Horizontal z={z}", moving_head[z, :, :])
    fig.suptitle("Same-transform moving brain/head edge overlay on warped MRI", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(qc_multi)
    plt.close(fig)

    qc_checker = fig_dir / "mri_allen_ants_qc_checkerboard.png"
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.2), dpi=180)
    views = [
        (fixed[:, :, xc], warped[:, :, xc], "Sagittal"),
        (fixed[:, yc, :], warped[:, yc, :], "Coronal"),
        (fixed[zc, :, :], warped[zc, :, :], "Horizontal"),
    ]
    for ax, (a, b, title) in zip(axes, views):
        aa = _norm_slice(a)
        bb = _norm_slice(b)
        tile = 24
        yy, xx = np.indices(aa.shape)
        check = ((yy // tile + xx // tile) % 2).astype(bool)
        out = np.where(check, aa, bb)
        ax.imshow(out, cmap="gray", origin="lower")
        ax.set_title(title, fontsize=8)
        ax.set_axis_off()
    fig.suptitle("Checkerboard: Allen template vs warped MRI", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(qc_checker)
    plt.close(fig)

    qc_jac = fig_dir / "mri_allen_ants_qc_jacobian_heatmap.png"
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.2), dpi=180)
    jac_views = [(jac[:, :, xc], "Sagittal"), (jac[:, yc, :], "Coronal"), (jac[zc, :, :], "Horizontal")]
    for ax, (sl, title) in zip(axes, jac_views):
        im = ax.imshow(np.clip(sl, 0.7, 1.3), cmap="coolwarm", origin="lower", vmin=0.7, vmax=1.3)
        ax.set_title(title, fontsize=8)
        ax.set_axis_off()
    fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.75, label="Jacobian det")
    fig.suptitle("Jacobian determinant heatmap", fontsize=11, fontweight="bold")
    fig.savefig(qc_jac, bbox_inches="tight")
    plt.close(fig)

    fixed_pts = _point_array(_load_points(Path(prepared["fixed_landmarks"])))
    moved_pts = _point_array(_load_points(Path(reg["transformed_landmarks"])))
    n = min(len(fixed_pts), len(moved_pts))
    fixed_pts = fixed_pts[:n]
    moved_pts = moved_pts[:n]
    finite_points = np.all(np.isfinite(fixed_pts), axis=1) & np.all(np.isfinite(moved_pts), axis=1)
    fixed_pts = fixed_pts[finite_points]
    moved_pts = moved_pts[finite_points]
    if fixed_pts.size == 0:
        raise RuntimeError("No finite transformed landmarks available for QC")
    qc_vec = fig_dir / "mri_allen_ants_qc_landmark_residual_vectors.png"
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), dpi=180)
    for ax, dims, title in (
        (axes[0], (0, 2), "AP vs ML"),
        (axes[1], (0, 1), "AP vs DV"),
    ):
        ax.scatter(fixed_pts[:, dims[0]], fixed_pts[:, dims[1]], s=8, c="#21a7c4", label="fixed")
        ax.scatter(moved_pts[:, dims[0]], moved_pts[:, dims[1]], s=8, c="#d05b45", label="warped moving")
        for p, q in zip(moved_pts[::3], fixed_pts[::3]):
            ax.plot([p[dims[0]], q[dims[0]]], [p[dims[1]], q[dims[1]]], color="0.35", lw=0.4)
        ax.set_title(title, fontsize=8)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(alpha=0.2)
    axes[0].legend(fontsize=7, loc="best")
    fig.suptitle("Same-transform landmark residual vectors", fontsize=11, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(qc_vec)
    plt.close(fig)

    stats = json.loads(Path(profile_info["stats"]).read_text(encoding="utf-8"))
    qc_final = fig_dir / "fig_mri_ccf_alignment_ants_baseline.png"
    fig = plt.figure(figsize=(10.5, 6.2), dpi=180)
    gs = fig.add_gridspec(2, 3, height_ratios=[2.2, 1.0])
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    overlay(axes[0], warped[:, :, xc], fixed_mask[:, :, xc], moving_mask[:, :, xc], f"Sagittal x={xc}", moving_head[:, :, xc])
    overlay(axes[1], warped[:, yc, :], fixed_mask[:, yc, :], moving_mask[:, yc, :], f"Coronal y={yc}", moving_head[:, yc, :])
    overlay(axes[2], warped[zc, :, :], fixed_mask[zc, :, :], moving_mask[zc, :, :], f"Horizontal z={zc}", moving_head[zc, :, :])
    axb = fig.add_subplot(gs[1, :])
    metric_vals = [
        stats["metrics"].get("dice") or 0.0,
        stats["metrics"].get("hd95Mm") or 0.0,
        stats["metrics"].get("treMm") or 0.0,
        stats["metrics"].get("jacobianAnomalyRatio") or 0.0,
    ]
    labels = ["Dice", "HD95 mm", "TRE mm", "Jac anomaly"]
    bars = axb.bar(labels, metric_vals, color=["#6fb6b2", "#b87d6a", "#c7a18e", "#777777"])
    axb.set_title("Same-run registration QC metrics", fontsize=9)
    axb.set_ylim(0.0, max(metric_vals + [1.0]) * 1.18)
    axb.grid(axis="y", alpha=0.25)
    for bar, val in zip(bars, metric_vals):
        axb.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.3f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle("MRI to Allen CCF baseline: warped MRI with same-transform mask evidence", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(qc_final)
    plt.close(fig)

    return {
        "multisliceMontage": str(qc_multi),
        "checkerboard": str(qc_checker),
        "jacobianHeatmap": str(qc_jac),
        "landmarkResidualVectors": str(qc_vec),
        "finalAlignmentFigure": str(qc_final),
    }


def build_manifest(args, prepared: dict, reg: dict, profile_info: dict, qc: dict) -> dict:
    profile = args.profile_dir
    manifest_path = profile / "artifact_manifest.json"
    fixed_shape = list(profile_info.get("fixed_shape") or _image_shape_xyz(Path(prepared["fixed_image"])))
    raw_jac_shape = _image_shape_xyz(profile / "registration" / "jacobian_det_native_raw.nii.gz")
    artifacts = {
        "annotation": _artifact_meta(profile / "annotation.npy", kind="volume", shape=fixed_shape),
        "region_map": _artifact_meta(profile / "region_map.json", kind="json"),
        "region_map_v2": _artifact_meta(profile / "region_map_v2.json", kind="json"),
        "stats": _artifact_meta(Path(profile_info["stats"]), kind="json"),
        "registration_summary": _artifact_meta(Path(profile_info["registration_summary"]), kind="json"),
        "warped_mri": _artifact_meta(profile / "registration" / "warped_mri_in_ccf.nii.gz", kind="image", shape=fixed_shape),
        "forward_affine": _artifact_meta(profile / "registration" / "0GenericAffine.mat", kind="transform"),
        "forward_warp": _artifact_meta(profile / "registration" / "1Warp.nii.gz", kind="transform"),
        "inverse_warp": _artifact_meta(profile / "registration" / "1InverseWarp.nii.gz", kind="transform"),
        "moving_mri": _artifact_meta(Path(prepared["moving_image"]), kind="image"),
        "moving_brain_mask": _artifact_meta(Path(prepared["moving_brain_mask"]), kind="mask"),
        "moving_head_mask": _artifact_meta(Path(prepared["moving_head_mask"]), kind="mask"),
        "transformed_moving_brain_mask": _artifact_meta(profile / "registration" / "transformed_moving_brain_mask.nii.gz", kind="mask", shape=fixed_shape),
        "transformed_moving_head_mask": _artifact_meta(profile / "registration" / "transformed_moving_head_mask.nii.gz", kind="mask", shape=fixed_shape),
        "transformed_moving_brain_mask_npy": _artifact_meta(profile / "transformed_moving_brain_mask.npy", kind="mask", shape=fixed_shape),
        "transformed_moving_head_mask_npy": _artifact_meta(profile / "transformed_moving_head_mask.npy", kind="mask", shape=fixed_shape),
        "fixed_landmarks": _artifact_meta(profile / "quality_eval" / "fixed_landmarks.csv", kind="landmarks"),
        "moving_landmarks": _artifact_meta(profile / "quality_eval" / "moving_landmarks.csv", kind="landmarks"),
        "transformed_moving_landmarks": _artifact_meta(profile / "quality_eval" / "transformed_moving_landmarks.csv", kind="landmarks"),
        "jacobian": _artifact_meta(profile / "jacobian.npy", kind="volume", shape=fixed_shape),
        "jacobian_det_ccf_grid": _artifact_meta(profile / "registration" / "jacobian_det_ccf_grid.nii.gz", kind="image", shape=fixed_shape),
        "native_jacobian_raw": _artifact_meta(profile / "registration" / "jacobian_det_native_raw.nii.gz", kind="image", shape=raw_jac_shape),
        "skull_mask": _artifact_meta(profile / "skull_mask.npy", kind="mask", shape=fixed_shape),
        "outer_tissue_mask": _artifact_meta(profile / "outer_tissue_mask.npy", kind="mask", shape=fixed_shape),
        "layered_sdf": _artifact_meta(profile / "layered_sdf.npz", kind="sdf", shape=fixed_shape),
    }
    for key, path_text in qc.items():
        artifacts[f"qc_{key}"] = _artifact_meta(Path(path_text), kind="qc_figure")
    missing = [k for k, v in artifacts.items() if v["required"] and not v["exists"]]
    hash_missing = [k for k, v in artifacts.items() if v["required"] and not str(v["sha256"])]
    manifest = {
        "schemaVersion": "probeplanner.mri_allen_ants.strict_manifest.v1",
        "generatedAt": _utc_now_iso(),
        "profileId": args.profile_id,
        "valid": not missing and not hash_missing,
        "validityPolicy": "all required same-run artifacts must exist and match sha256 before the profile can be treated as valid",
        "atlasShape": fixed_shape,
        "voxelSizeUm": 25,
        "orientation": {
            "fixed": "Allen CCF average_template_25 local AP,DV,ML grid",
            "movingOriginal": "AMBMC header RAS",
            "selectedMovingOrientCode": args.orient_code,
            "screenPath": str(args.orientation_screen) if args.orientation_screen else "",
        },
        "sameRunContract": {
            "fixed": str(args.fixed_image),
            "moving": str(args.moving_image),
            "imageForwardTransforms": ["forward_warp", "forward_affine"],
            "maskForwardTransforms": ["forward_warp", "forward_affine"],
            "pointMovingToFixedTransforms": ["inverse(forward_affine)", "inverse_warp"],
            "noWarpedMriDerivedMaskEvidence": True,
        },
        "artifacts": artifacts,
        "missing": missing,
        "hashMissing": hash_missing,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "valid": manifest["valid"], "missing": missing}, indent=2), flush=True)
    if not manifest["valid"]:
        raise RuntimeError(f"Strict manifest is invalid: missing={missing}, hashMissing={hash_missing}")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Build strict AMBMC MRI -> Allen CCF ANTs baseline.")
    ap.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    ap.add_argument("--ants-bin", type=Path, default=DEFAULT_ANTS_BIN)
    ap.add_argument("--fixed-image", type=Path, default=DEFAULT_FIXED)
    ap.add_argument("--fixed-annotation", type=Path, default=DEFAULT_ANN)
    ap.add_argument("--moving-image", type=Path, default=DEFAULT_MOVING)
    ap.add_argument("--moving-brain-mask", type=Path, default=DEFAULT_MOVING_MASK)
    ap.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    ap.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    ap.add_argument("--legacy-profile", type=Path, default=DEFAULT_LEGACY_PROFILE)
    ap.add_argument("--figure-dir", type=Path, default=DEFAULT_FIG_DIR)
    ap.add_argument("--profile-id", default="mri_allen_ants_shape_head_baseline")
    ap.add_argument("--orient-code", default="AIL")
    ap.add_argument("--orientation-screen", type=Path, default=workspace_root() / "baselines" / "local" / "mri_allen_ants_rsp" / "orientation_screen.json")
    ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--syn-gradient-step", type=float, default=0.035)
    ap.add_argument("--syn-update-sigma", type=float, default=4.0)
    ap.add_argument("--syn-total-sigma", type=float, default=1.0)
    ap.add_argument("--syn-iterations", default="80x50x30x15")
    ap.add_argument("--syn-shrink-factors", default="8x4x2x1")
    ap.add_argument("--syn-smoothing-sigmas", default="3x2x1x0vox")
    ap.add_argument("--registration-moving-mask", choices=("brain", "head"), default="head")
    ap.add_argument("--shape-metric-weight", type=float, default=0.35)
    ap.add_argument("--skip-registration", action="store_true")
    ap.add_argument("--prepare-only", action="store_true")
    args = ap.parse_args()

    for p in (args.fixed_image, args.fixed_annotation, args.moving_image, args.moving_brain_mask, args.ants_bin):
        if not p.exists():
            raise FileNotFoundError(str(p))
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.profile_dir.mkdir(parents=True, exist_ok=True)

    prepared = prepare_inputs(args)
    if args.prepare_only:
        print(json.dumps({"ok": True, "preparedOnly": True, "workDir": str(args.work_dir)}, indent=2), flush=True)
        return 0
    reg = run_registration(args, prepared)
    profile_info = build_profile_and_stats(args, prepared, reg)
    qc = generate_qc_figures(args, prepared, reg, profile_info)
    manifest = build_manifest(args, prepared, reg, profile_info, qc)
    print(
        json.dumps(
            {
                "ok": True,
                "profileDir": str(args.profile_dir),
                "manifest": str(args.profile_dir / "artifact_manifest.json"),
                "stats": profile_info["stats"],
                "qc": qc,
                "valid": manifest["valid"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
