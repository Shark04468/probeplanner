#!/usr/bin/env python
"""
One-command MRI+CCF pipeline:
ingest_raw -> register(optional) -> build_masks/sdf -> build_stats -> validate -> activate_profile
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


def _run(cmd: List[str], *, stage: str) -> None:
    print(json.dumps({"stage": stage, "cmd": cmd}, ensure_ascii=False))
    subprocess.run(cmd, check=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return data


def _first_existing_path(candidates: List[Path]) -> Path | None:
    for p in candidates:
        try:
            pp = Path(p).expanduser().resolve()
        except Exception:
            continue
        if pp.exists():
            return pp
    return None


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="Run end-to-end MRI+CCF artifact pipeline.")
    ap.add_argument("--project-root", default=str(project_root))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pipeline-mode", choices=("direct", "ambmc"), default="direct")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--dataset-profile", default="custom")
    ap.add_argument("--expected-shape", default="528,320,456")
    ap.add_argument("--activate", action="store_true", help="Write active_profile.json contract output")

    # direct builder inputs
    ap.add_argument("--label-image", default="")
    ap.add_argument("--region-map-json", default="")
    ap.add_argument("--stats", default="")
    ap.add_argument("--jacobian-image", default="")
    ap.add_argument("--skull-mask-image", default="")
    ap.add_argument("--outer-tissue-mask-image", default="")
    ap.add_argument("--ventricle-mask-image", default="")
    ap.add_argument("--vessel-mask-image", default="")
    ap.add_argument("--sulcus-mask-image", default="")
    ap.add_argument("--layered-sdf-image", default="")
    ap.add_argument("--registration-summary", default="")

    # ambmc mode inputs (forwarded)
    ap.add_argument("--ambmc-image", default="")
    ap.add_argument("--ccf-template", default="")
    ap.add_argument("--ccf-annotation", default="")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--skip-deformable", action="store_true")
    ap.add_argument("--deformable-mesh", default="8,8,8")
    ap.add_argument("--deformable-learning-rate", type=float, default=0.5)
    ap.add_argument("--deformable-iterations", type=int, default=180)
    ap.add_argument("--deformable-sampling", type=float, default=0.20)
    ap.add_argument("--deformable-smoothing-sigmas", default="2,1,0")
    ap.add_argument("--registration-spacing-mm", type=float, default=0.10)
    args = ap.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    py = str(Path(args.python).expanduser().resolve())

    build_script = project_root / "scripts" / "build_mri_ccf_hybrid_artifacts.py"
    quality_script = project_root / "scripts" / "build_mri_quality_metrics.py"
    validate_script = project_root / "scripts" / "validate_mri_ccf_inputs.py"
    ambmc_script = project_root / "scripts" / "build_ambmc_true_outer_pipeline.py"

    if args.pipeline_mode == "ambmc":
        cmd = [
            py,
            str(ambmc_script),
            "--project-root",
            str(project_root),
            "--output-dir",
            str(out_dir),
            "--dataset-profile",
            str(args.dataset_profile),
        ]
        if args.ambmc_image:
            cmd += ["--ambmc-image", str(Path(args.ambmc_image).expanduser().resolve())]
        if args.ccf_template:
            cmd += ["--ccf-template", str(Path(args.ccf_template).expanduser().resolve())]
        if args.ccf_annotation:
            cmd += ["--ccf-annotation", str(Path(args.ccf_annotation).expanduser().resolve())]
        if args.quick:
            cmd.append("--quick")
        if args.skip_deformable:
            cmd.append("--skip-deformable")
        cmd += [
            "--deformable-mesh",
            str(args.deformable_mesh),
            "--deformable-learning-rate",
            str(float(args.deformable_learning_rate)),
            "--deformable-iterations",
            str(int(args.deformable_iterations)),
            "--deformable-sampling",
            str(float(args.deformable_sampling)),
            "--deformable-smoothing-sigmas",
            str(args.deformable_smoothing_sigmas),
            "--registration-spacing-mm",
            str(float(args.registration_spacing_mm)),
        ]
        _run(cmd, stage="register_and_build")
    else:
        if not args.label_image:
            raise RuntimeError("--label-image is required for direct pipeline mode")
        cmd = [
            py,
            str(build_script),
            "--label-image",
            str(Path(args.label_image).expanduser().resolve()),
            "--output-dir",
            str(out_dir),
            "--expected-shape",
            str(args.expected_shape),
            "--dataset-profile",
            str(args.dataset_profile),
        ]
        if args.region_map_json:
            cmd += ["--region-map-json", str(Path(args.region_map_json).expanduser().resolve())]
        if args.jacobian_image:
            cmd += ["--jacobian-image", str(Path(args.jacobian_image).expanduser().resolve())]
        if args.skull_mask_image:
            cmd += ["--skull-mask-image", str(Path(args.skull_mask_image).expanduser().resolve())]
        if args.outer_tissue_mask_image:
            cmd += ["--outer-tissue-mask-image", str(Path(args.outer_tissue_mask_image).expanduser().resolve())]
        if args.ventricle_mask_image:
            cmd += ["--ventricle-mask-image", str(Path(args.ventricle_mask_image).expanduser().resolve())]
        if args.vessel_mask_image:
            cmd += ["--vessel-mask-image", str(Path(args.vessel_mask_image).expanduser().resolve())]
        if args.sulcus_mask_image:
            cmd += ["--sulcus-mask-image", str(Path(args.sulcus_mask_image).expanduser().resolve())]
        if args.layered_sdf_image:
            cmd += ["--layered-sdf-image", str(Path(args.layered_sdf_image).expanduser().resolve())]
        if args.registration_summary:
            cmd += ["--registration-summary", str(Path(args.registration_summary).expanduser().resolve())]
        _run(cmd, stage="build_artifacts")

    stats_path = out_dir / "stats.json"
    reg_summary_candidates: List[Path] = []
    if args.registration_summary:
        reg_summary_candidates.append(Path(args.registration_summary))
    reg_summary_candidates.extend(
        [
            out_dir / "registration_summary.json",
            out_dir / "registration" / "registration_summary.json",
        ]
    )
    jacobian_candidates: List[Path] = []
    if args.jacobian_image:
        jacobian_candidates.append(Path(args.jacobian_image))
    jacobian_candidates.extend(
        [
            out_dir / "jacobian.npy",
            out_dir / "jacobian.npz",
            out_dir / "registration" / "jacobian.npy",
            out_dir / "registration" / "jacobian.npz",
        ]
    )
    reg_summary_path = _first_existing_path(reg_summary_candidates)
    jacobian_path = _first_existing_path(jacobian_candidates)
    quality_cmd = [py, str(quality_script), "--stats", str(stats_path)]
    if reg_summary_path:
        quality_cmd += ["--registration-summary", str(reg_summary_path)]
    if jacobian_path:
        quality_cmd += ["--jacobian", str(jacobian_path)]
    _run(quality_cmd, stage="build_quality")

    manifest_path = out_dir / "artifact_manifest.json"
    validate_cmd = [
        py,
        str(validate_script),
        "--manifest",
        str(manifest_path),
        "--expected-shape",
        str(args.expected_shape),
    ]
    _run(validate_cmd, stage="validate")

    manifest = _load_json(manifest_path)
    active_profile = {
        "schemaVersion": "probeplanner.mri_ccf.active_profile.v1",
        "createdAt": _utc_now_iso(),
        "projectRoot": str(project_root),
        "outputDir": str(out_dir),
        "datasetProfile": str(args.dataset_profile),
        "manifestPath": str(manifest_path),
        "manifestHash": manifest.get("artifacts", {}).get("annotation", {}).get("sha256", ""),
        "atlasMode": "mri_ccf_hybrid",
        "environment": {
            "PROBEPLANNER_ATLAS_MODE": "mri_ccf_hybrid",
            "PROBEPLANNER_MRI_CCF_MANIFEST_PATH": str(manifest_path),
        },
        "status": "ready",
    }
    active_profile_path = out_dir / "active_profile.json"
    active_profile_path.write_text(json.dumps(active_profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "outputDir": str(out_dir),
                "manifestPath": str(manifest_path),
                "activeProfilePath": str(active_profile_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
