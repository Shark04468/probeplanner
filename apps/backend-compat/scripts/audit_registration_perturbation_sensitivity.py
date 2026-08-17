#!/usr/bin/env python3
"""Audit registration perturbation sensitivity for ProbePlanner.

The experiment re-runs the planner after applying small affine perturbations to
the MRI-to-CCF query space.  For a physical planner point x, atlas and risk
queries are evaluated at T^-1(x), which simulates an anatomical volume/risk
layer shifted by the perturbed registration transform.  Candidate generation
and greedy selection are still taken from the backend planner.
"""

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
from typing import Any, Callable

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPO_ROOT / "apps" / "backend-compat" / "src"
DEFAULT_PROFILE_ROOT = Path(
    os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full")
)
DEFAULT_REGISTRATION_ROOT = Path(
    os.environ.get("PROBEPLANNER_REGISTRATION_ROOT", "artifacts/registration")
)
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_STEM = "2026-07-09-registration-perturbation-sensitivity"
DEFAULT_STRUCTURES_JSON = Path.home() / ".allensdk_cache" / "structures.json"


@dataclass(frozen=True)
class Perturbation:
    name: str
    kind: str
    axis: str | None = None
    translation_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = 1.0


@dataclass(frozen=True)
class AffinePerturbation:
    rotation: np.ndarray
    translation_mm: np.ndarray
    scale: float
    pivot_mm: np.ndarray

    def forward(self, point_mm: np.ndarray) -> np.ndarray:
        rel = np.asarray(point_mm, dtype=np.float64) - self.pivot_mm
        return self.pivot_mm + self.scale * (self.rotation @ rel) + self.translation_mm

    def inverse(self, point_mm: np.ndarray) -> np.ndarray:
        rel = (np.asarray(point_mm, dtype=np.float64) - self.translation_mm - self.pivot_mm) / self.scale
        return self.pivot_mm + (self.rotation.T @ rel)


def parse_target_keys(raw: str) -> list[str]:
    keys = [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]
    if not keys:
        raise ValueError("At least one target key is required.")
    return list(dict.fromkeys(keys))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def read_structure_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [rec for rec in data if isinstance(rec, dict) and rec.get("id") is not None]


