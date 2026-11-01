from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import SimpleITK as sitk
from scipy.ndimage import binary_erosion, distance_transform_edt

try:
    import nibabel as nib
except Exception:  # pragma: no cover - optional raw-NIfTI fallback
    nib = None

from build_ambmc_true_outer_pipeline import (
    _binary_close_fill,
    _canonical_index_space,
    _largest_component,
    _normalize_percentile,
    _normalize_spacing_units_mm,
    _resample_to_spacing,
)
from build_mri_allen_ants_baseline import (
    _copy_or_link,
    _jacobian_to_array,
    _load_points,
    _point_array,
    _reattach_point_names,
    _write_landmarks_from_mask,
    _write_points_only_csv,
)
from build_mri_ccf_registration_candidate import _dice, _to_xyz_array
from path_defaults import ants_bin, mri_path


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _must_exist(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return path


def _run(exe: Path, args: Sequence[Any], *, env: dict[str, str], log_path: Path) -> None:
    cmd = [str(exe), *[str(a) for a in args]]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("[RUN] " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {' '.join(cmd)}")


def _intensity_summary(img: sitk.Image) -> dict[str, float]:
    arr = sitk.GetArrayViewFromImage(img).astype(np.float32)
    vals = arr[np.isfinite(arr)]
    nonzero = vals[np.abs(vals) > 1e-8]
    return {
        "min": float(np.min(vals)),
        "max": float(np.max(vals)),
        "p99": float(np.percentile(vals, 99.0)),
        "p999": float(np.percentile(vals, 99.9)),
        "nonzeroFraction": float(nonzero.size / max(1, vals.size)),
    }


def _hd95_mm_memorysafe(a: np.ndarray, b: np.ndarray, *, voxel_mm: float, max_voxels: int = 18_000_000) -> float | None:
    aa = np.asarray(a) > 0
    bb = np.asarray(b) > 0
    if int(aa.sum()) == 0 or int(bb.sum()) == 0:
        return None
    union = aa | bb
    coords = np.argwhere(union)
    if coords.size == 0:
        return None
    lo = np.maximum(coords.min(axis=0) - 4, 0)
    hi = np.minimum(coords.max(axis=0) + 5, np.asarray(aa.shape))
    slices = tuple(slice(int(lo[i]), int(hi[i])) for i in range(3))
    aa = aa[slices]
    bb = bb[slices]
    stride = 1
    while aa.size > max_voxels:
        stride += 1
        aa = aa[::stride, ::stride, ::stride]
        bb = bb[::stride, ::stride, ::stride]
        break
    spacing = float(voxel_mm) * float(stride)
    da = distance_transform_edt(~bb).astype(np.float32, copy=False) * spacing
    db = distance_transform_edt(~aa).astype(np.float32, copy=False) * spacing
    sa = aa & ~binary_erosion(aa)
    sb = bb & ~binary_erosion(bb)
    vals = np.concatenate([da[sa], db[sb]])
    if vals.size == 0:
        return None
    return float(np.percentile(vals, 95.0))


def _read_canonical(path: Path, pixel_id: int) -> sitk.Image:
    try:
        img = sitk.ReadImage(str(path))
    except RuntimeError:
        if nib is None:
            raise
        nii = nib.load(str(path))
        arr_xyz = np.asanyarray(nii.dataobj)
        dtype = np.float32 if pixel_id == sitk.sitkFloat32 else np.uint8
        arr_xyz = np.asarray(arr_xyz, dtype=dtype)
        img = sitk.GetImageFromArray(np.transpose(arr_xyz, (2, 1, 0)))
        zooms = tuple(float(v) for v in nii.header.get_zooms()[:3])
        img.SetSpacing(zooms)
    img = _normalize_spacing_units_mm(sitk.Cast(img, pixel_id))
    identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    if all(abs(float(v)) < 1e-9 for v in img.GetOrigin()) and all(
        abs(float(a) - float(b)) < 1e-9 for a, b in zip(img.GetDirection(), identity, strict=True)
    ):
        return img
    return _canonical_index_space(img)


def _parse_axis_order(text: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Expected three comma-separated axis indices, got {text!r}")
    order = tuple(int(p) for p in parts)
    if sorted(order) != [0, 1, 2]:
        raise ValueError(f"Axis order must be a permutation of 0,1,2; got {text!r}")
    return order


def _parse_float_triplet(text: str, *, default: tuple[float, float, float]) -> tuple[float, float, float]:
    raw = str(text or "").strip()
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) == 1:
        value = float(parts[0])
        return value, value, value
    if len(parts) != 3:
        raise ValueError(f"Expected one value or three comma-separated values, got {text!r}")
    return float(parts[0]), float(parts[1]), float(parts[2])


def _rotation_matrix_xyz(rotation_deg: tuple[float, float, float]) -> np.ndarray:
    rx, ry, rz = [np.deg2rad(v) for v in rotation_deg]
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    rot_x = np.asarray([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    rot_y = np.asarray([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    rot_z = np.asarray([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return rot_z @ rot_y @ rot_x


def _apply_pre_affine(
    img: sitk.Image,
    *,
    rotation_deg: tuple[float, float, float],
    scale: tuple[float, float, float],
    translation_mm: tuple[float, float, float],
    is_label: bool,
    pixel_id: int,
) -> sitk.Image:
    if all(abs(v) < 1e-9 for v in rotation_deg) and all(abs(v - 1.0) < 1e-9 for v in scale) and all(
        abs(v) < 1e-9 for v in translation_mm
    ):
        return sitk.Cast(img, pixel_id)
    center_idx = [0.5 * (float(size) - 1.0) for size in img.GetSize()]
    center = img.TransformContinuousIndexToPhysicalPoint(center_idx)
    matrix = _rotation_matrix_xyz(rotation_deg) @ np.diag(np.asarray(scale, dtype=np.float64))
    tx = sitk.AffineTransform(3)
    tx.SetCenter(tuple(float(v) for v in center))
    tx.SetMatrix(tuple(float(v) for v in matrix.reshape(-1)))
    tx.SetTranslation(tuple(float(v) for v in translation_mm))
    return sitk.Resample(
        img,
        img,
        tx.GetInverse(),
        sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear,
        0,
        pixel_id,
    )


def _permute_xyz_image(
    img: sitk.Image,
    order: tuple[int, int, int],
    *,
    reference: sitk.Image | None,
    is_label: bool,
    pixel_id: int,
) -> sitk.Image:
    if order == (0, 1, 2):
        return sitk.Cast(img, pixel_id)
    arr_xyz = np.transpose(sitk.GetArrayFromImage(img), (2, 1, 0))
    arr_xyz = np.transpose(arr_xyz, order)
    out = sitk.GetImageFromArray(np.transpose(arr_xyz, (2, 1, 0)))
    spacing = tuple(float(img.GetSpacing()[axis]) for axis in order)
    out.SetSpacing(spacing)
    out.SetOrigin((0.0, 0.0, 0.0))
    out.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    out = sitk.Cast(out, pixel_id)
    if reference is not None:
        out = sitk.Resample(
            out,
            reference,
            sitk.Transform(),
            sitk.sitkNearestNeighbor if is_label else sitk.sitkLinear,
            0,
            pixel_id,
        )
    return out


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        _copy_or_link(src, dst)


def main() -> int:
    default_ambmc = mri_path(
        "raw/ambmc/ambmc-c57bl6-model-symmet_v0.8_extracted/"
        "ambmc-c57bl6-model-symmet_v0.8-nii/ambmc-c57bl6-model-symmet_v0.8.nii"
    )
    ap = argparse.ArgumentParser(description="Run a memory-safe native ANTs AMBMC-to-CCF registration candidate.")
    ap.add_argument("--ambmc-image", default=str(default_ambmc))
    ap.add_argument("--moving-brain-mask", default=str(mri_path("ants_syn/ambmc_to_ccf/moving_masks/ambmc_moving_brain_mask_clean.nii.gz")))
    ap.add_argument("--moving-mask-npy", default="", help="Optional xyz-order .npy moving mask for prealigned CCF-space inputs.")
    ap.add_argument("--auto-moving-mask", action="store_true", help="Ignore --moving-brain-mask and derive the moving mask by Otsu segmentation.")
    ap.add_argument("--ccf-template", default=str(mri_path("raw/allen_ccf/average_template_25.nrrd")))
    ap.add_argument("--ccf-annotation", default=str(mri_path("raw/allen_ccf/CCFv3_annotation_25.nrrd")))
    ap.add_argument("--legacy-profile", default=str(mri_path("planner_artifacts/ambmc_true_outer_full")))
    ap.add_argument("--ants-bin", default=str(ants_bin()))
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--dataset-profile", required=True)
    ap.add_argument("--registration-spacing-mm", type=float, default=0.10)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--syn-gradient-step", type=float, default=0.02)
    ap.add_argument("--syn-update-sigma", type=float, default=4.0)
    ap.add_argument("--syn-total-sigma", type=float, default=1.0)
    ap.add_argument("--syn-iterations", default="60x40x20x10")
    ap.add_argument("--shape-weight", type=float, default=0.20)
    ap.add_argument("--initial-transform-source", choices=("mask", "image"), default="mask")
    ap.add_argument("--prealigned-moving", action="store_true", help="Treat --ambmc-image as already aligned to the CCF grid.")
    ap.add_argument("--syn-only", action="store_true", help="Skip ANTs rigid/affine stages and estimate only SyN deformation.")
    ap.add_argument("--skip-syn", action="store_true", help="Run only rigid/affine stages; useful for orientation/scale audits.")
    ap.add_argument("--no-registration-masks", action="store_true", help="Do not pass fixed/moving masks to antsRegistration metrics.")
    ap.add_argument("--write-affine-dummy-jacobian", action="store_true", help="Write a full-size all-ones Jacobian for affine-only render compatibility.")
    ap.add_argument(
        "--moving-xyz-axis-order",
        default="0,1,2",
        help="Optional moving-image xyz axis permutation before registration. Example 1,2,0 maps old C/A/B panels to new A/B/C.",
    )
    ap.add_argument("--moving-pre-rotation-deg", default="0,0,0", help="Pre-registration object rotation about x,y,z axes, in degrees.")
    ap.add_argument("--moving-pre-scale", default="1,1,1", help="Pre-registration object scale. One value or x,y,z triplet.")
    ap.add_argument("--moving-pre-translation-mm", default="0,0,0", help="Pre-registration object translation x,y,z in mm.")
    ap.add_argument("--skip-registration", action="store_true")
    args = ap.parse_args()
    moving_axis_order = _parse_axis_order(args.moving_xyz_axis_order)
    moving_pre_rotation_deg = _parse_float_triplet(args.moving_pre_rotation_deg, default=(0.0, 0.0, 0.0))
    moving_pre_scale = _parse_float_triplet(args.moving_pre_scale, default=(1.0, 1.0, 1.0))
    moving_pre_translation_mm = _parse_float_triplet(args.moving_pre_translation_mm, default=(0.0, 0.0, 0.0))
    if args.skip_syn and args.syn_only:
        raise ValueError("--skip-syn and --syn-only cannot be combined")

    out = Path(args.output_dir).expanduser().resolve()
    prep = out / "prepared"
    reg = out / "registration"
    quality = out / "quality_eval"
    logs = out / "logs"
    for folder in (prep, reg, quality, logs):
        folder.mkdir(parents=True, exist_ok=True)

    ants_dir = _must_exist(Path(args.ants_bin).expanduser().resolve(), "ants-bin")
    ants_registration = _must_exist(ants_dir / "antsRegistration.exe", "antsRegistration.exe")
    ants_apply = _must_exist(ants_dir / "antsApplyTransforms.exe", "antsApplyTransforms.exe")
    ants_points = _must_exist(ants_dir / "antsApplyTransformsToPoints.exe", "antsApplyTransformsToPoints.exe")
    ants_jacobian = _must_exist(ants_dir / "CreateJacobianDeterminantImage.exe", "CreateJacobianDeterminantImage.exe")

    ambmc = _must_exist(Path(args.ambmc_image).expanduser().resolve(), "ambmc-image")
    moving_mask_arg = "" if args.auto_moving_mask else str(args.moving_brain_mask or "").strip()
    moving_mask_path = Path(moving_mask_arg).expanduser().resolve() if moving_mask_arg else None
    moving_mask_npy = Path(args.moving_mask_npy).expanduser().resolve() if str(args.moving_mask_npy or "").strip() else None
    ccf_template = _must_exist(Path(args.ccf_template).expanduser().resolve(), "ccf-template")
    ccf_annotation = _must_exist(Path(args.ccf_annotation).expanduser().resolve(), "ccf-annotation")
    legacy = Path(args.legacy_profile).expanduser().resolve()

    fixed_full = _read_canonical(ccf_template, sitk.sitkFloat32)
    ann_full = _read_canonical(ccf_annotation, sitk.sitkUInt32)
    ann_full = sitk.Resample(ann_full, fixed_full, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk.sitkUInt32)
    fixed_mask_full = sitk.Cast(ann_full > 0, sitk.sitkUInt8)
    moving_full = _read_canonical(ambmc, sitk.sitkFloat32)
    if args.prealigned_moving:
        moving_full = sitk.Resample(moving_full, fixed_full, sitk.Transform(), sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    moving_full_before_axis = sitk.Image(moving_full)
    if moving_axis_order != (0, 1, 2):
        moving_full = _permute_xyz_image(
            moving_full,
            moving_axis_order,
            reference=fixed_full if args.prealigned_moving else None,
            is_label=False,
            pixel_id=sitk.sitkFloat32,
        )
    moving_mask_full = None
    if moving_mask_npy is not None and moving_mask_npy.exists():
        mask_xyz = np.load(moving_mask_npy, mmap_mode="r")
        moving_mask_full = sitk.GetImageFromArray(np.transpose(np.asarray(mask_xyz > 0, dtype=np.uint8), (2, 1, 0)))
        moving_mask_full.CopyInformation(fixed_full if args.prealigned_moving else moving_full_before_axis)
    elif moving_mask_path is not None and moving_mask_path.exists():
        moving_mask_full = _read_canonical(moving_mask_path, sitk.sitkUInt8)
        moving_mask_full = sitk.Resample(
            moving_mask_full,
            moving_full,
            sitk.Transform(),
            sitk.sitkNearestNeighbor,
            0,
            sitk.sitkUInt8,
        )
        moving_mask_full = sitk.Cast(moving_mask_full > 0, sitk.sitkUInt8)
    if moving_mask_full is not None and moving_axis_order != (0, 1, 2):
        moving_mask_full = _permute_xyz_image(
            moving_mask_full,
            moving_axis_order,
            reference=fixed_full if args.prealigned_moving else moving_full,
            is_label=True,
            pixel_id=sitk.sitkUInt8,
        )
    moving_full = _apply_pre_affine(
        moving_full,
        rotation_deg=moving_pre_rotation_deg,
        scale=moving_pre_scale,
        translation_mm=moving_pre_translation_mm,
        is_label=False,
        pixel_id=sitk.sitkFloat32,
    )
    if moving_mask_full is not None:
        moving_mask_full = _apply_pre_affine(
            moving_mask_full,
            rotation_deg=moving_pre_rotation_deg,
            scale=moving_pre_scale,
            translation_mm=moving_pre_translation_mm,
            is_label=True,
            pixel_id=sitk.sitkUInt8,
        )
    if moving_mask_full is None:
        moving_mask_full = _binary_close_fill(
            _largest_component(sitk.OtsuThreshold(_normalize_percentile(moving_full), 0, 1, 200)),
            radius=1,
        )

    spacing = (float(args.registration_spacing_mm),) * 3
    fixed_reg = _normalize_percentile(_resample_to_spacing(fixed_full, spacing, is_label=False))
    moving_reg = _normalize_percentile(_resample_to_spacing(moving_full, spacing, is_label=False))
    fixed_mask_reg = sitk.Cast(_resample_to_spacing(fixed_mask_full, spacing, is_label=True) > 0, sitk.sitkUInt8)
    moving_mask_reg = sitk.Cast(_resample_to_spacing(moving_mask_full, spacing, is_label=True) > 0, sitk.sitkUInt8)

    fixed_ref = prep / "fixed_ccf_template_full_mm.nii.gz"
    fixed_reg_path = prep / "fixed_ccf_template_reg_mm.nii.gz"
    moving_full_path = prep / "moving_ambmc_full_mm.nii.gz"
    moving_reg_path = prep / "moving_ambmc_reg_mm.nii.gz"
    fixed_mask_full_path = prep / "fixed_brain_mask_full.nii.gz"
    moving_mask_full_path = prep / "moving_brain_mask_full.nii.gz"
    fixed_mask_reg_path = prep / "fixed_brain_mask_reg.nii.gz"
    moving_mask_reg_path = prep / "moving_brain_mask_reg.nii.gz"
    fixed_landmarks_path = quality / "fixed_landmarks.csv"
    moving_landmarks_path = quality / "moving_landmarks.csv"

    sitk.WriteImage(sitk.Cast(fixed_full, sitk.sitkFloat32), str(fixed_ref), True)
    sitk.WriteImage(sitk.Cast(moving_full, sitk.sitkFloat32), str(moving_full_path), True)
    sitk.WriteImage(fixed_reg, str(fixed_reg_path), True)
    sitk.WriteImage(moving_reg, str(moving_reg_path), True)
    sitk.WriteImage(fixed_mask_full, str(fixed_mask_full_path), True)
    sitk.WriteImage(moving_mask_full, str(moving_mask_full_path), True)
    sitk.WriteImage(fixed_mask_reg, str(fixed_mask_reg_path), True)
    sitk.WriteImage(moving_mask_reg, str(moving_mask_reg_path), True)
    _write_landmarks_from_mask(fixed_mask_full, fixed_landmarks_path, prefix="lm")
    _write_landmarks_from_mask(moving_mask_full, moving_landmarks_path, prefix="lm")

    del moving_full
    if moving_mask_full is not None:
        del moving_mask_full
    gc.collect()

    env = dict(os.environ)
    env["ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS"] = str(int(args.threads))
    env["ANTSPATH"] = str(ants_dir) + os.sep
    env["PATH"] = str(ants_dir) + os.pathsep + env.get("PATH", "")

    prefix = reg / "ambmc_to_ccf_"
    affine = Path(str(prefix) + "0GenericAffine.mat")
    warp = Path(str(prefix) + "1Warp.nii.gz")
    inverse_warp = Path(str(prefix) + "1InverseWarp.nii.gz")
    intensity_weight = max(0.0, 1.0 - float(args.shape_weight))
    shape_weight = max(0.0, float(args.shape_weight))

    def metrics(kind: str) -> list[str]:
        if kind == "cc":
            items = ["--metric", f"CC[{fixed_reg_path},{moving_reg_path},{intensity_weight},4]"]
        else:
            items = ["--metric", f"MI[{fixed_reg_path},{moving_reg_path},{intensity_weight},32,Regular,0.25]"]
        if shape_weight > 0.0:
            items.extend(["--metric", f"MeanSquares[{fixed_mask_reg_path},{moving_mask_reg_path},{shape_weight},0]"])
        return items

    initial_fixed = fixed_mask_reg_path if args.initial_transform_source == "mask" else fixed_reg_path
    initial_moving = moving_mask_reg_path if args.initial_transform_source == "mask" else moving_reg_path

    if not args.skip_registration:
        syn_spec = f"SyN[{args.syn_gradient_step},{args.syn_update_sigma},{args.syn_total_sigma}]"
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
        ]
        if not args.no_registration_masks:
            reg_args.extend(["--masks", f"[{fixed_mask_reg_path},{moving_mask_reg_path}]"])
        if not args.prealigned_moving:
            reg_args.extend(["--initial-moving-transform", f"[{initial_fixed},{initial_moving},1]"])
        if not args.syn_only:
            reg_args.extend(
                [
                    "--transform",
                    "Rigid[0.1]",
                    *metrics("mi"),
                    "--convergence",
                    "[600x300x150x60,1e-6,10]",
                    "--shrink-factors",
                    "8x4x2x1",
                    "--smoothing-sigmas",
                    "3x2x1x0vox",
                    "--transform",
                    "Affine[0.1]",
                    *metrics("mi"),
                    "--convergence",
                    "[600x300x150x60,1e-6,10]",
                    "--shrink-factors",
                    "8x4x2x1",
                    "--smoothing-sigmas",
                    "3x2x1x0vox",
                ]
            )
        if not args.skip_syn:
            reg_args.extend(
                [
                    "--transform",
                    syn_spec,
                    *metrics("cc"),
                    "--convergence",
                    f"[{args.syn_iterations},1e-6,10]",
                    "--shrink-factors",
                    "8x4x2x1",
                    "--smoothing-sigmas",
                    "3x2x1x0vox",
                ]
            )
        reg_args.extend(["--verbose", "1"])
        _run(ants_registration, reg_args, env=env, log_path=logs / "ants_registration.log")

    if not args.skip_syn and not warp.exists():
        candidates = sorted(reg.glob("ambmc_to_ccf_*Warp.nii.gz"))
        candidates = [p for p in candidates if "InverseWarp" not in p.name]
        if candidates:
            warp = candidates[0]
    if not args.skip_syn and not inverse_warp.exists():
        candidates = sorted(reg.glob("ambmc_to_ccf_*InverseWarp.nii.gz"))
        if candidates:
            inverse_warp = candidates[0]
    affine_exists = affine.exists()
    if args.skip_syn:
        warp = None
        inverse_warp = None
        _must_exist(affine, affine.name)
    else:
        _must_exist(warp, warp.name)
        _must_exist(inverse_warp, inverse_warp.name)

    forward: list[str] = []
    if warp is not None:
        forward.extend(["-t", str(warp)])
    if affine_exists:
        forward.extend(["-t", str(affine)])
    warped_mri = reg / "ambmc_in_ccf.nii.gz"
    transformed_mask = reg / "transformed_moving_brain_mask.nii.gz"
    _run(
        ants_apply,
        ["-d", "3", "-i", str(moving_full_path), "-r", str(fixed_ref), "-o", str(warped_mri), "-n", "Linear", *forward],
        env=env,
        log_path=logs / "ants_apply.log",
    )
    _run(
        ants_apply,
        ["-d", "3", "-i", str(moving_mask_full_path), "-r", str(fixed_ref), "-o", str(transformed_mask), "-n", "NearestNeighbor", *forward],
        env=env,
        log_path=logs / "ants_apply.log",
    )

    native_jac = reg / "jacobian_det_native_raw.nii.gz"
    if warp is not None:
        _run(ants_jacobian, ["3", str(warp), str(native_jac), "0", "0"], env=env, log_path=logs / "jacobian.log")

    points_in = reg / "moving_landmarks_points_only.csv"
    points_tmp = reg / "transformed_moving_landmarks_points_only.csv"
    transformed_landmarks_path = quality / "warped_landmarks.csv"
    source_landmarks = _write_points_only_csv(moving_landmarks_path, points_in)
    point_args = ["-d", "3", "-i", str(points_in), "-o", str(points_tmp)]
    if affine_exists:
        point_args.extend(["-t", f"[{affine},1]"])
    if inverse_warp is not None:
        point_args.extend(["-t", str(inverse_warp)])
    _run(ants_points, point_args, env=env, log_path=logs / "ants_points.log")
    _reattach_point_names(source_landmarks, points_tmp, transformed_landmarks_path)

    gt_mask = _to_xyz_array(fixed_mask_full, np.uint8)
    pred_mask = _to_xyz_array(sitk.ReadImage(str(transformed_mask)), np.uint8)
    np.save(quality / "gt_mask.npy", gt_mask, allow_pickle=False)
    np.save(quality / "pred_mask.npy", pred_mask, allow_pickle=False)
    _copy_if_exists(legacy / "annotation.npy", out / "annotation.npy")
    _copy_if_exists(legacy / "region_map.json", out / "region_map.json")
    _copy_if_exists(legacy / "region_map_v2.json", out / "region_map_v2.json")

    if warp is not None:
        jac, jac_info = _jacobian_to_array(native_jac, out / "jacobian.npy", fixed_ref, reg / "jacobian_det_ccf_grid.nii.gz")
    else:
        jac_path = out / "jacobian.npy"
        if args.write_affine_dummy_jacobian:
            jac = np.ones_like(gt_mask, dtype=np.float32)
            np.save(jac_path, jac, allow_pickle=False)
        jac_info = {
            "path": str(jac_path) if args.write_affine_dummy_jacobian else None,
            "nativeRawPath": None,
            "ccfGridPath": None,
            "shape": [int(v) for v in gt_mask.shape],
            "dtype": "float32",
            "resampledToReferenceGrid": False,
            "referenceImage": str(fixed_ref),
            "min": 1.0,
            "max": 1.0,
            "mean": 1.0,
            "std": 0.0,
            "anomalyRatio": 0.0,
            "fractionLt0": 0.0,
            "fractionLt02": 0.0,
            "fractionGt5": 0.0,
            "realJacobianEvidence": False,
            "note": "Affine-only candidate; no deformation-field Jacobian was generated.",
        }
    fixed_pts = _point_array(_load_points(fixed_landmarks_path))
    warped_pts = _point_array(_load_points(transformed_landmarks_path))
    n = min(len(fixed_pts), len(warped_pts))
    residual_mm = np.linalg.norm(fixed_pts[:n] - warped_pts[:n], axis=1)
    mri_img = sitk.ReadImage(str(warped_mri), sitk.sitkFloat32)
    metrics_out = {
        "dice": _dice(pred_mask, gt_mask),
        "hd95Mm": _hd95_mm_memorysafe(pred_mask, gt_mask, voxel_mm=0.025),
        "treMm": float(np.mean(residual_mm)) if residual_mm.size else None,
        "jacobianAnomalyRatio": float(jac_info["anomalyRatio"]),
        "jacobianStd": float(jac_info["std"]),
    }
    intensity = _intensity_summary(mri_img)
    gates = {
        "dicePass": bool(metrics_out["dice"] >= 0.8),
        "hd95Pass": bool(metrics_out["hd95Mm"] is not None and metrics_out["hd95Mm"] <= 0.45),
        "trePass": bool(metrics_out["treMm"] is not None and metrics_out["treMm"] <= 0.3),
        "jacobianPass": bool(metrics_out["jacobianAnomalyRatio"] <= 0.03),
        "nonzeroIntensityPass": bool(intensity["nonzeroFraction"] >= 0.03 and intensity["p99"] > 0.0),
    }
    gates["numericPass"] = bool(all(gates.values()))

    stats = {
        "createdAt": _utc(),
        "source": "run_native_ants_registration_candidate",
        "datasetProfile": str(args.dataset_profile),
        "annotationShape": [int(x) for x in gt_mask.shape],
        "voxelSpacingMm": [0.025, 0.025, 0.025],
        "registrationSpacingMm": float(args.registration_spacing_mm),
        "metrics": metrics_out,
        "dice": metrics_out["dice"],
        "hd95Mm": metrics_out["hd95Mm"],
        "treMm": metrics_out["treMm"],
        "jacobianAnomalyRatio": metrics_out["jacobianAnomalyRatio"],
        "jacobianStd": metrics_out["jacobianStd"],
        "intensity": intensity,
        "gates": gates,
        "controlPointResidual": {
            "type": "automatic mask bounding-box control points",
            "manualAnatomicalLandmarks": False,
            "validAsAnatomicalTre": False,
            "note": "These points are generated from fixed/moving mask bounding boxes and must not be interpreted as manually curated anatomical landmarks.",
        },
        "registration": {
            "method": "native ANTs antsRegistration Rigid+Affine+SyN on downsampled registration grid",
            "fixed": str(ccf_template),
            "moving": str(ambmc),
            "movingBrainMask": str(moving_mask_path) if moving_mask_path is not None and moving_mask_path.exists() else None,
            "antsBin": str(ants_dir),
            "threads": int(args.threads),
            "shapeWeight": float(args.shape_weight),
            "intensityWeight": float(intensity_weight),
            "initialTransformSource": str(args.initial_transform_source),
            "prealignedMoving": bool(args.prealigned_moving),
            "movingXyzAxisOrder": [int(v) for v in moving_axis_order],
            "movingPreRotationDeg": [float(v) for v in moving_pre_rotation_deg],
            "movingPreScale": [float(v) for v in moving_pre_scale],
            "movingPreTranslationMm": [float(v) for v in moving_pre_translation_mm],
            "finalApplyInput": "full-resolution moving image and full-resolution moving mask",
            "registrationMasksEnabled": not bool(args.no_registration_masks),
            "synOnly": bool(args.syn_only),
            "skipSyn": bool(args.skip_syn),
            "synGradientStep": float(args.syn_gradient_step),
            "synUpdateSigma": float(args.syn_update_sigma),
            "synTotalSigma": float(args.syn_total_sigma),
            "synIterations": str(args.syn_iterations),
            "imageForwardTransforms": [*([str(warp)] if warp is not None else []), *([str(affine)] if affine_exists else [])],
            "pointMovingToFixedTransforms": (
                [*([f"[{affine},1]"] if affine_exists else []), *([str(inverse_warp)] if inverse_warp is not None else [])]
            ),
        },
        "jacobianSummary": jac_info,
        "landmarkResidualSummaryMm": {
            "count": int(residual_mm.size),
            "mean": float(np.mean(residual_mm)) if residual_mm.size else None,
            "median": float(np.median(residual_mm)) if residual_mm.size else None,
            "p95": float(np.percentile(residual_mm, 95.0)) if residual_mm.size else None,
            "max": float(np.max(residual_mm)) if residual_mm.size else None,
        },
        "paths": {
            "mriInCcf": str(warped_mri),
            "movingFull": str(moving_full_path),
            "movingMaskFull": str(moving_mask_full_path),
            "gtMask": str(quality / "gt_mask.npy"),
            "predMask": str(quality / "pred_mask.npy"),
            "fixedLandmarks": str(fixed_landmarks_path),
            "warpedLandmarks": str(transformed_landmarks_path),
            "jacobian": str(out / "jacobian.npy") if (warp is not None or args.write_affine_dummy_jacobian) else None,
            "warp": str(warp) if warp is not None else None,
            "affine": str(affine) if affine_exists else None,
            "inverseWarp": str(inverse_warp) if inverse_warp is not None else None,
        },
    }
    _write_json(reg / "registration_summary.json", stats)
    _write_json(out / "stats.json", stats)
    print(json.dumps({"ok": True, "outputDir": str(out), "metrics": metrics_out, "gates": gates}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
