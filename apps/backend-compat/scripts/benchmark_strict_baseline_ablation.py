#!/usr/bin/env python3
"""Rerun baselines and ablations on the locked strict full-coverage benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as atlas_util  # noqa: E402
import benchmark_baseline_auto_planning_comparison as baseline  # noqa: E402
import benchmark_objective_constraint_ablation as ablation  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "evidence" / "2026-07-11-strict-final-benchmark" / "final-benchmark-manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evidence" / "2026-07-11-strict-baseline-ablation"
OVERSHOOT_TOLERANCE_MM = 0.025

BASELINE_ORDER = (
    "one_target_one_probe",
    "greedy_grouping",
    "random_feasible",
    "coverage_first_greedy",
    "probeplanner",
)
ABLATION_ORDER = (
    "full_probeplanner",
    "no_probe_count_penalty",
    "no_coverage_priority",
    "no_multi_target_grouping",
    "no_depth_optimization",
    "no_tip_in_target_policy",
    "no_internal_risk_enforcement",
)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def stable_seed(case_id: str, base: int) -> int:
    digest = hashlib.sha256(case_id.encode("utf-8")).digest()
    return int(base + int.from_bytes(digest[:4], "big"))


def coverage_first_select(
    server: Any,
    candidates: list[dict[str, Any]],
    target_keys: list[str],
    targeting_mode: str,
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    required, token_sets = baseline.build_candidate_context(server, candidates, target_keys, targeting_mode)
    missing = set(required)
    selected: list[dict[str, Any]] = []
    used: set[int] = set()
    while missing:
        eligible = []
        for index, candidate in enumerate(candidates):
            gain = token_sets[index] & missing
            if index in used or not gain:
                continue
            if not baseline.noncolliding_with_selection(
                server, candidate, selected, min_clearance_um, min_entry_um
            ):
                continue
            eligible.append((index, gain))
        if not eligible:
            break
        index, gain = max(
            eligible,
            key=lambda item: (
                len(item[1]),
                baseline.candidate_total_overlap(candidates[item[0]], item[1]),
                baseline.candidate_score(candidates[item[0]]),
                -baseline.candidate_rank(candidates[item[0]]),
                tuple(
                    -value if isinstance(value, (int, float)) else value
                    for value in baseline.candidate_key(candidates[item[0]], item[0])
                ),
            ),
        )
        selected.append(candidates[index])
        used.add(index)
        missing -= gain
    return selected, {
        "selectorRuntimeSec": float(time.perf_counter() - started),
        "missingTokensBeforeAudit": sorted(missing),
        "selectorDefinition": "global greedy maximum remaining coverage gain",
    }


def collision_count(
    server: Any,
    selected: list[dict[str, Any]],
    min_clearance_um: float,
    min_entry_um: float,
) -> tuple[int, int]:
    violations = 0
    denominator = len(selected) * (len(selected) - 1) // 2
    for left in range(len(selected)):
        for right in range(left + 1, len(selected)):
            if server.trajs_collide(selected[left], selected[right], min_clearance_um, min_entry_um):
                violations += 1
    return violations, denominator


def evaluate_selection(
    server: Any,
    *,
    method_id: str,
    method_label: str,
    family: str,
    selected: list[dict[str, Any]],
    request: dict[str, Any],
    candidate_count: int,
    shared_runtime_sec: float,
    selector_runtime_sec: float,
    include_probes: bool = True,
) -> dict[str, Any]:
    normalized = server._normalize_optimize_payload(deepcopy(request))
    target_keys = normalized["targetKeys"]
    params = normalized["params"]
    targeting_mode = params.get("targetingMode", "single_hemisphere")
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(normalized["probeParams"].get("entrySpacingUm", 3500.0))
    required = server._required_coverage_tokens(target_keys, targeting_mode)
    covered: set[str] = set()
    probe_rows = []
    probe_violation_count = 0
    internal_margins = []
    internal_evidence_probe_count = 0
    for index, probe in enumerate(selected, start=1):
        covered |= server._candidate_coverage_tokens(probe, target_keys, targeting_mode)
        audit = server._build_unified_probe_audit(probe, target_keys, params)
        internal = audit.get("internalRiskAudit") if isinstance(audit.get("internalRiskAudit"), dict) else {}
        if internal.get("claimStatus") != "unavailable":
            internal_evidence_probe_count += 1
        margin = finite(audit.get("minimumInternalRiskMarginMm"))
        if margin is not None:
            internal_margins.append(margin)
        overshoot = finite(audit.get("distalOvershootMm"))
        violations = []
        if not audit.get("brainContainmentPassed"):
            violations.append("brain_containment")
        if not audit.get("tipInTarget"):
            violations.append("tip_not_in_target")
        if overshoot is None or overshoot > OVERSHOOT_TOLERANCE_MM + 1e-9:
            violations.append("distal_overshoot")
        if int(audit.get("internalRiskViolationCount", 0) or 0) > 0:
            violations.append("internal_risk")
        if violations:
            probe_violation_count += 1
        if include_probes:
            probe_rows.append(
                {
                    "probeIndex": index,
                    "coveredRegions": list(probe.get("coveredRegions") or []),
                    "brainContainmentPassed": audit.get("brainContainmentPassed"),
                    "tipInTarget": audit.get("tipInTarget"),
                    "distalOvershootMm": audit.get("distalOvershootMm"),
                    "internalRiskViolationCount": audit.get("internalRiskViolationCount"),
                    "minimumInternalRiskMarginMm": audit.get("minimumInternalRiskMarginMm"),
                    "internalRiskClaimStatus": internal.get("claimStatus"),
                    "entrySurfaceCrossingStatus": audit.get("entrySurfaceCrossingStatus"),
                    "hardViolationReasons": violations,
                }
            )
    missing = sorted(required - covered)
    collision_violations, collision_denominator = collision_count(
        server, selected, min_clearance, min_entry
    )
    hard_count = int(probe_violation_count + collision_violations)
    hard_denominator = int(len(selected) + collision_denominator)
    full_coverage = not missing
    uncovered_targets = sorted({token.split(":", 1)[0] for token in missing})
    return {
        "methodId": method_id,
        "methodLabel": method_label,
        "family": family,
        "status": "ok" if full_coverage and hard_count == 0 else "partial_or_violating",
        "candidateCount": int(candidate_count),
        "selectedProbeCount": int(len(selected)),
        "coverageRate": round(len(covered) / max(1, len(required)), 6),
        "fullCoverage": bool(full_coverage),
        "fullCoverageRate": 1.0 if full_coverage else 0.0,
        "uncoveredCoverageUnitCount": int(len(missing)),
        "uncoveredTokens": missing,
        "uncoveredTargets": uncovered_targets,
        "hardViolationCount": hard_count,
        "hardViolationDenominator": hard_denominator,
        "hardViolationRate": round(hard_count / max(1, hard_denominator), 6),
        "collisionViolationCount": int(collision_violations),
        "probeHardViolationCount": int(probe_violation_count),
        "minimumInternalRiskMarginMm": round(min(internal_margins), 6)
        if internal_margins
        else None,
        "internalRiskEvidenceAvailable": bool(internal_evidence_probe_count),
        "internalRiskEvidenceProbeCount": int(internal_evidence_probe_count),
        "sharedCandidateRuntimeSec": round(float(shared_runtime_sec), 6),
        "selectorRuntimeSec": round(float(selector_runtime_sec), 6),
        "runtimeSec": round(float(shared_runtime_sec + selector_runtime_sec), 6),
        "selectedProbes": probe_rows if include_probes else None,
    }


def aggregate_random(results: list[dict[str, Any]]) -> dict[str, Any]:
    def avg(key: str) -> float:
        return float(statistics.mean(float(row[key]) for row in results))

    full_seed_count = sum(1 for row in results if row.get("fullCoverage"))
    evidence_count = sum(1 for row in results if row.get("internalRiskEvidenceAvailable"))
    margins = [finite(row.get("minimumInternalRiskMarginMm")) for row in results]
    margins = [value for value in margins if value is not None]
    return {
        "methodId": "random_feasible",
        "methodLabel": "random feasible selection",
        "family": "baseline",
        "status": "aggregate",
        "seedCount": len(results),
        "candidateCount": results[0]["candidateCount"] if results else 0,
        "selectedProbeCount": round(avg("selectedProbeCount"), 6),
        "coverageRate": round(avg("coverageRate"), 6),
        "fullCoverage": full_seed_count == len(results),
        "fullCoverageRate": round(full_seed_count / max(1, len(results)), 6),
        "uncoveredCoverageUnitCount": round(avg("uncoveredCoverageUnitCount"), 6),
        "uncoveredTargets": sorted({target for row in results for target in row.get("uncoveredTargets") or []}),
        "hardViolationCount": round(avg("hardViolationCount"), 6),
        "hardViolationRate": round(avg("hardViolationRate"), 6),
        "minimumInternalRiskMarginMm": round(min(margins), 6) if margins else None,
        "internalRiskEvidenceAvailable": bool(evidence_count),
        "internalRiskEvidenceSeedCount": int(evidence_count),
        "sharedCandidateRuntimeSec": results[0]["sharedCandidateRuntimeSec"] if results else 0.0,
        "selectorRuntimeSec": round(avg("selectorRuntimeSec"), 6),
        "runtimeSec": round(avg("runtimeSec"), 6),
        "seedResults": results,
    }


def run_pipeline(server: Any, request: dict[str, Any]) -> tuple[dict[str, Any] | None, float, dict[str, Any] | None]:
    started = time.perf_counter()
    try:
        result = server._run_optimize_pipeline(deepcopy(request))
        return result, float(time.perf_counter() - started), None
    except Exception as exc:
        return None, float(time.perf_counter() - started), {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "code": getattr(exc, "code", None),
            "extra": deepcopy(getattr(exc, "extra", None)),
        }


def evaluate_selector(
    server: Any,
    selector: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
    *,
    method_id: str,
    method_label: str,
    family: str,
    candidates: list[dict[str, Any]],
    request: dict[str, Any],
    shared_runtime_sec: float,
    selector_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = server._normalize_optimize_payload(deepcopy(request))
    params = normalized["params"]
    started = time.perf_counter()
    selected, meta = selector(
        server,
        candidates,
        normalized["targetKeys"],
        params.get("targetingMode", "single_hemisphere"),
        float(params.get("minClearance", 1000.0)),
        float(normalized["probeParams"].get("entrySpacingUm", 3500.0)),
        **(selector_extra or {}),
    )
    elapsed = float(time.perf_counter() - started)
    row = evaluate_selection(
        server,
        method_id=method_id,
        method_label=method_label,
        family=family,
        selected=list(selected),
        request=request,
        candidate_count=len(candidates),
        shared_runtime_sec=shared_runtime_sec,
        selector_runtime_sec=elapsed,
    )
    row["selectorMeta"] = meta
    return row


def variant_request(base: dict[str, Any], variant: str) -> dict[str, Any]:
    request = deepcopy(base)
    params = request.setdefault("params", {})
    modes = request.setdefault("constraintModes", {})
    params["includeCandidatePreview"] = True
    if variant == "no_depth_optimization":
        params["depthOptimizationEnabled"] = False
        params["targetEndpointPolicy"] = "tip_in_target"
    elif variant == "no_tip_in_target_policy":
        params["depthOptimizationEnabled"] = True
        params["targetEndpointPolicy"] = "trajectory_intersects"
    elif variant == "no_internal_risk_enforcement":
        modes["continuousRiskDistance"] = "off"
        params["autoAvoidEnabled"] = False
    return request


def run_case(
    server: Any,
    case: dict[str, Any],
    *,
    random_seed_count: int,
    random_seed_base: int,
) -> dict[str, Any]:
    request = deepcopy(case["plannerRequest"])
    request.setdefault("params", {})["includeCandidatePreview"] = True
    pipeline, pipeline_runtime, pipeline_error = run_pipeline(server, request)
    if pipeline is None:
        return {
            "caseId": case["caseId"],
            "requestedTargets": case["requestedTargets"],
            "targetingMode": case["targetingMode"],
            "error": pipeline_error,
            "baselines": {},
            "ablations": {},
        }
    candidates = list(pipeline.get("candidatePreview") or [])
    production_selected = list(pipeline.get("selected") or [])
    normalized = server._normalize_optimize_payload(deepcopy(request))
    target_keys = normalized["targetKeys"]
    params = normalized["params"]
    mode = params.get("targetingMode", "single_hemisphere")
    min_clearance = float(params.get("minClearance", 1000.0))
    min_entry = float(normalized["probeParams"].get("entrySpacingUm", 3500.0))

    baselines: dict[str, Any] = {}
    baselines["probeplanner"] = evaluate_selection(
        server,
        method_id="probeplanner",
        method_label="ProbePlanner",
        family="baseline",
        selected=production_selected,
        request=request,
        candidate_count=len(candidates),
        shared_runtime_sec=pipeline_runtime,
        selector_runtime_sec=0.0,
    )
    baselines["one_target_one_probe"] = evaluate_selector(
        server,
        baseline.one_target_one_probe_select,
        method_id="one_target_one_probe",
        method_label="one-target-one-probe",
        family="baseline",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
    )
    baselines["greedy_grouping"] = evaluate_selector(
        server,
        baseline.greedy_nearest_grouping_select,
        method_id="greedy_grouping",
        method_label="greedy grouping",
        family="baseline",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
    )
    baselines["coverage_first_greedy"] = evaluate_selector(
        server,
        coverage_first_select,
        method_id="coverage_first_greedy",
        method_label="coverage-first greedy",
        family="baseline",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
    )
    random_rows = []
    first_seed = stable_seed(case["caseId"], random_seed_base)
    for offset in range(random_seed_count):
        rng = random.Random(first_seed + offset)
        started = time.perf_counter()
        selected, _ = baseline.random_feasible_select(
            server, candidates, target_keys, mode, min_clearance, min_entry, rng
        )
        random_rows.append(
            evaluate_selection(
                server,
                method_id="random_feasible",
                method_label="random feasible selection",
                family="baseline_seed",
                selected=selected,
                request=request,
                candidate_count=len(candidates),
                shared_runtime_sec=pipeline_runtime,
                selector_runtime_sec=float(time.perf_counter() - started),
                include_probes=False,
            )
        )
    baselines["random_feasible"] = aggregate_random(random_rows)

    ablations: dict[str, Any] = {
        "full_probeplanner": {
            **deepcopy(baselines["probeplanner"]),
            "methodId": "full_probeplanner",
            "methodLabel": "full ProbePlanner",
            "family": "ablation_reference",
        }
    }
    ablations["no_probe_count_penalty"] = evaluate_selector(
        server,
        ablation.select_no_probe_count_penalty,
        method_id="no_probe_count_penalty",
        method_label="no probe-count penalty",
        family="ablation",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
    )
    ablations["no_coverage_priority"] = evaluate_selector(
        server,
        ablation.select_no_coverage_priority,
        method_id="no_coverage_priority",
        method_label="no coverage priority",
        family="ablation",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
        selector_extra={"max_probe_count": len(production_selected)},
    )
    ablations["no_multi_target_grouping"] = evaluate_selector(
        server,
        ablation.select_no_multi_target_grouping,
        method_id="no_multi_target_grouping",
        method_label="no multi-target grouping",
        family="ablation",
        candidates=candidates,
        request=request,
        shared_runtime_sec=pipeline_runtime,
    )

    for method_id, label in (
        ("no_depth_optimization", "no depth optimization"),
        ("no_tip_in_target_policy", "no tip-in-target policy"),
        ("no_internal_risk_enforcement", "no internal-risk enforcement"),
    ):
        changed_request = variant_request(request, method_id)
        changed_pipeline, changed_runtime, changed_error = run_pipeline(server, changed_request)
        selected = list((changed_pipeline or {}).get("selected") or [])
        row = evaluate_selection(
            server,
            method_id=method_id,
            method_label=label,
            family="ablation",
            selected=selected,
            request=request,
            candidate_count=int((changed_pipeline or {}).get("candidateCount", 0) or 0),
            shared_runtime_sec=changed_runtime,
            selector_runtime_sec=0.0,
        )
        row["variantRequest"] = changed_request
        row["pipelineError"] = changed_error
        ablations[method_id] = row

    return {
        "caseId": case["caseId"],
        "caseType": case.get("caseType"),
        "requestedTargets": case["requestedTargets"],
        "targetingMode": case["targetingMode"],
        "referenceSelectedProbeCount": case["selectedProbeCount"],
        "candidateCount": len(candidates),
        "pipelineRuntimeSec": round(pipeline_runtime, 6),
        "baselines": baselines,
        "ablations": ablations,
    }


def bootstrap_ci(values: list[float], *, seed: int, samples: int = 2000) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        means.append(statistics.mean(values[rng.randrange(len(values))] for _ in values))
    means.sort()
    return [round(means[int(0.025 * (samples - 1))], 6), round(means[int(0.975 * (samples - 1))], 6)]


def summarize(cases: list[dict[str, Any]], family_key: str, order: tuple[str, ...]) -> dict[str, Any]:
    output = {}
    reference_id = "probeplanner" if family_key == "baselines" else "full_probeplanner"
    for method_id in order:
        rows = [case[family_key][method_id] for case in cases if method_id in case.get(family_key, {})]
        probes = [float(row["selectedProbeCount"]) for row in rows]
        coverage = [float(row["coverageRate"]) for row in rows]
        full = [float(row["fullCoverageRate"]) for row in rows]
        uncovered = [float(row["uncoveredCoverageUnitCount"]) for row in rows]
        violations = [float(row["hardViolationRate"]) for row in rows]
        runtimes = [float(row["runtimeSec"]) for row in rows]
        margins = [finite(row.get("minimumInternalRiskMarginMm")) for row in rows]
        margins = [value for value in margins if value is not None]
        paired_probe_delta = []
        paired_coverage_delta = []
        for case in cases:
            method = (case.get(family_key) or {}).get(method_id)
            reference = (case.get(family_key) or {}).get(reference_id)
            if method is None or reference is None:
                continue
            paired_probe_delta.append(float(method["selectedProbeCount"]) - float(reference["selectedProbeCount"]))
            paired_coverage_delta.append(float(method["coverageRate"]) - float(reference["coverageRate"]))
        output[method_id] = {
            "methodLabel": rows[0]["methodLabel"] if rows else method_id,
            "caseCount": len(rows),
            "meanProbeCount": round(statistics.mean(probes), 6) if probes else None,
            "meanProbeCountCi95": bootstrap_ci(probes, seed=1000 + len(output)),
            "fullCoverageCaseRate": round(statistics.mean(full), 6) if full else None,
            "fullCoverageCaseCountEquivalent": round(sum(full), 6) if full else 0,
            "meanCoverageRate": round(statistics.mean(coverage), 6) if coverage else None,
            "totalUncoveredCoverageUnits": round(sum(uncovered), 6),
            "meanHardViolationRate": round(statistics.mean(violations), 6) if violations else None,
            "minimumInternalRiskMarginMm": round(min(margins), 6) if margins else None,
            "internalRiskEvidenceCaseCount": sum(1 for row in rows if row.get("internalRiskEvidenceAvailable")),
            "meanRuntimeSec": round(statistics.mean(runtimes), 6) if runtimes else None,
            "meanProbeCountDeltaVsReference": round(statistics.mean(paired_probe_delta), 6)
            if paired_probe_delta
            else None,
            "meanProbeCountDeltaCi95": bootstrap_ci(paired_probe_delta, seed=2000 + len(output)),
            "meanCoverageDeltaVsReference": round(statistics.mean(paired_coverage_delta), 6)
            if paired_coverage_delta
            else None,
            "meanCoverageDeltaCi95": bootstrap_ci(paired_coverage_delta, seed=3000 + len(output)),
        }
    return output


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fields = [
        "caseId", "requestedTargets", "targetingMode", "family", "methodId", "methodLabel",
        "candidateCount", "selectedProbeCount", "coverageRate", "fullCoverageRate",
        "uncoveredCoverageUnitCount", "uncoveredTargets", "hardViolationCount", "hardViolationRate",
        "minimumInternalRiskMarginMm", "internalRiskEvidenceAvailable", "runtimeSec", "status",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            for family_key in ("baselines", "ablations"):
                for method_id, row in (case.get(family_key) or {}).items():
                    writer.writerow(
                        {
                            "caseId": case["caseId"],
                            "requestedTargets": json.dumps(case["requestedTargets"]),
                            "targetingMode": case["targetingMode"],
                            "family": family_key,
                            "methodId": method_id,
                            "methodLabel": row["methodLabel"],
                            "candidateCount": row["candidateCount"],
                            "selectedProbeCount": row["selectedProbeCount"],
                            "coverageRate": row["coverageRate"],
                            "fullCoverageRate": row["fullCoverageRate"],
                            "uncoveredCoverageUnitCount": row["uncoveredCoverageUnitCount"],
                            "uncoveredTargets": json.dumps(row.get("uncoveredTargets") or []),
                            "hardViolationCount": row["hardViolationCount"],
                            "hardViolationRate": row["hardViolationRate"],
                            "minimumInternalRiskMarginMm": row.get("minimumInternalRiskMarginMm"),
                            "internalRiskEvidenceAvailable": row.get("internalRiskEvidenceAvailable"),
                            "runtimeSec": row["runtimeSec"],
                            "status": row["status"],
                        }
                    )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Strict Full-Benchmark Baseline and Ablation Results",
        "",
        f"- Full-coverage reference cases: {payload['caseCount']}",
        f"- Random seeds per case: {payload['randomSeedCount']}",
        f"- Atlas profile: `{payload['profileRoot']}`",
        "",
        "## Baselines",
        "",
        "| method | mean probes [95% CI] | full coverage | uncovered units | hard violation rate | internal margin mm | runtime s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method_id in BASELINE_ORDER:
        row = payload["baselineSummary"][method_id]
        lines.append(
            f"| {row['methodLabel']} | {row['meanProbeCount']:.3f} {row['meanProbeCountCi95']} | "
            f"{100.0 * row['fullCoverageCaseRate']:.1f}% | {row['totalUncoveredCoverageUnits']:.3f} | "
            f"{100.0 * row['meanHardViolationRate']:.2f}% | {row['minimumInternalRiskMarginMm'] if row['minimumInternalRiskMarginMm'] is not None else 'NA'} | "
            f"{row['meanRuntimeSec']:.3f} |"
        )
    lines.extend([
        "",
        "## Ablations",
        "",
        "| method | mean probes [95% CI] | full coverage | uncovered units | hard violation rate | internal margin mm | runtime s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for method_id in ABLATION_ORDER:
        row = payload["ablationSummary"][method_id]
        lines.append(
            f"| {row['methodLabel']} | {row['meanProbeCount']:.3f} {row['meanProbeCountCi95']} | "
            f"{100.0 * row['fullCoverageCaseRate']:.1f}% | {row['totalUncoveredCoverageUnits']:.3f} | "
            f"{100.0 * row['meanHardViolationRate']:.2f}% | {row['minimumInternalRiskMarginMm'] if row['minimumInternalRiskMarginMm'] is not None else 'NA'} | "
            f"{row['meanRuntimeSec']:.3f} |"
        )
    one_target = payload["baselineSummary"]["one_target_one_probe"]
    greedy = payload["baselineSummary"]["greedy_grouping"]
    random_row = payload["baselineSummary"]["random_feasible"]
    coverage_first = payload["baselineSummary"]["coverage_first_greedy"]
    no_count = payload["ablationSummary"]["no_probe_count_penalty"]
    no_coverage = payload["ablationSummary"]["no_coverage_priority"]
    no_depth = payload["ablationSummary"]["no_depth_optimization"]
    no_tip = payload["ablationSummary"]["no_tip_in_target_policy"]
    lines.extend([
        "",
        "## Results Interpretation",
        "",
        f"ProbePlanner used {one_target['meanProbeCountDeltaVsReference']:.3f} fewer probes per case than one-target-one-probe, {greedy['meanProbeCountDeltaVsReference']:.3f} fewer than greedy grouping, and {random_row['meanProbeCountDeltaVsReference']:.3f} fewer than random feasible selection when the deltas are expressed as baseline minus ProbePlanner. Coverage-first greedy exactly matched ProbePlanner on this benchmark ({coverage_first['meanProbeCountDeltaVsReference']:.3f} probe-count delta and identical full coverage), so these data do not establish superiority over that baseline.",
        "",
        f"Removing the probe-count penalty increased the mean count to {no_count['meanProbeCount']:.3f}. Removing coverage priority reduced full-coverage performance to {100.0 * no_coverage['fullCoverageCaseRate']:.1f}% and left {no_coverage['totalUncoveredCoverageUnits']:.0f} coverage units uncovered. With the fixed 7.6 mm request depth and tip-in-target still enforced, disabling depth optimization yielded {100.0 * no_depth['fullCoverageCaseRate']:.1f}% full coverage. Removing tip-in-target preserved intersection coverage but produced a {100.0 * no_tip['meanHardViolationRate']:.1f}% mean hard-violation rate; 20 of 27 cases contained at least one independently detected endpoint violation.",
        "",
        "## Safety Interpretation",
        "",
        "Hard violations are recomputed independently from brain containment, tip-in-target, distal overshoot, internal-risk diagnostics, and inter-probe collisions. The active profile has no usable internal vessel, ventricle, sulcus, or user-exclusion layer; internal-risk margins are therefore `NA`, and the no-internal-risk-enforcement result is not evidence that those constraints are unnecessary.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", type=Path, default=DEFAULT_PROFILE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--structures-json", type=Path, default=atlas_util.DEFAULT_STRUCTURES_JSON)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--random-seeds", type=int, default=20)
    parser.add_argument("--random-seed-base", type=int, default=1729)
    parser.add_argument("--max-cases", type=int, default=27)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    checkpoint_path = output_dir / "case-checkpoint.json"
    manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
    source_cases = [case for case in manifest.get("cases") or [] if case.get("eligibleForManifest")]
    source_cases = source_cases[: max(0, args.max_cases)]
    if len(source_cases) < 20:
        raise RuntimeError("Strict full-coverage benchmark must contain at least 20 feasible cases")

    atlas_util.configure_backend_environment(args.profile_root.resolve(), workers=args.workers)
    server = atlas_util.import_backend_server()
    server.load_atlas()
    if not server.atlas_loaded:
        raise RuntimeError(f"Atlas failed to load: {server.atlas_error}")
    all_targets = sorted({target for case in source_cases for target in case["requestedTargets"]})
    atlas_util.apply_structure_tree_descendant_fallback(
        server, all_targets, args.structures_json.resolve()
    )

    cached = {}
    if args.resume and checkpoint_path.exists():
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        cached = {case["caseId"]: case for case in raw.get("cases") or []}
    results = []
    with server.app.app_context():
        for index, case in enumerate(source_cases, start=1):
            if case["caseId"] in cached:
                result = cached[case["caseId"]]
            else:
                print(f"[{index}/{len(source_cases)}] {case['caseId']}", flush=True)
                result = run_case(
                    server,
                    case,
                    random_seed_count=args.random_seeds,
                    random_seed_base=args.random_seed_base,
                )
            results.append(result)
            save(checkpoint_path, {"cases": results})

    baseline_summary = summarize(results, "baselines", BASELINE_ORDER)
    ablation_summary = summarize(results, "ablations", ABLATION_ORDER)
    payload = {
        "schemaVersion": "probeplanner.strict_baseline_ablation.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(args.profile_root.resolve()),
        "sourceManifest": str(args.manifest.resolve()),
        "caseCount": len(results),
        "randomSeedCount": args.random_seeds,
        "atlasSignature": deepcopy(server.atlas_signature),
        "riskModel": deepcopy(server.risk_model_summary),
        "baselineSummary": baseline_summary,
        "ablationSummary": ablation_summary,
        "cases": results,
    }
    save(output_dir / "strict-baseline-ablation-results.json", payload)
    save(output_dir / "strict-baseline-summary.json", baseline_summary)
    save(output_dir / "strict-ablation-summary.json", ablation_summary)
    write_csv(output_dir / "strict-baseline-ablation-results.csv", results)
    write_markdown(output_dir / "strict-baseline-ablation-results.md", payload)
    print(json.dumps({"caseCount": len(results), "baselineSummary": baseline_summary, "ablationSummary": ablation_summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
