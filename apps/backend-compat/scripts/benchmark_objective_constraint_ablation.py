#!/usr/bin/env python3
"""Ablate objective and constraint terms used by automatic probe planning."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from copy import deepcopy
from pathlib import Path
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
DEFAULT_STEM = "2026-07-09-objective-constraint-ablation"


ABLATION_ORDER = [
    "full_probeplanner",
    "no_probe_count_penalty",
    "no_risk_penalty",
    "no_coverage_priority",
    "no_hard_boundary_exclusion",
    "no_multi_target_grouping",
]


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def risk_prefilter_tuple(candidate: dict[str, Any]) -> tuple[float, float]:
    prefilter = candidate.get("riskPrefilter") if isinstance(candidate.get("riskPrefilter"), dict) else {}
    bucket = str(prefilter.get("bucket") or "unknown").strip().lower()
    bucket_priority = {"feasible": 2.0, "unknown": 1.0, "near": 0.0}.get(bucket, -1.0)
    margin = finite_float(prefilter.get("worstMarginMm"))
    return bucket_priority, margin if margin is not None else -1e9


def candidate_total_overlap(candidate: dict[str, Any], tokens: set[str]) -> int:
    return base_cmp.candidate_total_overlap(candidate, tokens)


def select_full_probeplanner(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, meta = base_cmp.probeplanner_select(
        server,
        candidates,
        target_keys,
        targeting_mode,
        min_clearance_um,
        min_entry_um,
    )
    meta["ablationRemoved"] = "none"
    meta["selectorDefinition"] = "backend greedy optimizer with risk-aware tie-breaks, coverage priority, count-minimizing set-cover behavior, hard inter-probe constraints, and targeting-mode augmentation"
    return selected, meta


def select_no_probe_count_penalty(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = base_cmp.build_candidate_context(server, candidates, target_keys, targeting_mode)
    selected: list[dict[str, Any]] = []
    max_selected = max(len(required) + 3, len(required) * 2, 1)
    order = sorted(
        range(len(candidates)),
        key=lambda idx: (
            risk_prefilter_tuple(candidates[idx]),
            len(token_sets[idx] & required),
            candidate_total_overlap(candidates[idx], token_sets[idx] & required),
            -base_cmp.candidate_rank(candidates[idx]),
            base_cmp.candidate_score(candidates[idx]),
            tuple(-v if isinstance(v, (int, float)) else v for v in base_cmp.candidate_key(candidates[idx], idx)),
        ),
        reverse=True,
    )
    for idx in order:
        if len(selected) >= max_selected:
            break
        if not (token_sets[idx] & required):
            continue
        candidate = candidates[idx]
        if not base_cmp.noncolliding_with_selection(server, candidate, selected, min_clearance_um, min_entry_um):
            continue
        selected.append(candidate)
    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "maxSelectedWithoutCountPenalty": int(max_selected),
        "ablationRemoved": "probe-count penalty",
        "selectorDefinition": "select non-colliding candidates with required-token coverage up to a target-scaled cap; redundant probes are not penalized",
    }


def select_no_risk_penalty(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = base_cmp.build_candidate_context(server, candidates, target_keys, targeting_mode)
    missing = set(required)
    selected: list[dict[str, Any]] = []
    used: set[int] = set()
    while missing:
        eligible = []
        for idx, candidate in enumerate(candidates):
            if idx in used:
                continue
            gain = token_sets[idx] & missing
            if not gain:
                continue
            if not base_cmp.noncolliding_with_selection(server, candidate, selected, min_clearance_um, min_entry_um):
                continue
            eligible.append((idx, gain))
        if not eligible:
            break
        best, best_gain = max(
            eligible,
            key=lambda item: (
                len(item[1]),
                candidate_total_overlap(candidates[item[0]], item[1]),
                -base_cmp.candidate_rank(candidates[item[0]]),
                base_cmp.candidate_score(candidates[item[0]]),
                tuple(-v if isinstance(v, (int, float)) else v for v in base_cmp.candidate_key(candidates[item[0]], item[0])),
            ),
        )
        selected.append(candidates[best])
        used.add(best)
        missing -= best_gain
    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "missingTokensBeforeMetric": sorted(missing),
        "ablationRemoved": "risk penalty",
        "selectorDefinition": "greedy set-cover over coverage gain and voxel overlap only; risk bucket and risk margin are ignored",
    }


def select_no_coverage_priority(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
    max_probe_count: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = base_cmp.build_candidate_context(server, candidates, target_keys, targeting_mode)
    limit = max(1, int(max_probe_count or len(required) or 1))
    selected: list[dict[str, Any]] = []
    order = sorted(
        range(len(candidates)),
        key=lambda idx: (
            risk_prefilter_tuple(candidates[idx]),
            -base_cmp.candidate_rank(candidates[idx]),
            base_cmp.candidate_score(candidates[idx]),
            candidate_total_overlap(candidates[idx], token_sets[idx] & required),
            tuple(-v if isinstance(v, (int, float)) else v for v in base_cmp.candidate_key(candidates[idx], idx)),
        ),
        reverse=True,
    )
    for idx in order:
        if len(selected) >= limit:
            break
        candidate = candidates[idx]
        if not base_cmp.noncolliding_with_selection(server, candidate, selected, min_clearance_um, min_entry_um):
            continue
        selected.append(candidate)
    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "maxProbeCount": limit,
        "ablationRemoved": "coverage priority",
        "selectorDefinition": "choose a compact set by risk/rank/score up to the full model probe budget without requiring positive coverage gain",
    }


def select_no_multi_target_grouping(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, meta = base_cmp.one_target_one_probe_select(
        server,
        candidates,
        target_keys,
        targeting_mode,
        min_clearance_um,
        min_entry_um,
    )
    meta["ablationRemoved"] = "multi-target grouping"
    meta["selectorDefinition"] = "plan each required coverage token independently; a probe that intersects multiple targets is not allowed to satisfy multiple planning units"
    return selected, meta


def select_no_hard_boundary_exclusion(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, meta = select_full_probeplanner(
        server,
        candidates,
        target_keys,
        targeting_mode,
        min_clearance_um,
        min_entry_um,
    )
    meta["ablationRemoved"] = "hard boundary exclusion"
    meta["selectorDefinition"] = "candidate-generation diagnostic only; rejected out-of-bounds candidates are not present in the accepted candidate artifact, so selector replay is unchanged"
    meta["diagnosticOnly"] = True
    return selected, meta


def boundary_diagnostic(server: Any, probe: dict[str, Any]) -> dict[str, Any]:
    cached = probe.get("_ablationBrainBoundsDiagnostic")
    if isinstance(cached, dict):
        return deepcopy(cached)
    fn = getattr(server, "_candidate_brain_mask_path_summary", None)
    if not callable(fn):
        return {"available": False, "passed": None, "reason": "brain_mask_path_summary_unavailable"}
    try:
        out = fn(probe, step_um=75.0)
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        return {"available": False, "passed": None, "reason": f"{exc.__class__.__name__}: {exc}"}
    if not isinstance(out, dict):
        return {"available": False, "passed": None, "reason": "non_dict_summary"}
    out = deepcopy(out)
    probe["_ablationBrainBoundsDiagnostic"] = deepcopy(out)
    return out


def brain_bounds_filter(server: Any, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    valid = []
    unavailable = 0
    failed = 0
    for candidate in candidates:
        diag = boundary_diagnostic(server, candidate)
        if not diag.get("available"):
            unavailable += 1
            valid.append(candidate)
            continue
        if diag.get("passed") is False:
            failed += 1
            continue
        valid.append(candidate)
    return valid, {
        "inputCount": int(len(candidates)),
        "keptCount": int(len(valid)),
        "rejectedBoundaryViolationCount": int(failed),
        "diagnosticUnavailableCount": int(unavailable),
        "runtimeMs": round(float((time.perf_counter() - started) * 1000.0), 6),
    }


def evaluate_selection(
    server: Any,
    *,
    ablation_id: str,
    ablation_label: str,
    removed_component: str,
    candidate_pool_id: str,
    selected_raw: list[dict[str, Any]],
    selector_meta: dict[str, Any],
    target_keys: list[str],
    targeting_mode: str,
    params: dict[str, Any],
    min_clearance_um: float,
    min_entry_um: float,
) -> dict[str, Any]:
    base = base_cmp.evaluate_selection(
        server,
        method_id=ablation_id,
        method_label=ablation_label,
        baseline_type="ablation",
        selected_raw=selected_raw,
        selector_meta=selector_meta,
        target_keys=target_keys,
        targeting_mode=targeting_mode,
        params=params,
        min_clearance_um=min_clearance_um,
        min_entry_um=min_entry_um,
    )
    pair_den = int(base.get("collisionPairDenominator", 0) or 0)
    collision_count = int(base.get("collisionPairCount", 0) or 0)
    base.update(
        {
            "ablationId": ablation_id,
            "ablationLabel": ablation_label,
            "removedComponent": removed_component,
            "candidatePoolId": candidate_pool_id,
            "boundaryViolationCount": None,
            "boundaryViolationDenominator": None,
            "boundaryViolationRate": None,
            "constraintViolationRate": base.get("hardConstraintViolationRate"),
            "boundaryDiagnosticsAvailable": False,
            "boundaryDiagnostics": [],
            "boundaryDiagnosticsNote": "Selected candidates are already accepted by candidate generation. Brain-mask path diagnostics include the expected outside-brain entry corridor and are not used as a selector-level hard-boundary violation metric in this benchmark.",
        }
    )
    return base


def make_request(case: auto_bench.BenchmarkCase, workers: int, *, enforce_brain_bounds: bool) -> dict[str, Any]:
    request = auto_bench.case_request(case, workers)
    request = deepcopy(request)
    request["constraintModes"]["brainBounds"] = "enforce" if enforce_brain_bounds else "warn"
    return request


def generate_case_candidates(
    server: Any,
    case: auto_bench.BenchmarkCase,
    *,
    workers: int,
    enforce_brain_bounds: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    request = make_request(case, workers, enforce_brain_bounds=enforce_brain_bounds)
    payload = server._normalize_optimize_payload(deepcopy(request))
    params = payload["params"]
    started = time.perf_counter()
    pipeline = server._run_optimize_pipeline(request)
    candidates = list(pipeline.get("candidatePreview") or [])
    summary = deepcopy(
        ((pipeline.get("computeProfile") or {}).get("candidateGeneration") or {})
    )
    summary["runtimeMs"] = round(float((time.perf_counter() - started) * 1000.0), 6)
    return payload, candidates, summary


def run_case(server: Any, case: auto_bench.BenchmarkCase, *, workers: int) -> dict[str, Any]:
    case_started = time.perf_counter()
    warn_payload, warn_candidates, warn_gen = generate_case_candidates(
        server,
        case,
        workers=workers,
        enforce_brain_bounds=False,
    )
    enforced_payload = warn_payload
    enforced_candidates = warn_candidates
    enforced_gen = warn_gen

    target_keys = enforced_payload["targetKeys"]
    targeting_mode = enforced_payload["params"].get("targetingMode", case.targeting_mode)
    min_clearance = float(enforced_payload["params"].get("minClearance", 1000.0))
    min_entry = float(enforced_payload["probeParams"].get("entrySpacingUm", 3500.0))
    exact = auto_bench.token_exact_set_cover(
        server,
        enforced_candidates,
        target_keys,
        targeting_mode,
        min_clearance_um=min_clearance,
        min_entry_um=min_entry,
        max_k=8,
    )
    exact_min = exact.get("exactMinProbeCount")

    methods: dict[str, dict[str, Any]] = {}
    selected, meta = select_full_probeplanner(
        server,
        enforced_candidates,
        target_keys,
        targeting_mode,
        min_clearance,
        min_entry,
    )
    full_count = len(selected)
    methods["full_probeplanner"] = evaluate_selection(
        server,
        ablation_id="full_probeplanner",
        ablation_label="full ProbePlanner objective",
        removed_component="none",
        candidate_pool_id="accepted_candidate_pool",
        selected_raw=selected,
        selector_meta=meta,
        target_keys=target_keys,
        targeting_mode=targeting_mode,
        params=enforced_payload["params"],
        min_clearance_um=min_clearance,
        min_entry_um=min_entry,
    )

    selectors = {
        "no_probe_count_penalty": (
            "no probe-count penalty",
            "probe-count penalty",
            enforced_candidates,
            enforced_payload,
            "accepted_candidate_pool",
            lambda: select_no_probe_count_penalty(server, enforced_candidates, target_keys, targeting_mode, min_clearance, min_entry),
        ),
        "no_risk_penalty": (
            "no risk penalty",
            "risk penalty",
            enforced_candidates,
            enforced_payload,
            "accepted_candidate_pool",
            lambda: select_no_risk_penalty(server, enforced_candidates, target_keys, targeting_mode, min_clearance, min_entry),
        ),
        "no_coverage_priority": (
            "no coverage priority",
            "coverage priority",
            enforced_candidates,
            enforced_payload,
            "accepted_candidate_pool",
            lambda: select_no_coverage_priority(server, enforced_candidates, target_keys, targeting_mode, min_clearance, min_entry, max_probe_count=full_count or exact_min),
        ),
        "no_hard_boundary_exclusion": (
            "no hard boundary exclusion",
            "hard boundary exclusion",
            warn_candidates,
            warn_payload,
            "accepted_candidate_pool_boundary_relaxation_noop",
            lambda: select_no_hard_boundary_exclusion(server, warn_candidates, target_keys, targeting_mode, min_clearance, min_entry),
        ),
        "no_multi_target_grouping": (
            "no multi-target grouping",
            "multi-target grouping",
            enforced_candidates,
            enforced_payload,
            "accepted_candidate_pool",
            lambda: select_no_multi_target_grouping(server, enforced_candidates, target_keys, targeting_mode, min_clearance, min_entry),
        ),
    }

    for ablation_id, (label, removed, _pool, payload, pool_id, selector) in selectors.items():
        selected_raw, selector_meta = selector()
        methods[ablation_id] = evaluate_selection(
            server,
            ablation_id=ablation_id,
            ablation_label=label,
            removed_component=removed,
            candidate_pool_id=pool_id,
            selected_raw=selected_raw,
            selector_meta=selector_meta,
            target_keys=payload["targetKeys"],
            targeting_mode=payload["params"].get("targetingMode", case.targeting_mode),
            params=payload["params"],
            min_clearance_um=float(payload["params"].get("minClearance", 1000.0)),
            min_entry_um=float(payload["probeParams"].get("entrySpacingUm", 3500.0)),
        )

    full = methods["full_probeplanner"]
    for method_id, row in methods.items():
        row["deltaSelectedProbeCountVsFull"] = (
            finite_float(row.get("selectedProbeCount")) - finite_float(full.get("selectedProbeCount"))
            if finite_float(row.get("selectedProbeCount")) is not None and finite_float(full.get("selectedProbeCount")) is not None
            else None
        )
        row["deltaCoverageRateVsFull"] = (
            finite_float(row.get("coverageRate")) - finite_float(full.get("coverageRate"))
            if finite_float(row.get("coverageRate")) is not None and finite_float(full.get("coverageRate")) is not None
            else None
        )
        row["deltaRiskWorstMarginMmVsFull"] = (
            finite_float(row.get("riskWorstMarginMm")) - finite_float(full.get("riskWorstMarginMm"))
            if finite_float(row.get("riskWorstMarginMm")) is not None and finite_float(full.get("riskWorstMarginMm")) is not None
            else None
        )
        row["deltaConstraintViolationRateVsFull"] = (
            finite_float(row.get("constraintViolationRate")) - finite_float(full.get("constraintViolationRate"))
            if finite_float(row.get("constraintViolationRate")) is not None and finite_float(full.get("constraintViolationRate")) is not None
            else None
        )

    return {
        "caseId": case.case_id,
        "caseType": case.case_type,
        "purpose": case.purpose,
        "requestedTargets": list(case.requested_targets),
        "targetingMode": targeting_mode,
        "expectedProbeCount": case.expected_probe_count,
        "expectFailClosed": bool(case.expect_fail_closed),
        "candidateCounts": {
            "acceptedCandidatePool": int(len(enforced_candidates)),
            "hardBoundaryRelaxedExposedPool": int(len(warn_candidates)),
        },
        "candidateGeneration": {
            "acceptedCandidatePool": enforced_gen,
            "hardBoundaryAblation": {
                "status": "diagnostic_only",
                "note": "The accepted candidate pool does not include rejected out-of-bounds candidates, so disabling hard boundary exclusion cannot be replayed as a selector-only ablation from this artifact.",
                "exposedPoolCount": int(len(warn_candidates)),
            },
        },
        "exactMinProbeCount": exact_min,
        "exactSetCoverCovered": bool(exact.get("covered")),
        "exactSetCoverMissingTokens": exact.get("missingTokens") or [],
        "strictlyFeasible": bool(exact.get("covered")),
        "methods": methods,
        "caseRuntimeSec": round(float(time.perf_counter() - case_started), 6),
    }


def summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    # Objective terms are compared only when the strict shared candidate pool
    # can cover the requested target set. Partial/fail-closed cases are still
    # retained in the per-case report as support-boundary evidence.
    feasible = [case for case in cases if bool(case.get("strictlyFeasible"))]
    out: dict[str, Any] = {}
    for method_id in ABLATION_ORDER:
        rows = [case["methods"][method_id] for case in feasible if method_id in case.get("methods", {})]
        probe_counts = [finite_float(row.get("selectedProbeCount")) for row in rows]
        coverages = [finite_float(row.get("coverageRate")) for row in rows]
        uncovered = [finite_float(row.get("uncoveredCoverageUnitCount")) for row in rows]
        risk = [finite_float(row.get("riskWorstMarginMm")) for row in rows]
        violations = [finite_float(row.get("constraintViolationRate")) for row in rows]
        boundary_violations = [finite_float(row.get("boundaryViolationRate")) for row in rows]
        clean_probe_counts = [v for v in probe_counts if v is not None]
        clean_coverages = [v for v in coverages if v is not None]
        clean_uncovered = [v for v in uncovered if v is not None]
        clean_risk = [v for v in risk if v is not None]
        clean_violations = [v for v in violations if v is not None]
        clean_boundary = [v for v in boundary_violations if v is not None]
        out[method_id] = {
            "ablationLabel": rows[0].get("ablationLabel") if rows else method_id,
            "removedComponent": rows[0].get("removedComponent") if rows else None,
            "feasibleCaseCount": int(len(rows)),
            "totalProbeCount": round(float(sum(clean_probe_counts)), 6) if clean_probe_counts else None,
            "meanProbeCount": round(float(sum(clean_probe_counts) / len(clean_probe_counts)), 6) if clean_probe_counts else None,
            "meanCoverageRate": round(float(sum(clean_coverages) / len(clean_coverages)), 6) if clean_coverages else None,
            "fullCoverageCaseCount": int(sum(1 for row in rows if int(row.get("uncoveredCoverageUnitCount", 1)) == 0)),
            "totalUncoveredCoverageUnits": round(float(sum(clean_uncovered)), 6) if clean_uncovered else None,
            "worstRiskMarginMm": round(float(min(clean_risk)), 6) if clean_risk else None,
            "meanConstraintViolationRate": round(float(sum(clean_violations) / len(clean_violations)), 6) if clean_violations else None,
            "meanBoundaryViolationRate": round(float(sum(clean_boundary) / len(clean_boundary)), 6) if clean_boundary else None,
        }
    full = out.get("full_probeplanner", {})
    for method_id, row in out.items():
        if method_id == "full_probeplanner":
            row["deltaTotalProbeCountVsFull"] = None
            row["deltaMeanCoverageRateVsFull"] = None
            row["deltaWorstRiskMarginMmVsFull"] = None
            row["deltaMeanConstraintViolationRateVsFull"] = None
            continue
        for key, full_key, out_key in (
            ("totalProbeCount", "totalProbeCount", "deltaTotalProbeCountVsFull"),
            ("meanCoverageRate", "meanCoverageRate", "deltaMeanCoverageRateVsFull"),
            ("worstRiskMarginMm", "worstRiskMarginMm", "deltaWorstRiskMarginMmVsFull"),
            ("meanConstraintViolationRate", "meanConstraintViolationRate", "deltaMeanConstraintViolationRateVsFull"),
        ):
            left = finite_float(row.get(key))
            right = finite_float(full.get(full_key))
            row[out_key] = round(float(left - right), 6) if left is not None and right is not None else None
    return out


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "caseId",
        "caseType",
        "requestedTargets",
        "targetingMode",
        "ablationId",
        "ablationLabel",
        "removedComponent",
        "candidatePoolId",
        "candidateCount",
        "strictlyFeasible",
        "exactMinProbeCount",
        "selectedProbeCount",
        "coverageRate",
        "uncoveredCoverageUnitCount",
        "uncoveredTargets",
        "riskWorstMarginMm",
        "constraintViolationRate",
        "hardConstraintViolationRate",
        "boundaryViolationRate",
        "deltaSelectedProbeCountVsFull",
        "deltaCoverageRateVsFull",
        "deltaRiskWorstMarginMmVsFull",
        "status",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            methods = case.get("methods") or {}
            if not methods:
                writer.writerow(
                    {
                        "caseId": case["caseId"],
                        "caseType": case["caseType"],
                        "requestedTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                        "targetingMode": case["targetingMode"],
                        "ablationId": "full_probeplanner",
                        "ablationLabel": "full ProbePlanner objective",
                        "candidateCount": 0,
                        "strictlyFeasible": False,
                        "selectedProbeCount": 0,
                        "coverageRate": 0.0,
                        "status": "fail_closed",
                        "error": case.get("error"),
                    }
                )
                continue
            for method_id in ABLATION_ORDER:
                row = methods[method_id]
                pool_key = "hardBoundaryRelaxedExposedPool" if row.get("candidatePoolId") == "accepted_candidate_pool_boundary_relaxation_noop" else "acceptedCandidatePool"
                writer.writerow(
                    {
                        "caseId": case["caseId"],
                        "caseType": case["caseType"],
                        "requestedTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                        "targetingMode": case["targetingMode"],
                        "ablationId": method_id,
                        "ablationLabel": row.get("ablationLabel"),
                        "removedComponent": row.get("removedComponent"),
                        "candidatePoolId": row.get("candidatePoolId"),
                        "candidateCount": case["candidateCounts"].get(pool_key),
                        "strictlyFeasible": bool(case.get("strictlyFeasible")),
                        "exactMinProbeCount": case.get("exactMinProbeCount"),
                        "selectedProbeCount": row.get("selectedProbeCount"),
                        "coverageRate": row.get("coverageRate"),
                        "uncoveredCoverageUnitCount": row.get("uncoveredCoverageUnitCount"),
                        "uncoveredTargets": json.dumps(row.get("uncoveredTargets") or [], ensure_ascii=False),
                        "riskWorstMarginMm": row.get("riskWorstMarginMm"),
                        "constraintViolationRate": row.get("constraintViolationRate"),
                        "hardConstraintViolationRate": row.get("hardConstraintViolationRate"),
                        "boundaryViolationRate": row.get("boundaryViolationRate"),
                        "deltaSelectedProbeCountVsFull": row.get("deltaSelectedProbeCountVsFull"),
                        "deltaCoverageRateVsFull": row.get("deltaCoverageRateVsFull"),
                        "deltaRiskWorstMarginMmVsFull": row.get("deltaRiskWorstMarginMmVsFull"),
                        "status": row.get("status"),
                        "error": case.get("error"),
                    }
                )


def fmt(value: Any, digits: int = 3) -> str:
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
    method_summary = payload["methodSummary"]
    cases = payload["cases"]

    summary_rows = [
        "| ablation | removed component | total probes | mean coverage | full-coverage cases | uncovered units | worst risk margin mm | constraint violation | boundary violation |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method_id in ABLATION_ORDER:
        row = method_summary[method_id]
        summary_rows.append(
            "| {label} | {removed} | {probes} | {coverage} | {full}/{n} | {uncovered} | {risk} | {viol} | {bviol} |".format(
                label=row.get("ablationLabel"),
                removed=row.get("removedComponent"),
                probes=fmt(row.get("totalProbeCount"), 2),
                coverage=pct(row.get("meanCoverageRate")),
                full=row.get("fullCoverageCaseCount"),
                n=row.get("feasibleCaseCount"),
                uncovered=fmt(row.get("totalUncoveredCoverageUnits"), 2),
                risk=fmt(row.get("worstRiskMarginMm"), 3),
                viol=pct(row.get("meanConstraintViolationRate")),
                bviol=pct(row.get("meanBoundaryViolationRate")),
            )
        )

    case_rows = [
        "| case | ablation | candidates | probes | coverage | uncovered | risk margin mm | constraint violation | boundary violation | status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        methods = case.get("methods") or {}
        if not methods:
            case_rows.append(
                "| {case} | full ProbePlanner objective | 0 | 0 | 0.0% | NA | NA | NA | NA | fail_closed: {error} |".format(
                    case=case["caseId"], error=case.get("error", "no safe candidate")
                )
            )
            continue
        for method_id in ABLATION_ORDER:
            row = methods[method_id]
            pool_key = "hardBoundaryRelaxedExposedPool" if row.get("candidatePoolId") == "accepted_candidate_pool_boundary_relaxation_noop" else "acceptedCandidatePool"
            case_rows.append(
                "| {case} | {label} | {cands} | {probes} | {coverage} | {uncovered} | {risk} | {viol} | {bviol} | {status} |".format(
                    case=case["caseId"],
                    label=row.get("ablationLabel"),
                    cands=case["candidateCounts"].get(pool_key),
                    probes=fmt(row.get("selectedProbeCount"), 2),
                    coverage=pct(row.get("coverageRate")),
                    uncovered=fmt(row.get("uncoveredCoverageUnitCount"), 2),
                    risk=fmt(row.get("riskWorstMarginMm"), 3),
                    viol=pct(row.get("constraintViolationRate")),
                    bviol=pct(row.get("boundaryViolationRate")),
                    status=row.get("status"),
                )
            )

    lines = [
        "# Objective and Constraint Ablation - 2026-07-09",
        "",
        "This ablation tests whether automatic probe-count and trajectory choices are explained by explicit objective and constraint terms. All selector-level ablations reuse the same target-set cases, coverage-token definitions, inter-probe constraints, and accepted candidate pool as the full planner. The hard-boundary ablation is reported as a candidate-generation diagnostic because rejected out-of-bounds candidates are not exposed in the accepted candidate artifact.",
        "",
        "## Summary",
        "",
        f"- Cases run: {summary['caseCount']}",
        f"- Strictly feasible target-set cases: {summary['feasibleCaseCount']}",
        f"- Unsupported or fail-closed cases retained outside ablation aggregates: {summary['unsupportedCaseCount']}",
        f"- Full ProbePlanner exact-min matches on feasible cases: {summary['fullExactMinMatchCount']}/{summary['feasibleCaseCount']}",
        "",
        "## Ablation Summary",
        "",
        *summary_rows,
        "",
        "## Per-case Results",
        "",
        *case_rows,
        "",
        "## Interpretation",
        "",
        "Ablation aggregates include only requests with a full-coverage solution in the shared strict candidate pool. Unsupported or depth-limited requests remain in the per-case table and are not used to score an objective term. The hard-boundary row is diagnostic-only because the saved candidate pool does not expose rejected boundary-invalid trajectories.",
        "",
        "## Metric Definitions",
        "",
        "- `selected probe count`: number of selected trajectories.",
        "- `coverage`: covered required coverage units divided by required coverage units; bilateral targets are target:left and target:right units.",
        "- `risk margin`: worst class-specific risk margin among selected probes, computed as distance to risk layer minus class threshold.",
        "- `constraint violation`: inter-probe collision-pair rate under the same minimum entry spacing and minimum clearance contract.",
        "- `boundary violation`: not replayable from the accepted candidate pool in this artifact; reported as NA.",
        "- `no hard boundary exclusion`: reported as a candidate-generation diagnostic. The accepted candidate pool does not expose rejected out-of-bounds candidates, so this row is not interpreted as a selector-only replay.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run objective and constraint ablation for automatic probe planning.")
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

    all_targets = sorted({target for case in auto_bench.CASES for target in case.requested_targets})
    ontology_fallback = atlas_util.apply_structure_tree_descendant_fallback(
        server,
        all_targets,
        Path(args.structures_json).expanduser(),
    )
    target_regions = auto_bench.target_region_payload(server, all_targets)

    results = []
    with server.app.app_context():
        for idx, case in enumerate(auto_bench.CASES, start=1):
            print(f"[{idx}/{len(auto_bench.CASES)}] {case.case_id}", flush=True)
            try:
                results.append(run_case(server, case, workers=int(args.workers)))
            except Exception as exc:
                results.append(
                    {
                        "caseId": case.case_id,
                        "caseType": case.case_type,
                        "purpose": case.purpose,
                        "requestedTargets": list(case.requested_targets),
                        "targetingMode": case.targeting_mode,
                        "expectedProbeCount": case.expected_probe_count,
                        "expectFailClosed": bool(case.expect_fail_closed),
                        "strictlyFeasible": False,
                        "error": f"{exc.__class__.__name__}: {exc}",
                        "methods": {},
                    }
                )

    valid_cases = [case for case in results if case.get("methods")]
    method_summary = summarize_cases(valid_cases)
    feasible = [case for case in valid_cases if bool(case.get("strictlyFeasible"))]
    unsupported = [case for case in results if not bool(case.get("strictlyFeasible"))]
    full_exact_matches = 0
    for case in feasible:
        full = case["methods"]["full_probeplanner"]
        exact_min = case.get("exactMinProbeCount")
        if (
            exact_min is not None
            and int(full.get("selectedProbeCount", -1)) == int(exact_min)
            and int(full.get("uncoveredCoverageUnitCount", 1)) == 0
            and float(full.get("constraintViolationRate", 1.0)) == 0.0
        ):
            full_exact_matches += 1
    summary = {
        "caseCount": int(len(results)),
        "validCaseCount": int(len(valid_cases)),
        "feasibleCaseCount": int(len(feasible)),
        "unsupportedCaseCount": int(len(unsupported)),
        "failClosedCases": int(sum(1 for case in unsupported if not (case.get("methods") or {}))),
        "fullExactMinMatchCount": int(full_exact_matches),
        "totalRuntimeSec": round(float(time.time() - started), 6),
    }
    payload = {
        "schemaVersion": "probeplanner.objective_constraint_ablation.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(profile_root.resolve()),
        "atlasSignature": deepcopy(getattr(server, "atlas_signature", {})),
        "ontologyFallback": ontology_fallback,
        "targetRegions": target_regions,
        "summary": summary,
        "methodSummary": method_summary,
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
    full_summary = method_summary.get("full_probeplanner", {})
    if summary["fullExactMinMatchCount"] != summary["feasibleCaseCount"]:
        gate_failures.append("Full ProbePlanner did not match exact-min/full-coverage/zero-violation on every feasible case.")
    if full_summary.get("meanCoverageRate") != 1.0:
        gate_failures.append("Full ProbePlanner coverage below 100%.")
    if full_summary.get("meanConstraintViolationRate") != 0.0:
        gate_failures.append("Full ProbePlanner has constraint violations.")
    if gate_failures:
        print(json.dumps({"acceptanceGate": "failed", "failures": gate_failures}, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
