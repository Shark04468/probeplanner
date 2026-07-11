#!/usr/bin/env python3
"""Build the public, path-sanitized Supplementary Data directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "evidence" / "2026-07-10-strict-full-rerun"
OUTPUT = ROOT / "supplementary-data"
TEXT_SUFFIXES = {".json", ".csv", ".md", ".svg", ".txt"}
SKIP_NAMES = {"run.log", "run.err.log", "run.stderr.log", "run.stdout.log"}

KNOWN_ROOTS = (
    (re.compile(r"D:\\mri_ccf\\planner_artifacts\\ambmc_true_outer_full", re.I), "${PROBEPLANNER_PROFILE_ROOT}"),
    (re.compile(r"D:\\mri_ccf\\planner_artifacts\\mri_allen_bridge_v2_candidate", re.I), "${PROBEPLANNER_REGISTRATION_PROFILE_ROOT}"),
    (re.compile(r"D:\\mri_ccf\\raw\\allen_ccf", re.I), "${ALLEN_CCF_ROOT}"),
    (re.compile(r"D:\\mri_ccf\\raw\\ambmc", re.I), "${AMBMC_DATA_ROOT}"),
    (
        re.compile(r"C:\\Users\\[^\\]+\\AppData\\Local\\Temp\\probeplanner_registration_candidates", re.I),
        "${PROBEPLANNER_REGISTRATION_ROOT}",
    ),
)
GENERIC_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s\"'<>|,\]\)]+")


def sanitize_text(value: str) -> str:
    out = value
    for pattern, replacement in KNOWN_ROOTS:
        out = pattern.sub(lambda _: replacement, out)
    out = GENERIC_WINDOWS_PATH.sub(lambda match: f"${{LOCAL_DATA_ROOT}}/{Path(match.group(0)).name}", out)
    out = re.sub(r"[ \t]+(?=\r?$)", "", out, flags=re.MULTILINE)
    return out.rstrip() + "\n"


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def copy_public_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".json":
        payload = sanitize_json(json.loads(source.read_text(encoding="utf-8-sig")))
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif source.suffix.lower() in TEXT_SUFFIXES:
        destination.write_text(sanitize_text(source.read_text(encoding="utf-8-sig")), encoding="utf-8")
    else:
        shutil.copy2(source, destination)


def include_file(path: Path) -> bool:
    lowered = path.name.lower()
    return (
        path.is_file()
        and lowered not in SKIP_NAMES
        and "checkpoint" not in lowered
        and path.suffix.lower() in TEXT_SUFFIXES.union({".png", ".pdf"})
    )


def copy_tree(source: Path, destination: Path) -> list[tuple[str, str]]:
    copied: list[tuple[str, str]] = []
    for path in sorted(source.rglob("*")):
        if not include_file(path):
            continue
        target = destination / path.relative_to(source)
        copy_public_file(path, target)
        copied.append((path.relative_to(ROOT).as_posix(), target.relative_to(OUTPUT).as_posix()))
    return copied


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--tag", default="v0.2.0-paper")
    args = parser.parse_args()
    output = args.output.resolve()

    required = [SOURCE, ROOT / "docs" / "evidence" / "2026-07-11-strict-final-benchmark"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required evidence: " + ", ".join(missing))

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    provenance: list[tuple[str, str]] = []
    for number in range(1, 11):
        candidates = sorted(SOURCE.glob(f"experiment_{number:02d}_*"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one source directory for experiment {number}, found {len(candidates)}")
        provenance.extend(copy_tree(candidates[0], output / "experiments" / f"experiment_{number:02d}"))

    validation_sources = {
        "final-benchmark": ROOT / "docs" / "evidence" / "2026-07-11-strict-final-benchmark",
        "baseline-ablation": ROOT / "docs" / "evidence" / "2026-07-11-strict-baseline-ablation",
        "registration-stability": ROOT / "docs" / "evidence" / "2026-07-11-trajectory-stability",
    }
    for label, source in validation_sources.items():
        provenance.extend(copy_tree(source, output / "validation" / label))

    contract = SOURCE / "RUN_CONTRACT.md"
    copy_public_file(contract, output / "RUN_CONTRACT.md")
    provenance.append((contract.relative_to(ROOT).as_posix(), "RUN_CONTRACT.md"))

    readme = f"""# ProbePlanner Supplementary Data

This directory contains the path-sanitized results, tables, and figures for experiments 1-10 and the final strict validation runs associated with release `{args.tag}`.

## Contents

- `experiments/experiment_01` to `experiment_10`: atlas provenance, registration QC, coordinate round trip, perturbation sensitivity, automatic probe-count planning, digital phantom, baselines, ablations, repeated-run stability, and real-atlas case studies.
- `validation/final-benchmark`: locked 32-case strict benchmark and single-target diagnostics.
- `validation/baseline-ablation`: baselines and six objective/constraint ablations on the full-coverage benchmark.
- `validation/registration-stability`: registration-perturbation spatial-stability analysis and lambda sweep.
- `RUN_CONTRACT.md`: experiment execution contract.
- `manifest.json` and `SHA256SUMS`: provenance and integrity records.

Absolute workstation paths were replaced with environment placeholders. Raw Allen CCF and MRI volumes are not redistributed; obtain them from their cited sources and set the roots documented in `docs/reproducibility.md`.

## Claim Boundary

These files support computational reproducibility and atlas-defined trajectory audits. They do not constitute prospective surgical or histological validation. Internal vessel, ventricle, sulcus, or user-exclusion safety claims are unavailable where the corresponding masks were absent.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")

    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS"}:
            relative = path.relative_to(output).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})

    manifest = {
        "schemaVersion": 1,
        "releaseTag": args.tag,
        "releaseDate": date(2026, 7, 11).isoformat(),
        "sourceCommit": f"tag:{args.tag}",
        "pathPolicy": "No absolute workstation paths; placeholders identify required external roots.",
        "provenance": [{"source": source, "publicPath": target} for source, target in provenance],
        "files": files,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checksum_paths = [path for path in sorted(output.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"]
    sums = "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in checksum_paths)
    (output / "SHA256SUMS").write_text(sums, encoding="ascii")
    print(f"Built {len(checksum_paths)} supplementary files in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
