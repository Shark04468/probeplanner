#!/usr/bin/env python3
"""Render real atlas case-study figures for automatic probe planning."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as atlas_util  # noqa: E402
import benchmark_auto_probe_count_planning as auto_bench  # noqa: E402
import render_mri_ccf_registration_qc_figure as qc_fig  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(
    os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full")
)
DEFAULT_REGISTRATION_ROOT = Path(
    os.environ.get("PROBEPLANNER_REGISTRATION_ROOT", "artifacts/registration")
)
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_FIGURE_DIR = DEFAULT_EVIDENCE_DIR / "figures"
DEFAULT_STEM = "2026-07-10-real-atlas-case-study-target-safe"
VOXEL_SIZE_UM = 25.0

TARGET_COLORS = {
    "VPM": "#2F80ED",
    "CA1": "#7A3E9D",
    "CA3": "#B05687",
    "DG": "#C4802A",
    "VISp": "#219653",
    "MOs": "#D3543A",
    "SSp": "#6A7C2B",
}
SELECTED_COLORS = ["#D53343", "#006D77", "#6F4E9B", "#B5651D"]
LABEL_OFFSETS = [(-14.0, -8.0), (14.0, 8.0), (0.0, 16.0), (-18.0, 14.0), (18.0, -14.0)]


@dataclass(frozen=True)
class CaseStudy:
    case_id: str
    title: str
    case_type: str
    display_purpose: str
    requested_targets: tuple[str, ...]
    expected_probe_count: int | None = None
    targeting_mode: str = "single_hemisphere"
    expect_fail_closed: bool = False
    depth_um: float = 7600.0
    grid_spacing_um: float = 1200.0
    angle_step_deg: float = 10.0
    max_angle_deg: float = 10.0
    coarse_step_um: float = 150.0
    refine_step_um: float = 75.0
    refine_fraction: float = 0.12
    entry_spacing_um: float = 1200.0
    min_clearance_um: float = 1000.0
    min_risk_distance_mm: float = 0.05


CASES: tuple[CaseStudy, ...] = (
    CaseStudy(
        case_id="case_01_single_vpm",
        title="Single target: VPM",
        case_type="single-region target",
        display_purpose="System outputs one selected probe for a localized thalamic target.",
        requested_targets=("VPM",),
        expected_probe_count=1,
    ),
    CaseStudy(
        case_id="case_02_merged_hippocampal",
        title="Nearby multi-target: CA1 + CA3 + DG",
        case_type="nearby multi-region target",
        display_purpose="System uses one probe to cover adjacent hippocampal targets.",
        requested_targets=("CA1", "CA3", "DG"),
        expected_probe_count=1,
    ),
    CaseStudy(
        case_id="case_03_distributed_multitarget",
        title="Distributed targets: CA1 + VPM + MOs",
        case_type="distributed multi-region target",
        display_purpose="System automatically increases probe count for spatially separated targets.",
        requested_targets=("CA1", "VPM", "MOs"),
        expected_probe_count=2,
    ),
    CaseStudy(
        case_id="case_04_depth_limited_uncovered",
        title="Infeasible constrained case: depth-limited VPM",
        case_type="infeasible target",
        display_purpose="System reports uncovered target instead of emitting a false trajectory.",
        requested_targets=("VPM",),
        expected_probe_count=0,
        expect_fail_closed=True,
        depth_um=500.0,
        max_angle_deg=0.0,
    ),
)


def case_to_benchmark(case: CaseStudy) -> auto_bench.BenchmarkCase:
    return auto_bench.BenchmarkCase(
        case_id=case.case_id,
        case_type=case.case_type,
        purpose=case.display_purpose,
        requested_targets=case.requested_targets,
        targeting_mode=case.targeting_mode,
        expected_probe_count=case.expected_probe_count,
        expect_fail_closed=case.expect_fail_closed,
        depth_um=case.depth_um,
        grid_spacing_um=case.grid_spacing_um,
        angle_step_deg=case.angle_step_deg,
        max_angle_deg=case.max_angle_deg,
        coarse_step_um=case.coarse_step_um,
        refine_step_um=case.refine_step_um,
        refine_fraction=case.refine_fraction,
        entry_spacing_um=case.entry_spacing_um,
        min_clearance_um=case.min_clearance_um,
        min_risk_distance_mm=case.min_risk_distance_mm,
    )


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def hex_to_rgb01(value: str) -> tuple[float, float, float]:
    value = value.strip().lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def rgba_mask(mask: np.ndarray, color_hex: str, alpha: float) -> np.ndarray:
    rgb = hex_to_rgb01(color_hex)
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[mask, 0] = rgb[0]
    out[mask, 1] = rgb[1]
    out[mask, 2] = rgb[2]
    out[mask, 3] = alpha
    return out


def um_to_vox(xyz_um: Any) -> np.ndarray | None:
    if not isinstance(xyz_um, (list, tuple, np.ndarray)) or len(xyz_um) != 3:
        return None
    vals = [finite_float(v) for v in xyz_um]
    if any(v is None for v in vals):
        return None
    return np.asarray([float(v) / VOXEL_SIZE_UM for v in vals], dtype=np.float64)


def region_center_vox(server: Any, key: str) -> np.ndarray | None:
    reg = server.PLANNER_REGIONS.get(key)
    if not isinstance(reg, dict):
        return None
    sid = reg.get("id")
    if sid is None:
        return None
    centroids = getattr(server, "REGION_CENTROIDS_UM", {})
    center_um = centroids.get(int(sid)) if isinstance(centroids, dict) else None
    if center_um is None:
        center_um = [
            float(server.BREGMA_UM[0]) + float(reg.get("ap", 0.0)) * 1000.0,
            float(server.BREGMA_UM[1]) + float(reg.get("dv", 0.0)) * 1000.0,
            float(server.BREGMA_UM[2]) + float(reg.get("ml", 0.0)) * 1000.0,
        ]
    return um_to_vox(center_um)


def target_label_ids(server: Any, key: str) -> set[int]:
    reg = server.PLANNER_REGIONS.get(key)
    if not isinstance(reg, dict) or reg.get("id") is None:
        return set()
    sid = int(reg["id"])
    desc = getattr(server, "descendant_ids", {}).get(sid)
    if desc:
        return {int(v) for v in desc}
    return {sid}


def candidate_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(candidate.get("id", "")),
        round(float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)) or 0.0), 6),
        round(float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)) or 0.0), 6),
        round(float(candidate.get("theta_deg", 0.0) or 0.0), 6),
        round(float(candidate.get("phi_deg", 0.0) or 0.0), 6),
        tuple(str(v) for v in candidate.get("coveredRegions", []) or []),
    )


def is_same_candidate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return candidate_key(a) == candidate_key(b)


def run_case(server: Any, case: CaseStudy, workers: int) -> dict[str, Any]:
    benchmark_case = case_to_benchmark(case)
    started = time.time()
    request = auto_bench.case_request(benchmark_case, workers)
    request["params"].update(
        {
            "targetEndpointPolicy": "tip_in_target",
            "includeCandidatePreview": True,
            "autoAvoidEnabled": True,
            "strictFallbackDecompose": False,
        }
    )
    request["constraintModes"].update(
        {
            "lambdaBoundary": "enforce",
            "brainBounds": "enforce",
            "continuousRiskDistance": "enforce",
        }
    )
    payload = server._normalize_optimize_payload(deepcopy(request))
    target_keys = payload["targetKeys"]
    unsupported = [key for key in case.requested_targets if key not in server.PLANNER_REGIONS]
    params = payload["params"]
    probe_params = payload["probeParams"]
    constraint_modes = payload["constraintModes"]
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(probe_params.get("entrySpacingUm", 3500.0))

    candidates: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    missing_tokens: set[str] = set()
    covered_tokens: set[str] = set()
    required_tokens: set[str] = set()
    generation_summary: dict[str, Any] = {}
    exact: dict[str, Any] = {"exactMinProbeCount": None}
    error = None
    error_code = None
    error_extra: dict[str, Any] | None = None

    if unsupported:
        missing_tokens = set(unsupported)
        required_tokens = set(unsupported)
    else:
        try:
            result = server._run_optimize_pipeline(request)
            selected = list(result.get("selected") or [])
            candidates = list(result.get("candidatePreview") or [])
            generation_summary = deepcopy(
                ((result.get("computeProfile") or {}).get("candidateGeneration") or {})
            )
            missing_tokens = set(result.get("uncovered") or [])
            covered_tokens = {
                key
                for probe in selected
                for key in (probe.get("coveredRegions") or [])
                if key in target_keys
            }
            required_tokens = set(target_keys)
            exact = auto_bench.token_exact_set_cover(
                server,
                candidates,
                target_keys,
                params.get("targetingMode", "single_hemisphere"),
                min_clearance_um=min_clearance,
                min_entry_um=min_entry,
                max_k=6,
            )
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            error_code = getattr(exc, "code", None)
            raw_extra = getattr(exc, "extra", None)
            error_extra = deepcopy(raw_extra) if isinstance(raw_extra, dict) else None
            missing_tokens = set(target_keys)
            required_tokens = set(target_keys)

    uncovered_targets, uncovered_sides = auto_bench.summarize_uncovered(
        params.get("targetingMode", case.targeting_mode),
        sorted(missing_tokens),
    )
    selected_probe_count = int(len(selected))
    selected_payload = [auto_bench.probe_payload(server, probe, idx) for idx, probe in enumerate(selected, start=1)]
    status = "ok"
    if unsupported:
        status = "unsupported"
    elif error:
        status = "fail_closed"
    elif missing_tokens or selected_probe_count == 0:
        status = "fail_closed" if case.expect_fail_closed else "partial"

    risk_values = [
        finite_float(probe.get("worstRiskMarginMm"))
        for probe in selected
        if finite_float(probe.get("worstRiskMarginMm")) is not None
    ]
    return {
        "caseId": case.case_id,
        "title": case.title,
        "caseType": case.case_type,
        "displayPurpose": case.display_purpose,
        "requestedTargets": list(case.requested_targets),
        "targetingMode": params.get("targetingMode", case.targeting_mode),
        "status": status,
        "error": error,
        "errorCode": error_code,
        "errorExtra": error_extra,
        "unsupportedTargets": unsupported,
        "candidateCount": int(len(candidates)),
        "selectedProbeCount": selected_probe_count,
        "expectedProbeCount": case.expected_probe_count,
        "exactMinProbeCount": exact.get("exactMinProbeCount"),
        "coveredTokens": sorted(covered_tokens),
        "requiredCoverageUnits": int(len(required_tokens)),
        "coveredCoverageUnits": int(len(covered_tokens)),
        "uncoveredTargets": uncovered_targets,
        "uncoveredSides": uncovered_sides,
        "riskWorstMarginMm": round(float(min(risk_values)), 6) if risk_values else None,
        "runtimeSec": round(float(time.time() - started), 6),
        "candidateGeneration": generation_summary,
        "plannerRequest": request,
        "candidates": candidates,
        "selectedRaw": selected,
        "selectedProbes": selected_payload,
    }


def plane_axis(plane: str) -> int:
    return {"coronal": 0, "horizontal": 1, "sagittal": 2}[plane]


def segment_near_slice(entry_vox: np.ndarray, tip_vox: np.ndarray, plane: str, index: int, tolerance: float) -> bool:
    axis = plane_axis(plane)
    a = float(entry_vox[axis])
    b = float(tip_vox[axis])
    lo = min(a, b)
    hi = max(a, b)
    if lo - tolerance <= float(index) <= hi + tolerance:
        return True
    return min(abs(a - float(index)), abs(b - float(index))) <= tolerance


def segment_in_slice_slab(
    entry_vox: np.ndarray,
    tip_vox: np.ndarray,
    plane: str,
    index: int,
    tolerance: float,
) -> np.ndarray | None:
    """Clip a 3D segment to the displayed slice slab before projecting it."""
    axis = plane_axis(plane)
    a = float(entry_vox[axis])
    b = float(tip_vox[axis])
    low = float(index) - float(tolerance)
    high = float(index) + float(tolerance)
    delta = b - a
    if abs(delta) <= 1e-9:
        if low <= a <= high:
            return np.vstack([entry_vox, tip_vox])
        return None
    t0 = (low - a) / delta
    t1 = (high - a) / delta
    start = max(0.0, min(t0, t1))
    end = min(1.0, max(t0, t1))
    if start > end:
        return None
    direction = tip_vox - entry_vox
    return np.vstack([entry_vox + direction * start, entry_vox + direction * end])


def point_in_slice_slab(point_vox: np.ndarray, plane: str, index: int, tolerance: float) -> bool:
    return abs(float(point_vox[plane_axis(plane)]) - float(index)) <= float(tolerance)


def project_segment(entry_vox: np.ndarray, tip_vox: np.ndarray, plane: str) -> np.ndarray:
    return qc_fig._display_xy(np.vstack([entry_vox, tip_vox]), plane)


def project_point(vox: np.ndarray, plane: str) -> np.ndarray:
    return qc_fig._display_xy(np.asarray([vox], dtype=np.float64), plane)[0]


def choose_slices(server: Any, case_result: dict[str, Any], shape: tuple[int, int, int]) -> dict[str, int]:
    target_voxels = []
    for key in case_result["requestedTargets"]:
        center = region_center_vox(server, key)
        if center is not None:
            target_voxels.append(center)
    selected_entries = []
    selected_tips = []
    selected_midpoints = []
    for probe in case_result.get("selectedRaw") or []:
        entry = um_to_vox(probe.get("entry_xyz"))
        tip = um_to_vox(probe.get("tip_xyz"))
        if entry is not None and tip is not None:
            selected_entries.append(entry)
            selected_tips.append(tip)
            selected_midpoints.append((entry + tip) / 2.0)
    if not target_voxels and not selected_midpoints:
        center = np.asarray(shape, dtype=np.float64) / 2.0
        target_voxels = [center]

    if selected_midpoints:
        mids = np.vstack(selected_midpoints)
        tips = np.vstack(selected_tips)
        targets = np.vstack(target_voxels) if target_voxels else mids
        # Coronal and sagittal planes are selected through trajectory midpoints
        # so a physical slice slab contains a visible selected segment. The
        # horizontal plane remains tip-focused to display terminal targeting.
        coronal = float(np.median(mids[:, 0]))
        sagittal = float(np.median(mids[:, 2]))
        horizontal = float(np.median(np.vstack([tips, targets])[:, 1]))
    else:
        arr = np.vstack(target_voxels)
        coronal = float(np.median(arr[:, 0]))
        horizontal = float(np.median(arr[:, 1]))
        sagittal = float(np.median(arr[:, 2]))
    return {
        "coronal": int(np.clip(round(coronal), 0, shape[0] - 1)),
        "horizontal": int(np.clip(round(horizontal), 0, shape[1] - 1)),
        "sagittal": int(np.clip(round(sagittal), 0, shape[2] - 1)),
    }


def crop_to_content(
    shape_2d: tuple[int, int],
    masks: list[np.ndarray],
    point_sets: list[np.ndarray],
    *,
    pad: float = 28.0,
) -> tuple[tuple[float, float], tuple[float, float]]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    for mask in masks:
        if mask.size == 0:
            continue
        rows, cols = np.where(mask)
        if rows.size:
            xs.append(cols.astype(np.float64))
            ys.append(rows.astype(np.float64))
    for points in point_sets:
        if points.size:
            xs.append(points[:, 0].astype(np.float64))
            ys.append(points[:, 1].astype(np.float64))
    if not xs or not ys:
        return (0.0, float(shape_2d[1])), (0.0, float(shape_2d[0]))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    xlim = (max(0.0, float(np.min(x)) - pad), min(float(shape_2d[1] - 1), float(np.max(x)) + pad))
    ylim = (max(0.0, float(np.min(y)) - pad), min(float(shape_2d[0] - 1), float(np.max(y)) + pad))
    if xlim[1] - xlim[0] < 80:
        mid = (xlim[0] + xlim[1]) / 2.0
        xlim = (max(0.0, mid - 40), min(float(shape_2d[1] - 1), mid + 40))
    if ylim[1] - ylim[0] < 80:
        mid = (ylim[0] + ylim[1]) / 2.0
        ylim = (max(0.0, mid - 40), min(float(shape_2d[0] - 1), mid + 40))
    return xlim, ylim


def draw_case_figure(
    *,
    server: Any,
    case_result: dict[str, Any],
    mri: np.ndarray,
    annotation: np.ndarray,
    structure_colors: dict[int, tuple[float, float, float]],
    figure_base: Path,
) -> dict[str, Any]:
    qc_fig._maybe_apply_style()
    panel_order = ["coronal", "sagittal", "horizontal"]
    slices = choose_slices(server, case_result, tuple(annotation.shape))
    target_ids = {key: target_label_ids(server, key) for key in case_result["requestedTargets"]}
    target_centers = {
        key: region_center_vox(server, key)
        for key in case_result["requestedTargets"]
        if region_center_vox(server, key) is not None
    }

    fig, axes = plt.subplots(1, 3, figsize=(10.8, 3.8), constrained_layout=False)
    selected_raw = case_result.get("selectedRaw") or []
    selected_keys = [candidate_key(probe) for probe in selected_raw]
    candidate_pool = case_result.get("candidates") or []
    panel_records = []

    for panel_idx, plane in enumerate(panel_order):
        ax = axes[panel_idx]
        index = slices[plane]
        mri_panel = qc_fig._normalise_grayscale(qc_fig._slice_panel(mri, plane, index))
        ann_panel = qc_fig._slice_panel(annotation, plane, index)
        brain_mask = ann_panel > 0

        ax.imshow(mri_panel, cmap="gray", origin="lower", interpolation="nearest")
        ax.imshow(qc_fig._annotation_rgba(ann_panel, structure_colors, alpha=0.11), origin="lower", interpolation="nearest")
        if np.any(brain_mask):
            ax.contour(brain_mask.astype(np.uint8), levels=[0.5], colors=["#315D72"], linewidths=0.9)

        target_masks = []
        label_points = []
        for target_idx, target in enumerate(case_result["requestedTargets"]):
            ids = target_ids.get(target) or set()
            if ids:
                mask = np.isin(ann_panel, list(ids))
            else:
                mask = np.zeros_like(ann_panel, dtype=bool)
            target_masks.append(mask)
            if np.any(mask):
                ax.imshow(
                    rgba_mask(mask, TARGET_COLORS.get(target, "#F2C94C"), 0.52),
                    origin="lower",
                    interpolation="nearest",
                    zorder=4,
                )
                rows, cols = np.where(mask)
                label_points.append(np.asarray([[float(np.median(cols)), float(np.median(rows))]], dtype=np.float64))
                label_xy = np.asarray([float(np.median(cols)), float(np.median(rows))])
            else:
                center = target_centers.get(target)
                label_xy = project_point(center, plane) if center is not None else np.asarray([18.0, 18.0])
                label_points.append(np.asarray([label_xy], dtype=np.float64))
            if len(case_result["requestedTargets"]) > 1:
                dx, dy = LABEL_OFFSETS[target_idx % len(LABEL_OFFSETS)]
                label_xy = np.asarray([float(label_xy[0]) + dx, float(label_xy[1]) + dy], dtype=np.float64)
                label_points[-1] = np.asarray([label_xy], dtype=np.float64)
            label_color = TARGET_COLORS.get(target, "#F2C94C")
            missing = target in set(case_result.get("uncoveredTargets") or [])
            label = f"{target}{' uncovered' if missing else ''}"
            ax.text(
                float(label_xy[0]),
                float(label_xy[1]),
                label,
                fontsize=6.7,
                color="#111111",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.16", "fc": "white", "ec": label_color, "lw": 0.65, "alpha": 0.82},
                zorder=12,
            )

        candidate_points = []
        for cand in candidate_pool:
            if candidate_key(cand) in selected_keys:
                continue
            entry = um_to_vox(cand.get("entry_xyz"))
            tip = um_to_vox(cand.get("tip_xyz"))
            if entry is None or tip is None:
                continue
            segment = segment_in_slice_slab(entry, tip, plane, index, tolerance=12.0)
            if segment is None:
                continue
            xy = project_segment(segment[0], segment[1], plane)
            candidate_points.append(xy)
            ax.plot(
                xy[:, 0],
                xy[:, 1],
                color="#8B8F96",
                linewidth=0.55,
                alpha=0.30,
                zorder=5,
            )

        selected_points = []
        for selected_idx, probe in enumerate(selected_raw):
            entry = um_to_vox(probe.get("entry_xyz"))
            tip = um_to_vox(probe.get("tip_xyz"))
            if entry is None or tip is None:
                continue
            segment = segment_in_slice_slab(entry, tip, plane, index, tolerance=12.0)
            if segment is None:
                continue
            xy = project_segment(segment[0], segment[1], plane)
            selected_points.append(xy)
            color = SELECTED_COLORS[selected_idx % len(SELECTED_COLORS)]
            ax.plot(xy[:, 0], xy[:, 1], color=color, linewidth=2.15, alpha=0.96, zorder=8)
            if point_in_slice_slab(entry, plane, index, tolerance=12.0):
                entry_xy = project_point(entry, plane)
                ax.scatter(entry_xy[0], entry_xy[1], marker="^", s=32, c=color, edgecolors="white", linewidths=0.55, zorder=10)
                ax.text(
                    float(entry_xy[0]),
                    float(entry_xy[1]) + 6.0,
                    f"E{selected_idx + 1}",
                    fontsize=6.5,
                    color=color,
                    ha="center",
                    va="bottom",
                    bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.72},
                    zorder=12,
                )
            if point_in_slice_slab(tip, plane, index, tolerance=12.0):
                tip_xy = project_point(tip, plane)
                ax.scatter(tip_xy[0], tip_xy[1], marker="o", s=30, c=color, edgecolors="white", linewidths=0.55, zorder=10)
                ax.text(
                    float(tip_xy[0]),
                    float(tip_xy[1]) - 6.0,
                    f"T{selected_idx + 1}",
                    fontsize=6.5,
                    color=color,
                    ha="center",
                    va="top",
                    bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.72},
                    zorder=12,
                )

        point_sets = [*candidate_points, *selected_points, *label_points]
        xlim, ylim = crop_to_content(mri_panel.shape, [brain_mask, *target_masks], point_sets, pad=34.0)
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        if plane == "sagittal":
            # CCF DV increases ventrally; use dorsal-up anatomical display.
            ax.invert_yaxis()
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{chr(65 + panel_idx)}. {qc_fig.PLANE_TITLES[plane]} slice {index}", fontsize=9, pad=4)
        for spine in ax.spines.values():
            spine.set_visible(False)
        panel_records.append(
            {
                "plane": plane,
                "sliceIndex": int(index),
                "candidateTrajectoriesDrawn": int(len(candidate_points)),
                "selectedProbesDrawn": int(len(selected_points)),
            }
        )

    status = case_result["status"]
    title = (
        f"{case_result['title']} | selected probes: {case_result['selectedProbeCount']} "
        f"| candidates: {case_result['candidateCount']} | status: {status}"
    )
    fig.suptitle(title, fontsize=10.2, y=0.98)
    legend_handles = [
        Line2D([0], [0], color="#315D72", lw=1.0, label="CCF annotation brain mask"),
        Line2D([0], [0], color="#8B8F96", lw=1.0, alpha=0.7, label="safe candidate segments"),
        Line2D([0], [0], color=SELECTED_COLORS[0], lw=2.0, label="selected probe"),
        Line2D([0], [0], marker="^", color="none", markerfacecolor=SELECTED_COLORS[0], markeredgecolor="white", label="entry"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=SELECTED_COLORS[0], markeredgecolor="white", label="tip"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="#F2C94C", alpha=0.58, label="target region"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="lower center",
        ncol=6,
        frameon=False,
        fontsize=7.0,
        bbox_to_anchor=(0.5, 0.015),
        handlelength=1.5,
        columnspacing=1.0,
    )
    fig.subplots_adjust(left=0.02, right=0.985, top=0.86, bottom=0.15, wspace=0.04)

    figure_base.parent.mkdir(parents=True, exist_ok=True)
    png = figure_base.with_suffix(".png")
    pdf = figure_base.with_suffix(".pdf")
    svg = figure_base.with_suffix(".svg")
    fig.savefig(png, dpi=320)
    fig.savefig(pdf)
    fig.savefig(svg)
    plt.close(fig)
    return {
        "png": str(png.resolve()),
        "pdf": str(pdf.resolve()),
        "svg": str(svg.resolve()),
        "panels": panel_records,
        "targetIds": {key: sorted(ids) for key, ids in target_ids.items()},
    }


def strip_heavy_case(case_result: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(case_result)
    out.pop("candidates", None)
    out.pop("selectedRaw", None)
    return out


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "caseId",
        "caseType",
        "requestedTargets",
        "candidateCount",
        "selectedProbeCount",
        "expectedProbeCount",
        "exactMinProbeCount",
        "coveredCoverageUnits",
        "requiredCoverageUnits",
        "uncoveredTargets",
        "unsupportedTargets",
        "riskWorstMarginMm",
        "runtimeSec",
        "status",
        "figurePng",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "caseId": case["caseId"],
                    "caseType": case["caseType"],
                    "requestedTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                    "candidateCount": case["candidateCount"],
                    "selectedProbeCount": case["selectedProbeCount"],
                    "expectedProbeCount": case.get("expectedProbeCount"),
                    "exactMinProbeCount": case.get("exactMinProbeCount"),
                    "coveredCoverageUnits": case.get("coveredCoverageUnits"),
                    "requiredCoverageUnits": case.get("requiredCoverageUnits"),
                    "uncoveredTargets": json.dumps(case.get("uncoveredTargets") or [], ensure_ascii=False),
                    "unsupportedTargets": json.dumps(case.get("unsupportedTargets") or [], ensure_ascii=False),
                    "riskWorstMarginMm": case.get("riskWorstMarginMm"),
                    "runtimeSec": case.get("runtimeSec"),
                    "status": case.get("status"),
                    "figurePng": case.get("figure", {}).get("png"),
                }
            )


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def repo_rel(path_text: str) -> str:
    path = Path(path_text).resolve()
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = payload["cases"]
    summary = payload["summary"]
    table = [
        "| case | purpose | targets | candidates | selected probes | coverage units | uncovered | risk margin mm | status |",
        "|---|---|---|---:|---:|---:|---|---:|---|",
    ]
    for case in cases:
        table.append(
            "| {case} | {purpose} | {targets} | {cand} | {sel} | {covered}/{required} | {uncovered} | {risk} | {status} |".format(
                case=case["caseId"],
                purpose=case["displayPurpose"],
                targets=", ".join(case["requestedTargets"]),
                cand=case["candidateCount"],
                sel=case["selectedProbeCount"],
                covered=case["coveredCoverageUnits"],
                required=case["requiredCoverageUnits"],
                uncovered=fmt(case.get("uncoveredTargets")),
                risk=fmt(case.get("riskWorstMarginMm"), 3),
                status=case["status"],
            )
        )

    figure_blocks = []
    for case in cases:
        fig_path = repo_rel(case["figure"]["png"])
        figure_blocks.extend(
            [
                f"### {case['title']}",
                "",
                f"![{case['caseId']}](figures/{Path(fig_path).name})",
                "",
                f"- Requested targets: {', '.join(case['requestedTargets'])}",
                f"- Candidate trajectories: {case['candidateCount']}",
                f"- Selected probe count: {case['selectedProbeCount']}",
                f"- Covered tokens: {fmt(case.get('coveredTokens'))}",
                f"- Uncovered targets: {fmt(case.get('uncoveredTargets'))}",
                f"- Status: {case['status']}",
                "",
            ]
        )

    lines = [
        "# Real Atlas Case Studies for Automatic Probe Planning - 2026-07-10",
        "",
        "This case-study experiment renders real CCF/MRI atlas planning examples. Each figure overlays the registered MRI grayscale image, Allen annotation regions, highlighted target regions, gray candidate trajectories, selected probe trajectories, entry/tip markers, covered/uncovered target labels, and the automatically selected probe count.",
        "",
        "## Summary",
        "",
        f"- Case-study figures: {summary['caseCount']}",
        f"- Successful planned cases: {summary['okCaseCount']}",
        f"- Fail-closed or unsupported cases: {summary['nonOkCaseCount']}",
        f"- Selected probe counts shown: {', '.join(str(v) for v in summary['selectedProbeCountsObserved'])}",
        f"- Full-coverage planned cases: {summary['fullCoverageCaseCount']}/{summary['caseCount']}",
        "",
        "## Case Summary",
        "",
        *table,
        "",
        "## Figures",
        "",
        *figure_blocks,
        "## Interpretation",
        "",
        "The case studies provide a visual check that ProbePlanner is not merely reporting aggregate feasibility. The same backend candidate generation and automatic selection path produces compact probe sets for localized and nearby targets, increases probe count for distributed targets, and reports uncovered targets when the constraints make a target unreachable.",
        "",
        "## Data Inputs",
        "",
        f"- Planner profile root: `{payload['profileRoot']}`",
        f"- MRI-in-CCF volume: `{payload['dataInputs']['mriInCcf']}`",
        f"- Allen annotation volume: `{payload['dataInputs']['annotation']}`",
        f"- Structure color source: `{payload['dataInputs']['structureTreeColors']}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render real atlas case-study planning figures.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--registration-root", default=str(DEFAULT_REGISTRATION_ROOT))
    parser.add_argument("--mri-path", default=None)
    parser.add_argument("--annotation-path", default=None)
    parser.add_argument("--structures-json", default=str(atlas_util.DEFAULT_STRUCTURES_JSON))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-json", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.json"))
    parser.add_argument("--output-md", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.md"))
    parser.add_argument("--output-csv", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.csv"))
    parser.add_argument("--figure-dir", default=str(DEFAULT_FIGURE_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile_root = Path(args.profile_root).expanduser()
    registration_root = Path(args.registration_root).expanduser()
    annotation_path = Path(args.annotation_path).expanduser() if args.annotation_path else profile_root / "annotation.npy"
    mri_path = (
        Path(args.mri_path).expanduser()
        if args.mri_path
        else registration_root / "registration" / "ambmc_in_ccf.nii.gz"
    )
    if not mri_path.exists():
        fallback = profile_root / "registration" / "ambmc_in_ccf.nii.gz"
        if fallback.exists():
            mri_path = fallback
    if not annotation_path.exists():
        raise FileNotFoundError(f"Annotation volume not found: {annotation_path}")
    if not mri_path.exists():
        raise FileNotFoundError(f"MRI-in-CCF volume not found: {mri_path}")

    atlas_util.configure_backend_environment(profile_root, workers=int(args.workers))
    server = atlas_util.import_backend_server()
    started = time.time()
    server.load_atlas()
    if not getattr(server, "atlas_loaded", False):
        raise RuntimeError(f"Atlas failed to load: {getattr(server, 'atlas_error', None)}")

    all_targets = sorted({target for case in CASES for target in case.requested_targets})
    ontology_fallback = atlas_util.apply_structure_tree_descendant_fallback(
        server,
        all_targets,
        Path(args.structures_json).expanduser(),
    )
    mri = qc_fig._load_nifti_array(mri_path)
    annotation = np.load(annotation_path, mmap_mode="r")
    structure_colors, structure_color_source = qc_fig._load_structure_colors(Path(args.structures_json).expanduser())

    figure_dir = Path(args.figure_dir).expanduser()
    results = []
    with server.app.app_context():
        for idx, case in enumerate(CASES, start=1):
            print(f"[{idx}/{len(CASES)}] {case.case_id}", flush=True)
            result = run_case(server, case, workers=int(args.workers))
            figure = draw_case_figure(
                server=server,
                case_result=result,
                mri=mri,
                annotation=annotation,
                structure_colors=structure_colors,
                figure_base=figure_dir / f"{DEFAULT_STEM}-{case.case_id}",
            )
            result["figure"] = figure
            results.append(strip_heavy_case(result))

    full_coverage = [
        case for case in results
        if int(case.get("requiredCoverageUnits", 0) or 0) > 0
        and int(case.get("coveredCoverageUnits", 0) or 0) == int(case.get("requiredCoverageUnits", 0) or 0)
    ]
    summary = {
        "caseCount": int(len(results)),
        "okCaseCount": int(sum(1 for case in results if case.get("status") == "ok")),
        "nonOkCaseCount": int(sum(1 for case in results if case.get("status") != "ok")),
        "fullCoverageCaseCount": int(len(full_coverage)),
        "selectedProbeCountsObserved": sorted({int(case.get("selectedProbeCount", 0) or 0) for case in results}),
        "totalRuntimeSec": round(float(time.time() - started), 6),
    }
    payload = {
        "schemaVersion": "probeplanner.real_atlas_case_study.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(profile_root.resolve()),
        "atlasSignature": deepcopy(getattr(server, "atlas_signature", {})),
        "ontologyFallback": ontology_fallback,
        "dataInputs": {
            "mriInCcf": str(mri_path.resolve()),
            "annotation": str(annotation_path.resolve()),
            "structureTreeColors": structure_color_source,
        },
        "summary": summary,
        "cases": results,
    }

    out_json = Path(args.output_json).expanduser()
    out_md = Path(args.output_md).expanduser()
    out_csv = Path(args.output_csv).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_csv, results)
    write_markdown(out_md, payload)
    print(json.dumps({"summary": summary, "json": str(out_json), "md": str(out_md), "csv": str(out_csv)}, indent=2))

    gate_failures = []
    if summary["caseCount"] < 4:
        gate_failures.append("Expected four case-study examples.")
    if results[0].get("selectedProbeCount") != 1:
        gate_failures.append("Single-target case did not select one probe.")
    if results[1].get("selectedProbeCount") != 1:
        gate_failures.append("Nearby multi-target case did not merge into one probe.")
    if int(results[2].get("selectedProbeCount", 0) or 0) < 2:
        gate_failures.append("Distributed case did not increase selected probe count.")
    if results[3].get("status") not in {"fail_closed", "unsupported"}:
        gate_failures.append("Infeasible case did not fail closed or report unsupported.")
    for case in results:
        figure = case.get("figure", {})
        if not Path(figure.get("png", "")).exists():
            gate_failures.append(f"Missing figure for {case.get('caseId')}.")
    if gate_failures:
        print(json.dumps({"acceptanceGate": "failed", "failures": gate_failures}, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
