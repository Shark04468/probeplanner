#!/usr/bin/env python3
"""Fail-closed checks for the v0.2.1 paper source and release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ABSOLUTE_WINDOWS_PATH = re.compile(rb"(?i)(?:[A-Z]:\\(?:Users|mri_ccf|Documents and Settings)\\)")
FORBIDDEN_PARTS = {
    "node_modules", "target", "build", "dist", ".cache", ".playwright-cli",
    "__pycache__", ".pytest_cache", ".probeplanner",
}
TEXT_SUFFIXES = {
    ".c", ".cc", ".cff", ".cpp", ".csv", ".css", ".html", ".ini", ".js",
    ".json", ".jsx", ".md", ".mjs", ".ps1", ".py", ".rs", ".svg", ".toml",
    ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
EXPECTED_SOURCE_HASHES = {
    "apps/backend-compat/src/optimizer_server.py": "dec6c2153b4d3ff2d3241ee12ec00abea856795828f9a2b19be85bc56cea065c",
    "apps/backend-compat/scripts/audit_registration_perturbation_sensitivity.py": "d3fc5d2e67f170bea96d98d883c0a6af7d10970fb2379131dbaf84a0c0627b7e",
    "apps/backend-compat/scripts/benchmark_auto_probe_count_planning.py": "baf65f012f8339fc0e1fa070415451dc71bf25aa1ede3e8d1f6a8dcf86fa658c",
    "apps/backend-compat/tests/test_validation_scripts.py": "e7bce7c50babf65b7bea5ad31e02dfb69d4adec6bb8844caa677dcc53de522cc",
}
V4_MANIFEST = "paper_final_validation/07_v021_revision/v021_v4_final_source_holdout_manifest.json"
V5_MANIFEST = "paper_final_validation/07_v021_revision/v021_v5_final_holdout_manifest.json"
V5_CONTRACT = "paper_final_validation/07_v021_revision/v021_v5_final_holdout_run_contract.json"
V5_EXACT_CONTRACT = "paper_final_validation/07_v021_revision/v021_v5_exact_oracle_contract.json"


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_evidence(release_dir: Path, errors: list[str]) -> None:
    manifest_path = release_dir / "IMMUTABLE_EVIDENCE_MANIFEST.json"
    if not manifest_path.is_file():
        errors.append("immutable evidence manifest missing")
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = release_dir / str(manifest.get("archive", ""))
    if not archive_path.is_file():
        errors.append("immutable evidence archive missing")
        return
    if hash_file(archive_path) != manifest.get("archiveSha256"):
        errors.append("immutable evidence archive SHA-256 mismatch")
        return

    expected_members = {row["path"]: row for row in manifest.get("files", [])}
    with zipfile.ZipFile(archive_path) as archive:
        actual_members = {
            name for name in archive.namelist() if name != "IMMUTABLE_EVIDENCE_MANIFEST.json"
        }
        if actual_members != set(expected_members):
            errors.append("immutable evidence member set mismatch")
            return
        for name, row in expected_members.items():
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != row["sha256"] or len(data) != row["size"]:
                errors.append(f"immutable evidence member mismatch: {name}")

        def load(name: str) -> dict:
            return json.loads(archive.read(name))

        if hashlib.sha256(archive.read(V4_MANIFEST)).hexdigest() != (
            "b3921fe300995a3a1faa061ef2635be57cfcfa903445679eb9c5b0b6bd0036af"
        ):
            errors.append("v4 frozen manifest changed")
        if hashlib.sha256(archive.read(V5_MANIFEST)).hexdigest() != (
            "b241575cb0f0bbccf54c2d166af1587655234a6c21fb4af2041a8fba5af0a7f1"
        ):
            errors.append("v5 frozen manifest changed")

        v4 = load("paper_final_validation/07_v021_revision/v021_v4_final_source_holdout_summary.json")
        if (
            v4.get("fullCoverageCount"), v4.get("partialCoverageCount"),
            v4.get("failClosedCount"), v4.get("acceptanceGate"),
        ) != (16, 3, 1, "FAIL"):
            errors.append("preserved v4 first-execution facts changed")
        remediation = load(
            "paper_final_validation/07_v021_revision/v021_v4_targeted_mirror_remediation_results.json"
        )
        if remediation.get("interpretation") != "post_hoc_remediation_not_untouched":
            errors.append("v4 remediation is no longer marked post-hoc")
        v5 = load("paper_final_validation/07_v021_revision/v021_v5_final_holdout_summary.json")
        if (
            v5.get("fullCoverageCount"), v5.get("partialCoverageCount"),
            v5.get("failClosedCount"), v5.get("errorCount"), v5.get("acceptanceGate"),
        ) != (19, 0, 1, 0, "PASS"):
            errors.append("v5 untouched summary changed")
        v5_exact = load("paper_final_validation/07_v021_revision/v021_v5_exact_oracle_summary.json")
        if (
            v5_exact.get("exactCertifiedCaseCount"),
            v5_exact.get("probePlannerExactMatchCount"),
            v5_exact.get("timeoutCount"),
            v5_exact.get("acceptanceGate"),
        ) != (19, 19, 0, "PASS"):
            errors.append("v5 exact-oracle summary changed")
        historical = load(
            "paper_final_validation/07_v021_revision/final_source_exact_oracle_summary.json"
        )
        if (
            historical.get("exactCertifiedCaseCount"),
            historical.get("probePlannerExactMatchCount"),
            historical.get("timeoutCount"),
            historical.get("acceptanceGate"),
        ) != (48, 48, 0, "PASS"):
            errors.append("historical exact regression summary changed")

        for contract_name in (V5_CONTRACT, V5_EXACT_CONTRACT):
            contract = load(contract_name)
            for relative, expected_hash in contract.get("sourceFiles", {}).items():
                source = ROOT / relative
                if source.is_file() and hash_file(source) != expected_hash:
                    errors.append(f"frozen contract source mismatch: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--tag", default="v0.2.1-paper")
    parser.add_argument("--paper-release-dir", type=Path)
    args = parser.parse_args()
    errors: list[str] = []

    has_git = (ROOT / ".git").exists()
    if has_git:
        listed = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
        paths = [ROOT / item for item in listed if item]
    else:
        paths = [
            path for path in ROOT.rglob("*")
            if path.is_file() and not any(
                part in FORBIDDEN_PARTS for part in path.relative_to(ROOT).parts
            )
        ]
    for path in paths:
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PARTS for part in relative.parts):
            errors.append(f"forbidden generated path: {relative.as_posix()}")
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            if ABSOLUTE_WINDOWS_PATH.search(path.read_bytes()):
                errors.append(f"absolute Windows path: {relative.as_posix()}")

    case_manifest = json.loads((ROOT / "cases" / "manifest.json").read_text(encoding="utf-8"))
    for case in case_manifest.get("cases", []):
        request = ROOT / "cases" / "requests" / f"{case['id']}.json"
        if not request.exists():
            errors.append(f"manifest request missing: {request.relative_to(ROOT).as_posix()}")

    supplement = ROOT / "supplementary-data"
    supplement_manifest = json.loads((supplement / "manifest.json").read_text(encoding="utf-8"))
    if supplement_manifest.get("releaseTag") != "v0.2.0-paper":
        errors.append("historical v0.2.0 Supplementary Data tag changed")
    for item in supplement_manifest.get("files", []):
        path = supplement / item["path"]
        if not path.exists() or hash_file(path) != item["sha256"]:
            errors.append(f"historical Supplementary Data checksum mismatch: {item['path']}")

    for relative, expected in EXPECTED_SOURCE_HASHES.items():
        if hash_file(ROOT / relative) != expected:
            errors.append(f"final source SHA-256 mismatch: {relative}")

    expected_versions = {"package.json": "0.2.1", "apps/ui/package.json": "0.2.1"}
    for relative, expected in expected_versions.items():
        actual = json.loads((ROOT / relative).read_text(encoding="utf-8"))["version"]
        if actual != expected:
            errors.append(f"version mismatch in {relative}: {actual} != {expected}")
    rust_versions = {
        "planner-engine/Cargo.toml": 'version = "0.2.1"',
        "bindings/python/Cargo.toml": 'version = "0.2.1"',
        "apps/desktop-tauri/src-tauri/Cargo.toml": 'version = "0.2.1"',
        "apps/desktop-tauri/src-tauri/tauri.conf.json": '"version": "0.2.1"',
    }
    for relative, expected_text in rust_versions.items():
        if expected_text not in (ROOT / relative).read_text(encoding="utf-8"):
            errors.append(f"version mismatch in {relative}")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    if 'version: "0.2.1-paper"' not in citation:
        errors.append("CITATION.cff version is not 0.2.1-paper")
    zenodo = json.loads((ROOT / ".zenodo.json").read_text(encoding="utf-8"))
    if zenodo.get("version") != "0.2.1-paper":
        errors.append(".zenodo.json version is not 0.2.1-paper")
    if zenodo.get("creators", [{}])[0].get("affiliation") != (
        "University of Shanghai for Science and Technology"
    ):
        errors.append("Zenodo creator affiliation mismatch")

    if args.paper_release_dir:
        validate_evidence(args.paper_release_dir.resolve(), errors)
    if args.require_clean and not has_git:
        errors.append("--require-clean requires a Git checkout")
    if args.require_clean and has_git and git("status", "--porcelain"):
        errors.append("git worktree is not clean")
    tags = git("tag", "--points-at", "HEAD").splitlines() if has_git else []
    if args.require_clean and has_git and args.tag not in tags:
        errors.append(f"HEAD is not tagged {args.tag}")

    if errors:
        print("Release verification FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Release verification passed for {len(paths)} publishable files and {args.tag}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
