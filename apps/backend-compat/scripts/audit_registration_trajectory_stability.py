#!/usr/bin/env python3
"""Compare unstabilized and reference-stabilized perturbation planning."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import statistics
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as perturb  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evidence" / "2026-07-11-trajectory-stability"
DEFAULT_PROFILE_ROOT = Path(os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full"))
WEIGHTS = (0.01, 0.02, 0.05, 0.10, 0.20)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def reference_payload(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for probe in selected:
        entry = probe.get("entry_xyz")
        tip = probe.get("tip_xyz")
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            continue
        if not isinstance(tip, (list, tuple)) or len(tip) != 3:
            continue
        output.append(
            {
                "entryCcfUm": [float(value) for value in entry],
                "tipCcfUm": [float(value) for value in tip],
            }
        )
    return output


def run_candidate_pool(
    server: Any,
    request: dict[str, Any],
    spec: perturb.Perturbation,
    affine: perturb.AffinePerturbation,
) -> dict[str, Any]:
    started = time.perf_counter()
    perturb.clear_backend_caches(server)
    with perturb.BackendPerturbationContext(server, affine):
        try:
            result = server._run_optimize_pipeline(deepcopy(request))
            return {
                "status": "ok" if not result.get("uncovered") else "partial",
                "error": None,
                "selected": deepcopy(result.get("selected") or []),
                "candidates": deepcopy(result.get("candidatePreview") or []),
                "uncoveredTargets": sorted(str(value) for value in (result.get("uncovered") or [])),
                "candidateCount": int(result.get("candidateCount", 0) or 0),
                "runtimeSec": round(float(time.perf_counter() - started), 6),
            }
        except Exception as exc:
            return {
                "status": "fail_closed",
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "code": getattr(exc, "code", None),
                },
                "selected": [],
                "candidates": [],
                "uncoveredTargets": list(request.get("targetKeys") or []),
                "candidateCount": 0,
                "runtimeSec": round(float(time.perf_counter() - started), 6),
            }


def select_with_reference(
    server: Any,
    candidates: list[dict[str, Any]],
    request: dict[str, Any],
    references: list[dict[str, Any]],
    weight: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    stable_request = deepcopy(request)
    stable_request.setdefault("params", {}).update(
        {
            "stabilityTieBreakerEnabled": True,
            "stabilityReferenceWeight": float(weight),
            "stabilityEntryWeight": 1.0,
            "stabilityTipWeight": 1.0,
            "stabilityAngleWeight": 1.0,
            "stabilityReferenceTrajectories": references,
            "stabilityReferenceProbeCount": len(references),
        }
    )
    payload = server._normalize_optimize_payload(stable_request)
    params = payload["params"]
    rescored = deepcopy(candidates)
    server.candidate_objective_cache.clear()
    server._score_candidates(rescored, payload["targetKeys"], params)
    for candidate in rescored:
        candidate["paretoRank"] = 1
    result = server.greedy_optimize(
        payload["targetKeys"],
        rescored,
        float(params.get("minClearance", 1000.0)),
        float(payload["probeParams"].get("entrySpacingUm", 3500.0)),
    )
    return {
        "status": "ok" if not result.get("uncovered") else "partial",
        "error": None,
        "selected": deepcopy(result.get("selected") or []),
        "uncoveredTargets": sorted(str(value) for value in (result.get("uncovered") or [])),
        "candidateCount": len(rescored),
        "runtimeSec": round(float(time.perf_counter() - started), 6),
        "stabilityReferenceWeight": float(weight),
    }


def probe_geometry(probe: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        entry = np.asarray(probe.get("entry_xyz"), dtype=np.float64)
        tip = np.asarray(probe.get("tip_xyz"), dtype=np.float64)
    except Exception:
        return None
    if entry.shape != (3,) or tip.shape != (3,):
        return None
    return entry, tip


def matched_spatial_metrics(
    baseline: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, Any]:
    base = [geometry for probe in baseline if (geometry := probe_geometry(probe)) is not None]
    other = [geometry for probe in current if (geometry := probe_geometry(probe)) is not None]
    if not base or not other:
        return {
            "matchedProbeCount": 0,
            "entryDisplacementMm": None,
            "tipDisplacementMm": None,
            "angularChangeDeg": None,
        }
    match_count = min(len(base), len(other))
    best = None
    for other_indices in itertools.permutations(range(len(other)), match_count):
        total = 0.0
        rows = []
        for base_index, other_index in enumerate(other_indices):
            base_entry, base_tip = base[base_index]
            cur_entry, cur_tip = other[other_index]
            entry_mm = float(np.linalg.norm(cur_entry - base_entry) / 1000.0)
            tip_mm = float(np.linalg.norm(cur_tip - base_tip) / 1000.0)
            base_direction = base_tip - base_entry
            cur_direction = cur_tip - cur_entry
            cosine = float(
                np.clip(
                    np.dot(base_direction, cur_direction)
                    / max(1e-12, np.linalg.norm(base_direction) * np.linalg.norm(cur_direction)),
                    -1.0,
                    1.0,
                )
            )
            angle = float(math.degrees(math.acos(cosine)))
            total += entry_mm + tip_mm + angle / 10.0
            rows.append((entry_mm, tip_mm, angle))
        if best is None or total < best[0]:
            best = (total, rows)
    rows = best[1] if best is not None else []
    return {
        "matchedProbeCount": len(rows),
        "entryDisplacementMm": round(max(row[0] for row in rows), 6) if rows else None,
        "tipDisplacementMm": round(max(row[1] for row in rows), 6) if rows else None,
        "angularChangeDeg": round(max(row[2] for row in rows), 6) if rows else None,
        "meanEntryDisplacementMm": round(statistics.mean(row[0] for row in rows), 6) if rows else None,
        "meanTipDisplacementMm": round(statistics.mean(row[1] for row in rows), 6) if rows else None,
        "meanAngularChangeDeg": round(statistics.mean(row[2] for row in rows), 6) if rows else None,
    }


def add_metrics(
    row: dict[str, Any],
    baseline_selected: list[dict[str, Any]],
    baseline_covered: set[str],
    target_keys: list[str],
) -> dict[str, Any]:
    selected = row.get("selected") or []
    covered = {str(key) for probe in selected for key in (probe.get("coveredRegions") or [])}
    spatial = matched_spatial_metrics(baseline_selected, selected)
    strict_probe_audits = []
    for probe in selected:
        brain = probe.get("brainBoundsDiagnostics") if isinstance(probe.get("brainBoundsDiagnostics"), dict) else {}
        endpoint = probe.get("endpointTargeting") if isinstance(probe.get("endpointTargeting"), dict) else {}
        overshoot_um = finite(endpoint.get("overshootAfterLastTargetUm"))
        strict_probe_audits.append(
            bool(
                brain.get("passed")
                and endpoint.get("tipInRequestedTarget")
                and overshoot_um is not None
                and overshoot_um <= 1e-6
            )
        )
    return {
        **row,
        "selectedProbeCount": len(selected),
        "probeCountChange": len(selected) - len(baseline_selected),
        "coveredTargets": sorted(covered),
        "targetCoverageRate": round(len(covered & set(target_keys)) / max(1, len(target_keys)), 6),
        "coverageChanged": covered != baseline_covered,
        "newUncoveredTargets": sorted(set(target_keys) - covered),
        "strictTrajectoryAuditPassed": bool(selected) and all(strict_probe_audits),
        "strictTrajectoryAuditFailureCount": sum(not passed for passed in strict_probe_audits),
        **spatial,
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), q)), 6)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    perturbed = [row for row in rows if row["name"] != "baseline"]
    def values(key: str) -> list[float]:
        return [value for row in perturbed if (value := finite(row.get(key))) is not None]
    entry = values("entryDisplacementMm")
    tip = values("tipDisplacementMm")
    angle = values("angularChangeDeg")
    return {
        "perturbationCaseCount": len(perturbed),
        "planningFailureCount": sum(row.get("status") != "ok" for row in perturbed),
        "coverageChangedCaseCount": sum(bool(row.get("coverageChanged")) for row in perturbed),
        "probeCountChangedCaseCount": sum(int(row.get("probeCountChange", 0)) != 0 for row in perturbed),
        "strictTrajectoryAuditFailureCaseCount": sum(
            not bool(row.get("strictTrajectoryAuditPassed")) for row in perturbed
        ),
        "maxEntryDisplacementMm": round(max(entry), 6) if entry else None,
        "p95EntryDisplacementMm": percentile(entry, 95),
        "medianEntryDisplacementMm": percentile(entry, 50),
        "meanEntryDisplacementMm": round(statistics.mean(entry), 6) if entry else None,
        "maxTipDisplacementMm": round(max(tip), 6) if tip else None,
        "p95TipDisplacementMm": percentile(tip, 95),
        "medianTipDisplacementMm": percentile(tip, 50),
        "meanTipDisplacementMm": round(statistics.mean(tip), 6) if tip else None,
        "maxAngularChangeDeg": round(max(angle), 6) if angle else None,
        "p95AngularChangeDeg": percentile(angle, 95),
        "medianAngularChangeDeg": percentile(angle, 50),
        "meanAngularChangeDeg": round(statistics.mean(angle), 6) if angle else None,
        "entryJumpOver1MmCaseCount": sum(value > 1.0 for value in entry),
        "tipJumpOver1MmCaseCount": sum(value > 1.0 for value in tip),
        "meanRuntimeSec": round(statistics.mean(float(row.get("runtimeSec", 0.0)) for row in perturbed), 6),
    }


def choose_weight(summaries: dict[str, dict[str, Any]]) -> float:
    eligible = []
    for key, summary in summaries.items():
        if summary["planningFailureCount"] or summary["coverageChangedCaseCount"]:
            continue
        weight = float(key)
        eligible.append(
            (
                float(summary.get("maxTipDisplacementMm") or 1e9),
                float(summary.get("p95TipDisplacementMm") or 1e9),
                float(summary.get("maxEntryDisplacementMm") or 1e9),
                weight,
            )
        )
    if not eligible:
        raise RuntimeError("No stability weight preserved coverage and planning success")
    return float(min(eligible)[-1])


def write_csv(path: Path, cases: list[dict[str, Any]], chosen_weight: float) -> None:
    fields = [
        "name", "kind", "axis", "method", "weight", "status", "selectedProbeCount",
        "probeCountChange", "targetCoverageRate", "coverageChanged", "entryDisplacementMm",
        "tipDisplacementMm", "angularChangeDeg", "candidateCount", "runtimeSec",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            rows = [("control", None, case["control"])]
            rows.extend(
                ("stabilized" if abs(float(key) - chosen_weight) <= 1e-12 else "lambda_sweep", key, value)
                for key, value in case["stabilizedByWeight"].items()
            )
            for method, weight, row in rows:
                writer.writerow(
                    {
                        "name": case["name"],
                        "kind": case["kind"],
                        "axis": case["axis"],
                        "method": method,
                        "weight": weight,
                        **{field: row.get(field) for field in fields if field in row},
                    }
                )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    control = payload["controlSummary"]
    stable = payload["stabilizedSummary"]
    lines = [
        "# Registration Perturbation Trajectory-Stability Results",
        "",
        f"- Perturbation cases: {control['perturbationCaseCount']}",
        f"- Selected stability weight: {payload['chosenStabilityReferenceWeight']}",
        f"- Control coverage-changed cases: {control['coverageChangedCaseCount']}",
        f"- Stabilized coverage-changed cases: {stable['coverageChangedCaseCount']}",
        f"- Control strict-audit failures: {control['strictTrajectoryAuditFailureCaseCount']}",
        f"- Stabilized strict-audit failures: {stable['strictTrajectoryAuditFailureCaseCount']}",
        "",
        "## Spatial Stability",
        "",
        "| metric | control | stabilized |",
        "|---|---:|---:|",
    ]
    for label, key in (
        ("max entry displacement mm", "maxEntryDisplacementMm"),
        ("p95 entry displacement mm", "p95EntryDisplacementMm"),
        ("median entry displacement mm", "medianEntryDisplacementMm"),
        ("max tip displacement mm", "maxTipDisplacementMm"),
        ("p95 tip displacement mm", "p95TipDisplacementMm"),
        ("median tip displacement mm", "medianTipDisplacementMm"),
        ("max angular change deg", "maxAngularChangeDeg"),
        ("p95 angular change deg", "p95AngularChangeDeg"),
        ("probe-count changed cases", "probeCountChangedCaseCount"),
        ("entry jumps >1 mm", "entryJumpOver1MmCaseCount"),
        ("tip jumps >1 mm", "tipJumpOver1MmCaseCount"),
    ):
        lines.append(f"| {label} | {control.get(key)} | {stable.get(key)} |")
    lines.extend([
        "",
        "## Lambda Sweep",
        "",
        "| lambda | coverage changes | failures | max entry mm | p95 entry mm | max tip mm | p95 tip mm |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for key, summary in payload["lambdaSweepSummary"].items():
        lines.append(
            f"| {key} | {summary['coverageChangedCaseCount']} | {summary['planningFailureCount']} | "
            f"{summary['maxEntryDisplacementMm']} | {summary['p95EntryDisplacementMm']} | "
            f"{summary['maxTipDisplacementMm']} | {summary['p95TipDisplacementMm']} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Coverage and trajectory stability are reported separately. The reference term is applied only within the already strict feasible candidate pool; it cannot make an unsafe candidate feasible. Remaining probe-count changes and spatial jumps are retained as sensitivity evidence rather than hidden.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", type=Path, default=DEFAULT_PROFILE_ROOT)
    parser.add_argument("--registration-root", type=Path, default=perturb.DEFAULT_REGISTRATION_ROOT)
    parser.add_argument("--structures-json", type=Path, default=perturb.DEFAULT_STRUCTURES_JSON)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-keys", default="CA1,VPM,LP,VISp")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--weights", default=",".join(str(value) for value in WEIGHTS))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    target_keys = perturb.parse_target_keys(args.target_keys)
    weights = tuple(float(value) for value in args.weights.split(",") if value.strip())
    output_dir = args.output_dir.resolve()
    checkpoint_path = output_dir / "trajectory-stability-checkpoint.json"
    perturb.configure_backend_environment(args.profile_root.resolve(), workers=args.workers)
    server = perturb.import_backend_server()
    server.load_atlas()
    if not server.atlas_loaded:
        raise RuntimeError(f"Atlas failed to load: {server.atlas_error}")
    perturb.apply_structure_tree_descendant_fallback(
        server, target_keys, args.structures_json.resolve()
    )
    pivot_mm = np.asarray(
        [
            np.mean([float(server.PLANNER_REGIONS[key]["ap"]) for key in target_keys]),
            np.mean([float(server.PLANNER_REGIONS[key]["dv"]) for key in target_keys]),
            np.mean([float(server.PLANNER_REGIONS[key]["ml"]) for key in target_keys]),
        ],
        dtype=np.float64,
    )
    request_args = argparse.Namespace(
        grid_spacing_um=1200.0,
        angle_step_deg=10.0,
        max_angle_deg=10.0,
        depth_um=7600.0,
        coarse_step_um=150.0,
        refine_step_um=75.0,
        refine_fraction=0.12,
        workers=args.workers,
        min_risk_distance_mm=0.05,
        entry_spacing_um=1200.0,
    )
    request = perturb.planner_request(request_args, target_keys)
    specs = perturb.build_perturbations()

    cached = {}
    if args.resume and checkpoint_path.exists():
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        cached = {case["name"]: case for case in raw.get("cases") or []}
    cases = []
    baseline_selected = None
    baseline_covered: set[str] = set()
    references = None
    for index, spec in enumerate(specs, start=1):
        if spec.name in cached:
            case = cached[spec.name]
            if spec.name == "baseline":
                baseline_selected = case["control"]["selected"]
                baseline_covered = set(case["control"]["coveredTargets"])
                references = reference_payload(baseline_selected)
            cases.append(case)
            continue
        print(f"[{index:02d}/{len(specs)}] {spec.name}", flush=True)
        affine = perturb.perturbation_to_affine(spec, pivot_mm)
        with server.app.app_context():
            pool = run_candidate_pool(server, request, spec, affine)
        if spec.name == "baseline":
            baseline_selected = pool["selected"]
            baseline_covered = {
                str(key) for probe in baseline_selected for key in (probe.get("coveredRegions") or [])
            }
            references = reference_payload(baseline_selected)
        if baseline_selected is None or references is None:
            raise RuntimeError("Identity baseline must be completed before perturbations")
        control = add_metrics(
            {key: value for key, value in pool.items() if key != "candidates"},
            baseline_selected,
            baseline_covered,
            target_keys,
        )
        stable_pool_request = deepcopy(request)
        stable_pool_request.setdefault("params", {}).update(
            {
                "stabilityTieBreakerEnabled": True,
                "stabilityReferenceWeight": float(weights[0]),
                "stabilityReferenceTrajectories": references,
                "stabilityReferenceProbeCount": len(references),
            }
        )
        with server.app.app_context():
            stable_pool = run_candidate_pool(server, stable_pool_request, spec, affine)
        stabilized = {}
        for weight in weights:
            selected = select_with_reference(
                server, stable_pool["candidates"], request, references, weight
            )
            selected["runtimeSec"] = round(
                float(stable_pool.get("runtimeSec", 0.0)) + float(selected.get("runtimeSec", 0.0)),
                6,
            )
            selected["candidateCount"] = int(len(stable_pool["candidates"]))
            if stable_pool.get("status") == "fail_closed":
                selected["status"] = "fail_closed"
                selected["error"] = stable_pool.get("error")
            stabilized[str(weight)] = add_metrics(
                selected, baseline_selected, baseline_covered, target_keys
            )
        case = {
            "name": spec.name,
            "kind": spec.kind,
            "axis": spec.axis,
            "translationMm": list(spec.translation_mm),
            "rotationDeg": list(spec.rotation_deg),
            "scale": spec.scale,
            "control": control,
            "stabilizedByWeight": stabilized,
        }
        cases.append(case)
        save(checkpoint_path, {"weights": weights, "cases": cases})

    baseline_case = next(case for case in cases if case["name"] == "baseline")
    final_baseline_selected = baseline_case["control"].get("selected") or []
    final_baseline_covered = {
        str(key)
        for probe in final_baseline_selected
        for key in (probe.get("coveredRegions") or [])
    }
    for case in cases:
        case["control"] = add_metrics(
            case["control"], final_baseline_selected, final_baseline_covered, target_keys
        )
        for weight in weights:
            key = str(weight)
            case["stabilizedByWeight"][key] = add_metrics(
                case["stabilizedByWeight"][key],
                final_baseline_selected,
                final_baseline_covered,
                target_keys,
            )

    control_rows = [case["control"] | {"name": case["name"]} for case in cases]
    weight_summaries = {
        str(weight): summarize(
            [
                case["stabilizedByWeight"][str(weight)] | {"name": case["name"]}
                for case in cases
            ]
        )
        for weight in weights
    }
    chosen_weight = choose_weight(weight_summaries)
    stabilized_rows = [
        case["stabilizedByWeight"][str(chosen_weight)] | {"name": case["name"]}
        for case in cases
    ]
    payload = {
        "schemaVersion": "probeplanner.registration_trajectory_stability.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targetKeys": target_keys,
        "profileRoot": str(args.profile_root.resolve()),
        "registrationRoot": str(args.registration_root.resolve()),
        "atlasSignature": deepcopy(server.atlas_signature),
        "stabilityDistance": "entry_mm + tip_mm + angular_change_deg / 10",
        "chosenStabilityReferenceWeight": chosen_weight,
        "controlSummary": summarize(control_rows),
        "lambdaSweepSummary": weight_summaries,
        "stabilizedSummary": summarize(stabilized_rows),
        "cases": cases,
    }
    save(output_dir / "trajectory-stability-results.json", payload)
    write_csv(output_dir / "trajectory-stability-results.csv", cases, chosen_weight)
    write_markdown(output_dir / "trajectory-stability-results.md", payload)
    print(json.dumps({
        "chosenWeight": chosen_weight,
        "control": payload["controlSummary"],
        "stabilized": payload["stabilizedSummary"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
