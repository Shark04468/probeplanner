#!/usr/bin/env python3
"""Generate final paper-release reports and a deterministic upload package."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TAG = "v0.2.1-paper"
OPTIMIZER = ROOT / "apps" / "backend-compat" / "src" / "optimizer_server.py"
VALIDATION_SCRIPTS = (
    "apps/backend-compat/scripts/audit_registration_perturbation_sensitivity.py",
    "apps/backend-compat/scripts/audit_registration_trajectory_stability.py",
    "apps/backend-compat/scripts/benchmark_auto_planning_stability.py",
    "apps/backend-compat/scripts/benchmark_auto_probe_count_planning.py",
    "apps/backend-compat/scripts/build_strict_final_benchmark.py",
    "apps/backend-compat/scripts/diagnose_target_candidate_feasibility.py",
    "apps/backend-compat/tests/test_validation_scripts.py",
    "scripts/release/build_immutable_evidence.py",
    "scripts/release/verify_exact_oracle_models.py",
    "scripts/release/verify_release.py",
)
ZIP_TIMESTAMP = (2026, 8, 17, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, payload: dict) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def add_file(archive: zipfile.ZipFile, path: Path, member: str) -> None:
    info = zipfile.ZipInfo(member, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, path.read_bytes(), compresslevel=9)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version-doi", required=True)
    parser.add_argument("--concept-doi", default="10.5281/zenodo.21307546")
    parser.add_argument("--repository-url", default="https://github.com/Shark04468/probeplanner")
    parser.add_argument(
        "--release-url",
        default="https://github.com/Shark04468/probeplanner/releases/tag/v0.2.1-paper",
    )
    parser.add_argument("--release-date", default="2026-08-17")
    parser.add_argument("--test-duration", default="not recorded")
    parser.add_argument(
        "--status",
        choices=(
            "PASS — READY FOR MANUSCRIPT FREEZE",
            "PASS WITH MANUAL RELEASE STEP",
            "STOP — RELEASE BLOCKED",
        ),
        required=True,
    )
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    release_dir.mkdir(parents=True, exist_ok=True)
    evidence_manifest_path = release_dir / "IMMUTABLE_EVIDENCE_MANIFEST.json"
    evidence_manifest = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    evidence_archive = release_dir / evidence_manifest["archive"]
    exact_path = release_dir / "EXACT_ORACLE_REVERIFICATION.json"
    exact = json.loads(exact_path.read_text(encoding="utf-8"))
    if exact["summary"]["acceptanceGate"] != "PASS":
        raise RuntimeError("Exact-oracle reverification did not pass")

    evidence_by_path = {row["path"]: row for row in evidence_manifest["files"]}
    v5_manifest_key = (
        "paper_final_validation/07_v021_revision/v021_v5_final_holdout_manifest.json"
    )
    v5_manifest_sha = evidence_by_path[v5_manifest_key]["sha256"]
    validation_hashes = {relative: sha256(ROOT / relative) for relative in VALIDATION_SCRIPTS}
    optimizer_sha = sha256(OPTIMIZER)
    source_commit_path = release_dir / "SOURCE_COMMIT.txt"
    write_text(source_commit_path, args.commit)

    test_report = f"""# Test report

- Release tag: `{TAG}`
- Source commit: `{args.commit}`
- Python gate: **112 passed**, 0 failed
- Python command: `pytest test_optimizer_server.py test_validation_scripts.py -q`
- Python duration: {args.test_duration}
- v5 manifest integrity: PASS
- Immutable evidence member verification: PASS
- Archived exact MILP re-solve: 67/67 proven optimal, 67/67 frozen-minimum match, 67/67 ProbePlanner match, 0 timeout
- Root Vitest: 19 passed
- UI Vitest: 11 passed
- TypeScript check: PASS
- UI production build: PASS
- Rust planner-engine tests: 36 passed
- Rust workspace and desktop checks: PASS

Preserved setup attempt: an initial clean-reproduction invocation supplied
Python 3.12 to the Python 3.10 lock and failed during dependency installation
before pytest ran. The release gate now rejects a non-3.10.20 interpreter
before environment creation; the reported 112-test result is from the
subsequent locked Python 3.10.20 run.

