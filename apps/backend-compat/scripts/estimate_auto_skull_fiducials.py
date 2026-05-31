#!/usr/bin/env python
"""Estimate native-space bregma/lambda surrogate fiducials.

Deprecated for fully automatic planning acceptance: a single projected
surrogate is not enough. Use `build_public_atlas_prior.py` with multiple public
MRI+CT/skull-derived atlas samples and leave-one-out TRE validation, then feed
`uncertaintyCalibration.riskMarginTreMm` into planning.

This script deliberately separates three things:

1. The current subject/native MRI and Allen CCF grid dimensions.
2. Downloaded public atlas provenance that may help later skull bridging.
3. A low-confidence automatic surrogate using Allen/IBL CCF bregma plus a
   lambda AP prior, transformed with the same CCF->native chain and projected
   onto the native head surface.

The output is not a replacement for subject-specific micro-CT or skull photos.
It is a reproducible placeholder so the planning stack can carry explicit
fiducials, confidence, and provenance instead of silently guessing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from path_defaults import ants_bin, backend_root, mri_path, workspace_root
from typing import Iterable, Sequence

import numpy as np
import SimpleITK as sitk


PROJECT_ROOT = workspace_root()
DEFAULT_NATIVE_PROFILE = mri_path("planner_artifacts/native_mri_planning_shape_head")
DEFAULT_CCF_PROFILE = mri_path("ants_syn/mri_allen_ants_shape_head_baseline")
DEFAULT_ANTS_BIN = (
    PROJECT_ROOT
    / "tools"
    / "ants"
    / "ants-2.6.5-windows-2022-X64-VS2019"
    / "ants-2.6.5"
    / "bin"
)
DEFAULT_FIGURE = PROJECT_ROOT / "output" / "pdf" / "figures" / "fig_auto_skull_fiducials_native.png"
DEFAULT_EXTERNAL_ROOT = mri_path("external")
IBL_ATLAS_RAW = "https://raw.githubusercontent.com/int-brain-lab/iblatlas/main/iblatlas/atlas.py"


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


def image_meta(path: Path) -> dict:
    img = sitk.ReadImage(str(path))
    size = [int(v) for v in img.GetSize()]
    spacing = [float(v) for v in img.GetSpacing()]
    return {
        "path": str(path),
        "size_xyz": size,
        "spacing_mm": spacing,
        "physical_extent_mm": [float(size[i] * spacing[i]) for i in range(3)],
        "origin": [float(v) for v in img.GetOrigin()],
        "direction": [float(v) for v in img.GetDirection()],
        "sha256": sha256_file(path),
    }


def artifact(path: Path, kind: str, required: bool = True) -> dict:
    return {
        "path": str(path),
        "kind": kind,
        "required": bool(required),
        "exists": path.exists(),
        "sha256": sha256_file(path) if path.exists() else "",
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def download_text(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        out_path.write_bytes(response.read())


def parse_ibl_bregma_mlapdv_um(path: Path) -> list[float]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"ALLEN_CCF_LANDMARKS_MLAPDV_UM\s*=\s*\{[^}]*'bregma'\s*:\s*np\.array\(\[([^\]]+)\]\)",
        text,
        flags=re.S,
    )
    if not match:
        raise RuntimeError(f"Could not parse IBL Allen bregma from {path}")
    vals = [float(v.strip()) for v in match.group(1).split(",")]
    if len(vals) != 3:
        raise RuntimeError(f"Unexpected IBL bregma triplet: {vals}")
    return vals


def write_points_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["x", "y", "z", "label"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "x": float(row["x"]),
                    "y": float(row["y"]),
                    "z": float(row["z"]),
                    "label": str(row["label"]),
                }
            )


def read_points_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def run(exe: Path, args: Sequence[str], *, env: dict, log: Path) -> None:
    cmd = [str(exe), *[str(a) for a in args]]
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


def ccf_points_from_ibl(args, ccf_img: sitk.Image, ibl_mlapdv_um: Sequence[float]) -> list[dict]:
    ml_um, ap_um, dv_um = [float(v) for v in ibl_mlapdv_um]
    lambda_ap_um = ap_um + float(args.lambda_posterior_mm) * 1000.0
    z_mid = ml_um / 1000.0
    points = [
        {
            "label": "bregma_ccf_ibl_prior",
            "x": ap_um / 1000.0,
            "y": dv_um / 1000.0,
            "z": z_mid,
        },
        {
            "label": "lambda_ccf_ap_prior",
            "x": lambda_ap_um / 1000.0,
            "y": dv_um / 1000.0,
            "z": z_mid,
        },
    ]
    size = ccf_img.GetSize()
    spacing = ccf_img.GetSpacing()
    extent = [size[i] * spacing[i] for i in range(3)]
    for p in points:
        inside = all(0.0 <= float(p[ax]) <= extent[i] for i, ax in enumerate(("x", "y", "z")))
        if not inside:
            raise RuntimeError(f"CCF prior point is outside local CCF extent: {p}, extent={extent}")
    return points


def apply_ccf_to_native_points(args, paths: dict, ccf_points: list[dict], env: dict) -> tuple[Path, Path, list[dict]]:
    apply_pts = args.ants_bin / "antsApplyTransformsToPoints.exe"
    src = args.work_dir / "auto_fiducials_ccf_points.csv"
    dst = args.work_dir / "auto_fiducials_native_raw_points.csv"
    write_points_csv(src, ccf_points)
    run(
        apply_pts,
        [
            "-d",
            "3",
            "-i",
            str(src),
            "-o",
            str(dst),
            "-t",
            str(paths["warp"]),
            "-t",
            str(paths["affine"]),
        ],
        env=env,
        log=args.work_dir / "logs" / "auto_fiducials_points.log",
    )
    native_rows = read_points_csv(dst)
    for idx, row in enumerate(native_rows):
        if idx < len(ccf_points):
            row["label"] = ccf_points[idx]["label"]
    return src, dst, native_rows


def load_mask_xyz(path: Path, fallback_image: Path) -> np.ndarray:
    if path.exists():
        return np.load(path, mmap_mode="r")
    img = sitk.ReadImage(str(fallback_image))
    return np.transpose((sitk.GetArrayFromImage(img) > 0).astype(np.uint8), (2, 1, 0))


def continuous_index(img: sitk.Image, point: Sequence[float]) -> np.ndarray:
    return np.asarray(img.TransformPhysicalPointToContinuousIndex(tuple(float(v) for v in point)), dtype=np.float64)


def index_to_physical(img: sitk.Image, idx_xyz: Sequence[float]) -> np.ndarray:
    return np.asarray(img.TransformContinuousIndexToPhysicalPoint(tuple(float(v) for v in idx_xyz)), dtype=np.float64)


def project_to_dorsal_surface(
    img: sitk.Image,
    head_xyz: np.ndarray,
    point_mm: Sequence[float],
    *,
    max_radius_vox: int,
) -> dict:
    idx = continuous_index(img, point_mm)
    shape = np.asarray(head_xyz.shape, dtype=int)
    x0 = int(round(idx[0]))
    z0 = int(round(idx[2]))
    best = None
    for radius in range(0, int(max_radius_vox) + 1, 5):
        x_min = max(0, x0 - radius)
        x_max = min(shape[0] - 1, x0 + radius)
        z_min = max(0, z0 - radius)
        z_max = min(shape[2] - 1, z0 + radius)
        candidates = []
        for x in range(x_min, x_max + 1):
            for z in range(z_min, z_max + 1):
                col = np.asarray(head_xyz[x, :, z]) > 0
                if not np.any(col):
                    continue
                y = int(np.flatnonzero(col)[0])
                dist_xz = float(np.hypot(x - idx[0], z - idx[2]))
                candidates.append((dist_xz, x, y, z))
        if candidates:
            best = min(candidates, key=lambda r: r[0])
            break
    if best is None:
        raise RuntimeError(f"Could not project point to native head surface: {point_mm}")
    _, x, y, z = best
    surface_idx = np.asarray([x, y, z], dtype=np.float64)
    surface_mm = index_to_physical(img, surface_idx)
    raw_mm = np.asarray(point_mm, dtype=np.float64)
    return {
        "surface_index_xyz": [float(v) for v in surface_idx],
        "surface_mm_xyz": [float(v) for v in surface_mm],
        "raw_index_xyz": [float(v) for v in idx],
        "raw_mm_xyz": [float(v) for v in raw_mm],
        "projection_distance_mm": float(np.linalg.norm(surface_mm - raw_mm)),
    }


def surface_support_at_index(
    img: sitk.Image,
    head_xyz: np.ndarray,
    x: float,
    z: float,
    label: str,
    max_radius_vox: int,
) -> dict:
    point = index_to_physical(img, [float(x), 0.0, float(z)])
    rec = project_to_dorsal_surface(img, head_xyz, point, max_radius_vox=max_radius_vox)
    rec["label"] = label
    return rec


def unit(v: np.ndarray, name: str) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n <= 1e-9:
        raise RuntimeError(f"Cannot normalize zero vector for {name}")
    return v / n


def build_flat_skull_transform(fiducials: dict) -> dict:
    b = np.asarray(fiducials["bregma"]["surface_mm_xyz"], dtype=np.float64)
    l = np.asarray(fiducials["lambda"]["surface_mm_xyz"], dtype=np.float64)
    left = np.asarray(fiducials["left_support"]["surface_mm_xyz"], dtype=np.float64)
    right = np.asarray(fiducials["right_support"]["surface_mm_xyz"], dtype=np.float64)

    ap_axis = unit(l - b, "bregma_to_lambda")
    ml_axis = right - left
    ml_axis = ml_axis - np.dot(ml_axis, ap_axis) * ap_axis
    ml_axis = unit(ml_axis, "left_to_right")
    dv_axis = unit(np.cross(ap_axis, ml_axis), "ventral_axis")
    if dv_axis[1] < 0:
        dv_axis *= -1.0
        ml_axis *= -1.0
    rotation = np.vstack([ap_axis, ml_axis, dv_axis])
    offset = -rotation @ b
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = offset
    inv = np.eye(4, dtype=np.float64)
    inv[:3, :3] = rotation.T
    inv[:3, 3] = b
    return {
        "coordinateOrder": ["ap_posterior_mm", "ml_right_mm", "dv_ventral_mm"],
        "origin": "bregma_surface",
        "nativeToFlatSkull4x4": matrix.tolist(),
        "flatSkullToNative4x4": inv.tolist(),
        "axesNativeUnit": {
            "apPosterior": ap_axis.tolist(),
            "mlRight": ml_axis.tolist(),
            "dvVentral": dv_axis.tolist(),
        },
        "bregmaLambdaDistanceMm": float(np.linalg.norm(l - b)),
    }


def save_nifti_points(img: sitk.Image, fiducials: dict, out_path: Path) -> None:
    arr = np.zeros(tuple(reversed(img.GetSize())), dtype=np.uint8)
    labels = [("bregma", 1), ("lambda", 2), ("left_support", 3), ("right_support", 4)]
    for name, value in labels:
        idx = [int(round(v)) for v in fiducials[name]["surface_index_xyz"]]
        if all(0 <= idx[i] < img.GetSize()[i] for i in range(3)):
            arr[idx[2], idx[1], idx[0]] = value
    out = sitk.GetImageFromArray(arr)
    out.CopyInformation(img)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(sitk.Cast(out, sitk.sitkUInt8), str(out_path), True)


def build_qc_figure(args, head_xyz: np.ndarray, brain_xyz: np.ndarray, fiducials: dict, stats: dict) -> Path:
    import matplotlib.pyplot as plt

    args.figure_out.parent.mkdir(parents=True, exist_ok=True)
    head = np.asarray(head_xyz > 0)
    brain = np.asarray(brain_xyz > 0)
    fig = plt.figure(figsize=(12.5, 7.2), dpi=180)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 0.92], hspace=0.28, wspace=0.22)

    points = {
        name: np.asarray(rec["surface_index_xyz"], dtype=np.float64)
        for name, rec in fiducials.items()
        if isinstance(rec, dict) and "surface_index_xyz" in rec
    }
    colors = {
        "bregma": "#2f7d6d",
        "lambda": "#9f5f45",
        "left_support": "#4b6f9f",
        "right_support": "#4b6f9f",
    }
    labels = {
        "bregma": "Bregma",
        "lambda": "Lambda",
        "left_support": "Left support",
        "right_support": "Right support",
    }

    def draw_projection(ax, bg, fg, title, x_label, y_label, mapper):
        ax.imshow(bg, cmap="Greys", alpha=0.28, origin="upper")
        ax.imshow(np.ma.masked_where(~fg, fg), cmap="Blues", alpha=0.18, origin="upper")
        for key, idx in points.items():
            x, y = mapper(idx)
            ax.scatter([x], [y], s=45 if key in ("bregma", "lambda") else 24, c=colors[key], edgecolor="white", linewidth=0.7)
            if key in ("bregma", "lambda"):
                ax.text(x + 5, y + 5, labels[key], color=colors[key], fontsize=8, weight="bold")
        ax.set_title(title, fontsize=10, weight="bold")
        ax.set_xlabel(x_label, fontsize=8)
        ax.set_ylabel(y_label, fontsize=8)
        ax.tick_params(labelsize=7)

    ax = fig.add_subplot(gs[0, 0])
    draw_projection(
        ax,
        head.any(axis=1).T,
        brain.any(axis=1).T,
        "Native head/brain AP-ML projection",
        "x/AP voxel",
        "z/ML voxel",
        lambda p: (p[0], p[2]),
    )

    ax = fig.add_subplot(gs[0, 1])
    draw_projection(
        ax,
        head.any(axis=2).T,
        brain.any(axis=2).T,
        "Native head/brain AP-DV projection",
        "x/AP voxel",
        "y/DV voxel",
        lambda p: (p[0], p[1]),
    )

    ax = fig.add_subplot(gs[0, 2])
    draw_projection(
        ax,
        head.any(axis=0),
        brain.any(axis=0),
        "Native head/brain DV-ML projection",
        "z/ML voxel",
        "y/DV voxel",
        lambda p: (p[2], p[1]),
    )

    ax = fig.add_subplot(gs[1, :2])
    b = np.asarray(fiducials["bregma"]["surface_index_xyz"])
    l = np.asarray(fiducials["lambda"]["surface_index_xyz"])
    ax.imshow(head.any(axis=1).T, cmap="Greys", alpha=0.22, origin="upper")
    ax.plot([b[0], l[0]], [b[2], l[2]], color="#2f7d6d", lw=2.0)
    for key, idx in points.items():
        ax.scatter(idx[0], idx[2], s=42 if key in ("bregma", "lambda") else 25, color=colors[key], edgecolor="white", linewidth=0.7)
    ax.set_title("Flat-skull AP axis surrogate on native head support", fontsize=10, weight="bold")
    ax.set_xlabel("x/AP voxel", fontsize=8)
    ax.set_ylabel("z/ML voxel", fontsize=8)
    ax.tick_params(labelsize=7)

    ax = fig.add_subplot(gs[1, 2])
    ax.axis("off")
    dim = stats["dimensionCheck"]
    lines = [
        "AUTOMATIC SURROGATE - NOT SUBJECT-SPECIFIC CT",
        f"Native MRI: {dim['native_mri']['size_xyz']} @ {dim['native_mri']['spacing_mm'][0]:.3f} mm",
        f"Allen CCF: {dim['ccf_fixed']['size_xyz']} @ {dim['ccf_fixed']['spacing_mm'][0]:.3f} mm",
        f"Perens atlas: {dim['perens_stereotaxic_mri_mouse_25um']['shape_xyz']} @ 0.025 mm",
        f"Perens direct-grid match: {dim['perens_stereotaxic_mri_mouse_25um']['directGridMatchToLocalCcf']}",
        f"Bregma-lambda surface distance: {stats['flatSkull']['bregmaLambdaDistanceMm']:.3f} mm",
        f"Bregma projection: {fiducials['bregma']['projection_distance_mm']:.3f} mm",
        f"Lambda projection: {fiducials['lambda']['projection_distance_mm']:.3f} mm",
        f"Confidence: {stats['readiness']['confidence']}",
        f"High precision ready: {stats['readiness']['highPrecisionReady']}",
    ]
    ax.text(0.02, 0.98, "\n".join(lines), va="top", ha="left", fontsize=8.4, family="monospace")
    fig.suptitle("Native-space surrogate skull fiducials from Allen/IBL bregma prior", fontsize=13, weight="bold")
    fig.savefig(args.figure_out, bbox_inches="tight")
    plt.close(fig)
    return args.figure_out


def write_fiducial_csv(path: Path, fiducials: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "label",
        "x_mm",
        "y_mm",
        "z_mm",
        "x_index",
        "y_index",
        "z_index",
        "source",
        "confidence",
        "projection_distance_mm",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for key in ("bregma", "lambda", "left_support", "right_support"):
            rec = fiducials[key]
            writer.writerow(
                {
                    "label": key,
                    "x_mm": rec["surface_mm_xyz"][0],
                    "y_mm": rec["surface_mm_xyz"][1],
                    "z_mm": rec["surface_mm_xyz"][2],
                    "x_index": rec["surface_index_xyz"][0],
                    "y_index": rec["surface_index_xyz"][1],
                    "z_index": rec["surface_index_xyz"][2],
                    "source": rec.get("source", ""),
                    "confidence": rec.get("confidence", "low"),
                    "projection_distance_mm": rec.get("projection_distance_mm", ""),
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate automatic native skull fiducial surrogate.")
    ap.add_argument("--native-profile", type=Path, default=DEFAULT_NATIVE_PROFILE)
    ap.add_argument("--ccf-work", type=Path, default=DEFAULT_CCF_PROFILE)
    ap.add_argument("--ants-bin", type=Path, default=DEFAULT_ANTS_BIN)
    ap.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    ap.add_argument("--work-dir", type=Path, default=mri_path("ants_syn/auto_skull_fiducials_shape_head"))
    ap.add_argument("--figure-out", type=Path, default=DEFAULT_FIGURE)
    ap.add_argument("--lambda-posterior-mm", type=float, default=4.0)
    ap.add_argument("--lateral-support-mm", type=float, default=2.0)
    ap.add_argument("--surface-search-radius-mm", type=float, default=1.2)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    profile = args.native_profile
    ccf_work = args.ccf_work
    paths = {
        "native_mri": profile / "native_mri_ail_mm.nii.gz",
        "native_brain_mask": profile / "native_brain_mask.nii.gz",
        "native_head_mask": profile / "native_head_mask.nii.gz",
        "native_brain_mask_npy": profile / "native_brain_mask.npy",
        "native_head_mask_npy": profile / "native_head_mask.npy",
        "affine": profile / "registration" / "mri_to_ccf_0GenericAffine.mat",
        "warp": profile / "registration" / "mri_to_ccf_1Warp.nii.gz",
        "inverse_warp": profile / "registration" / "mri_to_ccf_1InverseWarp.nii.gz",
        "ccf_fixed": ccf_work / "prepared" / "fixed_brain_mask.nii.gz",
    }
    missing = [str(p) for p in paths.values() if isinstance(p, Path) and not p.exists()]
    if missing:
        raise RuntimeError("Missing required inputs:\n" + "\n".join(missing))

    args.work_dir.mkdir(parents=True, exist_ok=True)
    online_dir = args.external_root / "online_sources"
    ibl_source = online_dir / "iblatlas_atlas.py"
    download_text(IBL_ATLAS_RAW, ibl_source)
    ibl_bregma = parse_ibl_bregma_mlapdv_um(ibl_source)

    native_img = sitk.ReadImage(str(paths["native_mri"]))
    ccf_img = sitk.ReadImage(str(paths["ccf_fixed"]))
    ccf_points = ccf_points_from_ibl(args, ccf_img, ibl_bregma)
    ccf_points_csv, native_raw_csv, native_rows = apply_ccf_to_native_points(args, paths, ccf_points, ants_env(args))

    head_xyz = load_mask_xyz(paths["native_head_mask_npy"], paths["native_head_mask"])
    brain_xyz = load_mask_xyz(paths["native_brain_mask_npy"], paths["native_brain_mask"])
    radius_vox = int(round(args.surface_search_radius_mm / native_img.GetSpacing()[0]))

    fiducials = {}
    for row in native_rows:
        label = str(row["label"])
        raw_mm = [float(row["x"]), float(row["y"]), float(row["z"])]
        key = "bregma" if "bregma" in label else "lambda"
        rec = project_to_dorsal_surface(native_img, head_xyz, raw_mm, max_radius_vox=radius_vox)
        rec["label"] = key
        rec["source"] = label
        rec["confidence"] = "low_surrogate"
        fiducials[key] = rec

    b_idx = np.asarray(fiducials["bregma"]["surface_index_xyz"], dtype=np.float64)
    l_idx = np.asarray(fiducials["lambda"]["surface_index_xyz"], dtype=np.float64)
    mid_idx = (b_idx + l_idx) / 2.0
    lateral_vox = float(args.lateral_support_mm / native_img.GetSpacing()[2])
    fiducials["left_support"] = surface_support_at_index(
        native_img, head_xyz, mid_idx[0], mid_idx[2] - lateral_vox, "left_support", radius_vox
    )
    fiducials["right_support"] = surface_support_at_index(
        native_img, head_xyz, mid_idx[0], mid_idx[2] + lateral_vox, "right_support", radius_vox
    )
    for key in ("left_support", "right_support"):
        fiducials[key]["source"] = "native_head_surface_lateral_support"
        fiducials[key]["confidence"] = "low_surrogate"

    flat = build_flat_skull_transform(fiducials)
    fiducials["flatSkull"] = flat

    perens_dir = args.external_root / "brainglobe" / "perens_stereotaxic_mri_mouse_25um_v1.0"
    perens_meta_path = perens_dir / "metadata.json"
    if perens_meta_path.exists():
        perens_meta = json.loads(perens_meta_path.read_text(encoding="utf-8"))
        perens_ref = perens_dir / "reference.tiff"
        perens_ann = perens_dir / "annotation.tiff"
    else:
        perens_meta = {}
        perens_ref = perens_ann = Path("")

    native_meta = image_meta(paths["native_mri"])
    ccf_meta = image_meta(paths["ccf_fixed"])
    perens_shape = [int(v) for v in perens_meta.get("shape", [])] if perens_meta else []
    perens_spacing_mm = [float(v) / 1000.0 for v in perens_meta.get("resolution", [])] if perens_meta else []
    stats = {
        "createdAt": utc_now(),
        "schemaVersion": "probeplanner.auto_skull_fiducials.surrogate.v1",
        "profileId": "auto_skull_fiducials_shape_head",
        "sourceNativeProfile": str(profile),
        "sourceCcfWork": str(ccf_work),
        "dimensionCheck": {
            "native_mri": native_meta,
            "ccf_fixed": ccf_meta,
            "perens_stereotaxic_mri_mouse_25um": {
                "path": str(perens_dir),
                "metadata": str(perens_meta_path) if perens_meta_path.exists() else "",
                "shape_xyz": perens_shape,
                "spacing_mm": perens_spacing_mm,
                "physical_extent_mm": [
                    float(perens_shape[i] * perens_spacing_mm[i]) for i in range(3)
                ]
                if perens_shape and perens_spacing_mm
                else [],
                "directGridMatchToLocalCcf": bool(perens_shape == ccf_meta["size_xyz"]),
                "usePolicy": (
                    "downloaded for provenance and future bridge registration; "
                    "not used as directly aligned CCF skull because grid shape differs"
                ),
            },
        },
        "onlineSources": {
            "iblAllenAtlasSource": {
                "url": IBL_ATLAS_RAW,
                "localPath": str(ibl_source),
                "sha256": sha256_file(ibl_source),
                "bregmaMlapdvUm": ibl_bregma,
            },
            "perensBrainGlobeAtlas": {
                "citation": perens_meta.get("citation", "Perens et al. 2023"),
                "atlasLink": perens_meta.get("atlas_link", ""),
                "localPath": str(perens_dir),
                "metadataSha256": sha256_file(perens_meta_path) if perens_meta_path.exists() else "",
            },
        },
        "ccfPriorPoints": ccf_points,
        "nativeRawPointsCsv": str(native_raw_csv),
        "fiducials": fiducials,
        "flatSkull": flat,
        "readiness": {
            "status": "surrogate_available_but_not_high_precision_ready",
            "confidence": "low",
            "highPrecisionReady": False,
            "reason": (
                "Bregma/lambda are atlas priors projected to the native head mask, "
                "not subject-specific micro-CT/photo skull landmarks."
            ),
            "nextDataNeeded": [
                "same-animal micro-CT or calibrated skull surface scan",
                "manual or semi-automatic bregma/lambda on that skull data",
                "validated flat-skull transform",
                "hold-out anatomical landmarks or post-hoc probe-track validation",
            ],
        },
    }

    fid_csv = profile / "auto_fiducials.csv"
    fid_json = profile / "auto_fiducials.json"
    flat_json = profile / "flat_skull_transform.json"
    points_nii = profile / "auto_fiducials_native_points.nii.gz"
    stats_json = profile / "auto_fiducial_stats.json"
    manifest_path = profile / "auto_fiducial_manifest.json"

    write_fiducial_csv(fid_csv, fiducials)
    fid_json.write_text(json.dumps(fiducials, ensure_ascii=False, indent=2), encoding="utf-8")
    flat_json.write_text(json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    save_nifti_points(native_img, fiducials, points_nii)
    qc = build_qc_figure(args, head_xyz, brain_xyz, fiducials, stats)
    stats["qcFigure"] = str(qc)
    stats_json.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = {
        "auto_fiducials_csv": artifact(fid_csv, "fiducials_csv"),
        "auto_fiducials_json": artifact(fid_json, "fiducials_json"),
        "flat_skull_transform": artifact(flat_json, "transform_json"),
        "auto_fiducials_native_points": artifact(points_nii, "label_image"),
        "auto_fiducial_stats": artifact(stats_json, "stats_json"),
        "qc_auto_fiducials": artifact(qc, "qc_figure"),
        "ibl_source": artifact(ibl_source, "downloaded_source"),
        "perens_metadata": artifact(perens_meta_path, "downloaded_metadata", required=perens_meta_path.exists()),
        "perens_reference": artifact(perens_ref, "downloaded_reference", required=perens_ref.exists()),
        "perens_annotation": artifact(perens_ann, "downloaded_annotation", required=perens_ann.exists()),
        "ccf_points_csv": artifact(ccf_points_csv, "work_points_csv"),
        "native_raw_points_csv": artifact(native_raw_csv, "work_points_csv"),
    }
    manifest = {
        "createdAt": utc_now(),
        "schemaVersion": "probeplanner.auto_skull_fiducials.manifest.v1",
        "profileId": "auto_skull_fiducials_shape_head",
        "valid": all((not rec["required"]) or (rec["exists"] and rec["sha256"]) for rec in artifacts.values()),
        "readiness": stats["readiness"],
        "dimensionCheck": stats["dimensionCheck"],
        "artifacts": artifacts,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": manifest["valid"],
                "manifest": str(manifest_path),
                "stats": str(stats_json),
                "figure": str(qc),
                "fiducials": str(fid_csv),
                "bregmaLambdaDistanceMm": flat["bregmaLambdaDistanceMm"],
                "highPrecisionReady": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
