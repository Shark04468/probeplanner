#!/usr/bin/env python3
"""Build the byte-preserving paper-validation evidence archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "paper_final_release"
ZIP_NAME = "ProbePlanner-v0.2.1-paper-immutable-evidence.zip"
MANIFEST_NAME = "IMMUTABLE_EVIDENCE_MANIFEST.json"
ZIP_TIMESTAMP = (2026, 8, 17, 0, 0, 0)

EXPECTED_HASHES = {
    "paper_final_validation/07_v021_revision/v021_v4_final_source_holdout_manifest.json": "b3921fe300995a3a1faa061ef2635be57cfcfa903445679eb9c5b0b6bd0036af",
    "paper_final_validation/07_v021_revision/v021_v4_final_source_holdout_results.json": "72d4a7518bf561b6993d802369c2f6a8351ea446ca22272264927f06c1b9d299",
    "paper_final_validation/07_v021_revision/v021_v4_final_source_holdout_summary.json": "c70d941542b6663284487a27caaf3d49b0e3a04953f156a0eb08d2f784f223e3",
    "paper_final_validation/07_v021_revision/v021_v4_failure_oracle_results.json": "da8615557e406816f8b1d3e583bf7e49963a05073b0376ce616da03341897ed4",
    "paper_final_validation/07_v021_revision/final_source_historical_regression_summary.json": "f9e7088ffd36cad3c68b4311f9c5710c95e17bf2e7c4564d752073fa07c7b74e",
    "paper_final_validation/07_v021_revision/final_source_exact_oracle_summary.json": "ae9eab401f677b05a956add1efbee1cf224998ae431d4428991d87afd725770b",
    "paper_final_validation/07_v021_revision/v021_v5_final_holdout_manifest.json": "b241575cb0f0bbccf54c2d166af1587655234a6c21fb4af2041a8fba5af0a7f1",
    "paper_final_validation/07_v021_revision/v021_v5_final_holdout_results.json": "f1a43881fa03df2de238cd182e41db22aad4fb7d77dcf2096f9f8d7f9c792043",
    "paper_final_validation/07_v021_revision/v021_v5_final_holdout_summary.json": "c55eab61f007fe85916622d8411cad11007578ca1c865f622fcd696befc083fc",
    "paper_final_validation/07_v021_revision/v021_v5_exact_oracle_summary.json": "481468dc87c82a50d0eaba102bbba8f4c054069805c70f48500b2e6ac8938f1e",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include_revision_file(path: Path) -> bool:
    relative = path.as_posix().lower()
    name = path.name.lower()
    if "__pycache__" in path.parts or "checkpoint" in relative:
        return False
    if "smoke" in name or "diagnostic" in name:
        return False
    return path.is_file()


def collect_files(source_root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    revision = source_root / "paper_final_validation" / "07_v021_revision"
    for path in sorted(revision.rglob("*")):
        if include_revision_file(path):
            files[path.relative_to(source_root).as_posix()] = path

    historical = source_root / "paper_final_validation" / "02_prespecified_holdout"
    historical_names = {
        "CONFIG.json",
        "REPORT.md",
        "holdout_case_results.csv",
        "holdout_manifest.json",
        "holdout_manifest.sha256",
        "holdout_results.json",
        "holdout_selected_probes.csv",
        "holdout_summary.json",
        "run_holdout.py",
    }
    for name in sorted(historical_names):
        path = historical / name
        files[path.relative_to(source_root).as_posix()] = path

    oracle = source_root / "paper_final_validation" / "03_exact_probe_count_oracle"
    oracle_names = {
        "CONFIG.json",
        "REPORT.md",
        "exact_oracle_case_results.csv",
        "exact_oracle_results.json",
        "exact_oracle_summary.json",
        "run_exact_oracle.py",
    }
    for name in sorted(oracle_names):
        path = oracle / name
        files[path.relative_to(source_root).as_posix()] = path
    for path in sorted((oracle / "models").glob("*.json.gz")):
        files[path.relative_to(source_root).as_posix()] = path

    for name in ("FINAL_VALIDATION_SUMMARY.md", "PROVENANCE.md", "README.md"):
        path = source_root / "paper_final_validation" / name
        files[path.relative_to(source_root).as_posix()] = path
    return files


def add_bytes(archive: zipfile.ZipFile, member: str, data: bytes) -> None:
    info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compresslevel=9)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--historical-clean-manifest", type=Path, required=True)
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(source_root)
    files["external_inputs/historical_clean_locked_manifest.json"] = args.historical_clean_manifest.resolve()
    files["external_inputs/planning_profile_artifact_manifest.json"] = args.profile_manifest.resolve()

    missing = [member for member, path in files.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing immutable evidence inputs: {missing}")

    entries = []
    payloads: dict[str, bytes] = {}
    for member, path in sorted(files.items()):
        data = path.read_bytes()
        digest = sha256_bytes(data)
        expected = EXPECTED_HASHES.get(member)
        if expected is not None and digest != expected:
            raise RuntimeError(f"Frozen evidence hash mismatch for {member}: {digest} != {expected}")
        payloads[member] = data
        entries.append({"path": member, "sha256": digest, "size": len(data)})

    manifest = {
        "schemaVersion": "probeplanner.paper_immutable_evidence.v1",
        "releaseTag": "v0.2.1-paper",
        "scope": "byte-preserved validation evidence; local paths inside frozen records are provenance only",
        "files": entries,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    archive_path = output_dir / ZIP_NAME
    with zipfile.ZipFile(archive_path, "w") as archive:
        for member, data in sorted(payloads.items()):
            add_bytes(archive, member, data)
        add_bytes(archive, MANIFEST_NAME, manifest_bytes)

    public_manifest = {
        **manifest,
        "archive": ZIP_NAME,
        "archiveSha256": sha256_file(archive_path),
        "embeddedManifestSha256": sha256_bytes(manifest_bytes),
    }
    (output_dir / MANIFEST_NAME).write_text(
        json.dumps(public_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "archive": str(archive_path),
        "archiveSha256": public_manifest["archiveSha256"],
        "fileCount": len(entries),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