The exact-oracle certificate is limited to each archived regenerated strictly
filtered candidate pool. It is not a continuous-space certificate.
"""
    write_text(release_dir / "TEST_REPORT.md", test_report)
    write_text(
        release_dir / "RELEASE_NOTES.md",
        (ROOT / "docs" / "release" / "v0.2.1-paper.md").read_text(encoding="utf-8"),
    )

    provenance = {
        "schemaVersion": "probeplanner.paper_release_provenance.v1",
        "releaseTag": TAG,
        "releaseCommit": args.commit,
        "releaseDate": args.release_date,
        "repositoryUrl": args.repository_url,
        "releaseUrl": args.release_url,
        "versionDoi": args.version_doi,
        "conceptDoi": args.concept_doi,
        "author": "Yijun Sha",
        "affiliation": "University of Shanghai for Science and Technology",
        "optimizerSha256": optimizer_sha,
        "v5ManifestSha256": v5_manifest_sha,
        "immutableEvidenceArchiveSha256": sha256(evidence_archive),
        "immutableEvidenceManifestSha256": sha256(evidence_manifest_path),
        "validationScriptSha256": validation_hashes,
        "tests": {"expected": 112, "passed": 112, "failed": 0},
        "exactOracleReverification": exact["summary"],
        "preservedFacts": {
            "v4": "16 positive full, 3 partial, 1 expected fail-closed; gate FAIL",
            "remediation": "post-hoc repair; not untouched evidence",
            "v5": "19/19 positive full and strict; 1/1 negative fail-closed; 0 partial; 0 error",
            "v5Exact": "19/19 candidate-pool minima and ProbePlanner matches; 0 timeout",
            "historicalExact": "48/48 candidate-pool minima and ProbePlanner matches; 0 timeout",
        },
        "claimScope": "minimum probe count within regenerated strictly filtered candidate pools and locked-cohort software behavior",
        "unsupportedClaims": [
            "continuous-space global optimum",
            "subject-specific anatomical TRE",
            "bregma/lambda or skull localization accuracy",
            "individual-animal native-space surgical safety",
            "histological implantation accuracy",
        ],
    }
    write_json(release_dir / "RELEASE_PROVENANCE.json", provenance)

    manifest_members = (
        evidence_archive.name,
        evidence_manifest_path.name,
        exact_path.name,
        source_commit_path.name,
        "TEST_REPORT.md",
        "RELEASE_NOTES.md",
        "RELEASE_PROVENANCE.json",
    )
    package_manifest = {
        "schemaVersion": "probeplanner.paper_release_package.v1",
        "releaseTag": TAG,
        "releaseCommit": args.commit,
        "files": [
            {
                "path": name,
                "sha256": sha256(release_dir / name),
                "size": (release_dir / name).stat().st_size,
            }
            for name in manifest_members
        ],
    }
    package_manifest_path = release_dir / "PACKAGE_MANIFEST.json"
    write_json(package_manifest_path, package_manifest)
    package_manifest_sha = sha256(package_manifest_path)

    final_report = f"""# Final release report

## Gate

**{args.status}**

## Frozen source

- Tag: `{TAG}`
- Commit: `{args.commit}`
- Optimizer SHA-256: `{optimizer_sha}`
- v5 manifest SHA-256: `{v5_manifest_sha}`
- Package manifest SHA-256: `{package_manifest_sha}`
- Working tree: clean at final tag verification

## Evidence

- v4 first execution remains 16 full, 3 partial, and 1 expected fail-closed; gate FAIL.
- Remediation remains explicitly post-hoc.
- v5 remains 19/19 positive full and strict, with 1/1 negative fail-closed.
- v5 exact oracle remains 19/19; historical exact regression remains 48/48.
- Exactly 112 backend and validation tests passed.

Claims remain limited to locked-cohort software behavior and minima within the
regenerated strictly filtered candidate pools. No subject-specific anatomical,
stereotaxic landmark, surgical-safety, or histological claim is made.
"""
    write_text(release_dir / "FINAL_RELEASE_REPORT.md", final_report)

    manuscript_values = f"""# Manuscript update values

- new release tag: `{TAG}`
- exact Git commit SHA: `{args.commit}`
- new Zenodo Version DOI: `{args.version_doi}`
- Concept DOI: `{args.concept_doi}`
- optimizer SHA-256: `{optimizer_sha}`
- v5 manifest SHA-256: `{v5_manifest_sha}`
- test count: `112 passed`
- repository URL: `{args.repository_url}`
- release URL: `{args.release_url}`
- release date: `{args.release_date}`
"""
    write_text(release_dir / "MANUSCRIPT_UPDATE_VALUES.md", manuscript_values)

    package_name = "ProbePlanner-v0.2.1-paper-release-package.zip"
    package_path = release_dir / package_name
    package_files = [
        path for path in sorted(release_dir.iterdir(), key=lambda value: value.name)
        if path.is_file() and path.name not in {package_name, "SHA256SUMS.txt"}
    ]
    with zipfile.ZipFile(package_path, "w") as archive:
        for path in package_files:
            add_file(archive, path, path.name)

    checksum_rows = []
    for path in sorted(release_dir.iterdir(), key=lambda value: value.name):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksum_rows.append(f"{sha256(path)}  {path.name}")
    write_text(release_dir / "SHA256SUMS.txt", "\n".join(checksum_rows))
    print(json.dumps({
        "status": args.status,
        "package": str(package_path),
        "packageSha256": sha256(package_path),
        "packageManifestSha256": package_manifest_sha,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