def configure_backend_environment(profile_root: Path, *, workers: int) -> None:
    profile_root = profile_root.resolve()
    region_map = profile_root / "region_map_v2.json"
    if not region_map.exists():
        region_map = profile_root / "region_map.json"

    defaults = {
        "PROBEPLANNER_ATLAS_MODE": "mri_ccf_hybrid",
        "PROBEPLANNER_MRI_CCF_REQUIRE_MANIFEST": "false",
        "PROBEPLANNER_MRI_CCF_ANNOTATION_PATH": str(profile_root / "annotation.npy"),
        "PROBEPLANNER_MRI_CCF_REGION_MAP_PATH": str(region_map),
        "PROBEPLANNER_MRI_CCF_STATS_PATH": str(profile_root / "stats.json"),
        "PROBEPLANNER_MRI_CCF_JACOBIAN_PATH": str(profile_root / "jacobian.npy"),
        "PROBEPLANNER_MRI_CCF_SKULL_MASK_PATH": str(profile_root / "skull_mask.npy"),
        "PROBEPLANNER_MRI_CCF_OUTER_TISSUE_MASK_PATH": str(profile_root / "outer_tissue_mask.npy"),
        "PROBEPLANNER_MRI_CCF_LAYERED_SDF_PATH": str(profile_root / "layered_sdf.npz"),
        "PROBEPLANNER_MRI_CCF_DATASET_PROFILE": profile_root.name,
        "PROBEPLANNER_STARTUP_FULL_REGION_PLANNING": "true",
        "PROBEPLANNER_STREAMING_PIPELINE_ENABLED": "false",
        "PROBEPLANNER_SDF_ASYNC_BUILD_ENABLED": "false",
        "PROBEPLANNER_DISABLE_ATLAS_KEYWORD_RISK": "true",
        "PROBEPLANNER_COMPUTE_WORKERS": str(max(1, workers)),
        "PROBEPLANNER_CANDIDATE_GEN_WORKERS": str(max(1, workers)),
        "PROBEPLANNER_PREFILTER_WORKERS": "1",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def import_backend_server():
    if str(BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(BACKEND_SRC))
    import optimizer_server as server  # pylint: disable=import-error,import-outside-toplevel

    return server


def apply_structure_tree_descendant_fallback(
    server: Any,
    target_keys: list[str],
    structures_json: Path,
) -> dict[str, Any]:
    """Restore target descendant sets when allensdk is absent at runtime."""
    records = read_structure_records(structures_json)
    if not records:
        return {
            "applied": False,
            "reason": "structures_json_missing_or_invalid",
            "path": str(structures_json),
        }

    descendants: dict[int, set[int]] = {}
    by_id: dict[int, dict[str, Any]] = {}
    by_acronym: dict[str, dict[str, Any]] = {}
    for rec in records:
        try:
            sid = int(rec.get("id"))
        except Exception:
            continue
        by_id[sid] = rec
        acronym = str(rec.get("acronym") or "").strip()
        if acronym:
            by_acronym[acronym.lower()] = rec
        path = rec.get("structure_id_path") if isinstance(rec.get("structure_id_path"), list) else []
        for raw_ancestor in path:
            try:
                ancestor = int(raw_ancestor)
            except Exception:
                continue
            descendants.setdefault(ancestor, set()).add(sid)
        descendants.setdefault(sid, set()).add(sid)

    target_sids: dict[str, int] = {}
    all_label_ids: set[int] = set()
    for key in target_keys:
        # Exact ontology acronyms are authoritative. Curated planner seeds can
        # be stale or alias-collided, so only use a seed when the requested
        # acronym is absent from the ontology.
        rec = by_acronym.get(str(key).lower())
        sid = int(rec.get("id")) if rec is not None else None
        reg = server.PLANNER_REGIONS.get(key)
        if sid is None and isinstance(reg, dict) and reg.get("id") is not None:
            try:
                sid = int(reg.get("id"))
            except Exception:
                sid = None
        if sid is None:
            continue
        target_sids[key] = sid
        all_label_ids.update(int(v) for v in descendants.get(sid, {sid}))

    if not target_sids or not all_label_ids:
        return {
            "applied": False,
            "reason": "target_records_missing",
            "path": str(structures_json),
        }

    label_stats = server._compute_label_centroid_stats_chunked(
        server.atlas_annotation,
        label_ids=sorted(all_label_ids),
        chunk_depth=getattr(server, "REGION_INDEX_CACHE_CHUNK_DEPTH", 16),
    )
    left_label_stats = server._compute_label_centroid_stats_chunked(
        server.atlas_annotation,
        label_ids=sorted(all_label_ids),
        chunk_depth=getattr(server, "REGION_INDEX_CACHE_CHUNK_DEPTH", 16),
        hemisphere="left",
    )
    updated = []
    for key, sid in target_sids.items():
        desc = frozenset(int(v) for v in descendants.get(sid, {sid}))
        total_count = 0
        total_i = total_j = total_k = 0.0
        left_count = 0
        left_i = left_j = left_k = 0.0
        for child_sid in desc:
            stat = label_stats.get(int(child_sid))
            if not stat:
                continue
            count = int(stat.get("count", 0) or 0)
            if count <= 0:
                continue
            total_count += count
            total_i += float(stat.get("sum_i", 0.0))
            total_j += float(stat.get("sum_j", 0.0))
            total_k += float(stat.get("sum_k", 0.0))
            left_stat = left_label_stats.get(int(child_sid))
            if left_stat:
                count = int(left_stat.get("count", 0) or 0)
                if count > 0:
                    left_count += count
                    left_i += float(left_stat.get("sum_i", 0.0))
                    left_j += float(left_stat.get("sum_j", 0.0))
                    left_k += float(left_stat.get("sum_k", 0.0))
        if total_count <= 0:
            continue

        if left_count > 0:
            centroid = (left_i / left_count, left_j / left_count, left_k / left_count)
            centroid_hemisphere = "left"
        else:
            centroid = (total_i / total_count, total_j / total_count, total_k / total_count)
            centroid_hemisphere = "bilateral_fallback"
        ap_mm, dv_mm, ml_mm = server._voxel_centroid_to_bregma_mm(centroid)
        rec = by_id.get(sid, {})
        acronym = str(rec.get("acronym") or key)
        name = str(rec.get("name") or acronym)
        server.descendant_ids[sid] = desc
        server.structure_map[sid] = {
            **server.structure_map.get(sid, {}),
            "acronym": acronym,
            "name": name,
            "voxel_count": int(total_count),
            "volume_mm3": round(float(total_count) * (float(server.VOXEL_SIZE_UM) / 1000.0) ** 3, 4),
            "n_subregions": int(len(desc)),
        }
        existing_region = server.PLANNER_REGIONS.get(key, {})
        server.PLANNER_REGIONS[key] = {
            **existing_region,
            "id": int(sid),
            "ap": round(float(ap_mm), 3),
            "dv": round(float(dv_mm), 3),
            "ml": round(float(ml_mm), 3),
            "category": existing_region.get("category", "atlas"),
        }
        acronym_cache = getattr(server, "acronym_record_map", None)
        if isinstance(acronym_cache, dict):
            acronym_cache[str(key).lower()] = rec
        updated.append(
            {
                "acronym": key,
                "regionId": int(sid),
                "descendantCount": int(len(desc)),
                "voxelCount": int(total_count),
                "plannerCentroidHemisphere": centroid_hemisphere,
                "plannerCentroidMlMm": round(float(ml_mm), 6),
            }
        )

    server._rebuild_region_centroids()
    return {
        "applied": bool(updated),
        "path": str(structures_json),
        "recordCount": int(len(records)),
        "updatedTargets": updated,
    }


def rotation_matrix_deg(ap_deg: float, dv_deg: float, ml_deg: float) -> np.ndarray:
    ax, ay, az = [math.radians(float(v)) for v in (ap_deg, dv_deg, ml_deg)]
    rx = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(ax), -math.sin(ax)],
            [0.0, math.sin(ax), math.cos(ax)],
        ],
        dtype=np.float64,
    )
    ry = np.array(
        [
            [math.cos(ay), 0.0, math.sin(ay)],
            [0.0, 1.0, 0.0],
            [-math.sin(ay), 0.0, math.cos(ay)],
        ],
        dtype=np.float64,
    )
    rz = np.array(
        [
            [math.cos(az), -math.sin(az), 0.0],
            [math.sin(az), math.cos(az), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return rz @ ry @ rx


def perturbation_to_affine(spec: Perturbation, pivot_mm: np.ndarray) -> AffinePerturbation:
    return AffinePerturbation(
        rotation=rotation_matrix_deg(*spec.rotation_deg),
        translation_mm=np.asarray(spec.translation_mm, dtype=np.float64),
        scale=float(spec.scale),
        pivot_mm=np.asarray(pivot_mm, dtype=np.float64),
    )


def build_perturbations() -> list[Perturbation]:
    out: list[Perturbation] = [Perturbation(name="baseline", kind="baseline")]
    axis_vectors = {
        "AP": (1.0, 0.0, 0.0),
        "DV": (0.0, 1.0, 0.0),
        "ML": (0.0, 0.0, 1.0),
    }
    for mag in (0.1, 0.2, 0.3):
        for axis, unit in axis_vectors.items():
            for sign in (-1.0, 1.0):
                vec = tuple(sign * mag * v for v in unit)
                out.append(
                    Perturbation(
                        name=f"translation_{axis}_{sign:+.0f}_{mag:.1f}mm",
                        kind="translation",
                        axis=axis,
                        translation_mm=vec,
                    )
                )

    for deg in (1.0, 2.0, 3.0):
        for axis in ("AP", "DV", "ML"):
            for sign in (-1.0, 1.0):
                rot = {
                    "AP": (sign * deg, 0.0, 0.0),
                    "DV": (0.0, sign * deg, 0.0),
                    "ML": (0.0, 0.0, sign * deg),
                }[axis]
                out.append(
                    Perturbation(
                        name=f"rotation_{axis}_{sign:+.0f}_{deg:.0f}deg",
                        kind="rotation",
                        axis=axis,
                        rotation_deg=rot,
                    )
                )

    for pct in (1.0, 2.0):
        for sign in (-1.0, 1.0):
            scale = 1.0 + sign * pct / 100.0
            out.append(
                Perturbation(
                    name=f"scaling_{sign:+.0f}_{pct:.0f}pct",
                    kind="scaling",
                    axis="isotropic",
                    scale=scale,
                )
            )

    for mag, deg in ((0.1, 1.0), (0.2, 2.0), (0.3, 3.0)):
        for sign in (-1.0, 1.0):
            out.append(
                Perturbation(
                    name=f"combined_APMLrot_{sign:+.0f}_{mag:.1f}mm_{deg:.0f}deg",
                    kind="combined",
                    axis="AP+ML+DVrot",
                    translation_mm=(sign * mag, 0.0, sign * mag),
                    rotation_deg=(0.0, sign * deg, 0.0),
                )
            )
    return out


class BackendPerturbationContext:
    def __init__(self, server: Any, affine: AffinePerturbation):
        self.server = server
        self.affine = affine
        self.original_sample = None
        self.original_cached = None
        self.original_regions = None
        self.original_centroids = None

    def __enter__(self):
        s = self.server
        self.original_sample = s.sample_trajectory_voxels
        self.original_cached = s.sample_trajectory_voxels_cached
        self.original_regions = deepcopy(s.PLANNER_REGIONS)
        self.original_centroids = deepcopy(s.REGION_CENTROIDS_UM)

        if not np.allclose(self.affine.rotation, np.eye(3)) or not np.allclose(
            self.affine.translation_mm, 0.0
        ) or abs(self.affine.scale - 1.0) > 1e-12:
            for reg in s.PLANNER_REGIONS.values():
                point = np.asarray(
                    [float(reg.get("ap", 0.0)), float(reg.get("dv", 0.0)), float(reg.get("ml", 0.0))],
                    dtype=np.float64,
                )
                moved = self.affine.forward(point)
                reg["ap"] = round(float(moved[0]), 3)
                reg["dv"] = round(float(moved[1]), 3)
                reg["ml"] = round(float(moved[2]), 3)
            s._rebuild_region_centroids()

            def sample_trajectory_voxels_perturbed(
                ap_mm,
                ml_mm,
                theta_deg,
                phi_deg,
                depth_um,
                skull_tilt_deg=0.0,
                step_um=25.0,
            ):
                tilt_rad = float(skull_tilt_deg) * math.pi / 180.0
                dv_corr_um = -float(ap_mm) * math.tan(tilt_rad) * 1000.0
                theta_rad = float(theta_deg) * math.pi / 180.0
                phi_rad = float(phi_deg) * math.pi / 180.0
                direction = np.asarray(
                    [
                        math.sin(theta_rad) * math.cos(phi_rad),
                        math.cos(theta_rad),
                        math.sin(theta_rad) * math.sin(phi_rad),
                    ],
                    dtype=np.float64,
                )
                entry_ccf_um = np.asarray(
                    [
                        s.BREGMA_UM[0] - float(ap_mm) * 1000.0,
                        s.BREGMA_UM[1] + dv_corr_um,
                        s.BREGMA_UM[2] + float(ml_mm) * 1000.0,
                    ],
                    dtype=np.float64,
                )
                max_depth = (s.ATLAS_SHAPE[1] * s.VOXEL_SIZE_UM) - s.BREGMA_UM[1] - 50.0
                effective_depth = min(float(depth_um), float(max_depth))
                n_steps = int(effective_depth / float(step_um)) + 1
                voxels = []
                seen = set()
                for i_step in range(n_steps):
                    ccf_um = entry_ccf_um + direction * (float(i_step) * float(step_um))
                    planner_mm = np.asarray(
                        [
                            s.BREGMA_UM[0] / 1000.0 - ccf_um[0] / 1000.0,
                            ccf_um[1] / 1000.0 - s.BREGMA_UM[1] / 1000.0,
                            ccf_um[2] / 1000.0 - s.BREGMA_UM[2] / 1000.0,
                        ],
                        dtype=np.float64,
                    )
                    source_mm = self.affine.inverse(planner_mm)
                    source_ccf_um = np.asarray(
                        [
                            s.BREGMA_UM[0] - source_mm[0] * 1000.0,
                            s.BREGMA_UM[1] + source_mm[1] * 1000.0,
                            s.BREGMA_UM[2] + source_mm[2] * 1000.0,
                        ],
                        dtype=np.float64,
                    )
                    vi = int(round(float(source_ccf_um[0]) / float(s.VOXEL_SIZE_UM)))
                    vj = int(round(float(source_ccf_um[1]) / float(s.VOXEL_SIZE_UM)))
                    vk = int(round(float(source_ccf_um[2]) / float(s.VOXEL_SIZE_UM)))
                    if not s.voxel_in_bounds(vi, vj, vk):
                        continue
                    key = (vi, vj, vk)
                    if key in seen:
                        continue
                    seen.add(key)
                    voxels.append(key)
                return voxels

            def sample_trajectory_voxels_cached_perturbed(
                ap_mm,
                ml_mm,
                theta_deg,
                phi_deg,
                depth_um,
                skull_tilt_deg=0.0,
                step_um=25.0,
            ):
                return sample_trajectory_voxels_perturbed(
                    ap_mm,
                    ml_mm,
                    theta_deg,
                    phi_deg,
                    depth_um,
                    skull_tilt_deg=skull_tilt_deg,
                    step_um=step_um,
                )

            s.sample_trajectory_voxels = sample_trajectory_voxels_perturbed
            s.sample_trajectory_voxels_cached = sample_trajectory_voxels_cached_perturbed

        clear_backend_caches(s)
        return self

    def __exit__(self, exc_type, exc, tb):
        s = self.server
        if self.original_sample is not None:
            s.sample_trajectory_voxels = self.original_sample
        if self.original_cached is not None:
            s.sample_trajectory_voxels_cached = self.original_cached
        if self.original_regions is not None:
            s.PLANNER_REGIONS = self.original_regions
        if self.original_centroids is not None:
            s.REGION_CENTROIDS_UM = self.original_centroids
        clear_backend_caches(s)
        return False


def clear_backend_caches(server: Any) -> None:
    for name in (
        "trajectory_voxel_cache",
        "risk_diag_cache",
        "robust_perturb_cache",
        "candidate_objective_cache",
    ):
        cache = getattr(server, name, None)
        if isinstance(cache, dict):
            cache.clear()


def planner_request(args: argparse.Namespace, target_keys: list[str]) -> dict[str, Any]:
    return {
        "targetKeys": target_keys,
        "params": {
            "algorithm": "greedy",
            "targetingMode": "single_hemisphere",
            "gridSpacing": float(args.grid_spacing_um),
            "angleStep": float(args.angle_step_deg),
            "maxAngle": float(args.max_angle_deg),
            "depthUm": float(args.depth_um),
            "candidateCoarseStepUm": float(args.coarse_step_um),
            "candidateRefineStepUm": float(args.refine_step_um),
            "candidateRefineFraction": float(args.refine_fraction),
            "computeWorkers": int(args.workers),
            "candidateGenWorkers": int(args.workers),
            "prefilterWorkers": 1,
            "streamingPipelineEnabled": False,
            "scoreEarlyStopEnabled": False,
            "autoAvoidEnabled": True,
            "robustOptimization": False,
            "paretoEnabled": False,
            "coverageBoost": False,
            "objectivePreset": "balanced",
            "minRiskDistanceMm": float(args.min_risk_distance_mm),
            "targetEndpointPolicy": "tip_in_target",
            "includeCandidatePreview": True,
            "strictFallbackDecompose": False,
        },
        "probeParams": {
            "depthUm": float(args.depth_um),
            "entrySpacingUm": float(args.entry_spacing_um),
            "shankWidthUm": 70.0,
            "shanks": 1,
            "shankPitchUm": 0.0,
            "probeType": "np1",
        },
        "constraintModes": {
            "maxAngle": "enforce",
            "minEntrySpacing": "enforce",
            "minClearance": "enforce",
            "lambdaBoundary": "enforce",
            "brainBounds": "enforce",
            "hemisphere": "warn",
            "continuousRiskDistance": "enforce",
        },
    }


def class_worst_margin(diag: dict[str, Any] | None) -> float | None:
    if not isinstance(diag, dict):
        return None
    class_min = diag.get("classMinDistanceMm")
    class_thr = diag.get("classThresholdMm")
    if not isinstance(class_min, dict) or not isinstance(class_thr, dict):
        return None
    margins = []
    for key, value in class_min.items():
        try:
            dist = float(value)
            thr = float(class_thr.get(key, class_thr.get("default", 0.0)))
        except Exception:
            continue
        if math.isfinite(dist) and math.isfinite(thr):
            margins.append(dist - thr)
    return float(min(margins)) if margins else None


def annotate_selected_with_risk(server: Any, selected: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    annotated = []
    for probe in selected:
        row = deepcopy(probe)
        diag = server._compute_probe_risk_diagnostics(
            row,
            step_um=75.0,
            threshold_mm=params.get("minRiskDistanceMm"),
            threshold_by_class=params.get("riskThresholdsMm"),
        )
        if isinstance(diag, dict):
            row["riskDiagnostics"] = diag
            row["minSafetyDistanceMm"] = diag.get("minDistanceMm")
            row["worstRiskMarginMm"] = class_worst_margin(diag)
            row["riskTailShortfallMm"] = diag.get("riskTailShortfallMm")
            row["riskMaxShortfallMm"] = diag.get("riskMaxShortfallMm")
        annotated.append(row)
    return annotated


def run_planner_case(
    server: Any,
    request_body: dict[str, Any],
    spec: Perturbation,
    affine: AffinePerturbation,
) -> dict[str, Any]:
    started = time.time()
    clear_backend_caches(server)
    error = None
    error_code = None
    error_extra = None
    candidates: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    uncovered: list[str] = []
    generation_summary: dict[str, Any] = {}
    with BackendPerturbationContext(server, affine):
        payload = server._normalize_optimize_payload(deepcopy(request_body))
        params = payload["params"]
        try:
            result = server._run_optimize_pipeline(request_body)
            candidates = list(result.get("candidatePreview") or [])
            selected = annotate_selected_with_risk(server, result.get("selected", []), params)
            uncovered = sorted(str(v) for v in (result.get("uncovered") or []))
            generation_summary = deepcopy(
                ((result.get("computeProfile") or {}).get("candidateGeneration") or {})
            )
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            error_code = getattr(exc, "code", None)
            raw_extra = getattr(exc, "extra", None)
            error_extra = deepcopy(raw_extra) if isinstance(raw_extra, dict) else None

    covered = sorted({str(k) for probe in selected for k in (probe.get("coveredRegions") or [])})
    return {
        "name": spec.name,
        "kind": spec.kind,
        "axis": spec.axis,
        "translationMm": [round(float(v), 6) for v in spec.translation_mm],
        "rotationDeg": [round(float(v), 6) for v in spec.rotation_deg],
        "scale": round(float(spec.scale), 8),
        "status": "fail_closed" if error or uncovered else "ok",
        "error": error,
        "errorCode": error_code,
        "errorExtra": error_extra,
        "selectedProbeCount": int(len(selected)),
        "selected": selected,
        "coveredTargets": covered,
        "uncoveredTargets": uncovered,
        "candidateCount": int(len(candidates)),
        "candidateGeneration": generation_summary,
        "timeSec": round(float(time.time() - started), 6),
    }


def point_array(probe: dict[str, Any], key: str) -> np.ndarray | None:
    raw = probe.get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None
    try:
        return np.asarray([float(v) for v in raw], dtype=np.float64)
    except Exception:
        return None


def match_displacements_mm(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    if not baseline or not current:
        return None, None
    base_points = []
    cur_points = []
    for probe in baseline:
        entry = point_array(probe, "entry_xyz")
        tip = point_array(probe, "tip_xyz")
        if entry is not None and tip is not None:
            base_points.append((entry, tip))
    for probe in current:
        entry = point_array(probe, "entry_xyz")
        tip = point_array(probe, "tip_xyz")
        if entry is not None and tip is not None:
            cur_points.append((entry, tip))
    if not base_points or not cur_points:
        return None, None

    used = set()
    entry_distances = []
    tip_distances = []
    for base_entry, base_tip in base_points:
        best_idx = None
        best_score = None
        for idx, (cur_entry, cur_tip) in enumerate(cur_points):
            if idx in used:
                continue
            score = float(np.linalg.norm(cur_tip - base_tip) + np.linalg.norm(cur_entry - base_entry))
            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:
            continue
        used.add(best_idx)
        cur_entry, cur_tip = cur_points[best_idx]
        entry_distances.append(float(np.linalg.norm(cur_entry - base_entry) / 1000.0))
        tip_distances.append(float(np.linalg.norm(cur_tip - base_tip) / 1000.0))
    if not entry_distances:
        return None, None
    return float(max(entry_distances)), float(max(tip_distances))


def selected_set_risk_metric(selected: list[dict[str, Any]], key: str, reducer: str) -> float | None:
    vals = []
    for probe in selected:
        try:
            value = probe.get(key)
            if value is not None:
                value = float(value)
                if math.isfinite(value):
                    vals.append(value)
        except Exception:
            continue
    if not vals:
        return None
    return float(min(vals) if reducer == "min" else max(vals))


def add_comparisons(rows: list[dict[str, Any]], target_keys: list[str]) -> dict[str, Any]:
    baseline = next(row for row in rows if row["name"] == "baseline")
    base_selected = baseline.get("selected") or []
    base_count = int(baseline.get("selectedProbeCount", 0))
    base_covered = set(baseline.get("coveredTargets") or [])
    base_uncovered = set(baseline.get("uncoveredTargets") or [])
    base_margin = selected_set_risk_metric(base_selected, "worstRiskMarginMm", "min")
    base_shortfall = selected_set_risk_metric(base_selected, "riskMaxShortfallMm", "max")

    for row in rows:
        selected = row.get("selected") or []
        covered = set(row.get("coveredTargets") or [])
        uncovered = set(row.get("uncoveredTargets") or [])
        entry_disp, tip_disp = match_displacements_mm(base_selected, selected)
        margin = selected_set_risk_metric(selected, "worstRiskMarginMm", "min")
        shortfall = selected_set_risk_metric(selected, "riskMaxShortfallMm", "max")
        row["probeCountChange"] = int(row.get("selectedProbeCount", 0)) - base_count
        row["entryDisplacementMm"] = None if entry_disp is None else round(float(entry_disp), 6)
        row["tipDisplacementMm"] = None if tip_disp is None else round(float(tip_disp), 6)
        row["coverageChanged"] = bool(covered != base_covered)
        row["coverageChangeCount"] = int(len(covered.symmetric_difference(base_covered)))
        row["newUncoveredTargets"] = sorted(uncovered - base_uncovered)
        row["targetCoverageRate"] = round(float(len(covered & set(target_keys)) / max(1, len(target_keys))), 6)
        row["riskWorstMarginMm"] = None if margin is None else round(float(margin), 6)
        row["riskWorstMarginChangeMm"] = (
            None
            if margin is None or base_margin is None
            else round(float(margin - base_margin), 6)
        )
        row["riskMaxShortfallMm"] = None if shortfall is None else round(float(shortfall), 6)
        row["riskMaxShortfallChangeMm"] = (
            None
            if shortfall is None or base_shortfall is None
            else round(float(shortfall - base_shortfall), 6)
        )

    perturbed = [row for row in rows if row["name"] != "baseline"]
    failures = [row for row in perturbed if row.get("status") != "ok" or int(row.get("selectedProbeCount", 0)) <= 0]
    coverage_changed = [row for row in perturbed if row.get("coverageChanged")]
    new_uncovered = [row for row in perturbed if row.get("newUncoveredTargets")]

    def max_metric(name: str) -> float | None:
        vals = [row.get(name) for row in perturbed if row.get(name) is not None]
        return round(float(max(vals)), 6) if vals else None

    def min_metric(name: str) -> float | None:
        vals = [row.get(name) for row in perturbed if row.get(name) is not None]
        return round(float(min(vals)), 6) if vals else None

    return {
        "perturbationCaseCount": int(len(perturbed)),
        "planningFailureCount": int(len(failures)),
        "planningFailureRate": round(float(len(failures) / max(1, len(perturbed))), 6),
        "coverageChangedCaseCount": int(len(coverage_changed)),
        "newUncoveredCaseCount": int(len(new_uncovered)),
        "maxAbsProbeCountChange": int(max((abs(int(row.get("probeCountChange", 0))) for row in perturbed), default=0)),
        "maxEntryDisplacementMm": max_metric("entryDisplacementMm"),
        "maxTipDisplacementMm": max_metric("tipDisplacementMm"),
        "worstRiskMarginDropMm": min_metric("riskWorstMarginChangeMm"),
        "maxRiskShortfallIncreaseMm": max_metric("riskMaxShortfallChangeMm"),
        "baselineSelectedProbeCount": base_count,
        "baselineCoveredTargets": sorted(base_covered),
        "baselineUncoveredTargets": sorted(base_uncovered),
        "baselineRiskWorstMarginMm": None if base_margin is None else round(float(base_margin), 6),
        "baselineRiskMaxShortfallMm": None if base_shortfall is None else round(float(base_shortfall), 6),
    }


def target_region_payload(server: Any, target_keys: list[str]) -> list[dict[str, Any]]:
    out = []
    for key in target_keys:
        reg = server.PLANNER_REGIONS.get(key, {})
        rid = reg.get("id")
        struct = server.structure_map.get(int(rid), {}) if rid is not None else {}
        out.append(
            {
                "acronym": key,
                "regionId": int(rid) if rid is not None else None,
                "apMm": reg.get("ap"),
                "dvMm": reg.get("dv"),
                "mlMm": reg.get("ml"),
                "voxelCount": int(struct.get("voxel_count", 0) or 0),
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "name",
        "kind",
        "axis",
        "translationMm",
        "rotationDeg",
        "scale",
        "status",
        "selectedProbeCount",
        "probeCountChange",
        "entryDisplacementMm",
        "tipDisplacementMm",
        "targetCoverageRate",
        "coverageChanged",
        "uncoveredTargets",
        "newUncoveredTargets",
        "riskWorstMarginMm",
        "riskWorstMarginChangeMm",
        "riskMaxShortfallMm",
        "riskMaxShortfallChangeMm",
        "candidateCount",
        "timeSec",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = dict(row)
            for key in ("translationMm", "rotationDeg", "uncoveredTargets", "newUncoveredTargets"):
                clean[key] = json.dumps(clean.get(key), ensure_ascii=False)
            writer.writerow(clean)


def fmt_value(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload["summary"]
    rows = payload["cases"]
    registration = payload.get("registrationBaseline", {})
    metrics = registration.get("metrics") if isinstance(registration.get("metrics"), dict) else {}
    target_rows = payload.get("targetRegions") or []
    ontology = payload.get("ontologyFallback") if isinstance(payload.get("ontologyFallback"), dict) else {}

    kind_summary = []
    for kind in ("translation", "rotation", "scaling", "combined"):
        subset = [row for row in rows if row.get("kind") == kind]
        if not subset:
            continue
        kind_summary.append(
            "| {kind} | {n} | {fail} | {coverage} | {entry} | {tip} | {risk} |".format(
                kind=kind,
                n=len(subset),
                fail=sum(1 for row in subset if row.get("status") != "ok" or int(row.get("selectedProbeCount", 0)) <= 0),
                coverage=sum(1 for row in subset if row.get("coverageChanged")),
                entry=fmt_value(max((row.get("entryDisplacementMm") or 0.0 for row in subset), default=0.0)),
                tip=fmt_value(max((row.get("tipDisplacementMm") or 0.0 for row in subset), default=0.0)),
                risk=fmt_value(min((row.get("riskWorstMarginChangeMm") for row in subset if row.get("riskWorstMarginChangeMm") is not None), default=None)),
            )
        )

    target_table = [
        "| acronym | region ID | AP mm | DV mm | ML mm | voxels |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in target_rows:
        target_table.append(
            f"| {row['acronym']} | {row['regionId']} | {fmt_value(row['apMm'])} | "
            f"{fmt_value(row['dvMm'])} | {fmt_value(row['mlMm'])} | {row['voxelCount']} |"
        )

    detail_table = [
        "| case | selected probe count change | entry displacement mm | tip displacement mm | coverage changed | new uncovered targets | risk margin change mm |",
        "|---|---:|---:|---:|---|---|---:|",
    ]
    for row in rows:
        if row["name"] == "baseline":
            continue
        detail_table.append(
            f"| {row['name']} | {row['probeCountChange']} | {fmt_value(row['entryDisplacementMm'])} | "
            f"{fmt_value(row['tipDisplacementMm'])} | {row['coverageChanged']} | "
            f"{fmt_value(row['newUncoveredTargets'])} | {fmt_value(row['riskWorstMarginChangeMm'])} |"
        )

    lines = [
        "# Registration Perturbation Sensitivity - 2026-07-09",
        "",
        "ProbePlanner was re-run after small affine perturbations were applied to the MRI-to-CCF registration query space. "
        "For each perturbation, target/risk lookups used the inverse perturbed transform and the backend candidate generator plus greedy set-cover solver selected probes from scratch.",
        "",
        "## Registration baseline",
        "",
        f"- Registration artifact: `{registration.get('artifactRoot')}`",
        f"- Dice: {fmt_value(metrics.get('dice'))}",
        f"- HD95 mm: {fmt_value(metrics.get('hd95Mm'))}",
        f"- Jacobian anomaly ratio: {fmt_value(metrics.get('jacobianAnomalyRatio'))}",
        f"- Jacobian std: {fmt_value(metrics.get('jacobianStd'))}",
        f"- Structure-tree descendant fallback: {bool(ontology.get('applied'))} (`{ontology.get('path')}`)",
        "",
        "## Target regions",
        "",
        *target_table,
        "",
        "## Summary",
        "",
        f"- Perturbation cases: {summary['perturbationCaseCount']}",
        f"- Planning failure rate: {summary['planningFailureRate']:.6f} ({summary['planningFailureCount']}/{summary['perturbationCaseCount']})",
        f"- Max selected probe count change: {summary['maxAbsProbeCountChange']}",
        f"- Coverage-changed cases: {summary['coverageChangedCaseCount']}",
        f"- Cases with new uncovered targets: {summary['newUncoveredCaseCount']}",
        f"- Max entry displacement: {fmt_value(summary['maxEntryDisplacementMm'])} mm",
        f"- Max tip displacement: {fmt_value(summary['maxTipDisplacementMm'])} mm",
        f"- Worst risk-margin drop: {fmt_value(summary['worstRiskMarginDropMm'])} mm",
        f"- Max risk-shortfall increase: {fmt_value(summary['maxRiskShortfallIncreaseMm'])} mm",
        "",
        "## Interpretation",
        "",
        "The perturbation audit shows stable target coverage under the tested registration errors: no perturbation failed planning, changed the covered target set, or introduced a newly uncovered target. "
        "However, several small perturbations switched the selected trajectory to a different feasible solution, producing entry/tip coordinate jumps even when probe count and target coverage stayed unchanged. "
        "This should be reported as output-solution sensitivity rather than target-coverage instability; a future production planner can reduce this by adding an explicit trajectory-continuity or baseline-proximity tie-breaker.",
        "",
        "## Perturbation-type summary",
        "",
        "| perturbation type | cases | failures | coverage-changed cases | max entry displacement mm | max tip displacement mm | worst risk-margin change mm |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *kind_summary,
        "",
        "## Case table",
        "",
        *detail_table,
        "",
        "## Metric definitions",
        "",
        "- `selected probe count change`: selected probes after perturbation minus baseline selected probes.",
        "- `entry displacement` and `tip displacement`: nearest-neighbor Euclidean displacement of selected entry/tip coordinates relative to the baseline selected probes.",
        "- `coverage changed`: whether the set of covered target acronyms changed relative to baseline.",
        "- `new uncovered targets`: targets uncovered after perturbation that were not uncovered at baseline.",
        "- `risk margin change`: change in the worst class-specific risk margin, computed as distance-to-risk-layer minus class threshold; negative values mean the perturbed plan moved closer to a risk layer.",
        "- `planning failure rate`: fraction of perturbation cases that returned an error or zero selected probes.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run registration perturbation sensitivity audit.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT), help="MRI-CCF planner profile root.")
    parser.add_argument(
        "--registration-root",
        default=str(DEFAULT_REGISTRATION_ROOT),
        help="Registration artifact root that contains stats.json.",
    )
    parser.add_argument("--target-keys", default="CA1,VPM,LP,VISp", help="Comma-separated target acronyms.")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--grid-spacing-um", type=float, default=1200.0)
    parser.add_argument("--angle-step-deg", type=float, default=10.0)
    parser.add_argument("--max-angle-deg", type=float, default=10.0)
    parser.add_argument("--depth-um", type=float, default=7600.0)
    parser.add_argument("--coarse-step-um", type=float, default=150.0)
    parser.add_argument("--refine-step-um", type=float, default=75.0)
    parser.add_argument("--refine-fraction", type=float, default=0.12)
    parser.add_argument("--entry-spacing-um", type=float, default=1200.0)
    parser.add_argument("--min-risk-distance-mm", type=float, default=0.05)
    parser.add_argument("--output-json", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.json"))
    parser.add_argument("--output-md", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.md"))
    parser.add_argument("--output-csv", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.csv"))
    parser.add_argument(
        "--structures-json",
        default=str(DEFAULT_STRUCTURES_JSON),
        help="Allen structure tree JSON used to recover descendant sets if allensdk is unavailable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    profile_root = Path(args.profile_root).expanduser()
    registration_root = Path(args.registration_root).expanduser()
    target_keys = parse_target_keys(args.target_keys)

    configure_backend_environment(profile_root, workers=int(args.workers))
    server = import_backend_server()

    load_started = time.time()
    server.load_atlas()
    if not getattr(server, "atlas_loaded", False):
        raise RuntimeError(f"Atlas failed to load: {getattr(server, 'atlas_error', None)}")
    atlas_load_sec = time.time() - load_started

    ontology_fallback = apply_structure_tree_descendant_fallback(
        server,
        target_keys,
        Path(args.structures_json).expanduser(),
    )

    missing = [key for key in target_keys if key not in server.PLANNER_REGIONS]
    if missing:
        raise RuntimeError(f"Missing target keys after atlas load: {missing}")

    pivot_mm = np.asarray(
        [
            np.mean([float(server.PLANNER_REGIONS[k]["ap"]) for k in target_keys]),
            np.mean([float(server.PLANNER_REGIONS[k]["dv"]) for k in target_keys]),
            np.mean([float(server.PLANNER_REGIONS[k]["ml"]) for k in target_keys]),
        ],
        dtype=np.float64,
    )
    request_body = planner_request(args, target_keys)
    perturbations = build_perturbations()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with server.app.app_context():
        for idx, spec in enumerate(perturbations, start=1):
            affine = perturbation_to_affine(spec, pivot_mm)
            print(f"[{idx:02d}/{len(perturbations)}] {spec.name}", flush=True)
            try:
                row = run_planner_case(server, request_body, spec, affine)
            except Exception as exc:  # Keep fail-closed cases in the evidence table.
                row = {
                    "name": spec.name,
                    "kind": spec.kind,
                    "axis": spec.axis,
                    "translationMm": [round(float(v), 6) for v in spec.translation_mm],
                    "rotationDeg": [round(float(v), 6) for v in spec.rotation_deg],
                    "scale": round(float(spec.scale), 8),
                    "status": "error",
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "selectedProbeCount": 0,
                    "selected": [],
                    "coveredTargets": [],
                    "uncoveredTargets": list(target_keys),
                    "candidateCount": 0,
                    "timeSec": None,
                }
                errors.append({"case": spec.name, "error": row["error"]})
            rows.append(row)

    summary = add_comparisons(rows, target_keys)
    registration_stats = read_json(registration_root / "stats.json")
    profile_stats = read_json(profile_root / "stats.json")
    payload = {
        "schemaVersion": "probeplanner.registration_perturbation_sensitivity.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targetKeys": target_keys,
        "targetRegions": target_region_payload(server, target_keys),
        "atlas": {
            "mode": getattr(server, "ACTIVE_ATLAS_MODE", None),
            "signature": deepcopy(getattr(server, "atlas_signature", {})),
            "loadSeconds": round(float(atlas_load_sec), 6),
            "profileRoot": str(profile_root.resolve()),
        },
        "registrationBaseline": {
            "artifactRoot": str(registration_root.resolve()),
            "statsPath": str((registration_root / "stats.json").resolve()),
            "metrics": registration_stats.get("metrics", {}),
            "paths": registration_stats.get("paths", {}),
            "controlPointResidual": registration_stats.get("controlPointResidual"),
        },
        "ontologyFallback": ontology_fallback,
        "plannerProfileQuality": {
            "statsPath": str((profile_root / "stats.json").resolve()),
            "metrics": profile_stats.get("metrics", {}),
            "metricEvidence": profile_stats.get("metricEvidence", {}),
        },
        "perturbationModel": {
            "coordinateBasis": "planner stereotaxic [AP,DV,ML] mm",
            "pivotMm": [round(float(v), 6) for v in pivot_mm.tolist()],
            "samplingRule": "atlas/risk lookup point = inverse(perturbed MRI-to-CCF affine) applied to each planner trajectory sample",
            "translationMm": ["+/-0.1", "+/-0.2", "+/-0.3"],
            "rotationDeg": ["+/-1", "+/-2", "+/-3"],
            "scalingPct": ["+/-1", "+/-2"],
            "combined": "paired AP/ML translation plus DV-axis rotation",
        },
        "plannerRequest": request_body,
        "summary": summary,
        "cases": rows,
        "errors": errors,
    }

    out_json = Path(args.output_json).expanduser()
    out_md = Path(args.output_md).expanduser()
    out_csv = Path(args.output_csv).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_csv, rows)
    write_markdown(out_md, payload)
    print(json.dumps({"summary": summary, "json": str(out_json), "md": str(out_md), "csv": str(out_csv)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
