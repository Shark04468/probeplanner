#!/usr/bin/env python3
"""Compare ProbePlanner automatic probe-set selection with simple baselines."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
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


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
DEFAULT_STEM = "2026-07-09-baseline-auto-planning-comparison"


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def mean(values: list[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def token_region(token: str) -> str:
    return token.split(":", 1)[0]


def token_overlap(candidate: dict[str, Any], token: str) -> int:
    overlaps = candidate.get("overlapVoxels") if isinstance(candidate.get("overlapVoxels"), dict) else {}
    return int(overlaps.get(token_region(token), 0) or 0)


def candidate_total_overlap(candidate: dict[str, Any], tokens: set[str]) -> int:
    return int(sum(token_overlap(candidate, token) for token in tokens))


def candidate_rank(candidate: dict[str, Any]) -> int:
    try:
        return int(candidate.get("paretoRank", 10**6))
    except Exception:
        return 10**6


def candidate_score(candidate: dict[str, Any]) -> float:
    return float(candidate.get("explainScore", 0.0) or 0.0)


def candidate_key(candidate: dict[str, Any], index: int) -> tuple[Any, ...]:
    return (
        str(candidate.get("id", "")),
        round(float(candidate.get("ap_mm_corrected", candidate.get("ap_mm", 0.0)) or 0.0), 6),
        round(float(candidate.get("ml_mm_corrected", candidate.get("ml_mm", 0.0)) or 0.0), 6),
        round(float(candidate.get("theta_deg", 0.0) or 0.0), 6),
        round(float(candidate.get("phi_deg", 0.0) or 0.0), 6),
        int(index),
    )


def build_candidate_context(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
) -> tuple[set[str], list[set[str]]]:
    required = server._required_coverage_tokens(target_keys, targeting_mode)
    token_sets = [
        server._candidate_coverage_tokens(candidate, target_keys, targeting_mode)
        for candidate in candidates
    ]
    return required, token_sets


def noncolliding_with_selection(
    server: Any,
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
    min_clearance_um: float,
    min_entry_um: float,
) -> bool:
    return not any(server.trajs_collide(candidate, prior, min_clearance_um, min_entry_um) for prior in selected)


def probeplanner_select(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    result = server.greedy_optimize(target_keys, candidates, min_clearance_um, min_entry_um)
    selected, missing, covered, required = server._augment_selection_for_targeting_mode(
        result.get("selected", []),
        candidates,
        target_keys,
        targeting_mode,
        min_clearance_um,
        min_entry_um,
    )
    return list(selected), {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "missingTokensBeforeMetric": sorted(missing),
        "coveredTokensBeforeMetric": sorted(covered),
        "requiredTokens": sorted(required),
        "backendOptimizer": "greedy_optimize + targeting-mode augmentation",
    }


def one_target_one_probe_select(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del min_clearance_um, min_entry_um
    started = time.perf_counter()
    required, token_sets = build_candidate_context(server, candidates, target_keys, targeting_mode)
    selected: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    missing: list[str] = []
    for token in sorted(required):
        eligible = [
            idx for idx, tokens in enumerate(token_sets)
            if idx not in used_indices and token in tokens
        ]
        if not eligible:
            missing.append(token)
            continue
        best = max(
            eligible,
            key=lambda idx: (
                token_overlap(candidates[idx], token),
                -candidate_rank(candidates[idx]),
                candidate_score(candidates[idx]),
                tuple(-v if isinstance(v, (int, float)) else v for v in candidate_key(candidates[idx], idx)),
            ),
        )
        selected.append(candidates[best])
        used_indices.add(best)
    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "missingTokensBeforeMetric": missing,
        "baselineRule": "choose one independently best covering candidate per required coverage token; inter-probe collisions are not optimized",
    }


def greedy_nearest_grouping_select(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = build_candidate_context(server, candidates, target_keys, targeting_mode)
    missing = set(required)
    selected: list[dict[str, Any]] = []
    used_indices: set[int] = set()

    while missing:
        anchor = sorted(missing)[0]
        eligible = []
        for idx, candidate in enumerate(candidates):
            if idx in used_indices or anchor not in token_sets[idx]:
                continue
            if not noncolliding_with_selection(server, candidate, selected, min_clearance_um, min_entry_um):
                continue
            gain = token_sets[idx] & missing
            if gain:
                eligible.append((idx, gain))
        if not eligible:
            break
        best, best_gain = max(
            eligible,
            key=lambda item: (
                token_overlap(candidates[item[0]], anchor),
                len(item[1]),
                candidate_total_overlap(candidates[item[0]], item[1]),
                -candidate_rank(candidates[item[0]]),
                candidate_score(candidates[item[0]]),
                tuple(-v if isinstance(v, (int, float)) else v for v in candidate_key(candidates[item[0]], item[0])),
            ),
        )
        selected.append(candidates[best])
        used_indices.add(best)
        missing -= best_gain

    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "missingTokensBeforeMetric": sorted(missing),
        "baselineRule": "anchor the first uncovered token, prefer local overlap for that token, and opportunistically keep additional covered tokens",
    }


def random_feasible_select(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = build_candidate_context(server, candidates, target_keys, targeting_mode)
    missing = set(required)
    selected: list[dict[str, Any]] = []
    used_indices: set[int] = set()

    while missing:
        eligible = []
        for idx, candidate in enumerate(candidates):
            if idx in used_indices:
                continue
            gain = token_sets[idx] & missing
            if not gain:
                continue
            if not noncolliding_with_selection(server, candidate, selected, min_clearance_um, min_entry_um):
                continue
            eligible.append((idx, gain))
        if not eligible:
            break
        idx, gain = rng.choice(eligible)
        selected.append(candidates[idx])
        used_indices.add(idx)
        missing -= gain

    return selected, {
        "selectorCoreRuntimeMs": (time.perf_counter() - started) * 1000.0,
        "missingTokensBeforeMetric": sorted(missing),
        "baselineRule": "randomly choose a non-colliding candidate with positive remaining coverage gain until no gain remains",
    }


def collision_pair_count(
    server: Any,
    selected: list[dict[str, Any]],
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[int, int]:
    pair_count = len(selected) * (len(selected) - 1) // 2
    collisions = 0
    for i in range(len(selected)):
        for j in range(i + 1, len(selected)):
            if server.trajs_collide(selected[i], selected[j], min_clearance_um, min_entry_um):
                collisions += 1
    return collisions, pair_count


def evaluate_selection(
    server: Any,
    *,
    method_id: str,
    method_label: str,
    baseline_type: str,
    selected_raw: list[dict[str, Any]],
    selector_meta: dict[str, Any],
    target_keys: list[str],
    targeting_mode: str,
    params: dict[str, Any],
    min_clearance_um: float,
    min_entry_um: float,
    annotate_risk: bool = True,
) -> dict[str, Any]:
    annotate_started = time.perf_counter()
    annotated = atlas_util.annotate_selected_with_risk(server, list(selected_raw), params) if annotate_risk else list(selected_raw)
    annotation_runtime_ms = (time.perf_counter() - annotate_started) * 1000.0
    required_tokens = server._required_coverage_tokens(target_keys, targeting_mode)
    covered_tokens: set[str] = set()
    for probe in annotated:
        covered_tokens |= server._candidate_coverage_tokens(probe, target_keys, targeting_mode)
    missing_tokens = sorted(required_tokens - covered_tokens)
    uncovered_targets, uncovered_sides = auto_bench.summarize_uncovered(targeting_mode, missing_tokens)
    collision_count, pair_count = collision_pair_count(server, annotated, min_clearance_um, min_entry_um)
    risk_values = [
        value for value in (finite_float(probe.get("worstRiskMarginMm")) for probe in annotated)
        if value is not None
    ]
    selected_probe_count = int(len(annotated))
    coverage_rate = len(covered_tokens) / len(required_tokens) if required_tokens else 1.0
    hard_constraint_violation_rate = collision_count / pair_count if pair_count else 0.0
    status = "ok"
    if missing_tokens:
        status = "partial" if selected_probe_count else "fail_closed"
    if collision_count:
        status = "constraint_violation" if not missing_tokens else "partial_with_constraint_violation"
    selector_runtime_ms = float(selector_meta.get("selectorCoreRuntimeMs", 0.0) or 0.0)
    return {
        "methodId": method_id,
        "methodLabel": method_label,
        "baselineType": baseline_type,
        "status": status,
        "selectedProbeCount": selected_probe_count,
        "coverageRate": round(float(coverage_rate), 6),
        "requiredCoverageUnits": int(len(required_tokens)),
        "coveredCoverageUnits": int(len(covered_tokens)),
        "uncoveredCoverageUnitCount": int(len(missing_tokens)),
        "coveredTokens": sorted(covered_tokens),
        "uncoveredTokens": missing_tokens,
        "uncoveredTargets": uncovered_targets,
        "uncoveredSides": uncovered_sides,
        "riskWorstMarginMm": round(float(min(risk_values)), 6) if risk_values else None,
        "hardConstraintViolationRate": round(float(hard_constraint_violation_rate), 6),
        "collisionPairCount": int(collision_count),
        "collisionPairDenominator": int(pair_count),
        "selectorRuntimeMs": round(selector_runtime_ms, 6),
        "annotationRuntimeMs": round(annotation_runtime_ms, 6),
        "runtimeMs": round(selector_runtime_ms + annotation_runtime_ms, 6),
        "selectorMeta": selector_meta,
        "selectedProbes": [
            auto_bench.probe_payload(server, probe, idx)
            for idx, probe in enumerate(annotated, start=1)
        ],
    }


def aggregate_random_seed_results(seed_results: list[dict[str, Any]]) -> dict[str, Any]:
    numeric_keys = [
        "selectedProbeCount",
        "coverageRate",
        "uncoveredCoverageUnitCount",
        "hardConstraintViolationRate",
        "runtimeMs",
    ]
    aggregate: dict[str, Any] = {
        "methodId": "random_feasible",
        "methodLabel": "random feasible candidate selection",
        "baselineType": "random_baseline_mean_of_seeds",
        "seedCount": int(len(seed_results)),
        "seedResults": [
            {
                "seed": row.get("seed"),
                "status": row.get("status"),
                "selectedProbeCount": row.get("selectedProbeCount"),
                "coverageRate": row.get("coverageRate"),
                "uncoveredCoverageUnitCount": row.get("uncoveredCoverageUnitCount"),
                "riskWorstMarginMm": row.get("riskWorstMarginMm"),
                "hardConstraintViolationRate": row.get("hardConstraintViolationRate"),
                "runtimeMs": row.get("runtimeMs"),
            }
            for row in seed_results
        ],
    }
    for key in numeric_keys:
        vals = [float(row[key]) for row in seed_results if finite_float(row.get(key)) is not None]
        aggregate[f"{key}Mean"] = round(float(mean(vals)), 6) if vals else None
        aggregate[f"{key}Median"] = round(float(median(vals)), 6) if vals else None
    risk_vals = [float(row["riskWorstMarginMm"]) for row in seed_results if finite_float(row.get("riskWorstMarginMm")) is not None]
    aggregate["riskWorstMarginMmMean"] = round(float(mean(risk_vals)), 6) if risk_vals else None
    aggregate["riskWorstMarginMmMin"] = round(float(min(risk_vals)), 6) if risk_vals else None
    aggregate["fullCoverageSeedRate"] = round(
        float(sum(1 for row in seed_results if int(row.get("uncoveredCoverageUnitCount", 1)) == 0) / len(seed_results)),
        6,
    ) if seed_results else None
    aggregate["constraintValidSeedRate"] = round(
        float(sum(1 for row in seed_results if float(row.get("hardConstraintViolationRate", 1.0)) == 0.0) / len(seed_results)),
        6,
    ) if seed_results else None
    full_cover_counts = [
        int(row["selectedProbeCount"]) for row in seed_results
        if int(row.get("uncoveredCoverageUnitCount", 1)) == 0
    ]
    aggregate["bestFullCoverageProbeCount"] = int(min(full_cover_counts)) if full_cover_counts else None
    aggregate["selectedProbeCount"] = aggregate["selectedProbeCountMean"]
    aggregate["coverageRate"] = aggregate["coverageRateMean"]
    aggregate["uncoveredCoverageUnitCount"] = aggregate["uncoveredCoverageUnitCountMean"]
    aggregate["hardConstraintViolationRate"] = aggregate["hardConstraintViolationRateMean"]
    aggregate["riskWorstMarginMm"] = aggregate["riskWorstMarginMmMean"]
    aggregate["runtimeMs"] = aggregate["runtimeMsMean"]
    aggregate["status"] = "aggregate"
    return aggregate


def run_case(
    server: Any,
    case: auto_bench.BenchmarkCase,
    *,
    workers: int,
    random_seed_count: int,
    random_seed_base: int,
) -> dict[str, Any]:
    case_started = time.perf_counter()
    request = auto_bench.case_request(case, workers)
    payload = server._normalize_optimize_payload(deepcopy(request))
    target_keys = payload["targetKeys"]
    params = payload["params"]
    probe_params = payload["probeParams"]
    constraint_modes = payload["constraintModes"]
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(probe_params.get("entrySpacingUm", 3500.0))

    generation_started = time.perf_counter()
    pipeline = server._run_optimize_pipeline(request)
    candidates = list(pipeline.get("candidatePreview") or [])
    production_selected = list(pipeline.get("selected") or [])
    candidate_generation_ms = (time.perf_counter() - generation_started) * 1000.0
    candidate_generation = deepcopy(
        ((pipeline.get("computeProfile") or {}).get("candidateGeneration") or {})
    )

    exact = auto_bench.token_exact_set_cover(
        server,
        candidates,
        target_keys,
        params.get("targetingMode", "single_hemisphere"),
        min_clearance_um=min_clearance,
        min_entry_um=min_entry,
        max_k=8,
    )

    method_results: dict[str, Any] = {
        "probeplanner": evaluate_selection(
            server,
            method_id="probeplanner",
            method_label="ProbePlanner automatic planner",
            baseline_type="system",
            selected_raw=production_selected,
            selector_meta={
                "selectorCoreRuntimeMs": float(candidate_generation_ms),
                "backendOptimizer": "_run_optimize_pipeline",
                "strictProductionPipeline": True,
            },
            target_keys=target_keys,
            targeting_mode=params.get("targetingMode", "single_hemisphere"),
            params=params,
            min_clearance_um=min_clearance,
            min_entry_um=min_entry,
        )
    }
    deterministic_methods: list[tuple[str, str, str, Any]] = [
        ("one_target_one_probe", "one-target-one-probe", "baseline", one_target_one_probe_select),
        ("greedy_nearest_grouping", "greedy nearest-target grouping", "baseline", greedy_nearest_grouping_select),
    ]
    for method_id, method_label, baseline_type, selector in deterministic_methods:
        selected_raw, selector_meta = selector(
            server,
            candidates,
            target_keys,
            params.get("targetingMode", "single_hemisphere"),
            min_clearance,
            min_entry,
        )
        method_results[method_id] = evaluate_selection(
            server,
            method_id=method_id,
            method_label=method_label,
            baseline_type=baseline_type,
            selected_raw=selected_raw,
            selector_meta=selector_meta,
            target_keys=target_keys,
            targeting_mode=params.get("targetingMode", "single_hemisphere"),
            params=params,
            min_clearance_um=min_clearance,
            min_entry_um=min_entry,
        )

    random_seed_results = []
    for seed_offset in range(random_seed_count):
        seed = int(random_seed_base + seed_offset)
        rng = random.Random(seed)
        selected_raw, selector_meta = random_feasible_select(
            server,
            candidates,
            target_keys,
            params.get("targetingMode", "single_hemisphere"),
            min_clearance,
            min_entry,
            rng,
        )
        result = evaluate_selection(
            server,
            method_id="random_feasible",
            method_label="random feasible candidate selection",
            baseline_type="baseline_seed",
            selected_raw=selected_raw,
            selector_meta={**selector_meta, "seed": seed},
            target_keys=target_keys,
            targeting_mode=params.get("targetingMode", "single_hemisphere"),
            params=params,
            min_clearance_um=min_clearance,
            min_entry_um=min_entry,
        )
        result["seed"] = seed
        result.pop("selectedProbes", None)
        random_seed_results.append(result)
    method_results["random_feasible"] = aggregate_random_seed_results(random_seed_results)

    probeplanner = method_results["probeplanner"]
    exact_min = exact.get("exactMinProbeCount")
    probeplanner_exact_match = (
        exact_min is not None
        and int(probeplanner.get("uncoveredCoverageUnitCount", 1)) == 0
        and int(probeplanner.get("collisionPairCount", 1)) == 0
        and int(probeplanner.get("selectedProbeCount", -1)) == int(exact_min)
    )

    return {
        "caseId": case.case_id,
        "caseType": case.case_type,
        "purpose": case.purpose,
        "requestedTargets": list(case.requested_targets),
        "targetingMode": params.get("targetingMode", case.targeting_mode),
        "expectedProbeCount": case.expected_probe_count,
        "expectFailClosed": bool(case.expect_fail_closed),
        "candidateCount": int(len(candidates)),
        "candidateGenerationRuntimeMs": round(float(candidate_generation_ms), 6),
        "candidateGeneration": candidate_generation,
        "requiredCoverageUnits": int(probeplanner.get("requiredCoverageUnits", 0)),
        "exactMinProbeCount": exact_min,
        "exactSetCoverCovered": bool(exact.get("covered")),
        "exactSetCoverMissingTokens": exact.get("missingTokens") or [],
        "strictlyFeasible": bool(exact.get("covered")),
        "probeplannerExactMinMatch": bool(probeplanner_exact_match),
        "methods": method_results,
        "caseRuntimeSec": round(float(time.perf_counter() - case_started), 6),
    }


def deterministic_value(row: dict[str, Any], key: str) -> float | None:
    return finite_float(row.get(key))


def summarize_methods(cases: list[dict[str, Any]]) -> dict[str, Any]:
    method_ids = ["probeplanner", "one_target_one_probe", "greedy_nearest_grouping", "random_feasible"]
    # Compare selectors only where the shared strict candidate pool can fully
    # cover the request. Unsupported requests remain in the per-case table.
    feasible_cases = [case for case in cases if bool(case.get("strictlyFeasible"))]
    out: dict[str, Any] = {}
    for method_id in method_ids:
        rows = [case["methods"][method_id] for case in feasible_cases]
        probe_counts = [deterministic_value(row, "selectedProbeCount") for row in rows]
        coverage_rates = [deterministic_value(row, "coverageRate") for row in rows]
        uncovered_units = [deterministic_value(row, "uncoveredCoverageUnitCount") for row in rows]
        violation_rates = [deterministic_value(row, "hardConstraintViolationRate") for row in rows]
        runtime_ms = [deterministic_value(row, "runtimeMs") for row in rows]
        risk_margins = [deterministic_value(row, "riskWorstMarginMm") for row in rows]
        probe_counts_clean = [v for v in probe_counts if v is not None]
        coverage_clean = [v for v in coverage_rates if v is not None]
        uncovered_clean = [v for v in uncovered_units if v is not None]
        violation_clean = [v for v in violation_rates if v is not None]
        runtime_clean = [v for v in runtime_ms if v is not None]
        risk_clean = [v for v in risk_margins if v is not None]
        if method_id == "random_feasible":
            full_coverage_rate = mean([
                float(row.get("fullCoverageSeedRate", 0.0) or 0.0)
                for row in rows
            ])
            constraint_valid_rate = mean([
                float(row.get("constraintValidSeedRate", 0.0) or 0.0)
                for row in rows
            ])
        else:
            full_coverage_rate = mean([
                1.0 if int(row.get("uncoveredCoverageUnitCount", 1)) == 0 else 0.0
                for row in rows
            ])
            constraint_valid_rate = mean([
                1.0 if float(row.get("hardConstraintViolationRate", 1.0) or 0.0) == 0.0 else 0.0
                for row in rows
            ])
        out[method_id] = {
            "methodLabel": rows[0].get("methodLabel") if rows else method_id,
            "feasibleCaseCount": int(len(feasible_cases)),
            "totalProbeCount": round(float(sum(probe_counts_clean)), 6) if probe_counts_clean else None,
            "meanProbeCount": round(float(mean(probe_counts_clean)), 6) if probe_counts_clean else None,
            "meanCoverageRate": round(float(mean(coverage_clean)), 6) if coverage_clean else None,
            "totalUncoveredCoverageUnits": round(float(sum(uncovered_clean)), 6) if uncovered_clean else None,
            "fullCoverageRate": round(float(full_coverage_rate), 6) if full_coverage_rate is not None else None,
            "meanHardConstraintViolationRate": round(float(mean(violation_clean)), 6) if violation_clean else None,
            "constraintValidRate": round(float(constraint_valid_rate), 6) if constraint_valid_rate is not None else None,
            "worstRiskMarginMm": round(float(min(risk_clean)), 6) if risk_clean else None,
            "medianRuntimeMs": round(float(median(runtime_clean)), 6) if runtime_clean else None,
        }
    probeplanner_total = out["probeplanner"]["totalProbeCount"]
    for method_id, row in out.items():
        baseline_total = row.get("totalProbeCount")
        if method_id == "probeplanner" or baseline_total in (None, 0) or probeplanner_total is None:
            row["probeCountReductionVsProbePlanner"] = None
        else:
            row["probeCountReductionVsProbePlanner"] = round(float(baseline_total - probeplanner_total), 6)
    return out


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "caseId",
        "caseType",
        "requestedTargets",
        "targetingMode",
        "methodId",
        "methodLabel",
        "candidateCount",
        "strictlyFeasible",
        "exactMinProbeCount",
        "selectedProbeCount",
        "coverageRate",
        "requiredCoverageUnits",
        "coveredCoverageUnits",
        "uncoveredCoverageUnitCount",
        "uncoveredTargets",
        "riskWorstMarginMm",
        "hardConstraintViolationRate",
        "collisionPairCount",
        "runtimeMs",
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
                        "methodId": "probeplanner",
                        "methodLabel": "ProbePlanner automatic planner",
                        "candidateCount": case.get("candidateCount", 0),
                        "strictlyFeasible": False,
                        "exactMinProbeCount": case.get("exactMinProbeCount"),
                        "selectedProbeCount": 0,
                        "coverageRate": 0.0,
                        "uncoveredTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                        "status": "fail_closed",
                        "error": case.get("error"),
                    }
                )
                continue
            for method in methods.values():
                writer.writerow(
                    {
                        "caseId": case["caseId"],
                        "caseType": case["caseType"],
                        "requestedTargets": json.dumps(case.get("requestedTargets") or [], ensure_ascii=False),
                        "targetingMode": case["targetingMode"],
                        "methodId": method["methodId"],
                        "methodLabel": method["methodLabel"],
                        "candidateCount": case["candidateCount"],
                        "strictlyFeasible": bool(case.get("strictlyFeasible")),
                        "exactMinProbeCount": case.get("exactMinProbeCount"),
                        "selectedProbeCount": method.get("selectedProbeCount"),
                        "coverageRate": method.get("coverageRate"),
                        "requiredCoverageUnits": method.get("requiredCoverageUnits", case.get("requiredCoverageUnits")),
                        "coveredCoverageUnits": method.get("coveredCoverageUnits"),
                        "uncoveredCoverageUnitCount": method.get("uncoveredCoverageUnitCount"),
                        "uncoveredTargets": json.dumps(method.get("uncoveredTargets") or [], ensure_ascii=False),
                        "riskWorstMarginMm": method.get("riskWorstMarginMm"),
                        "hardConstraintViolationRate": method.get("hardConstraintViolationRate"),
                        "collisionPairCount": method.get("collisionPairCount"),
                        "runtimeMs": method.get("runtimeMs"),
                        "status": method.get("status"),
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
    method_order = ["probeplanner", "one_target_one_probe", "greedy_nearest_grouping", "random_feasible"]

    summary_table = [
        "| method | total probes | mean probes/case | mean coverage | full coverage rate | uncovered units | hard violation rate | constraint-valid rate | worst risk margin mm | median runtime ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method_id in method_order:
        row = method_summary[method_id]
        summary_table.append(
            "| {label} | {total} | {mean_probe} | {coverage} | {full} | {uncovered} | {viol} | {valid} | {risk} | {runtime} |".format(
                label=row["methodLabel"],
                total=fmt(row.get("totalProbeCount"), 2),
                mean_probe=fmt(row.get("meanProbeCount"), 2),
                coverage=pct(row.get("meanCoverageRate")),
                full=pct(row.get("fullCoverageRate")),
                uncovered=fmt(row.get("totalUncoveredCoverageUnits"), 2),
                viol=pct(row.get("meanHardConstraintViolationRate")),
                valid=pct(row.get("constraintValidRate")),
                risk=fmt(row.get("worstRiskMarginMm"), 3),
                runtime=fmt(row.get("medianRuntimeMs"), 3),
            )
        )

    case_table = [
        "| case | method | probes | coverage | uncovered units | hard violation rate | risk margin mm | runtime ms | status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        methods = case.get("methods") or {}
        if not methods:
            case_table.append(
                "| {case} | ProbePlanner automatic planner | 0 | 0.0% | NA | NA | NA | NA | fail_closed: {error} |".format(
                    case=case["caseId"], error=case.get("error", "no safe candidate")
                )
            )
            continue
        for method_id in method_order:
            method = methods[method_id]
            case_table.append(
                "| {case} | {method} | {probes} | {coverage} | {uncovered} | {viol} | {risk} | {runtime} | {status} |".format(
                    case=case["caseId"],
                    method=method["methodLabel"],
                    probes=fmt(method.get("selectedProbeCount"), 2),
                    coverage=pct(method.get("coverageRate")),
                    uncovered=fmt(method.get("uncoveredCoverageUnitCount"), 2),
                    viol=pct(method.get("hardConstraintViolationRate")),
                    risk=fmt(method.get("riskWorstMarginMm"), 3),
                    runtime=fmt(method.get("runtimeMs"), 3),
                    status=method.get("status"),
                )
            )

    lines = [
        "# Baseline Comparison for Automatic Probe Planning - 2026-07-09",
        "",
        "This experiment compares ProbePlanner against three simple automatic baselines on the same target-combination cases, candidate pools, coverage-token definitions, and inter-probe collision constraints used in the automatic probe-count benchmark.",
        "",
        "## Summary",
        "",
        f"- Cases run: {summary['caseCount']}",
        f"- Strictly feasible target-set cases: {summary['feasibleCaseCount']}",
        f"- Unsupported or fail-closed cases retained outside selector aggregates: {summary['unsupportedCaseCount']}",
        f"- ProbePlanner exact-min matches on feasible cases: {summary['probeplannerExactMinMatchCount']}/{summary['feasibleCaseCount']}",
        f"- ProbePlanner total probes on feasible cases: {fmt(method_summary['probeplanner']['totalProbeCount'], 2)}",
        f"- one-target-one-probe total probes on feasible cases: {fmt(method_summary['one_target_one_probe']['totalProbeCount'], 2)}",
        f"- greedy nearest-target grouping total probes on feasible cases: {fmt(method_summary['greedy_nearest_grouping']['totalProbeCount'], 2)}",
        f"- random feasible mean total probes on feasible cases: {fmt(method_summary['random_feasible']['totalProbeCount'], 2)}",
        "",
        "## Method Summary",
        "",
        *summary_table,
        "",
        "## Per-case Results",
        "",
        *case_table,
        "",
        "## Interpretation",
        "",
        "Selector aggregates include only requests for which the shared strict candidate pool has a full-coverage solution. Unsupported or depth-limited requests are retained above as partial or fail-closed evidence and are not used to score any selector. On the strictly feasible subset, ProbePlanner is compared with the exact candidate-pool minimum and with the three baselines under identical candidate and hard-constraint contracts.",
        "",
        "## Metric Definitions",
        "",
        "- `probe count`: number of selected trajectories; random feasible reports the mean over deterministic seeds.",
        "- `coverage`: covered required coverage units divided by required coverage units; bilateral targets are counted as target:left and target:right units.",
        "- `uncovered units`: number of uncovered required coverage units.",
        "- `risk margin`: worst class-specific risk margin among selected probes, computed as distance to risk layer minus class threshold.",
        "- `hard violation rate`: pairwise inter-probe collision rate under the same minimum entry spacing and minimum clearance contract; single-candidate boundary/obstacle hard filters are already applied in candidate generation.",
        "- `runtime`: selector plus selected-probe risk annotation time; candidate generation is shared across methods and reported in JSON.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run baseline comparison for automatic probe planning.")
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--structures-json", default=str(atlas_util.DEFAULT_STRUCTURES_JSON))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--random-seeds", type=int, default=20)
    parser.add_argument("--random-seed-base", type=int, default=1729)
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
            case_seed_base = int(args.random_seed_base) + idx * 1000
            try:
                result = run_case(
                    server,
                    case,
                    workers=int(args.workers),
                    random_seed_count=int(args.random_seeds),
                    random_seed_base=case_seed_base,
                )
            except Exception as exc:
                result = {
                    "caseId": case.case_id,
                    "caseType": case.case_type,
                    "purpose": case.purpose,
                    "requestedTargets": list(case.requested_targets),
                    "targetingMode": case.targeting_mode,
                    "expectedProbeCount": case.expected_probe_count,
                    "expectFailClosed": bool(case.expect_fail_closed),
                    "candidateCount": 0,
                    "requiredCoverageUnits": 0,
                    "exactMinProbeCount": None,
                    "exactSetCoverCovered": False,
                    "strictlyFeasible": False,
                    "probeplannerExactMinMatch": False,
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "methods": {},
                }
            results.append(result)

    method_summary = summarize_methods([case for case in results if case.get("methods")])
    feasible_cases = [case for case in results if bool(case.get("strictlyFeasible"))]
    unsupported_cases = [case for case in results if not bool(case.get("strictlyFeasible"))]
    summary = {
        "caseCount": int(len(results)),
        "feasibleCaseCount": int(len(feasible_cases)),
        "unsupportedCaseCount": int(len(unsupported_cases)),
        "failClosedCases": int(sum(1 for case in unsupported_cases if not (case.get("methods") or {}))),
        "probeplannerExactMinMatchCount": int(sum(1 for case in feasible_cases if case.get("probeplannerExactMinMatch"))),
        "randomSeedCount": int(args.random_seeds),
        "totalRuntimeSec": round(float(time.time() - started), 6),
    }
    payload = {
        "schemaVersion": "probeplanner.baseline_auto_planning_comparison.v1",
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
    probeplanner_summary = method_summary.get("probeplanner", {})
    if summary["probeplannerExactMinMatchCount"] != summary["feasibleCaseCount"]:
        gate_failures.append("ProbePlanner did not match the exact candidate-pool minimum on every feasible case.")
    if probeplanner_summary.get("meanCoverageRate") != 1.0:
        gate_failures.append("ProbePlanner mean feasible-case coverage was below 100%.")
    if probeplanner_summary.get("constraintValidRate") != 1.0:
        gate_failures.append("ProbePlanner had at least one hard-constraint-invalid feasible case.")
    if probeplanner_summary.get("totalUncoveredCoverageUnits") not in (0, 0.0):
        gate_failures.append("ProbePlanner left feasible-case coverage units uncovered.")
    if gate_failures:
        print(json.dumps({"acceptanceGate": "failed", "failures": gate_failures}, indent=2), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
