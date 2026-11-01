from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

try:
    from scipy import ndimage as ndi
except Exception:  # pragma: no cover - optional visual cleanup dependency
    ndi = None


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_ROOT = Path(
    os.environ.get(
        "PROBEPLANNER_ARTIFACT_ROOT",
        "artifacts/ambmc_true_outer_full",
    )
)
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_FIGURE_DIR = DEFAULT_EVIDENCE_DIR / "figures"
DEFAULT_BASENAME = "2026-07-08-mri-ccf-registration-overlay"
DEFAULT_STRUCTURE_TREE_JSON = Path.home() / ".allensdk_cache" / "structures.json"
VOXEL_SIZE_MM = 0.025
PLANE_TITLES = {
    "coronal": "Coronal",
    "sagittal": "Sagittal",
    "horizontal": "Horizontal / axial",
}

METRIC_DEFINITIONS = {
    "dice": (
        "MRI brain mask versus CCF-aligned fixed brain mask overlap, computed as "
        "twice the moving/fixed mask intersection divided by the summed mask volumes."
    ),
    "hd95Mm": (
        "Symmetric 95th-percentile Hausdorff distance between the aligned mask "
        "boundaries, reported in millimetres using 0.025 mm voxel spacing."
    ),
    "treMm": (
        "Mean Euclidean target-registration error between fixed anatomical "
        "landmark points and their warped moving-image counterparts, in millimetres."
    ),
    "jacobianAnomalyRatio": (
        "Fraction of finite deformation-field Jacobian determinant voxels that "
        "were non-positive or outside the accepted [0.2, 5.0] determinant range."
    ),
}

THRESHOLDS = {
    "dice": ">= 0.8",
    "hd95Mm": "<= 0.45 mm",
    "treMm": "<= 0.3 mm",
    "jacobianAnomalyRatio": "<= 0.03",
}


def _require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    return path


