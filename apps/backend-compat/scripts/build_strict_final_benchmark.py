#!/usr/bin/env python3
"""Build the strict single-target diagnostics and audited final benchmark."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
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


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evidence" / "2026-07-11-strict-final-benchmark"
SINGLE_TARGETS = ("CA1", "CA3", "DG", "VISp", "SSp", "VPM", "MOs")
PRIORITY_TARGETS = ("CA1", "CA3", "DG", "VISp", "SSp", "VPM", "MOs", "LP", "LGd", "MOp")
BUCKET_QUOTAS = {1: 10, 2: 8, 3: 5, 4: 4}
FAIL_CLOSED_QUOTA = 5
OVERSHOOT_TOLERANCE_MM = 0.025


def make_case(
    case_id: str,
    targets: tuple[str, ...],
    *,
    case_type: str,
    targeting_mode: str = "single_hemisphere",
    expect_fail_closed: bool = False,
    depth_um: float = 7600.0,
) -> auto_bench.BenchmarkCase:
    return auto_bench.BenchmarkCase(
        case_id=case_id,
        case_type=case_type,
        purpose="Strict production-pipeline benchmark with independent probe audit.",
        requested_targets=targets,
        targeting_mode=targeting_mode,
        expect_fail_closed=expect_fail_closed,
        depth_um=depth_um,
    )


def stage_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    generation = result.get("candidateGeneration") if isinstance(result.get("candidateGeneration"), dict) else {}
    extra = result.get("errorExtra") if isinstance(result.get("errorExtra"), dict) else {}
    if not generation:
        generation = ((extra.get("computeProfile") or {}).get("candidateGeneration") or {})
    breakdown = result.get("candidateStageBreakdown") if isinstance(result.get("candidateStageBreakdown"), dict) else {}
    if not breakdown:
        breakdown = extra.get("candidateStageBreakdown") if isinstance(extra.get("candidateStageBreakdown"), dict) else {}
    breakdown_summary = breakdown.get("summary") if isinstance(breakdown.get("summary"), dict) else {}
    reject = generation.get("coarseRejectReasons") if isinstance(generation.get("coarseRejectReasons"), dict) else {}
    raw = int(generation.get("specDeduped", breakdown_summary.get("specTotal", 0)) or 0)
    geometry_or_brain_rejects = sum(
        int(reject.get(key, 0) or 0)
        for key in ("bounds", "emptyTrajectory", "brainMaskNoIntersection", "brainMaskRejected")
    )
    after_brain = max(0, raw - geometry_or_brain_rejects)
    after_tip = int(generation.get("endpointTrimmedCount", generation.get("finalCount", 0)) or 0)
    after_risk = int(breakdown_summary.get("safePoolCount", result.get("candidateCount", 0)) or 0)
    fail_reason = result.get("errorCode") or result.get("failureReason")
    if not fail_reason and result.get("uncoveredTargets"):
        fail_reason = "uncovered_required_targets"
    return {
        "rawCandidateCount": raw,
        "brainMaskPassedCount": after_brain,
        "tipInTargetPassedCount": after_tip,
        "riskEnforcedPassedCount": after_risk,
        "selectedProbeCount": int(result.get("selectedProbeCount", 0) or 0),
        "uncoveredTargets": list(result.get("uncoveredTargets") or []),
        "failClosedReason": fail_reason,
        "targetLocalGridSpecs": int(generation.get("targetLocalGridSpecs", 0) or 0),
        "globalGridSpecs": int(generation.get("globalGridSpecs", 0) or 0),
        "pcaSpecs": int(generation.get("pcaSpecs", 0) or 0),
        "pairSpecs": int(generation.get("pairSpecs", 0) or 0),
        "coarseRejectReasons": deepcopy(reject),
        "refineRejectReasons": deepcopy(generation.get("refineRejectReasons") or {}),
    }


def reconstruct_probe(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "ap_mm": probe.get("apMm"),
        "ap_mm_corrected": probe.get("apMm"),
        "ml_mm": probe.get("mlMm"),
        "ml_mm_corrected": probe.get("mlMm"),
        "theta_deg": probe.get("thetaDeg"),
        "phi_deg": probe.get("phiDeg"),
        "depthUm": probe.get("depthUm"),
        "depthUm_corrected": probe.get("depthUm"),
        "skullTiltDeg": 0.0,
        "riskDiagnostics": {
            "classMinDistanceMm": deepcopy(probe.get("riskClassMinDistanceMm") or {}),
            "classThresholdMm": deepcopy(probe.get("riskClassThresholdMm") or {}),
        },
    }


def independent_case_audit(server: Any, result: dict[str, Any]) -> dict[str, Any]:
    request = result.get("plannerRequest") if isinstance(result.get("plannerRequest"), dict) else {}
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    rows = []
    for probe in result.get("selectedProbes") or []:
        independent = server._build_unified_probe_audit(
            reconstruct_probe(probe),
            result.get("requestedTargets") or [],
            params,
        )
        strict_pass = bool(
            independent.get("brainContainmentPassed")
            and independent.get("tipInTarget")
            and independent.get("distalOvershootMm") is not None
            and float(independent.get("distalOvershootMm")) <= OVERSHOOT_TOLERANCE_MM + 1e-9
            and int(independent.get("internalRiskViolationCount", -1)) == 0
            and independent.get("entrySurfaceCrossingStatus") != "failed_post_entry_containment"
        )
        keys = (
            "brainContainmentPassed",
            "tipInTarget",
            "distalOvershootMm",
            "internalRiskViolationCount",
            "minimumInternalRiskMarginMm",
            "entrySurfaceCrossingStatus",
        )
        matches = all(probe.get(key) == independent.get(key) for key in keys)
        rows.append(
            {
                "probeIndex": probe.get("probeIndex"),
                "strictAuditPassed": strict_pass,
                "reportedMatchesIndependent": matches,
                **{key: independent.get(key) for key in keys},
                "internalRiskAudit": independent.get("internalRiskAudit"),
                "entrySurfaceAudit": independent.get("entrySurfaceAudit"),
            }
        )
    full_coverage = not bool(result.get("uncoveredTargets") or result.get("uncoveredSides"))
    return {
        "probeCount": len(rows),
        "fullCoverage": full_coverage,
        "allProbesStrictPass": bool(rows) and all(row["strictAuditPassed"] for row in rows),
        "allReportedFieldsMatch": bool(rows) and all(row["reportedMatchesIndependent"] for row in rows),
        "probes": rows,
    }


def run_and_audit(server: Any, case: auto_bench.BenchmarkCase, workers: int) -> dict[str, Any]:
    result = auto_bench.run_case(server, case, workers, exact_validate=False)
    result["stageDiagnostics"] = stage_diagnostics(result)
    result["independentAudit"] = independent_case_audit(server, result)
    result["eligibleForManifest"] = bool(
        result.get("status") == "ok"
        and result["independentAudit"]["fullCoverage"]
        and result["independentAudit"]["allProbesStrictPass"]
        and result["independentAudit"]["allReportedFieldsMatch"]
        and int(result.get("selectedProbeCount", 0) or 0) in BUCKET_QUOTAS
    )
    return result


def candidate_cases(available_targets: list[str]) -> list[auto_bench.BenchmarkCase]:
    targets = [key for key in PRIORITY_TARGETS if key in available_targets]
    cases: list[auto_bench.BenchmarkCase] = []
    seen = set()

    def add(combo: tuple[str, ...], case_type: str, mode: str = "single_hemisphere") -> None:
        slug = "_".join(key.lower() for key in combo)
        prefix = "bilateral" if mode == "bilateral_symmetric" else "unilateral"
        case_id = f"search_{prefix}_{slug}"
        if case_id in seen:
            return
        seen.add(case_id)
        cases.append(make_case(case_id, combo, case_type=case_type, targeting_mode=mode))

    for key in targets:
        add((key,), "single-region")

    # Probe-count-critical cases run first: bilateral pairs commonly expose the
    # four-probe regime, while distributed unilateral triples expose three.
    for combo in itertools.combinations(targets, 2):
        add(combo, "bilateral-distributed", "bilateral_symmetric")
    for combo in list(itertools.combinations(targets, 3))[:24]:
        add(combo, "bilateral-distributed", "bilateral_symmetric")
    for combo in list(itertools.combinations(targets, 3))[:24]:
        add(combo, "distributed-unilateral")
    for combo in list(itertools.combinations(targets, 2))[:24]:
        add(combo, "distributed-unilateral")
    for key in targets[:6]:
        add((key,), "bilateral-single", "bilateral_symmetric")
    for size, limit in ((4, 24), (5, 16)):
        for combo in list(itertools.combinations(targets, size))[:limit]:
            add(combo, "distributed-unilateral")
    for size, limit in ((3, 16), (4, 12)):
        for combo in list(itertools.combinations(targets, size))[:limit]:
            add(combo, "bilateral-distributed", "bilateral_symmetric")
    return cases


def search_quotas_met(results: list[dict[str, Any]]) -> bool:
    counts = {bucket: 0 for bucket in BUCKET_QUOTAS}
    for result in results:
        if not result.get("eligibleForManifest"):
            continue
        bucket = int(result.get("selectedProbeCount", 0) or 0)
        if bucket in counts:
            counts[bucket] += 1
    return all(counts[bucket] >= quota for bucket, quota in BUCKET_QUOTAS.items())


def failure_cases(available_targets: list[str]) -> list[auto_bench.BenchmarkCase]:
    deep_target_order = ("CA1", "CA3", "DG", "VPM", "LP", "LGd")
    targets = [key for key in deep_target_order if key in available_targets][:FAIL_CLOSED_QUOTA]
    return [
        make_case(
            f"fail_{idx:02d}_depth_limited_{key.lower()}",
            (key,),
            case_type="infeasible-depth-limited",
            expect_fail_closed=True,
            depth_um=500.0,
        )
        for idx, key in enumerate(targets, start=1)
    ]


def save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["caseId"]): row for row in raw.get("cases") or [] if row.get("caseId")}


def select_manifest(search_results: list[dict[str, Any]], fail_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    buckets = {count: [] for count in BUCKET_QUOTAS}
    for result in search_results:
        count = int(result.get("selectedProbeCount", 0) or 0)
        if result.get("eligibleForManifest") and count in buckets:
            buckets[count].append(result)
    selected = []
    bucket_gate = {}
    for count, quota in BUCKET_QUOTAS.items():
        rows = buckets[count]
        selected.extend(rows[:quota])
        bucket_gate[str(count)] = {
            "required": quota,
            "available": len(rows),
            "passed": len(rows) >= quota,
        }
    valid_failures = [
        row for row in fail_results
        if row.get("status") == "fail_closed"
        and int(row.get("selectedProbeCount", 0) or 0) == 0
        and bool(row.get("uncoveredTargets"))
    ]
    selected.extend(valid_failures[:FAIL_CLOSED_QUOTA])
    gates = {
        "bucketGate": bucket_gate,
        "feasibleCaseCount": sum(1 for row in selected if row.get("eligibleForManifest")),
        "minimumFeasibleCasesRequired": 20,
        "failClosedRequired": FAIL_CLOSED_QUOTA,
        "failClosedAvailable": len(valid_failures),
        "manifestSize": len(selected),
    }
    gates["passed"] = bool(
        all(item["passed"] for item in bucket_gate.values())
        and gates["feasibleCaseCount"] >= 20
        and len(valid_failures) >= FAIL_CLOSED_QUOTA
        and 30 <= len(selected) <= 50
    )
    return selected, gates


def write_single_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "target", "rawCandidateCount", "brainMaskPassedCount", "tipInTargetPassedCount",
        "riskEnforcedPassedCount", "selectedProbeCount", "uncoveredTargets", "failClosedReason",
        "targetLocalGridSpecs", "status", "runtimeSec",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in rows:
            diag = result["stageDiagnostics"]
            writer.writerow({
                "target": result["requestedTargets"][0],
                **{key: diag.get(key) for key in fields if key in diag},
                "uncoveredTargets": json.dumps(diag.get("uncoveredTargets") or []),
                "status": result.get("status"),
                "runtimeSec": result.get("runtimeSec"),
            })


def write_report(path: Path, single_results: list[dict[str, Any]], search_results: list[dict[str, Any]], gates: dict[str, Any]) -> None:
    lines = [
        "# Strict Final Benchmark Acceptance Report",
        "",
        f"- Manifest gate: {'PASS' if gates['passed'] else 'FAIL'}",
        f"- Production target sets searched: {len(search_results)}",
        f"- Independently audited feasible cases: {sum(1 for row in search_results if row.get('eligibleForManifest'))}",
        f"- Proposed manifest size: {gates['manifestSize']}",
        "",
        "## Single-Target Diagnostics",
        "",
        "| target | raw | brain mask pass | tip-in-target pass | risk-enforced pass | selected | uncovered | status | fail reason |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for result in single_results:
        diag = result["stageDiagnostics"]
        lines.append(
            f"| {result['requestedTargets'][0]} | {diag['rawCandidateCount']} | {diag['brainMaskPassedCount']} | "
            f"{diag['tipInTargetPassedCount']} | {diag['riskEnforcedPassedCount']} | {diag['selectedProbeCount']} | "
            f"{', '.join(diag['uncoveredTargets']) or 'none'} | {result['status']} | {diag['failClosedReason'] or 'none'} |"
        )
    lines.extend(["", "## Probe-Count Search Coverage", "", "| probes | required | available | gate |", "|---:|---:|---:|---|"])
    for count, item in gates["bucketGate"].items():
        lines.append(f"| {count} | {item['required']} | {item['available']} | {'PASS' if item['passed'] else 'FAIL'} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "The manifest is written only when all count buckets, independent trajectory audits, fail-closed controls, and the 30-50 case-size gate pass. Missing internal risk layers remain explicitly unavailable and are not included in the safety claim.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", type=Path, default=DEFAULT_PROFILE_ROOT)
    parser.add_argument("--structures-json", type=Path, default=atlas_util.DEFAULT_STRUCTURES_JSON)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-search-cases", type=int, default=120)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    checkpoint_path = output_dir / "search-checkpoint.json"
    atlas_util.configure_backend_environment(args.profile_root.resolve(), workers=args.workers)
    server = atlas_util.import_backend_server()
    server.load_atlas()
    if not server.atlas_loaded:
        raise RuntimeError(f"Atlas failed to load: {server.atlas_error}")
    available_targets = [key for key in PRIORITY_TARGETS if key in server.PLANNER_REGIONS]
    atlas_util.apply_structure_tree_descendant_fallback(server, available_targets, args.structures_json.resolve())

    single_cases = [make_case(f"single_{key.lower()}", (key,), case_type="single-region-diagnostic") for key in SINGLE_TARGETS]
    single_path = output_dir / "single-target-diagnostics.json"
    if args.resume and single_path.exists():
        single_results = list(json.loads(single_path.read_text(encoding="utf-8")).get("cases") or [])
    else:
        single_results = []
        with server.app.app_context():
            for index, case in enumerate(single_cases, start=1):
                print(f"[single {index}/{len(single_cases)}] {case.case_id}", flush=True)
                single_results.append(run_and_audit(server, case, args.workers))
        save_checkpoint(single_path, {"cases": single_results})
        write_single_csv(output_dir / "single-target-diagnostics.csv", single_results)

    cached = load_checkpoint(checkpoint_path) if args.resume else {}
    search_results = []
    cases = candidate_cases(available_targets)[: max(0, args.max_search_cases)]
    with server.app.app_context():
        for index, case in enumerate(cases, start=1):
            if case.case_id in cached:
                result = cached[case.case_id]
            else:
                print(f"[search {index}/{len(cases)}] {case.case_id} {case.requested_targets} {case.targeting_mode}", flush=True)
                result = run_and_audit(server, case, args.workers)
            search_results.append(result)
            save_checkpoint(checkpoint_path, {"cases": search_results})
            if search_quotas_met(search_results):
                print(f"[search] all probe-count quotas met after {len(search_results)} cases", flush=True)
                break

        fail_results = []
        for index, case in enumerate(failure_cases(available_targets), start=1):
            print(f"[fail {index}/{FAIL_CLOSED_QUOTA}] {case.case_id}", flush=True)
            fail_results.append(run_and_audit(server, case, args.workers))

    manifest_cases, gates = select_manifest(search_results, fail_results)
    audit_payload = {
        "schemaVersion": "probeplanner.strict_final_benchmark.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(args.profile_root.resolve()),
        "atlasSignature": deepcopy(server.atlas_signature),
        "riskModel": deepcopy(server.risk_model_summary),
        "acceptanceGates": gates,
        "singleTargetDiagnostics": single_results,
        "searchResults": search_results,
        "failClosedResults": fail_results,
        "proposedManifestCases": manifest_cases,
    }
    save_checkpoint(output_dir / "strict-final-benchmark-results.json", audit_payload)
    save_checkpoint(output_dir / "acceptance-report.json", gates)
    write_report(output_dir / "acceptance-report.md", single_results, search_results, gates)
    if gates["passed"]:
        save_checkpoint(
            output_dir / "final-benchmark-manifest.json",
            {"locked": True, "acceptanceGates": gates, "cases": manifest_cases},
        )
    else:
        stale_manifest = output_dir / "final-benchmark-manifest.json"
        if stale_manifest.exists():
            stale_manifest.unlink()
        save_checkpoint(
            output_dir / "manifest-not-locked.json",
            {"locked": False, "acceptanceGates": gates, "searchedCaseCount": len(search_results)},
        )
    print(json.dumps(gates, indent=2))
    return 0 if gates["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
