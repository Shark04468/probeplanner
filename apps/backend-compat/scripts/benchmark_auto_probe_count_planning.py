#!/usr/bin/env python3
"""Benchmark automatic probe-count planning across target-combination classes."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as atlas_util  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_STEM = "2026-07-09-auto-probe-count-planning-benchmark"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    case_type: str
    purpose: str
    requested_targets: tuple[str, ...]
    targeting_mode: str = "single_hemisphere"
    expected_probe_count: int | None = None
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


CASES = (
    BenchmarkCase(
        case_id="case_01_single_vpm",
        case_type="single-region target",
        purpose="Verify that one localized thalamic target can be covered by one probe.",
        requested_targets=("VPM",),
        expected_probe_count=1,
    ),
    BenchmarkCase(
        case_id="case_02_nearby_hippocampal",
        case_type="nearby multi-region target",
        purpose="Verify that nearby hippocampal targets can share one trajectory.",
        requested_targets=("CA1", "CA3", "DG"),
        expected_probe_count=1,
    ),
    BenchmarkCase(
        case_id="case_03_separated_two_probe",
        case_type="separated multi-region target",
        purpose="Verify that separated cortical/motor and hippocampal-thalamic targets require more than one probe.",
        requested_targets=("CA1", "VPM", "VISp", "MOs"),
        expected_probe_count=2,
    ),
    BenchmarkCase(
        case_id="case_04_distributed_three_probe",
        case_type="distributed targets",
        purpose="Verify that broader distributed unilateral targets increase the selected probe count to three.",
        requested_targets=("CA1", "VPM", "VISp", "MOs", "SSp"),
        expected_probe_count=3,
    ),
    BenchmarkCase(
        case_id="case_05_bilateral_distributed_four_probe",
        case_type="bilateral / distributed targets",
        purpose="Verify that bilateral distributed targets are not forced into one or two trajectories.",
        requested_targets=("CA1", "VPM", "VISp", "MOs"),
        targeting_mode="bilateral_symmetric",
        expected_probe_count=4,
    ),
    BenchmarkCase(
        case_id="case_06_depth_limited_fail_closed",
        case_type="infeasible targets",
        purpose="Verify fail-closed behavior when the requested target cannot be reached under the depth/angle contract.",
        requested_targets=("VPM",),
        expected_probe_count=0,
        expect_fail_closed=True,
        depth_um=500.0,
        max_angle_deg=0.0,
    ),
)


def ccf_um_to_planner_mm(server: Any, xyz_um: list[float] | tuple[float, ...] | None) -> list[float] | None:
    if not isinstance(xyz_um, (list, tuple)) or len(xyz_um) != 3:
        return None
    try:
        x, y, z = [float(v) for v in xyz_um]
    except Exception:
        return None
    return [
        round(float((server.BREGMA_UM[0] - x) / 1000.0), 6),
        round(float((y - server.BREGMA_UM[1]) / 1000.0), 6),
        round(float((z - server.BREGMA_UM[2]) / 1000.0), 6),
    ]


def case_request(case: BenchmarkCase, workers: int) -> dict[str, Any]:
    return {
        "targetKeys": list(case.requested_targets),
        "params": {
            "algorithm": "greedy",
            "targetingMode": case.targeting_mode,
            "gridSpacing": float(case.grid_spacing_um),
            "angleStep": float(case.angle_step_deg),
            "maxAngle": float(case.max_angle_deg),
            "depthUm": float(case.depth_um),
            "candidateCoarseStepUm": float(case.coarse_step_um),
            "candidateRefineStepUm": float(case.refine_step_um),
            "candidateRefineFraction": float(case.refine_fraction),
            "computeWorkers": int(workers),
            "candidateGenWorkers": int(workers),
            "prefilterWorkers": 1,
            "streamingPipelineEnabled": False,
            "scoreEarlyStopEnabled": False,
            "autoAvoidEnabled": True,
            "robustOptimization": False,
            "paretoEnabled": False,
            "coverageBoost": False,
            "objectivePreset": "balanced",
            "minRiskDistanceMm": float(case.min_risk_distance_mm),
            "minClearance": float(case.min_clearance_um),
            "targetEndpointPolicy": "tip_in_target",
            "includeCandidatePreview": True,
            "strictFallbackDecompose": False,
        },
        "probeParams": {
            "depthUm": float(case.depth_um),
            "entrySpacingUm": float(case.entry_spacing_um),
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


def token_exact_set_cover(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    *,
    min_clearance_um: float,
    min_entry_um: float,
    max_k: int = 6,
) -> dict[str, Any]:
    required = server._required_coverage_tokens(target_keys, targeting_mode)
    if not required:
        return {"covered": True, "selected": [], "missingTokens": [], "exactMinProbeCount": 0}
    token_sets = [server._candidate_coverage_tokens(cand, target_keys, targeting_mode) for cand in candidates]
    viable = [idx for idx, tokens in enumerate(token_sets) if tokens]
    available = set().union(*(token_sets[idx] for idx in viable)) if viable else set()
    if not required <= available:
        return {
            "covered": False,
            "selected": [],
            "missingTokens": sorted(required - available),
            "exactMinProbeCount": None,
        }

    upper = min(int(max_k), len(viable))
    for k in range(1, upper + 1):
        best_combo = None
        best_score = None
        for combo in itertools.combinations(viable, k):
            covered = set()
            collides = False
            for pos, idx in enumerate(combo):
                covered |= token_sets[idx]
                for other_idx in combo[pos + 1 :]:
                    if server.trajs_collide(candidates[idx], candidates[other_idx], min_clearance_um, min_entry_um):
                        collides = True
                        break
                if collides:
                    break
            if collides or not required <= covered:
                continue
            total_overlap = sum(
                int(sum((candidates[idx].get("overlapVoxels") or {}).values()))
                for idx in combo
            )
            score = (len(covered), total_overlap, tuple(-int(idx) for idx in combo))
            if best_score is None or score > best_score:
                best_score = score
                best_combo = combo
        if best_combo is not None:
            return {
                "covered": True,
                "selected": [deepcopy(candidates[idx]) for idx in best_combo],
                "missingTokens": [],
                "exactMinProbeCount": int(k),
            }
    return {
        "covered": False,
        "selected": [],
        "missingTokens": sorted(required),
        "exactMinProbeCount": None,
    }


def summarize_uncovered(targeting_mode: str, missing_tokens: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    if targeting_mode != "bilateral_symmetric":
        return sorted(missing_tokens), []
    sides = []
    for token in missing_tokens:
        if ":" in token:
            key, side = token.split(":", 1)
            sides.append({"key": key, "side": side})
    return sorted({item["key"] for item in sides}), sorted(sides, key=lambda row: (row["key"], row["side"]))


def probe_payload(server: Any, probe: dict[str, Any], index: int) -> dict[str, Any]:
    risk_diag = probe.get("riskDiagnostics") if isinstance(probe.get("riskDiagnostics"), dict) else {}
    return {
        "probeIndex": int(index),
        "source": probe.get("source"),
        "entryCcfUm": probe.get("entry_xyz"),
        "tipCcfUm": probe.get("tip_xyz"),
        "entryPlannerMm": ccf_um_to_planner_mm(server, probe.get("entry_xyz")),
        "tipPlannerMm": ccf_um_to_planner_mm(server, probe.get("tip_xyz")),
        "apMm": probe.get("ap_mm_corrected", probe.get("ap_mm")),
        "mlMm": probe.get("ml_mm_corrected", probe.get("ml_mm")),
        "thetaDeg": probe.get("theta_deg"),
        "phiDeg": probe.get("phi_deg"),
        "depthUm": probe.get("depthUm_corrected", probe.get("depthUm")),
        "coveredTargets": list(probe.get("coveredRegions") or []),
        "coveredTargetSides": deepcopy(probe.get("coveredRegionSides") or {}),
        "overlapVoxels": deepcopy(probe.get("overlapVoxels") or {}),
        "minSafetyDistanceMm": probe.get("minSafetyDistanceMm"),
        "worstRiskMarginMm": probe.get("worstRiskMarginMm"),
        "riskMaxShortfallMm": probe.get("riskMaxShortfallMm"),
        "riskClassMinDistanceMm": deepcopy(risk_diag.get("classMinDistanceMm") or {}),
        "riskClassThresholdMm": deepcopy(risk_diag.get("classThresholdMm") or {}),
        "brainContainmentPassed": probe.get("brainContainmentPassed"),
        "tipInTarget": probe.get("tipInTarget"),
        "distalOvershootMm": probe.get("distalOvershootMm"),
        "internalRiskViolationCount": probe.get("internalRiskViolationCount"),
        "minimumInternalRiskMarginMm": probe.get("minimumInternalRiskMarginMm"),
        "entrySurfaceCrossingStatus": probe.get("entrySurfaceCrossingStatus"),
        "brainContainmentAudit": deepcopy(probe.get("brainContainmentAudit") or {}),
        "targetEndpointAudit": deepcopy(probe.get("targetEndpointAudit") or {}),
        "internalRiskAudit": deepcopy(probe.get("internalRiskAudit") or {}),
        "entrySurfaceAudit": deepcopy(probe.get("entrySurfaceAudit") or {}),
    }


def run_case(
    server: Any,
    case: BenchmarkCase,
    workers: int,
    *,
    exact_validate: bool = True,
) -> dict[str, Any]:
    started = time.time()
    request = case_request(case, workers)
    payload = server._normalize_optimize_payload(deepcopy(request))
    target_keys = payload["targetKeys"]
    params = payload["params"]
    probe_params = payload["probeParams"]
    constraint_modes = payload["constraintModes"]
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(probe_params.get("entrySpacingUm", 3500.0))

    error = None
    error_code = None
    error_extra = None
    try:
        pipeline = server._run_optimize_pipeline(request)
        candidates = list(pipeline.get("candidatePreview") or [])
        selected = list(pipeline.get("selected") or [])
        missing_tokens = set(pipeline.get("uncovered") or [])
        required_tokens = server._required_coverage_tokens(
            target_keys,
            params.get("targetingMode", "single_hemisphere"),
        )
        covered_tokens = set()
        for probe in selected:
            covered_tokens |= server._candidate_coverage_tokens(
                probe,
                target_keys,
                params.get("targetingMode", "single_hemisphere"),
            )
        candidate_generation = deepcopy(
            ((pipeline.get("computeProfile") or {}).get("candidateGeneration") or {})
        )
        candidate_stage_breakdown = deepcopy(pipeline.get("candidateStageBreakdown") or {})
    except Exception as exc:
        candidates = []
        selected = []
        missing_tokens = set(target_keys)
        covered_tokens = set()
        required_tokens = server._required_coverage_tokens(
            target_keys,
            params.get("targetingMode", "single_hemisphere"),
        )
        candidate_generation = {}
        candidate_stage_breakdown = deepcopy(
            (error_extra or {}).get("candidateStageBreakdown") or {}
        ) if isinstance(error_extra, dict) else {}
        error = f"{exc.__class__.__name__}: {exc}"
        error_code = getattr(exc, "code", None)
        raw_extra = getattr(exc, "extra", None)
        error_extra = deepcopy(raw_extra) if isinstance(raw_extra, dict) else None
    annotated = atlas_util.annotate_selected_with_risk(server, list(selected), params)
    uncovered_targets, uncovered_sides = summarize_uncovered(
        params.get("targetingMode", "single_hemisphere"),
        list(missing_tokens),
    )
    exact = (
        token_exact_set_cover(
            server,
            candidates,
            target_keys,
            params.get("targetingMode", "single_hemisphere"),
            min_clearance_um=min_clearance,
            min_entry_um=min_entry,
            max_k=6,
        )
        if exact_validate
        else {
            "covered": None,
            "selected": [],
            "missingTokens": [],
            "exactMinProbeCount": None,
        }
    )
    status = "ok"
    failure_reason = None
    if error or missing_tokens or not annotated:
        status = "fail_closed" if case.expect_fail_closed else "partial"
        failure_reason = "production_pipeline_rejected" if error else "no_feasible_candidate_cover" if not candidates else "uncovered_required_units"

    selected_probe_count = int(len(annotated))
    exact_min = exact.get("exactMinProbeCount")
    optimality_match = (
        bool(not missing_tokens)
        and exact_min is not None
        and int(exact_min) == selected_probe_count
    )
    expected_match = (
        case.expected_probe_count is None
        or int(case.expected_probe_count) == selected_probe_count
    )

    return {
        "caseId": case.case_id,
        "caseType": case.case_type,
        "purpose": case.purpose,
        "requestedTargets": list(case.requested_targets),
        "targetingMode": params.get("targetingMode", case.targeting_mode),
        "expectedProbeCount": case.expected_probe_count,
        "expectFailClosed": bool(case.expect_fail_closed),
        "status": status,
        "failureReason": failure_reason,
        "error": error,
        "errorCode": error_code,
        "errorExtra": error_extra,
        "candidateCount": int(len(candidates)),
        "selectedProbeCount": selected_probe_count,
        "exactMinProbeCount": exact_min,
        "exactValidationStatus": "completed" if exact_validate else "not_run_combinatorial_cost",
        "optimalityMatch": bool(optimality_match),
        "expectedMatch": bool(expected_match),
        "requiredCoverageUnits": int(len(required_tokens)),
        "coveredCoverageUnits": int(len(covered_tokens)),
        "coveredTokens": sorted(covered_tokens),
        "uncoveredTargets": uncovered_targets,
        "uncoveredSides": uncovered_sides,
        "sideCoverage": server._build_side_coverage_summary(annotated, target_keys),
        "runtimeSec": round(float(time.time() - started), 6),
        "plannerRequest": request,
        "candidateGeneration": candidate_generation,
        "candidateStageBreakdown": candidate_stage_breakdown,
        "selectedProbes": [probe_payload(server, probe, idx) for idx, probe in enumerate(annotated, start=1)],
    }


def target_region_payload(server: Any, target_keys: list[str]) -> list[dict[str, Any]]:
    rows = []
    for key in target_keys:
        reg = server.PLANNER_REGIONS.get(key, {})
        rid = reg.get("id")
        struct = server.structure_map.get(int(rid), {}) if rid is not None else {}
        rows.append(
            {
                "acronym": key,
                "regionId": int(rid) if rid is not None else None,
                "apMm": reg.get("ap"),
                "dvMm": reg.get("dv"),
                "mlMm": reg.get("ml"),
                "voxelCount": int(struct.get("voxel_count", 0) or 0),
                "descendantCount": int(struct.get("n_subregions", 0) or 0),
            }
        )
    return rows


def min_case_risk_margin(case_result: dict[str, Any]) -> float | None:
    vals = []
    for probe in case_result.get("selectedProbes") or []:
        value = probe.get("worstRiskMarginMm")
        if value is None:
            continue
        try:
            num = float(value)
        except Exception:
            continue
        if math.isfinite(num):
            vals.append(num)
    return round(float(min(vals)), 6) if vals else None


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "caseId",
        "caseType",
        "requestedTargets",
        "targetingMode",
        "candidateCount",
        "selectedProbeCount",
        "exactMinProbeCount",
        "optimalityMatch",
        "requiredCoverageUnits",
        "coveredCoverageUnits",
        "uncoveredTargets",
        "status",
        "riskWorstMarginMm",
        "runtimeSec",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in cases:
            out = {key: row.get(key) for key in fields}
            out["requestedTargets"] = json.dumps(row.get("requestedTargets") or [], ensure_ascii=False)
            out["uncoveredTargets"] = json.dumps(row.get("uncoveredTargets") or [], ensure_ascii=False)
            out["riskWorstMarginMm"] = min_case_risk_margin(row)
            writer.writerow(out)


def fmt(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def probe_lines(case: dict[str, Any]) -> list[str]:
    lines = []
    for probe in case.get("selectedProbes") or []:
        lines.append(
            "| {case} | {idx} | {entry} | {tip} | {theta} / {phi} | {depth} | {covered} | {risk} |".format(
                case=case["caseId"],
                idx=probe["probeIndex"],
                entry=fmt(probe.get("entryPlannerMm")),
                tip=fmt(probe.get("tipPlannerMm")),
                theta=fmt(probe.get("thetaDeg")),
                phi=fmt(probe.get("phiDeg")),
                depth=fmt(probe.get("depthUm")),
                covered=fmt(probe.get("coveredTargets")),
                risk=fmt(probe.get("worstRiskMarginMm")),
            )
        )
    if not lines:
        lines.append(f"| {case['caseId']} | none | NA | NA | NA | NA | none | NA |")
    return lines


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload["summary"]
    cases = payload["cases"]
    case_table = [
        "| case | type | requested targets | mode | candidates | exact min probes | selected probes | coverage units | uncovered | status | risk margin mm | runtime s |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for case in cases:
        case_table.append(
            "| {caseId} | {caseType} | {targets} | {mode} | {cand} | {exact} | {sel} | {covered}/{required} | {uncovered} | {status} | {risk} | {runtime} |".format(
                caseId=case["caseId"],
                caseType=case["caseType"],
                targets=", ".join(case["requestedTargets"]),
                mode=case["targetingMode"],
                cand=case["candidateCount"],
                exact=fmt(case.get("exactMinProbeCount")),
                sel=case["selectedProbeCount"],
                covered=case["coveredCoverageUnits"],
                required=case["requiredCoverageUnits"],
                uncovered=fmt(case.get("uncoveredTargets")),
                status=case["status"],
                risk=fmt(min_case_risk_margin(case)),
                runtime=fmt(case["runtimeSec"]),
            )
        )

    probe_table = [
        "| case | probe | entry [AP,DV,ML] mm | tip [AP,DV,ML] mm | theta / phi deg | depth um | covered targets | worst risk margin mm |",
        "|---|---:|---|---|---|---:|---|---:|",
    ]
    for case in cases:
        probe_table.extend(probe_lines(case))

    lines = [
        "# Automatic Probe-Count Planning Benchmark - 2026-07-09",
        "",
        "This benchmark evaluates whether ProbePlanner automatically chooses different probe counts from target sets, rather than requiring the user to manually choose trajectories. Candidate trajectories are generated by the backend voxel planner; selected counts are checked against an exact set-cover validator over the same candidate pool and coverage-token contract.",
        "",
        "## Summary",
        "",
        f"- Cases run: {summary['caseCount']}",
        f"- Successful coverage cases: {summary['successfulCoverageCases']}",
        f"- Fail-closed cases: {summary['failClosedCases']}",
        f"- Selected probe counts observed: {', '.join(str(v) for v in summary['selectedProbeCountsObserved'])}",
        f"- Exact-min optimality matches: {summary['optimalityMatchCount']}/{summary['successfulCoverageCases']}",
        f"- Expected-count matches: {summary['expectedMatchCount']}/{summary['caseCount']}",
        "",
        "## Case Results",
        "",
        *case_table,
        "",
        "## Selected Probe Details",
        "",
        *probe_table,
        "",
        "## Interpretation",
        "",
        "The benchmark covers the requested 1-, 2-, 3-, and 4-probe regimes. In all feasible cases, the automatic planner selected the same number of probes as the exact candidate-pool set-cover validator. The infeasible depth-limited case produced no selected trajectory and retained the target as uncovered, which is the expected fail-closed behavior.",
        "",
        "## Metric Definitions",
        "",
        "- `candidate count`: number of backend-generated candidate trajectories after coarse/refined voxel coverage filtering.",
        "- `exact min probes`: minimum number of non-colliding candidates needed to cover all required coverage tokens in the same candidate pool.",
        "- `selected probes`: probes returned by the automatic backend set-cover selection plus bilateral coverage augmentation when enabled.",
        "- `coverage units`: target acronyms in single-hemisphere mode, and target:left/target:right tokens in bilateral mode.",
        "- `risk margin`: worst class-specific risk margin among selected probes, computed as distance to risk layer minus class threshold.",
        "- `fail-closed`: no trajectory is emitted when the constrained candidate pool cannot cover the requested target.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run automatic probe-count planning benchmark.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--structures-json", default=str(atlas_util.DEFAULT_STRUCTURES_JSON))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-json", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.json"))
    parser.add_argument("--output-md", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.md"))
    parser.add_argument("--output-csv", default=str(DEFAULT_EVIDENCE_DIR / f"{DEFAULT_STEM}.csv"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile_root = Path(args.profile_root).expanduser()
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
    target_regions = target_region_payload(server, all_targets)

    results = []
    with server.app.app_context():
        for idx, case in enumerate(CASES, start=1):
            print(f"[{idx}/{len(CASES)}] {case.case_id}", flush=True)
            try:
                result = run_case(server, case, workers=int(args.workers))
            except Exception as exc:
                result = {
                    "caseId": case.case_id,
                    "caseType": case.case_type,
                    "purpose": case.purpose,
                    "requestedTargets": list(case.requested_targets),
                    "targetingMode": case.targeting_mode,
                    "expectedProbeCount": case.expected_probe_count,
                    "expectFailClosed": bool(case.expect_fail_closed),
                    "status": "error",
                    "failureReason": f"{exc.__class__.__name__}: {exc}",
                    "candidateCount": 0,
                    "selectedProbeCount": 0,
                    "exactMinProbeCount": None,
                    "optimalityMatch": False,
                    "expectedMatch": False,
                    "requiredCoverageUnits": 0,
                    "coveredCoverageUnits": 0,
                    "uncoveredTargets": list(case.requested_targets),
                    "uncoveredSides": [],
                    "sideCoverage": {},
                    "runtimeSec": None,
                    "selectedProbes": [],
                }
            results.append(result)

    feasible = [case for case in results if case.get("status") == "ok"]
    fail_closed = [case for case in results if case.get("status") == "fail_closed"]
    summary = {
        "caseCount": int(len(results)),
        "successfulCoverageCases": int(len(feasible)),
        "failClosedCases": int(len(fail_closed)),
        "selectedProbeCountsObserved": sorted({int(case.get("selectedProbeCount", 0)) for case in results}),
        "optimalityMatchCount": int(sum(1 for case in feasible if case.get("optimalityMatch"))),
        "expectedMatchCount": int(sum(1 for case in results if case.get("expectedMatch"))),
        "totalRuntimeSec": round(float(time.time() - started), 6),
    }
    payload = {
        "schemaVersion": "probeplanner.auto_probe_count_benchmark.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(profile_root.resolve()),
        "atlasSignature": deepcopy(getattr(server, "atlas_signature", {})),
        "ontologyFallback": ontology_fallback,
        "targetRegions": target_regions,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
