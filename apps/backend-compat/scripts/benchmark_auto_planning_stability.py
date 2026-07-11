#!/usr/bin/env python3
"""Audit repeat-run stability of automatic probe planning outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as atlas_util  # noqa: E402
import benchmark_auto_probe_count_planning as auto_bench  # noqa: E402
import benchmark_baseline_auto_planning_comparison as base_cmp  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_STEM = "2026-07-09-auto-planning-stability"


def make_case(
    case_id: str,
    case_type: str,
    requested_targets: tuple[str, ...],
    *,
    targeting_mode: str = "single_hemisphere",
) -> auto_bench.BenchmarkCase:
    return auto_bench.BenchmarkCase(
        case_id=case_id,
        case_type=case_type,
        purpose="Repeat-run stability audit for automatic probe selection.",
        requested_targets=requested_targets,
        targeting_mode=targeting_mode,
    )


STABILITY_CASES: tuple[auto_bench.BenchmarkCase, ...] = (
    make_case("stab_001_single_vpm", "single-region target", ("VPM",)),
    make_case("stab_002_single_ca1", "single-region target", ("CA1",)),
    make_case("stab_003_single_ca3", "single-region target", ("CA3",)),
    make_case("stab_004_single_dg", "single-region target", ("DG",)),
    make_case("stab_005_single_visp", "single-region target", ("VISp",)),
    make_case("stab_006_single_mos", "single-region target", ("MOs",)),
    make_case("stab_007_single_ssp", "single-region target", ("SSp",)),
    make_case("stab_008_pair_ca1_ca3", "nearby multi-region target", ("CA1", "CA3")),
    make_case("stab_009_pair_ca1_dg", "nearby multi-region target", ("CA1", "DG")),
    make_case("stab_010_pair_ca1_vpm", "separated multi-region target", ("CA1", "VPM")),
    make_case("stab_011_pair_ca1_visp", "separated multi-region target", ("CA1", "VISp")),
    make_case("stab_012_pair_vpm_visp", "separated multi-region target", ("VPM", "VISp")),
    make_case("stab_013_pair_vpm_mos", "separated multi-region target", ("VPM", "MOs")),
    make_case("stab_014_pair_visp_mos", "nearby cortical target", ("VISp", "MOs")),
    make_case("stab_015_pair_ssp_mos", "nearby cortical target", ("SSp", "MOs")),
    make_case("stab_016_pair_dg_vpm", "separated multi-region target", ("DG", "VPM")),
    make_case("stab_017_pair_ca3_visp", "separated multi-region target", ("CA3", "VISp")),
    make_case("stab_018_triple_hippocampal", "nearby multi-region target", ("CA1", "CA3", "DG")),
    make_case("stab_019_triple_ca1_vpm_visp", "distributed targets", ("CA1", "VPM", "VISp")),
    make_case("stab_020_triple_ca1_vpm_mos", "distributed targets", ("CA1", "VPM", "MOs")),
    make_case("stab_021_triple_vpm_visp_mos", "distributed targets", ("VPM", "VISp", "MOs")),
    make_case("stab_022_triple_ca1_visp_ssp", "distributed targets", ("CA1", "VISp", "SSp")),
    make_case("stab_023_triple_dg_vpm_ssp", "distributed targets", ("DG", "VPM", "SSp")),
    make_case("stab_024_triple_ca3_dg_vpm", "distributed targets", ("CA3", "DG", "VPM")),
    make_case("stab_025_triple_ca1_mos_ssp", "distributed targets", ("CA1", "MOs", "SSp")),
    make_case("stab_026_quad_ca1_vpm_visp_mos", "distributed targets", ("CA1", "VPM", "VISp", "MOs")),
    make_case("stab_027_quad_ca1_vpm_mos_ssp", "distributed targets", ("CA1", "VPM", "MOs", "SSp")),
    make_case("stab_028_quad_hippo_vpm", "distributed targets", ("CA1", "CA3", "DG", "VPM")),
    make_case(
        "stab_029_bilateral_ca1_vpm",
        "bilateral / distributed targets",
        ("CA1", "VPM"),
        targeting_mode="bilateral_symmetric",
    ),
    make_case(
        "stab_030_bilateral_distributed",
        "bilateral / distributed targets",
        ("CA1", "VPM", "VISp", "MOs"),
        targeting_mode="bilateral_symmetric",
    ),
)


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def round_vector(values: Any, digits: int = 9) -> list[float] | None:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    out = []
    for value in values:
        number = finite_float(value)
        if number is None:
            return None
        out.append(round(number, digits))
    return out


def stable_json_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def canonical_sides(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, sides in value.items():
        if isinstance(sides, (list, tuple, set)):
            out[str(key)] = sorted(str(side) for side in sides)
        elif sides is not None:
            out[str(key)] = [str(sides)]
    return dict(sorted(out.items()))


def canonical_probe(probe: dict[str, Any], *, include_coordinates: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "coveredTargets": sorted(str(v) for v in (probe.get("coveredTargets") or [])),
        "coveredTargetSides": canonical_sides(probe.get("coveredTargetSides")),
    }
    if include_coordinates:
        row.update(
            {
                "entryPlannerMm": round_vector(probe.get("entryPlannerMm")),
                "tipPlannerMm": round_vector(probe.get("tipPlannerMm")),
                "thetaDeg": round(float(probe.get("thetaDeg") or 0.0), 9),
                "phiDeg": round(float(probe.get("phiDeg") or 0.0), 9),
                "depthUm": round(float(probe.get("depthUm") or 0.0), 6),
            }
        )
    return row


def canonical_probe_list(probes: list[dict[str, Any]], *, include_coordinates: bool) -> list[dict[str, Any]]:
    rows = [canonical_probe(probe, include_coordinates=include_coordinates) for probe in probes]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))


def coordinate_variance_summary(repeats: list[dict[str, Any]], coord_key: str) -> dict[str, Any]:
    counts = {int(row.get("selectedProbeCount", 0) or 0) for row in repeats}
    if len(counts) != 1:
        return {
            "available": False,
            "reason": "selected_probe_count_changed",
            "maxAxisVarianceMm2": None,
            "maxAxisStdMm": None,
            "maxEuclideanStdMm": None,
        }
    probe_count = counts.pop()
    if probe_count == 0:
        return {
            "available": True,
            "probeCount": 0,
            "maxAxisVarianceMm2": 0.0,
            "maxAxisStdMm": 0.0,
            "maxEuclideanStdMm": 0.0,
        }
    aligned = [
        sorted(
            row.get("selectedProbes") or [],
            key=lambda probe: json.dumps(canonical_probe(probe, include_coordinates=True), sort_keys=True),
        )
        for row in repeats
    ]
    if any(len(probes) != probe_count for probes in aligned):
        return {
            "available": False,
            "reason": "selected_probe_payload_count_changed",
            "maxAxisVarianceMm2": None,
            "maxAxisStdMm": None,
            "maxEuclideanStdMm": None,
        }

    max_axis_variance = 0.0
    max_axis_std = 0.0
    max_euclidean_std = 0.0
    for probe_index in range(probe_count):
        vectors = [round_vector(probes[probe_index].get(coord_key), digits=12) for probes in aligned]
        if any(vector is None for vector in vectors):
            return {
                "available": False,
                "reason": f"missing_{coord_key}",
                "maxAxisVarianceMm2": None,
                "maxAxisStdMm": None,
                "maxEuclideanStdMm": None,
            }
        numeric_vectors = [vector for vector in vectors if vector is not None]
        means = [mean(vector[axis] for vector in numeric_vectors) for axis in range(3)]
        for axis in range(3):
            axis_values = [vector[axis] for vector in numeric_vectors]
            axis_std = pstdev(axis_values) if len(axis_values) > 1 else 0.0
            max_axis_std = max(max_axis_std, axis_std)
            max_axis_variance = max(max_axis_variance, axis_std * axis_std)
        euclidean_var = mean(
            sum((vector[axis] - means[axis]) ** 2 for axis in range(3))
            for vector in numeric_vectors
        )
        max_euclidean_std = max(max_euclidean_std, math.sqrt(euclidean_var))

    return {
        "available": True,
        "probeCount": int(probe_count),
        "maxAxisVarianceMm2": round(float(max_axis_variance), 15),
        "maxAxisStdMm": round(float(max_axis_std), 15),
        "maxEuclideanStdMm": round(float(max_euclidean_std), 15),
    }


def modal_fraction(values: list[Any]) -> tuple[Any, float, dict[str, int]]:
    if not values:
        return None, 0.0, {}
    keys = [json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for value in values]
    counter = Counter(keys)
    modal_key, modal_count = counter.most_common(1)[0]
    modal_value = json.loads(modal_key)
    return modal_value, modal_count / len(values), dict(sorted(counter.items()))


def prepare_candidate_pool(
    server: Any,
    case: auto_bench.BenchmarkCase,
    workers: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    request = auto_bench.case_request(case, workers)
    payload = server._normalize_optimize_payload(deepcopy(request))
    target_keys = payload["targetKeys"]
    params = payload["params"]
    probe_params = payload["probeParams"]
    constraint_modes = payload["constraintModes"]
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(probe_params.get("entrySpacingUm", 3500.0))
    pipeline = server._run_optimize_pipeline(request)
    candidates = list(pipeline.get("candidatePreview") or [])
    production_selected = list(pipeline.get("selected") or [])
    generation_summary = deepcopy(
        ((pipeline.get("computeProfile") or {}).get("candidateGeneration") or {})
    )
    required_tokens = server._required_coverage_tokens(target_keys, params.get("targetingMode", "single_hemisphere"))
    candidate_keys = [base_cmp.candidate_key(candidate, idx) for idx, candidate in enumerate(candidates)]
    return {
        "request": request,
        "payload": payload,
        "targetKeys": target_keys,
        "targetingMode": params.get("targetingMode", case.targeting_mode),
        "params": params,
        "probeParams": probe_params,
        "constraintModes": constraint_modes,
        "minClearanceUm": min_clearance,
        "minEntryUm": min_entry,
        "candidates": candidates,
        "productionSelected": production_selected,
        "candidateCount": int(len(candidates)),
        "candidateGenerationRuntimeMs": round(float((time.perf_counter() - started) * 1000.0), 6),
        "candidateGeneration": generation_summary,
        "candidateSignatureHash": stable_json_hash(candidate_keys),
        "requiredTokens": sorted(required_tokens),
    }


def run_selection_repeat(
    server: Any,
    pool: dict[str, Any],
    repeat_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    selected, selector_meta = base_cmp.probeplanner_select(
        server,
        pool["candidates"],
        pool["targetKeys"],
        pool["targetingMode"],
        pool["minClearanceUm"],
        pool["minEntryUm"],
    )
    evaluated = base_cmp.evaluate_selection(
        server,
        method_id="probeplanner",
        method_label="ProbePlanner",
        baseline_type="automatic_planner_repeat",
        selected_raw=selected,
        selector_meta=selector_meta,
        target_keys=pool["targetKeys"],
        targeting_mode=pool["targetingMode"],
        params=pool["params"],
        min_clearance_um=pool["minClearanceUm"],
        min_entry_um=pool["minEntryUm"],
        annotate_risk=True,
    )
    wall_runtime_ms = (time.perf_counter() - started) * 1000.0
    coverage_signature = canonical_probe_list(evaluated.get("selectedProbes") or [], include_coordinates=False)
    trajectory_signature = canonical_probe_list(evaluated.get("selectedProbes") or [], include_coordinates=True)
    evaluated.update(
        {
            "repeatIndex": int(repeat_index),
            "wallRuntimeMs": round(float(wall_runtime_ms), 6),
            "coverageAssignmentSignatureHash": stable_json_hash(coverage_signature),
            "trajectorySignatureHash": stable_json_hash(trajectory_signature),
            "coverageAssignmentSignature": coverage_signature,
        }
    )
    return evaluated


def run_production_pipeline_repeat(
    server: Any,
    pool: dict[str, Any],
    repeat_index: int,
) -> dict[str, Any]:
    """Evaluate the selected output returned by the production optimization pipeline."""
    started = time.perf_counter()
    evaluated = base_cmp.evaluate_selection(
        server,
        method_id="probeplanner",
        method_label="ProbePlanner",
        baseline_type="automatic_full_planner_repeat",
        selected_raw=pool["productionSelected"],
        selector_meta={
            "backendOptimizer": "_run_optimize_pipeline",
            "strictProductionPipeline": True,
        },
        target_keys=pool["targetKeys"],
        targeting_mode=pool["targetingMode"],
        params=pool["params"],
        min_clearance_um=pool["minClearanceUm"],
        min_entry_um=pool["minEntryUm"],
        annotate_risk=True,
    )
    wall_runtime_ms = (time.perf_counter() - started) * 1000.0
    coverage_signature = canonical_probe_list(evaluated.get("selectedProbes") or [], include_coordinates=False)
    trajectory_signature = canonical_probe_list(evaluated.get("selectedProbes") or [], include_coordinates=True)
    evaluated.update(
        {
            "repeatIndex": int(repeat_index),
            "wallRuntimeMs": round(float(wall_runtime_ms), 6),
            "coverageAssignmentSignatureHash": stable_json_hash(coverage_signature),
            "trajectorySignatureHash": stable_json_hash(trajectory_signature),
            "coverageAssignmentSignature": coverage_signature,
        }
    )
    return evaluated


def summarize_repeats(repeats: list[dict[str, Any]]) -> dict[str, Any]:
    selected_counts = [int(row.get("selectedProbeCount", 0) or 0) for row in repeats]
    modal_count, count_consistency, count_hist = modal_fraction(selected_counts)
    candidate_counts = [
        int(row.get("candidateCount", 0) or 0)
        for row in repeats
        if row.get("candidateCount") is not None
    ]
    candidate_signatures = [
        row.get("candidateSignatureHash")
        for row in repeats
        if row.get("candidateSignatureHash") is not None
    ]
    _, candidate_count_consistency, candidate_count_hist = modal_fraction(candidate_counts)
    candidate_signature_modal, candidate_signature_consistency, candidate_signature_hist = modal_fraction(candidate_signatures)
    coverage_signatures = [row.get("coverageAssignmentSignatureHash") for row in repeats]
    trajectory_signatures = [row.get("trajectorySignatureHash") for row in repeats]
    coverage_modal, coverage_consistency, coverage_hist = modal_fraction(coverage_signatures)
    trajectory_modal, trajectory_consistency, trajectory_hist = modal_fraction(trajectory_signatures)
    coverage_rates = [float(row.get("coverageRate", 0.0) or 0.0) for row in repeats]
    runtime_values = [float(row.get("wallRuntimeMs", 0.0) or 0.0) for row in repeats]
    warm_runtime_values = runtime_values[1:] if len(runtime_values) > 1 else runtime_values

    entry_variance = coordinate_variance_summary(repeats, "entryPlannerMm")
    tip_variance = coordinate_variance_summary(repeats, "tipPlannerMm")

    runtime_mean = mean(runtime_values) if runtime_values else 0.0
    runtime_std = pstdev(runtime_values) if len(runtime_values) > 1 else 0.0
    warm_runtime_mean = mean(warm_runtime_values) if warm_runtime_values else 0.0
    warm_runtime_std = pstdev(warm_runtime_values) if len(warm_runtime_values) > 1 else 0.0

    return {
        "repeatCount": int(len(repeats)),
        "selectedProbeCountMode": int(modal_count) if modal_count is not None else None,
        "selectedProbeCountConsistency": round(float(count_consistency), 6),
        "selectedProbeCountHistogram": count_hist,
        "allSelectedProbeCountsIdentical": bool(count_consistency == 1.0),
        "candidateCountConsistency": round(float(candidate_count_consistency), 6),
        "allCandidateCountsIdentical": bool(candidate_count_consistency == 1.0),
        "candidateCountHistogram": candidate_count_hist,
        "candidateSignatureConsistency": round(float(candidate_signature_consistency), 6),
        "allCandidateSignaturesIdentical": bool(candidate_signature_consistency == 1.0),
        "candidateSignatureModalHash": candidate_signature_modal,
        "candidateSignatureHistogram": candidate_signature_hist,
        "coverageAssignmentConsistency": round(float(coverage_consistency), 6),
        "allCoverageAssignmentsIdentical": bool(coverage_consistency == 1.0),
        "coverageAssignmentModalHash": coverage_modal,
        "coverageAssignmentHistogram": coverage_hist,
        "trajectorySignatureConsistency": round(float(trajectory_consistency), 6),
        "allTrajectorySignaturesIdentical": bool(trajectory_consistency == 1.0),
        "trajectorySignatureModalHash": trajectory_modal,
        "trajectorySignatureHistogram": trajectory_hist,
        "coverageRateMin": round(float(min(coverage_rates)), 6) if coverage_rates else None,
        "coverageRateMax": round(float(max(coverage_rates)), 6) if coverage_rates else None,
        "coverageRateStd": round(float(pstdev(coverage_rates)), 15) if len(coverage_rates) > 1 else 0.0,
        "entryCoordinateVariance": entry_variance,
        "tipCoordinateVariance": tip_variance,
        "runtimeMeanMs": round(float(runtime_mean), 6),
        "runtimeMedianMs": round(float(median(runtime_values)), 6) if runtime_values else 0.0,
        "runtimeStdMs": round(float(runtime_std), 6),
        "runtimeCv": round(float(runtime_std / runtime_mean), 6) if runtime_mean else 0.0,
        "warmRuntimeMeanMs": round(float(warm_runtime_mean), 6),
        "warmRuntimeStdMs": round(float(warm_runtime_std), 6),
        "warmRuntimeCv": round(float(warm_runtime_std / warm_runtime_mean), 6) if warm_runtime_mean else 0.0,
        "repeatStatuses": dict(Counter(str(row.get("status")) for row in repeats)),
    }


def run_case(
    server: Any,
    case: auto_bench.BenchmarkCase,
    *,
    workers: int,
    repeats: int,
    repeat_scope: str,
) -> dict[str, Any]:
    pool: dict[str, Any] | None = None
    repeats_out = []
    fallback_request = auto_bench.case_request(case, workers)
    fallback_payload = server._normalize_optimize_payload(deepcopy(fallback_request))

    def fail_closed_pool(error: Exception) -> dict[str, Any]:
        target_keys = fallback_payload["targetKeys"]
        targeting_mode = fallback_payload["params"].get("targetingMode", case.targeting_mode)
        required_tokens = sorted(server._required_coverage_tokens(target_keys, targeting_mode))
        return {
            "request": fallback_request, "payload": fallback_payload, "targetKeys": target_keys,
            "targetingMode": targeting_mode, "params": fallback_payload["params"],
            "probeParams": fallback_payload["probeParams"], "constraintModes": fallback_payload["constraintModes"],
            "minClearanceUm": float(fallback_payload["params"].get("minClearance", 1000.0)),
            "minEntryUm": float(fallback_payload["probeParams"].get("entrySpacingUm", 3500.0)),
            "candidates": [], "productionSelected": [], "candidateCount": 0,
            "candidateGenerationRuntimeMs": 0.0, "candidateGeneration": {},
            "candidateSignatureHash": stable_json_hash({"failClosed": f"{error.__class__.__name__}: {error}"}),
            "requiredTokens": required_tokens,
        }

    def fail_closed_repeat(error: Exception, repeat_index: int, wall_runtime_ms: float) -> dict[str, Any]:
        signature = stable_json_hash({"failClosed": f"{error.__class__.__name__}: {error}"})
        return {
            "repeatIndex": repeat_index, "status": "fail_closed", "selectedProbeCount": 0,
            "coverageRate": 0.0, "uncoveredCoverageUnitCount": len(fallback_payload["targetKeys"]),
            "uncoveredTargets": list(fallback_payload["targetKeys"]), "selectedProbes": [],
            "wallRuntimeMs": round(float(wall_runtime_ms), 6), "candidateCount": 0,
            "candidateSignatureHash": signature, "candidateGenerationRuntimeMs": round(float(wall_runtime_ms), 6),
            "coverageAssignmentSignatureHash": stable_json_hash([]), "trajectorySignatureHash": stable_json_hash([]),
            "coverageAssignmentSignature": [], "error": f"{error.__class__.__name__}: {error}",
        }
    if repeat_scope == "selector_replay":
        pool = prepare_candidate_pool(server, case, workers)
        for idx in range(1, repeats + 1):
            repeat = run_selection_repeat(server, pool, idx)
            repeat.update(
                {
                    "candidateCount": pool["candidateCount"],
                    "candidateSignatureHash": pool["candidateSignatureHash"],
                    "candidateGenerationRuntimeMs": 0.0 if idx > 1 else pool["candidateGenerationRuntimeMs"],
                }
            )
            repeats_out.append(repeat)
    elif repeat_scope == "full_planner":
        for idx in range(1, repeats + 1):
            repeat_started = time.perf_counter()
            try:
                current_pool = prepare_candidate_pool(server, case, workers)
            except Exception as exc:
                current_pool = fail_closed_pool(exc)
                if pool is None:
                    pool = current_pool
                repeats_out.append(fail_closed_repeat(exc, idx, (time.perf_counter() - repeat_started) * 1000.0))
                continue
            if pool is None:
                pool = current_pool
            repeat = run_production_pipeline_repeat(server, current_pool, idx)
            repeat["selectorWallRuntimeMs"] = repeat["wallRuntimeMs"]
            repeat.update(
                {
                    "wallRuntimeMs": round(float((time.perf_counter() - repeat_started) * 1000.0), 6),
                    "candidateCount": current_pool["candidateCount"],
                    "candidateSignatureHash": current_pool["candidateSignatureHash"],
                    "candidateGenerationRuntimeMs": current_pool["candidateGenerationRuntimeMs"],
                }
            )
            repeats_out.append(repeat)
    else:
        raise ValueError(f"unknown repeat_scope: {repeat_scope}")
    if pool is None:
        raise RuntimeError("no candidate pool was produced")
    exact = auto_bench.token_exact_set_cover(
        server,
        pool["candidates"],
        pool["targetKeys"],
        pool["targetingMode"],
        min_clearance_um=pool["minClearanceUm"],
        min_entry_um=pool["minEntryUm"],
        max_k=6,
    )
    summary = summarize_repeats(repeats_out)
    first = repeats_out[0] if repeats_out else {}
    status = "ok"
    if not summary.get("allSelectedProbeCountsIdentical"):
        status = "unstable_probe_count"
    elif not summary.get("allCandidateSignaturesIdentical"):
        status = "unstable_candidate_generation"
    elif not summary.get("allTrajectorySignaturesIdentical"):
        status = "unstable_trajectory"
    elif any(str(row.get("status")) == "error" for row in repeats_out):
        status = "error"
    elif all(str(row.get("status")) == "fail_closed" for row in repeats_out):
        status = "fail_closed"
    return {
        "caseId": case.case_id,
        "caseType": case.case_type,
        "requestedTargets": list(case.requested_targets),
        "targetingMode": pool["targetingMode"],
        "status": status,
        "candidateCount": pool["candidateCount"],
        "candidateSignatureHash": pool["candidateSignatureHash"],
        "candidateGenerationRuntimeMs": pool["candidateGenerationRuntimeMs"],
        "candidateGeneration": pool["candidateGeneration"],
        "requiredCoverageUnits": int(len(pool["requiredTokens"])),
        "requiredTokens": pool["requiredTokens"],
        "exactMinProbeCount": exact.get("exactMinProbeCount"),
        "selectedProbeCount": first.get("selectedProbeCount"),
        "coverageRate": first.get("coverageRate"),
        "uncoveredCoverageUnitCount": first.get("uncoveredCoverageUnitCount"),
        "uncoveredTargets": first.get("uncoveredTargets"),
        "riskWorstMarginMm": first.get("riskWorstMarginMm"),
        "selectedProbes": first.get("selectedProbes") or [],
        "stability": summary,
        "plannerRequest": pool["request"],
        "repeats": repeats_out,
    }


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [case for case in cases if case.get("status") == "ok"]
    entry_std = [
        case["stability"]["entryCoordinateVariance"]["maxAxisStdMm"]
        for case in cases
        if case.get("stability", {}).get("entryCoordinateVariance", {}).get("maxAxisStdMm") is not None
    ]
    tip_std = [
        case["stability"]["tipCoordinateVariance"]["maxAxisStdMm"]
        for case in cases
        if case.get("stability", {}).get("tipCoordinateVariance", {}).get("maxAxisStdMm") is not None
    ]
    runtime_cv = [
        float(value)
        for case in cases
        if (value := case.get("stability", {}).get("runtimeCv")) is not None
    ]
    warm_runtime_cv = [
        float(value)
        for case in cases
        if (value := case.get("stability", {}).get("warmRuntimeCv")) is not None
    ]
    coverage_rates = [float(case.get("coverageRate") or 0.0) for case in cases]
    return {
        "caseCount": int(len(cases)),
        "successfulStableCases": int(len(successful)),
        "totalRepeats": int(sum(case.get("stability", {}).get("repeatCount", 0) for case in cases)),
        "allCasesSelectedProbeCountConsistent": bool(
            all(case.get("stability", {}).get("allSelectedProbeCountsIdentical") for case in cases)
        ),
        "allCasesCandidateSignaturesConsistent": bool(
            all(case.get("stability", {}).get("allCandidateSignaturesIdentical") for case in cases)
        ),
        "allCasesCoverageAssignmentConsistent": bool(
            all(case.get("stability", {}).get("allCoverageAssignmentsIdentical") for case in cases)
        ),
        "allCasesTrajectoryConsistent": bool(
            all(case.get("stability", {}).get("allTrajectorySignaturesIdentical") for case in cases)
        ),
        "selectedProbeCountConsistencyMean": round(
            float(mean(case["stability"]["selectedProbeCountConsistency"] for case in cases if case.get("stability"))),
            6,
        )
        if cases
        else None,
        "candidateSignatureConsistencyMean": round(
            float(mean(case["stability"]["candidateSignatureConsistency"] for case in cases if case.get("stability"))),
            6,
        )
        if cases
        else None,
        "coverageAssignmentConsistencyMean": round(
            float(mean(case["stability"]["coverageAssignmentConsistency"] for case in cases if case.get("stability"))),
            6,
        )
        if cases
        else None,
        "trajectorySignatureConsistencyMean": round(
            float(mean(case["stability"]["trajectorySignatureConsistency"] for case in cases if case.get("stability"))),
            6,
        )
        if cases
        else None,
        "entryMaxAxisStdMmMax": round(float(max(entry_std)), 15) if entry_std else None,
        "tipMaxAxisStdMmMax": round(float(max(tip_std)), 15) if tip_std else None,
        "runtimeCvMedian": round(float(median(runtime_cv)), 6) if runtime_cv else None,
        "runtimeCvMax": round(float(max(runtime_cv)), 6) if runtime_cv else None,
        "warmRuntimeCvMedian": round(float(median(warm_runtime_cv)), 6) if warm_runtime_cv else None,
        "warmRuntimeCvMax": round(float(max(warm_runtime_cv)), 6) if warm_runtime_cv else None,
        "coverageRateMin": round(float(min(coverage_rates)), 6) if coverage_rates else None,
        "fullCoverageCaseCount": int(sum(1 for value in coverage_rates if value == 1.0)),
    }


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "caseId",
        "caseType",
        "requestedTargets",
        "targetingMode",
        "candidateCount",
        "repeatCount",
        "candidateSignatureConsistency",
        "selectedProbeCountMode",
        "selectedProbeCountConsistency",
        "coverageAssignmentConsistency",
        "trajectorySignatureConsistency",
        "entryMaxAxisVarianceMm2",
        "entryMaxAxisStdMm",
        "tipMaxAxisVarianceMm2",
        "tipMaxAxisStdMm",
        "runtimeMeanMs",
        "runtimeStdMs",
        "runtimeCv",
        "warmRuntimeStdMs",
        "warmRuntimeCv",
        "coverageRate",
        "uncoveredCoverageUnitCount",
        "riskWorstMarginMm",
        "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            stability = case.get("stability") or {}
            entry = stability.get("entryCoordinateVariance") or {}
            tip = stability.get("tipCoordinateVariance") or {}
            writer.writerow(
                {
                    "caseId": case["caseId"],
                    "caseType": case["caseType"],
                    "requestedTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                    "targetingMode": case.get("targetingMode"),
                    "candidateCount": case.get("candidateCount"),
                    "repeatCount": stability.get("repeatCount"),
                    "candidateSignatureConsistency": stability.get("candidateSignatureConsistency"),
                    "selectedProbeCountMode": stability.get("selectedProbeCountMode"),
                    "selectedProbeCountConsistency": stability.get("selectedProbeCountConsistency"),
                    "coverageAssignmentConsistency": stability.get("coverageAssignmentConsistency"),
                    "trajectorySignatureConsistency": stability.get("trajectorySignatureConsistency"),
                    "entryMaxAxisVarianceMm2": entry.get("maxAxisVarianceMm2"),
                    "entryMaxAxisStdMm": entry.get("maxAxisStdMm"),
                    "tipMaxAxisVarianceMm2": tip.get("maxAxisVarianceMm2"),
                    "tipMaxAxisStdMm": tip.get("maxAxisStdMm"),
                    "runtimeMeanMs": stability.get("runtimeMeanMs"),
                    "runtimeStdMs": stability.get("runtimeStdMs"),
                    "runtimeCv": stability.get("runtimeCv"),
                    "warmRuntimeStdMs": stability.get("warmRuntimeStdMs"),
                    "warmRuntimeCv": stability.get("warmRuntimeCv"),
                    "coverageRate": case.get("coverageRate"),
                    "uncoveredCoverageUnitCount": case.get("uncoveredCoverageUnitCount"),
                    "riskWorstMarginMm": case.get("riskWorstMarginMm"),
                    "status": case.get("status"),
                }
            )


def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def pct(value: Any) -> str:
    number = finite_float(value)
    if number is None:
        return "NA"
    return f"{number * 100.0:.1f}%"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload["summary"]
    cases = payload["cases"]
    table = [
        "| case | targets | mode | candidates | repeats | candidate signature consistency | probe-count consistency | entry max std mm | tip max std mm | coverage-assignment consistency | runtime mean ms | runtime CV | warm runtime CV | status |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        stability = case.get("stability") or {}
        entry = stability.get("entryCoordinateVariance") or {}
        tip = stability.get("tipCoordinateVariance") or {}
        table.append(
            "| {case_id} | {targets} | {mode} | {cand} | {repeats} | {cand_cons} | {count_cons} | {entry_std} | {tip_std} | {coverage_cons} | {runtime} | {runtime_cv} | {warm_runtime_cv} | {status} |".format(
                case_id=case["caseId"],
                targets=", ".join(case.get("requestedTargets") or []),
                mode=case.get("targetingMode"),
                cand=case.get("candidateCount"),
                repeats=stability.get("repeatCount"),
                cand_cons=pct(stability.get("candidateSignatureConsistency")),
                count_cons=pct(stability.get("selectedProbeCountConsistency")),
                entry_std=fmt(entry.get("maxAxisStdMm"), 12),
                tip_std=fmt(tip.get("maxAxisStdMm"), 12),
                coverage_cons=pct(stability.get("coverageAssignmentConsistency")),
                runtime=fmt(stability.get("runtimeMeanMs"), 3),
                runtime_cv=fmt(stability.get("runtimeCv"), 3),
                warm_runtime_cv=fmt(stability.get("warmRuntimeCv"), 3),
                status=case.get("status"),
            )
        )

    lines = [
        "# Automatic Planning Result Stability - 2026-07-09",
        "",
        "This experiment repeats automatic ProbePlanner selection for target-set cases to test whether the selected probe count, trajectory coordinates, coverage assignment, and runtime remain stable for the same planning input.",
        "",
        "## Summary",
        "",
        f"- Target-set cases: {summary['caseCount']}",
        f"- Repeats per case: {payload['runConfig']['repeats']}",
        f"- Total repeated selection runs: {summary['totalRepeats']}",
        f"- Candidate-pool signature consistency: {pct(summary['candidateSignatureConsistencyMean'])} mean; all cases identical = {summary['allCasesCandidateSignaturesConsistent']}",
        f"- Selected probe count consistency: {pct(summary['selectedProbeCountConsistencyMean'])} mean; all cases identical = {summary['allCasesSelectedProbeCountConsistent']}",
        f"- Coverage assignment consistency: {pct(summary['coverageAssignmentConsistencyMean'])} mean; all cases identical = {summary['allCasesCoverageAssignmentConsistent']}",
        f"- Trajectory signature consistency: {pct(summary['trajectorySignatureConsistencyMean'])} mean; all cases identical = {summary['allCasesTrajectoryConsistent']}",
        f"- Maximum entry-coordinate axis std: {fmt(summary['entryMaxAxisStdMmMax'], 12)} mm",
        f"- Maximum tip-coordinate axis std: {fmt(summary['tipMaxAxisStdMmMax'], 12)} mm",
        f"- Median runtime coefficient of variation: {fmt(summary['runtimeCvMedian'], 6)} all repeats; {fmt(summary['warmRuntimeCvMedian'], 6)} after first-repeat cache warmup",
        f"- Full-coverage cases: {summary['fullCoverageCaseCount']}/{summary['caseCount']}",
        "",
        "## Per-case Stability",
        "",
        *table,
        "",
        "## Interpretation",
        "",
        "In this backend configuration, candidate generation and automatic selection are deterministic and no random candidate sampling seed is used. The default run repeats the full planning path for each target set: candidate generation, automatic selection, and selected-probe risk annotation. A consistency value of 100% means all repeats returned the same candidate-pool signature, selected probe count, selected trajectory signature, and target-coverage assignment.",
        "",
        "## Metric Definitions",
        "",
        "- `candidate signature consistency`: fraction of repeats matching the modal SHA-256 signature of the generated candidate pool.",
        "- `selected probe count consistency`: fraction of repeats matching the modal selected probe count for the case.",
        "- `entry/tip coordinate variance`: population variance and standard deviation of selected entry/tip planner-coordinate axes after canonical sorting of selected probes.",
        "- `coverage assignment consistency`: fraction of repeats matching the modal per-probe covered-target assignment.",
        "- `trajectory signature consistency`: fraction of repeats matching the modal selected trajectory coordinates, angle/depth, and coverage signature.",
        "- `runtime CV`: standard deviation divided by mean wall-clock runtime for the selector plus selected-probe risk annotation; `warm runtime CV` excludes the first repeat, which includes risk-annotation cache warmup.",
        "- `seed sensitivity`: not applicable for this deterministic configuration because random candidate sampling is not used.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_checkpoint(path: Path, *, run_config: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """Atomically persist completed cases so a long stability audit can resume safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "probeplanner.auto_planning_stability.checkpoint.v1",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runConfig": run_config,
        "cases": results,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run automatic planning stability benchmark.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--structures-json", default=str(atlas_util.DEFAULT_STRUCTURES_JSON))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--case-limit", type=int, default=30)
    parser.add_argument("--min-case-count", type=int, default=30)
    parser.add_argument(
        "--repeat-scope",
        choices=("full_planner", "selector_replay"),
        default="full_planner",
        help="full_planner regenerates candidates on every repeat; selector_replay reuses one audited candidate pool.",
    )
    parser.add_argument("--output-json", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.json"))
    parser.add_argument("--output-md", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.md"))
    parser.add_argument("--output-csv", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.csv"))
    parser.add_argument(
        "--checkpoint-json",
        default=None,
        help="Optional resumable checkpoint written after every completed target-set case.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if int(args.repeats) < 2:
        raise ValueError("--repeats must be >= 2 for a stability audit")
    cases = STABILITY_CASES[: int(args.case_limit)]
    out_json = Path(args.output_json).expanduser()
    checkpoint_path = (
        Path(args.checkpoint_json).expanduser()
        if args.checkpoint_json
        else out_json.with_name(f"{out_json.stem}-checkpoint.json")
    )
    checkpoint_config = {
        "caseLimit": int(args.case_limit),
        "repeats": int(args.repeats),
        "workers": int(args.workers),
        "repeatScope": str(args.repeat_scope),
    }
    resumed_by_case: dict[str, dict[str, Any]] = {}
    if checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("runConfig") == checkpoint_config:
                resumed_by_case = {
                    str(row.get("caseId")): row
                    for row in checkpoint.get("cases", [])
                    if isinstance(row, dict)
                    and str(row.get("status")) != "error"
                    and int((row.get("stability") or {}).get("repeatCount", 0)) == int(args.repeats)
                }
        except Exception:
            resumed_by_case = {}
    profile_root = Path(args.profile_root).expanduser()
    atlas_util.configure_backend_environment(profile_root, workers=int(args.workers))
    server = atlas_util.import_backend_server()

    started = time.time()
    server.load_atlas()
    if not getattr(server, "atlas_loaded", False):
        raise RuntimeError(f"Atlas failed to load: {getattr(server, 'atlas_error', None)}")

    all_targets = sorted({target for case in cases for target in case.requested_targets})
    ontology_fallback = atlas_util.apply_structure_tree_descendant_fallback(
        server,
        all_targets,
        Path(args.structures_json).expanduser(),
    )
    target_regions = auto_bench.target_region_payload(server, all_targets)

    results_by_case: dict[str, dict[str, Any]] = dict(resumed_by_case)
    with server.app.app_context():
        for idx, case in enumerate(cases, start=1):
            if case.case_id in results_by_case:
                print(f"[{idx}/{len(cases)}] {case.case_id} resume-skip", flush=True)
                continue
            print(f"[{idx}/{len(cases)}] {case.case_id} x{int(args.repeats)}", flush=True)
            try:
                result = run_case(
                    server,
                    case,
                    workers=int(args.workers),
                    repeats=int(args.repeats),
                    repeat_scope=str(args.repeat_scope),
                )
            except Exception as exc:
                result = {
                    "caseId": case.case_id,
                    "caseType": case.case_type,
                    "requestedTargets": list(case.requested_targets),
                    "targetingMode": case.targeting_mode,
                    "status": "error",
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "candidateCount": 0,
                    "stability": {
                        "repeatCount": 0,
                        "selectedProbeCountConsistency": 0.0,
                        "candidateSignatureConsistency": 0.0,
                        "coverageAssignmentConsistency": 0.0,
                        "trajectorySignatureConsistency": 0.0,
                        "allSelectedProbeCountsIdentical": False,
                        "allCandidateSignaturesIdentical": False,
                        "allCoverageAssignmentsIdentical": False,
                        "allTrajectorySignaturesIdentical": False,
                        "entryCoordinateVariance": {"maxAxisStdMm": None, "maxAxisVarianceMm2": None},
                        "tipCoordinateVariance": {"maxAxisStdMm": None, "maxAxisVarianceMm2": None},
                        "runtimeMeanMs": None,
                        "runtimeStdMs": None,
                        "runtimeCv": None,
                        "warmRuntimeMeanMs": None,
                        "warmRuntimeStdMs": None,
                        "warmRuntimeCv": None,
                    },
                    "repeats": [],
                }
            results_by_case[case.case_id] = result
            ordered_checkpoint = [results_by_case[case_def.case_id] for case_def in cases if case_def.case_id in results_by_case]
            write_checkpoint(checkpoint_path, run_config=checkpoint_config, results=ordered_checkpoint)

    results = [results_by_case[case.case_id] for case in cases if case.case_id in results_by_case]

    summary = aggregate_cases(results)
    summary["totalRuntimeSec"] = round(float(time.time() - started), 6)
    payload = {
        "schemaVersion": "probeplanner.auto_planning_stability.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(profile_root.resolve()),
        "atlasSignature": deepcopy(getattr(server, "atlas_signature", {})),
        "ontologyFallback": ontology_fallback,
        "targetRegions": target_regions,
        "runConfig": {
            "caseLimit": int(args.case_limit),
            "minCaseCount": int(args.min_case_count),
            "repeats": int(args.repeats),
            "workers": int(args.workers),
            "randomCandidateSampling": False,
            "seedSensitivity": "not_applicable_deterministic_candidate_generation_and_selector",
            "repeatScope": str(args.repeat_scope),
        },
        "summary": summary,
        "cases": results,
    }

    out_md = Path(args.output_md).expanduser()
    out_csv = Path(args.output_csv).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(out_csv, results)
    write_markdown(out_md, payload)
    print(json.dumps({"summary": summary, "json": str(out_json), "md": str(out_md), "csv": str(out_csv)}, indent=2))

    gate_failures = []
    if summary["caseCount"] < int(args.min_case_count):
        gate_failures.append(f"Only {summary['caseCount']} cases ran; required at least {int(args.min_case_count)}.")
    if summary["totalRepeats"] != summary["caseCount"] * int(args.repeats):
        gate_failures.append("Not every case produced the requested number of repeated runs.")
    if not summary["allCasesSelectedProbeCountConsistent"]:
        gate_failures.append("Selected probe count changed in at least one case.")
    if not summary["allCasesCandidateSignaturesConsistent"]:
        gate_failures.append("Candidate generation signature changed in at least one case.")
    if not summary["allCasesCoverageAssignmentConsistent"]:
        gate_failures.append("Coverage assignment changed in at least one case.")
    if not summary["allCasesTrajectoryConsistent"]:
        gate_failures.append("Selected trajectory signature changed in at least one case.")
    if summary.get("entryMaxAxisStdMmMax") not in (0.0, None):
        gate_failures.append("Entry coordinate variance was non-zero.")
    if summary.get("tipMaxAxisStdMmMax") not in (0.0, None):
        gate_failures.append("Tip coordinate variance was non-zero.")
    if any(case.get("status") == "error" for case in results):
        gate_failures.append("At least one case raised an execution error.")
    if gate_failures:
        print(json.dumps({"acceptanceGate": "failed", "failures": gate_failures}, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