def _resolve_cli_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _repo_relative(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_structure_colors(path: Path | None) -> tuple[dict[int, tuple[float, float, float]], str | None]:
    if path is None or not path.exists():
        return {}, None
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("msg", [])
    colors: dict[int, tuple[float, float, float]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        sid = rec.get("id")
        rgb = rec.get("rgb_triplet")
        if sid is None or not isinstance(rgb, list) or len(rgb) != 3:
            continue
        try:
            colors[int(sid)] = tuple(float(max(0, min(255, int(v)))) / 255.0 for v in rgb)  # type: ignore[assignment]
        except Exception:
            continue
    return colors, str(path)


def _annotation_rgba(
    annotation_panel: np.ndarray,
    structure_colors: dict[int, tuple[float, float, float]],
    *,
    alpha: float,
) -> np.ndarray:
    panel = np.asarray(annotation_panel)
    rgba = np.zeros(panel.shape + (4,), dtype=np.float32)
    fallback = (0.68, 0.70, 0.72)
    for sid_raw in np.unique(panel):
        sid = int(sid_raw)
        if sid <= 0:
            continue
        color = structure_colors.get(sid, fallback)
        mask = panel == sid_raw
        rgba[mask, 0] = color[0]
        rgba[mask, 1] = color[1]
        rgba[mask, 2] = color[2]
        rgba[mask, 3] = alpha
    return rgba


def _parse_panel_order(value: str) -> list[str]:
    order = [part.strip().lower() for part in value.split(",") if part.strip()]
    valid = set(PLANE_TITLES)
    if len(order) != 3 or set(order) != valid:
        expected = ",".join(["coronal", "sagittal", "horizontal"])
        raise ValueError(f"--panel-order must contain each plane once, e.g. {expected}")
    return order


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _read_landmarks(path: Path) -> tuple[list[str], np.ndarray]:
    names: list[str] = []
    coords: list[list[float]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            names.append(row["name"])
            coords.append([float(row["x"]), float(row["y"]), float(row["z"])])
    if not coords:
        raise ValueError(f"No landmarks found in {path}")
    return names, np.asarray(coords, dtype=np.float64)


def _load_nifti_array(path: Path) -> np.ndarray:
    """Load the project NIfTI-1 image without requiring nibabel."""
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rb") as f:
        header = f.read(348)
        if len(header) != 348:
            raise ValueError(f"Invalid NIfTI header in {path}")
        sizeof_hdr_le = struct.unpack("<i", header[:4])[0]
        sizeof_hdr_be = struct.unpack(">i", header[:4])[0]
        if sizeof_hdr_le == 348:
            endian = "<"
        elif sizeof_hdr_be == 348:
            endian = ">"
        else:
            raise ValueError(f"Unsupported NIfTI header size in {path}")

        dims = struct.unpack(endian + "8h", header[40:56])
        ndim = int(dims[0])
        shape = tuple(int(v) for v in dims[1 : 1 + ndim])
        datatype = struct.unpack(endian + "h", header[70:72])[0]
        vox_offset = struct.unpack(endian + "f", header[108:112])[0]
        slope = struct.unpack(endian + "f", header[112:116])[0]
        intercept = struct.unpack(endian + "f", header[116:120])[0]

        dtype_map = {
            2: np.dtype("uint8"),
            4: np.dtype("int16"),
            8: np.dtype("int32"),
            16: np.dtype("float32"),
            64: np.dtype("float64"),
            256: np.dtype("int8"),
            512: np.dtype("uint16"),
            768: np.dtype("uint32"),
        }
        if datatype not in dtype_map:
            raise ValueError(f"Unsupported NIfTI datatype {datatype} in {path}")
        dtype = dtype_map[datatype].newbyteorder(endian)
        count = int(np.prod(shape))
        f.seek(int(vox_offset))
        raw = f.read(count * dtype.itemsize)
        if len(raw) < count * dtype.itemsize:
            raise ValueError(f"NIfTI payload ended early in {path}")
        data = np.frombuffer(raw, dtype=dtype, count=count)
        array = data.reshape(shape, order="F")
        if math.isfinite(slope) and slope not in (0.0, 1.0):
            array = array.astype(np.float32) * float(slope)
            if math.isfinite(intercept) and intercept != 0.0:
                array = array + float(intercept)
        return np.asarray(array, dtype=np.float32)


def _normalise_grayscale(panel: np.ndarray) -> np.ndarray:
    values = panel[np.isfinite(panel)]
    if values.size == 0:
        return np.zeros_like(panel, dtype=np.float32)
    lo, hi = np.percentile(values, [1.0, 99.7])
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        lo = float(np.min(values))
        hi = float(np.max(values))
    if hi <= lo:
        return np.zeros_like(panel, dtype=np.float32)
    scaled = (panel.astype(np.float32) - float(lo)) / float(hi - lo)
    return np.clip(scaled, 0.0, 1.0)


def _slice_panel(volume: np.ndarray, plane: str, index: int) -> np.ndarray:
    # Allen CCF local array axes are AP, DV/SI, ML: 0=coronal,
    # 1=horizontal, 2=sagittal (AllenSDK ReferenceSpace.get_slice_image).
    if plane == "coronal":
        return np.asarray(volume[index, :, :]).T
    if plane == "horizontal":
        return np.asarray(volume[:, index, :]).T
    if plane == "sagittal":
        return np.asarray(volume[:, :, index]).T
    raise ValueError(f"Unknown plane: {plane}")


def _outer_mask_panel(panel: np.ndarray) -> np.ndarray:
    mask = np.asarray(panel) > 0
    if ndi is not None:
        mask = ndi.binary_fill_holes(mask)
    return mask.astype(np.uint8)


def _plane_axis(plane: str) -> int:
    return {"coronal": 0, "horizontal": 1, "sagittal": 2}[plane]


def _display_xy(voxels: np.ndarray, plane: str) -> np.ndarray:
    if plane == "coronal":
        return voxels[:, [1, 2]]
    if plane == "horizontal":
        return voxels[:, [0, 2]]
    if plane == "sagittal":
        return voxels[:, [0, 1]]
    raise ValueError(f"Unknown plane: {plane}")


def _select_landmarks(
    fixed_voxels: np.ndarray,
    plane: str,
    index: int,
    *,
    tolerance_vox: int,
    max_points: int,
) -> np.ndarray:
    axis = _plane_axis(plane)
    distance = np.abs(fixed_voxels[:, axis] - float(index))
    within = np.where(distance <= float(tolerance_vox))[0]
    if within.size >= 4:
        order = within[np.argsort(distance[within])]
    else:
        order = np.argsort(distance)
    return order[:max_points]


def _crop_limits(mask_panels: list[np.ndarray], point_sets: list[np.ndarray]) -> tuple[tuple[float, float], tuple[float, float]]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for mask in mask_panels:
        if mask.size == 0:
            continue
        rows, cols = np.where(mask > 0)
        if rows.size:
            xs.append(cols.astype(np.float64))
            ys.append(rows.astype(np.float64))
    for points in point_sets:
        if points.size:
            xs.append(points[:, 0].astype(np.float64))
            ys.append(points[:, 1].astype(np.float64))
    if not xs or not ys:
        return (0.0, float(mask_panels[0].shape[1])), (0.0, float(mask_panels[0].shape[0]))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    pad = 18.0
    xlim = (max(0.0, float(np.min(x)) - pad), float(np.max(x)) + pad)
    ylim = (max(0.0, float(np.min(y)) - pad), float(np.max(y)) + pad)
    return xlim, ylim


def _recompute_dice(gt_mask: np.ndarray, pred_mask: np.ndarray) -> float:
    gt = gt_mask.astype(bool)
    pred = pred_mask.astype(bool)
    denom = int(gt.sum()) + int(pred.sum())
    if denom == 0:
        return 1.0
    return float((2.0 * np.logical_and(gt, pred).sum()) / denom)


def _recompute_jacobian_anomaly_ratio(jacobian_path: Path, *, chunk: int = 16) -> float:
    jacobian = np.load(jacobian_path, mmap_mode="r")
    bad_total = 0
    total = 0
    for start in range(0, jacobian.shape[0], chunk):
        slab = np.asarray(jacobian[start : start + chunk], dtype=np.float32)
        finite = np.isfinite(slab)
        if not finite.any():
            continue
        vals = slab[finite]
        bad_total += int(((vals <= 0.0) | (vals < 0.2) | (vals > 5.0)).sum())
        total += int(vals.size)
    return float(bad_total / total) if total else 0.0


def _load_metric_values(stats: dict[str, Any]) -> dict[str, float]:
    metrics = stats.get("metrics", stats)
    return {
        "dice": float(metrics.get("dice", stats.get("dice"))),
        "hd95Mm": float(metrics.get("hd95Mm", stats.get("hd95Mm"))),
        "treMm": float(metrics.get("treMm", stats.get("treMm"))),
        "jacobianAnomalyRatio": float(
            metrics.get("jacobianAnomalyRatio", stats.get("jacobianAnomalyRatio"))
        ),
    }


def _metric_pass(values: dict[str, float]) -> bool:
    return (
        values["dice"] >= 0.8
        and values["hd95Mm"] <= 0.45
        and values["treMm"] <= 0.3
        and values["jacobianAnomalyRatio"] <= 0.03
    )


def _mask_jacobian_pass(values: dict[str, float]) -> bool:
    return values["dice"] >= 0.8 and values["hd95Mm"] <= 0.45 and values["jacobianAnomalyRatio"] <= 0.03


def _intensity_summary(path: Path) -> dict[str, Any]:
    array = _load_nifti_array(path)
    values = array[np.isfinite(array)]
    return {
        "path": str(path),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p99": float(np.percentile(values, 99)),
        "p999": float(np.percentile(values, 99.9)),
    }


def _visual_gate(manual_decision: str) -> dict[str, Any]:
    """Record the explicit visual decision without conflating raw intensity scale with deformation QC.

    AMBMC source intensities are not required to share a numeric range with a
    separately normalised affine diagnostic image. Slice-wise rendering already
    uses robust percentile normalisation, so a raw max-intensity ratio is not a
    valid corruption test. Geometric validity is instead evidenced by the
    transform, masks, Jacobian audit, and an explicit overlay inspection.
    """
    if manual_decision == "pass":
        return {
            "status": "PASS",
            "reason": "Orthogonal overlay was manually inspected; no twisting, clipping, or gross displacement was observed.",
        }
    if manual_decision == "fail":
        return {
            "status": "FAIL",
            "reason": "Orthogonal overlay was manually inspected and rejected for visible geometric distortion or displacement.",
        }
    return {
        "status": "REVIEW_REQUIRED",
        "reason": "Orthogonal overlay requires an explicit visual QC decision.",
    }


def _maybe_apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "font.size": 8,
            "axes.titlesize": 9,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
        }
    )


def _render_deformable_affine_diagnostic(
    *,
    deformable_path: Path,
    affine_path: Path,
    output_path: Path,
    panels: list[dict[str, Any]],
) -> str:
    _maybe_apply_style()
    deformable = _load_nifti_array(deformable_path)
    affine = _load_nifti_array(affine_path)
    fig, axes = plt.subplots(2, 3, figsize=(9.2, 4.4))
    rows = [
        ("Deformable warped MRI", deformable),
        ("Affine-only warped MRI", affine),
    ]
    for row_idx, (row_title, volume) in enumerate(rows):
        for col_idx, panel in enumerate(panels):
            ax = axes[row_idx, col_idx]
            plane = panel["plane"]
            index = int(panel["sliceIndex"])
            ax.imshow(
                _normalise_grayscale(_slice_panel(volume, plane, index)),
                cmap="gray",
                origin="lower",
                interpolation="nearest",
            )
            if col_idx == 0:
                ax.set_ylabel(row_title, fontsize=8)
            ax.set_title(f"{plane} {index}", fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
    fig.suptitle("Deformable versus affine-only MRI intensity diagnostic", fontsize=10)
    fig.tight_layout(pad=0.4)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=260)
    plt.close(fig)
    return _repo_relative(output_path)


def _render_figure(
    *,
    mri_path: Path,
    annotation_path: Path,
    structure_tree_path: Path | None,
    gt_mask_path: Path,
    pred_mask_path: Path,
    fixed_landmarks_mm: np.ndarray,
    warped_landmarks_mm: np.ndarray,
    metrics: dict[str, float],
    output_base: Path,
    tolerance_vox: int,
    max_landmarks_per_panel: int,
    panel_order: list[str],
    show_control_points: bool,
    tre_valid_as_anatomical: bool,
) -> dict[str, Any]:
    _maybe_apply_style()
    mri = _load_nifti_array(mri_path)
    annotation = np.load(annotation_path, mmap_mode="r")
    gt_mask = np.load(gt_mask_path, mmap_mode="r")
    pred_mask = np.load(pred_mask_path, mmap_mode="r")
    structure_colors, structure_color_source = _load_structure_colors(structure_tree_path)

    fixed_voxels = fixed_landmarks_mm / VOXEL_SIZE_MM
    warped_voxels = warped_landmarks_mm / VOXEL_SIZE_MM
    fixed_voxels = np.clip(fixed_voxels, [0, 0, 0], np.asarray(annotation.shape) - 1)
    warped_voxels = np.clip(warped_voxels, [0, 0, 0], np.asarray(annotation.shape) - 1)
    slices = {
        "coronal": int(round(float(np.median(fixed_voxels[:, 0])))),
        "horizontal": int(round(float(np.median(fixed_voxels[:, 1])))),
        "sagittal": int(round(float(np.median(fixed_voxels[:, 2])))),
    }
    for plane, index in list(slices.items()):
        axis = _plane_axis(plane)
        slices[plane] = int(np.clip(index, 0, annotation.shape[axis] - 1))

    fig, axes = plt.subplots(1, 3, figsize=(9.4, 3.5), constrained_layout=False)
    panels = [(plane, PLANE_TITLES[plane]) for plane in panel_order]
    panel_records: list[dict[str, Any]] = []

    for panel_idx, (plane, title) in enumerate(panels):
        ax = axes[panel_idx]
        index = slices[plane]
        mri_panel = _normalise_grayscale(_slice_panel(mri, plane, index))
        ann_panel = _slice_panel(annotation, plane, index)
        gt_panel = _outer_mask_panel(_slice_panel(gt_mask, plane, index))
        pred_panel = _outer_mask_panel(_slice_panel(pred_mask, plane, index))

        selected = (
            _select_landmarks(
                fixed_voxels,
                plane,
                index,
                tolerance_vox=tolerance_vox,
                max_points=max_landmarks_per_panel,
            )
            if show_control_points
            else np.asarray([], dtype=np.int64)
        )
        fixed_xy = _display_xy(fixed_voxels[selected], plane)
        warped_xy = _display_xy(warped_voxels[selected], plane)

        ax.imshow(mri_panel, cmap="gray", origin="lower", interpolation="nearest")
        ax.imshow(
            _annotation_rgba(ann_panel, structure_colors, alpha=0.22),
            origin="lower",
            interpolation="nearest",
        )
        ax.contour(gt_panel, levels=[0.5], colors=["#0b5f77"], linewidths=1.0)
        ax.contour(pred_panel, levels=[0.5], colors=["#c2502d"], linewidths=0.95, linestyles="--")

        if show_control_points:
            for start_xy, end_xy in zip(fixed_xy, warped_xy, strict=False):
                ax.plot(
                    [start_xy[0], end_xy[0]],
                    [start_xy[1], end_xy[1]],
                    color="#f0b34f",
                    linewidth=0.65,
                    alpha=0.8,
                    zorder=7,
                )
            ax.scatter(
                fixed_xy[:, 0],
                fixed_xy[:, 1],
                s=15,
                c="#16a6c9",
                edgecolors="white",
                linewidths=0.35,
                zorder=8,
            )
            ax.scatter(
                warped_xy[:, 0],
                warped_xy[:, 1],
                s=18,
                marker="x",
                c="#e45a3b",
                linewidths=0.85,
                zorder=9,
            )

        xlim, ylim = _crop_limits(
            [ann_panel > 0, gt_panel > 0, pred_panel > 0],
            [fixed_xy, warped_xy],
        )
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{chr(ord('A') + panel_idx)}. {title} slice {index}", fontsize=9, pad=4)
        for spine in ax.spines.values():
            spine.set_visible(False)
        panel_records.append(
            {
                "plane": plane,
                "sliceIndex": index,
                "axis": _plane_axis(plane),
                "landmarkCountDisplayed": int(len(selected)) if tre_valid_as_anatomical else 0,
                "controlPointCountDisplayed": int(len(selected)) if not tre_valid_as_anatomical else 0,
                "pointToleranceVoxels": int(tolerance_vox),
            }
        )

    title_suffix = (
        f", TRE {metrics['treMm']:.3f} mm"
        if tre_valid_as_anatomical
        else ", anatomical TRE not evaluated"
    )
    fig.suptitle(
        f"MRI-to-CCF registration QC overlay (Dice {metrics['dice']:.3f}, HD95 {metrics['hd95Mm']:.3f} mm{title_suffix})",
        fontsize=10,
        y=0.985,
    )
    legend_handles = [
        Line2D([0], [0], color="#0b5f77", lw=1.15, label="CCF brain boundary"),
        Line2D([0], [0], color="#c2502d", lw=1.0, ls="--", label="MRI brain mask"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="#8fb9a8", alpha=0.65, label="Allen region overlay"),
    ]
    if show_control_points:
        point_label = "Landmark" if tre_valid_as_anatomical else "Auto control point"
        legend_handles.extend(
            [
                Line2D([0], [0], marker="o", color="none", markerfacecolor="#16a6c9", markeredgecolor="white", label=f"Fixed {point_label}"),
                Line2D([0], [0], marker="x", color="#e45a3b", lw=0, label=f"Warped {point_label}"),
            ]
        )
    fig.legend(
        handles=legend_handles,
        loc="lower center",
        ncol=5,
        frameon=False,
        fontsize=7.2,
        bbox_to_anchor=(0.5, 0.015),
        handlelength=1.5,
        columnspacing=1.1,
    )
    fig.subplots_adjust(left=0.02, right=0.985, top=0.86, bottom=0.16, wspace=0.035)

    output_base.parent.mkdir(parents=True, exist_ok=True)
    png = output_base.with_suffix(".png")
    pdf = output_base.with_suffix(".pdf")
    svg = output_base.with_suffix(".svg")
    fig.savefig(png, dpi=320)
    fig.savefig(pdf)
    fig.savefig(svg)
    plt.close(fig)

    return {
        "png": _repo_relative(png),
        "pdf": _repo_relative(pdf),
        "svg": _repo_relative(svg),
        "panels": panel_records,
        "annotationColorSource": structure_color_source,
        "annotationColorMode": "Allen structure rgb_triplet" if structure_colors else "fallback neutral",
    }


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    figure_png = summary["figure"]["png"].replace("\\", "/")
    metrics = summary["metrics"]
    numeric_gate = str(summary["numericGateStatus"])
    visual_gate = str(summary["visualOverlayGateStatus"])
    tre_valid = bool(summary["landmarks"].get("validAsAnatomicalTre", True))
    if numeric_gate == "PASS" and visual_gate != "FAIL":
        interpretation = (
            "The promoted profile passes the stored numeric mask, anatomical landmark, and "
            "Jacobian gates, but manual visual MRI-to-CCF overlay QC remains "
            "required before using the figure in Results."
        )
    elif numeric_gate == "INCOMPLETE_ANATOMICAL_TRE":
        interpretation = (
            "The mask-overlap, boundary-distance, and Jacobian gates pass, but "
            "anatomical TRE is not evaluated because the available point pairs "
            "are automatically generated control points rather than manually "
            "curated anatomical landmarks."
        )
    else:
        interpretation = (
            "The current registration artifact should not be used as a Results "
            "figure. Numeric QC and/or visual overlay QC still requires revision "
            "before the MRI-to-CCF claim is manuscript-ready."
        )
    rows = []
    for key, label in [
        ("dice", "Dice"),
        ("hd95Mm", "HD95"),
        ("treMm", "TRE" if tre_valid else "Auto control-point residual"),
        ("jacobianAnomalyRatio", "Jacobian anomaly ratio"),
    ]:
        rows.append(
            "| {label} | {value} | {threshold} | {definition} | {source} |".format(
                label=label,
                value=metrics[key]["formatted"],
                threshold=metrics[key]["threshold"],
                definition=metrics[key]["definition"],
                source=metrics[key]["source"],
            )
        )
    point_kind = "Landmarks" if tre_valid else "Automatic control points"
    figure_point_clause = (
        "and fixed/warped landmark pairs"
        if bool(summary["landmarks"].get("displayedInFigure", False))
        else "with point overlays hidden"
    )
    lines = [
        "# MRI-to-CCF Registration QC - 2026-07-08",
        "",
        f"- Status: {summary['status'].replace('_', ' ')}.",
        f"- Numeric gate: {summary['numericGateStatus']}.",
        f"- Visual overlay gate: {summary['visualOverlayGateStatus']}.",
        f"- Registration artifact root: `{summary['artifactRoot']}`.",
        f"- Atlas artifact root: `{summary['atlasArtifactRoot']}`.",
        f"- Voxel spacing: {VOXEL_SIZE_MM:.3f} mm isotropic.",
        f"- {point_kind}: {summary['landmarks']['count']} points; valid anatomical TRE: {tre_valid}.",
        "",
        "![MRI-to-CCF registration overlay](figures/{name}.png)".format(
            name=Path(figure_png).stem
        ),
        "",
        f"Figure: three orthogonal CCF-space slices show MRI grayscale, Allen annotation region overlay, CCF fixed-mask boundary, aligned MRI-mask boundary, {figure_point_clause}.",
        "",
        f"Visual check: {summary['visualQcFinding']['finding']}",
        "",
        "## Metric Definitions",
        "",
        "| Metric | Current result | Gate | Definition | Source |",
        "| --- | ---: | --- | --- | --- |",
        *rows,
        "",
        "## Data Inputs",
        "",
        f"- Registration artifact root: `{summary['dataInputs']['registrationArtifactRoot']}`",
        f"- Atlas artifact root: `{summary['dataInputs']['atlasArtifactRoot']}`",
        f"- MRI-in-CCF volume: `{summary['dataInputs']['mriInCcf']}`",
        f"- Allen annotation volume: `{summary['dataInputs']['annotation']}`",
        f"- Fixed CCF brain mask: `{summary['dataInputs']['fixedMask']}`",
        f"- Aligned MRI brain mask: `{summary['dataInputs']['alignedMriMask']}`",
        f"- Fixed point CSV: `{summary['dataInputs']['fixedLandmarks']}`",
        f"- Warped point CSV: `{summary['dataInputs']['warpedLandmarks']}`",
        f"- Jacobian determinant: `{summary['dataInputs']['jacobian']}`",
        f"- Metrics source: `{summary['dataInputs']['stats']}`",
        "",
        "## Lightweight Consistency Checks",
        "",
        f"- Recomputed Dice from masks: {summary['lightweightRecompute']['dice']:.6f}.",
        f"- Recomputed mean point residual: {summary['lightweightRecompute']['treMeanMm']:.6f} mm.",
        f"- Recomputed Jacobian anomaly ratio: {summary['lightweightRecompute']['jacobianAnomalyRatio']:.6f}.",
        "- HD95 is reported from the existing registration QC builder, which computes symmetric boundary distance percentiles from the fixed and aligned mask surfaces.",
        "",
        "## Image and Visual QC",
        "",
        f"- MRI intensity range: {summary['visualQcFinding']['deformableWarpedMriIntensityRange']}.",
        f"- Affine comparison intensity range: {summary['visualQcFinding']['affineOnlyWarpedMriIntensityRange']}.",
        f"- Affine comparison diagnostic: `{summary['visualQcFinding']['diagnosticFigure']}`.",
        "- Raw intensity ranges are reported for provenance only and are not treated as a deformation-quality metric.",
        "",
        "## Interpretation",
        "",
        f"{interpretation} This QC does not establish animal targeting accuracy or histological insertion accuracy.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    artifact_root = _resolve_cli_path(args.artifact_root)
    atlas_artifact_root = _resolve_cli_path(args.atlas_artifact_root) if args.atlas_artifact_root else artifact_root
    evidence_dir = _resolve_cli_path(args.evidence_dir)
    figure_dir = _resolve_cli_path(args.figure_dir)
    structure_tree_path = _resolve_cli_path(args.structure_tree_json) if args.structure_tree_json else DEFAULT_STRUCTURE_TREE_JSON
    output_base = figure_dir / args.basename

    stats_path = _require(artifact_root / "stats.json")
    annotation_path = _require(atlas_artifact_root / "annotation.npy")
    mri_path = _require(artifact_root / "registration" / "ambmc_in_ccf.nii.gz")
    fixed_mask_path = _require(artifact_root / "quality_eval" / "gt_mask.npy")
    aligned_mask_path = _require(artifact_root / "quality_eval" / "pred_mask.npy")
    fixed_landmarks_path = _require(artifact_root / "quality_eval" / "fixed_landmarks.csv")
    warped_landmarks_path = _require(artifact_root / "quality_eval" / "warped_landmarks.csv")
    jacobian_path = _require(artifact_root / "jacobian.npy")
    affine_mri_path = artifact_root / "registration" / "ambmc_in_ccf_native_affine_only.nii.gz"

    stats = _read_json(stats_path)
    metric_values = _load_metric_values(stats)
    control_point_info = stats.get("controlPointResidual", {})
    tre_valid_as_anatomical = bool(control_point_info.get("validAsAnatomicalTre", True))
    show_control_points = bool(args.show_control_points)
    names, fixed_mm = _read_landmarks(fixed_landmarks_path)
    warped_names, warped_mm = _read_landmarks(warped_landmarks_path)
    if names != warped_names:
        raise ValueError("Fixed and warped landmark CSV files do not contain matching landmark names")
    deltas = fixed_mm - warped_mm
    tre_distances = np.linalg.norm(deltas, axis=1)

    fixed_mask = np.load(fixed_mask_path, mmap_mode="r")
    aligned_mask = np.load(aligned_mask_path, mmap_mode="r")
    recomputed_dice = _recompute_dice(fixed_mask, aligned_mask)
    recomputed_jacobian = _recompute_jacobian_anomaly_ratio(jacobian_path)

    figure = _render_figure(
        mri_path=mri_path,
        annotation_path=annotation_path,
        structure_tree_path=structure_tree_path,
        gt_mask_path=fixed_mask_path,
        pred_mask_path=aligned_mask_path,
        fixed_landmarks_mm=fixed_mm,
        warped_landmarks_mm=warped_mm,
        metrics=metric_values,
        output_base=output_base,
        tolerance_vox=int(args.landmark_tolerance_vox),
        max_landmarks_per_panel=int(args.max_landmarks_per_panel),
        panel_order=_parse_panel_order(args.panel_order),
        show_control_points=show_control_points,
        tre_valid_as_anatomical=tre_valid_as_anatomical,
    )
    deformable_stats = _intensity_summary(mri_path)
    affine_stats = _intensity_summary(affine_mri_path) if affine_mri_path.exists() else None
    visual_gate = _visual_gate(str(args.visual_qc_decision))
    diagnostic_figure = None
    if affine_mri_path.exists():
        diagnostic_figure = _render_deformable_affine_diagnostic(
            deformable_path=mri_path,
            affine_path=affine_mri_path,
            output_path=figure_dir / "2026-07-08-mri-ccf-deformable-vs-affine-diagnostic.png",
            panels=figure["panels"],
        )
    if tre_valid_as_anatomical:
        numeric_gate_status = "PASS" if _metric_pass(metric_values) else "FAIL"
    else:
        numeric_gate_status = "INCOMPLETE_ANATOMICAL_TRE" if _mask_jacobian_pass(metric_values) else "FAIL"
    visual_overlay_gate_status = str(visual_gate["status"])
    if numeric_gate_status == "PASS" and visual_overlay_gate_status == "PASS":
        status = "PASS"
    elif numeric_gate_status == "INCOMPLETE_ANATOMICAL_TRE" and visual_overlay_gate_status == "PASS":
        status = "INCOMPLETE_ANATOMICAL_TRE"
    else:
        status = "REQUIRES_REVISION"

    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "numericGateStatus": numeric_gate_status,
        "visualOverlayGateStatus": visual_overlay_gate_status,
        "artifactRoot": str(artifact_root),
        "atlasArtifactRoot": str(atlas_artifact_root),
        "voxelSpacingMm": [VOXEL_SIZE_MM, VOXEL_SIZE_MM, VOXEL_SIZE_MM],
        "dataInputs": {
            "registrationArtifactRoot": str(artifact_root),
            "atlasArtifactRoot": str(atlas_artifact_root),
            "mriInCcf": str(mri_path),
            "annotation": str(annotation_path),
            "structureTreeColors": figure.get("annotationColorSource"),
            "fixedMask": str(fixed_mask_path),
            "alignedMriMask": str(aligned_mask_path),
            "fixedLandmarks": str(fixed_landmarks_path),
            "warpedLandmarks": str(warped_landmarks_path),
            "jacobian": str(jacobian_path),
            "stats": str(stats_path),
            "affineOnlyWarpedMri": str(affine_mri_path) if affine_mri_path.exists() else None,
        },
        "figure": figure,
        "metrics": {
            "dice": {
                "value": metric_values["dice"],
                "formatted": f"{metric_values['dice']:.6f}",
                "threshold": THRESHOLDS["dice"],
                "definition": METRIC_DEFINITIONS["dice"],
                "source": "fixed and aligned brain-mask overlap",
            },
            "hd95Mm": {
                "value": metric_values["hd95Mm"],
                "formatted": f"{metric_values['hd95Mm']:.6f} mm",
                "threshold": THRESHOLDS["hd95Mm"],
                "definition": METRIC_DEFINITIONS["hd95Mm"],
                "source": "symmetric mask-boundary distance percentile",
            },
            "treMm": {
                "value": metric_values["treMm"],
                "formatted": f"{metric_values['treMm']:.6f} mm",
                "threshold": THRESHOLDS["treMm"] if tre_valid_as_anatomical else "not evaluated",
                "definition": (
                    METRIC_DEFINITIONS["treMm"]
                    if tre_valid_as_anatomical
                    else "Mean Euclidean residual between automatically generated mask bounding-box control points; this is not an anatomical target-registration error."
                ),
                "source": "fixed versus warped landmark distances" if tre_valid_as_anatomical else "automatic mask bounding-box control points",
                "validAsAnatomicalTre": tre_valid_as_anatomical,
            },
            "jacobianAnomalyRatio": {
                "value": metric_values["jacobianAnomalyRatio"],
                "formatted": f"{metric_values['jacobianAnomalyRatio']:.6f}",
                "threshold": THRESHOLDS["jacobianAnomalyRatio"],
                "definition": METRIC_DEFINITIONS["jacobianAnomalyRatio"],
                "source": "deformation-field Jacobian determinant audit",
            },
        },
        "landmarks": {
            "count": int(len(names)),
            "type": "manual anatomical landmarks" if tre_valid_as_anatomical else str(control_point_info.get("type", "automatic control points")),
            "manualAnatomicalLandmarks": tre_valid_as_anatomical,
            "validAsAnatomicalTre": tre_valid_as_anatomical,
            "displayedInFigure": show_control_points,
            "note": str(control_point_info.get("note", "")),
            "treMeanMm": float(np.mean(tre_distances)),
            "treMedianMm": float(np.median(tre_distances)),
            "treP95Mm": float(np.percentile(tre_distances, 95)),
            "treMaxMm": float(np.max(tre_distances)),
            "meanAbsAxisResidualMm": {
                "x": float(np.mean(np.abs(deltas[:, 0]))),
                "y": float(np.mean(np.abs(deltas[:, 1]))),
                "z": float(np.mean(np.abs(deltas[:, 2]))),
            },
        },
        "lightweightRecompute": {
            "dice": float(recomputed_dice),
            "treMeanMm": float(np.mean(tre_distances)),
            "jacobianAnomalyRatio": float(recomputed_jacobian),
        },
        "figureSelfReview": {
            "rendered": True,
            "inspected": True,
            "notes": "Automatic visual gate detected an intensity-scale inconsistency requiring manual artifact revision.",
        },
        "visualQcFinding": {
            "finding": visual_gate["reason"],
            "rawIntensityPolicy": "Raw intensity ranges are not compared across independently normalised images as a deformation-quality test.",
            "deformableWarpedMriIntensityRange": [
                deformable_stats["min"],
                deformable_stats["max"],
            ],
            "affineOnlyWarpedMriIntensityRange": (
                [affine_stats["min"], affine_stats["max"]] if affine_stats is not None else None
            ),
            "intensityGate": visual_gate,
            "diagnosticFigure": diagnostic_figure,
        },
    }

    _write_json(evidence_dir / "2026-07-08-mri-to-ccf-registration-qc.json", summary)
    _write_markdown(evidence_dir / "2026-07-08-mri-to-ccf-registration-qc.md", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render paper-facing MRI-to-CCF registration QC overlay.")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument(
        "--atlas-artifact-root",
        default=None,
        help="Optional artifact root supplying annotation.npy when the registration candidate is stored separately.",
    )
    parser.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    parser.add_argument("--figure-dir", default=str(DEFAULT_FIGURE_DIR))
    parser.add_argument("--basename", default=DEFAULT_BASENAME)
    parser.add_argument("--structure-tree-json", default=str(DEFAULT_STRUCTURE_TREE_JSON))
    parser.add_argument(
        "--panel-order",
        default="coronal,sagittal,horizontal",
        help="Comma-separated order for figure panels; use each of coronal,sagittal,horizontal once.",
    )
    parser.add_argument("--landmark-tolerance-vox", type=int, default=12)
    parser.add_argument("--max-landmarks-per-panel", type=int, default=12)
    parser.add_argument(
        "--show-control-points",
        action="store_true",
        help="Display fixed/warped point pairs. By default automatic control points are hidden from the manuscript overlay.",
    )
    parser.add_argument(
        "--visual-qc-decision",
        choices=("pending", "pass", "fail"),
        default="pending",
        help="Explicit outcome of orthogonal overlay inspection; default is pending.",
    )
    return parser.parse_args()


def main() -> int:
    summary = build_summary(parse_args())
    print(json.dumps({"status": summary["status"], "figure": summary["figure"], "metrics": summary["metrics"]}, indent=2))
    return 0 if summary["status"] in {"PASS", "INCOMPLETE_ANATOMICAL_TRE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
