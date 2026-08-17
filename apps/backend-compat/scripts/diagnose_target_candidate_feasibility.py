#!/usr/bin/env python3
"""Run a single-target planner probe and localize candidate-funnel failure."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audit_registration_perturbation_sensitivity as atlas_util  # noqa: E402
import benchmark_auto_probe_count_planning as benchmark  # noqa: E402


DEFAULT_PROFILE_ROOT = Path(
    os.environ.get("PROBEPLANNER_PROFILE_ROOT", "artifacts/ambmc_true_outer_full")
)


def integer(mapping: dict[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def classify_target_failure(
    *,
    target: str,
    target_region: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    generation = result.get("candidateGeneration") or {}
    local_source = f"target_grid_{target}"
    rejects_by_source = generation.get("coarseRejectReasonsBySource") or {}
    local_rejects = rejects_by_source.get(local_source) or {}
    coarse_by_target = generation.get("coarseCoverageCountsByTarget") or {}
    refined_by_target = generation.get("refinedCoverageCountsByTarget") or {}
    final_by_target = generation.get("finalCoverageCountsByTarget") or {}
    diversity_by_target = generation.get("targetDiversitySeedCounts") or {}

    voxel_count = integer(target_region, "voxelCount")
    coarse_count = integer(coarse_by_target, target)
    if coarse_count is None:
        coarse_count = integer(diversity_by_target, target)
    if coarse_count is None and integer(generation, "coarseKept") == 0:
        coarse_count = 0
    if not local_rejects and coarse_count == 0:
        local_rejects = generation.get("coarseRejectReasons") or {}
    refined_count = integer(refined_by_target, target)
    final_count = integer(final_by_target, target)

    if not target_region or voxel_count in (None, 0):
        stage = "annotation_or_ontology"
        conclusion = "The target is absent or empty in the loaded annotation/descendant mapping."
    elif coarse_count == 0:
        stage = "coarse_target_coverage"
        conclusion = (
            "No coarse candidate covered the target. Risk prefiltering and the selector are downstream "
            "and therefore cannot be the primary cause."
        )
    elif refined_count == 0:
        stage = "fine_refinement"
        conclusion = "The target had coarse coverage but all target-covering candidates were lost at refinement."
    elif final_count == 0:
        stage = "endpoint_policy"
        conclusion = "Target-covering refined candidates existed but none survived the final endpoint policy."
    elif target in (result.get("uncoveredTargets") or []):
        stage = "constraint_or_selector"
        conclusion = "Final target-covering candidates existed, but constraints or selection left the target uncovered."
    else:
        stage = "feasible"
        conclusion = "The target is covered by the current planner request."

    recommendations = []
    if stage == "coarse_target_coverage":
        recommendations.extend(
            [
                "Inspect target_grid source rejection counts before changing risk thresholds.",
                "Compare the target centroid/bounding box with the target-local AP/ML lattice and sampled depth range.",
                "If noCoverage dominates, increase target-local lattice density or add target-surface/PCA rays on a development case.",
                "If brainMaskRejected dominates, audit the moving brain mask and entry-surface semantics at the target AP/ML range.",
            ]
        )
    elif stage == "fine_refinement":
        recommendations.append("Preserve more target-specific coarse seeds and inspect fine-step brain/coverage rejection counts.")
    elif stage == "endpoint_policy":
        recommendations.append("Inspect tip-in-target trimming and depth variants; do not disable endpoint validation globally.")
    elif stage == "constraint_or_selector":
        recommendations.append("Audit per-target risk, spacing, and collision feasibility before changing the objective.")

    return {
        "target": target,
        "failureStage": stage,
        "conclusion": conclusion,
        "targetRegion": target_region,
        "counts": {
            "coarseCoverage": coarse_count,
            "refinedCoverage": refined_count,
            "finalCoverage": final_count,
            "selectedProbeCount": int(result.get("selectedProbeCount", 0) or 0),
        },
        "targetLocalSource": local_source,
        "targetLocalRejectReasons": local_rejects,
        "sourceAttribution": (
            "target-local specs may be deduplicated into an earlier global or target-local grid source"
            if local_source not in rejects_by_source
            else "direct target-local source telemetry"
        ),
        "recommendations": recommendations,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-root", default=str(DEFAULT_PROFILE_ROOT))
    parser.add_argument("--structures-json", default=str(atlas_util.DEFAULT_STRUCTURES_JSON))
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--context-targets",
        nargs="*",
        default=(),
        help="Additional targets included in the same planning request.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--depth-um", type=float, default=7600.0)
    parser.add_argument("--grid-spacing-um", type=float, default=1200.0)
    parser.add_argument("--max-angle-deg", type=float, default=10.0)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = str(args.target).strip()
    requested_targets = tuple(
        dict.fromkeys(
            [target, *[str(value).strip() for value in args.context_targets if str(value).strip()]]
        )
    )
    profile_root = Path(args.profile_root).expanduser()
    structures_json = Path(args.structures_json).expanduser()
    atlas_util.configure_backend_environment(profile_root, workers=int(args.workers))
    server = atlas_util.import_backend_server()
    server.load_atlas()
    if not getattr(server, "atlas_loaded", False):
        raise RuntimeError(f"Atlas failed to load: {getattr(server, 'atlas_error', None)}")
    ontology = atlas_util.apply_structure_tree_descendant_fallback(
        server, list(requested_targets), structures_json
    )
    target_rows = benchmark.target_region_payload(server, [target])
    target_region = target_rows[0] if target_rows else {}
    case = benchmark.BenchmarkCase(
        case_id=f"diagnostic_{'_'.join(value.lower() for value in requested_targets)}",
        case_type="target_feasibility_diagnostic",
        purpose="Localize target-specific candidate-funnel failure without changing constraints.",
        requested_targets=requested_targets,
        depth_um=float(args.depth_um),
        grid_spacing_um=float(args.grid_spacing_um),
        max_angle_deg=float(args.max_angle_deg),
    )
    started = time.time()
    with server.app.app_context():
        result = benchmark.run_case(server, case, workers=int(args.workers), exact_validate=False)
    diagnosis = classify_target_failure(
        target=target,
        target_region=target_region,
        result=result,
    )
    payload = {
        "schemaVersion": "probeplanner.target_feasibility_diagnostic.v1",
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profileRoot": str(profile_root.resolve()),
        "atlasSignature": getattr(server, "atlas_signature", {}),
        "ontologyFallback": ontology,
        "runConfig": {
            "target": target,
            "requestedTargets": list(requested_targets),
            "workers": int(args.workers),
            "depthUm": float(args.depth_um),
            "gridSpacingUm": float(args.grid_spacing_um),
            "maxAngleDeg": float(args.max_angle_deg),
        },
        "runtimeSec": round(float(time.time() - started), 6),
        "diagnosis": diagnosis,
        "plannerResult": result,
    }
    output_json = Path(args.output_json).expanduser()
    benchmark.atomic_write_json(output_json, payload)
    output_md = Path(args.output_md).expanduser() if args.output_md else output_json.with_suffix(".md")
    lines = [
        f"# Target feasibility diagnostic: {target}",
        "",
        f"- Stage: `{diagnosis['failureStage']}`",
        f"- Conclusion: {diagnosis['conclusion']}",
        f"- Target voxels: {target_region.get('voxelCount')}",
        f"- Coarse/refined/final target candidates: {diagnosis['counts']['coarseCoverage']} / {diagnosis['counts']['refinedCoverage']} / {diagnosis['counts']['finalCoverage']}",
        f"- Target-local reject reasons: `{json.dumps(diagnosis['targetLocalRejectReasons'], sort_keys=True)}`",
        "",
        "## Next actions",
        "",
        *[f"- {item}" for item in diagnosis["recommendations"]],
        "",
    ]
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"diagnosis": diagnosis, "json": str(output_json), "md": str(output_md)}, indent=2))
    return 0 if diagnosis["failureStage"] == "feasible" else 2


if __name__ == "__main__":
    raise SystemExit(main())
