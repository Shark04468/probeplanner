"""External CI orchestration for the unmodified v0.3.0-paper source."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

SOURCE = "56868d57c23cbbb1207666f5e420bf47019591c6"
OPTIMIZER = "5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9"
BUNDLE = "f3ab8b854a6f665ad484ea7ae95c41d6212fafb92099044949ec84cec171ad9a"
PROFILE = "3580f40eb42b475319ea62796ef8c7b8789b1d1110a9ca4638f8e2168b09c677"
BASE = "https://github.com/Shark04468/probeplanner/releases/download/v0.3.0-paper"
EVIDENCE = {
    "PUBLIC_EVIDENCE_MANIFEST.json": (955886, "ff539dcc0781061acd4a8d15f0e2b3d1fc8d66d1eb2c603db2d5f142b3e614a2"),
    "PUBLIC_VALIDATION_EVIDENCE.zip": (43539684, "7c12730fe141dbf52bb55f2fe663d98f1414a7601185797fb0276e398a21890e"),
}
CASES = ["retrospective/search_unilateral_ca1", "confirmation/independent_request_01",
         "confirmation/reused_negative_control"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "data"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    source, out, data = [p.resolve() for p in (args.source, args.output, args.data)]
    paths = [source, out, data]
    require(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(paths) for b in paths[i + 1:]), "Separate roots required")
    require(not out.exists() and not data.exists(), "Preserve previous output directories")
    out.mkdir(parents=True)
    data.mkdir(parents=True)
    report = dict(status="FAIL", source_commit=SOURCE, stages=[],
                  real_browser_3d_verified=False, interactive_export_verified=False,
                  full_macos_ui_workflow_accepted=False, anatomy_accepted=False,
                  new_untouched_evidence=False, paper_release_modified=False)

    def step(name, command, timeout):
        print("START " + name, flush=True)
        start = time.monotonic()
        record = dict(name=name, command=command, timeout_seconds=timeout)
        with (out / (name + ".log")).open("x", encoding="utf-8") as log:
            try:
                result = subprocess.run(command, cwd=source, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=timeout)
                record["returncode"] = result.returncode
            except subprocess.TimeoutExpired:
                record.update(returncode=None, timed_out=True)
        record.update(seconds=time.monotonic() - start, log_sha256=sha(out / (name + ".log")))
        write(out / (name + ".step.json"), record)
        report["stages"].append(record)
        require(record["returncode"] == 0, "Failed stage: " + name)
        print("PASS " + name, flush=True)

    try:
        observed = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        require(observed == SOURCE, "Incorrect frozen commit")
        require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=source,
                                            text=True).strip(), "Frozen checkout is dirty")
        require(sha(source / "apps/backend-compat/src/optimizer_server.py") == OPTIMIZER,
                "Optimizer byte identity mismatch")
        require(sys.version_info[:3] == (3, 10, 20), "Python version differs")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        require(npm, "npm unavailable")
        require(subprocess.check_output(["node", "--version"], text=True).strip() == "v24.14.0",
                "Node version differs")
        write(out / "RUN_CONTRACT.json", dict(
            created_utc=datetime.now(timezone.utc).isoformat(), source_commit=SOURCE,
            harness_commit=os.environ.get("GITHUB_SHA"), driver_sha256=sha(__file__),
            optimizer_sha256=OPTIMIZER, bundle_sha256=BUNDLE, profile_sha256=PROFILE,
            cases=CASES, python=sys.version, platform=platform.platform(), machine=platform.machine(),
            runner={k: os.environ.get(k) for k in ("RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
                   "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")},
            expected_original_tests=112, expected_backend_tests=329, expected_frontend_tests=152,
            expected_surface_tests=6, scope="Frozen software, profile startup preflight, candidate/exact replay and machine-readable evidence. No real browser or GUI export qualification."))
        step("source_identity", [sys.executable, "scripts/release/verify_source_v030.py",
                                "--output", str(out / "SOURCE_IDENTITY.json")], 180)
        step("bootstrap", ["uv", "pip", "install", "--python", sys.executable,
                           "pip==23.0.1", "setuptools==79.0.1", "wheel==0.48.0"], 300)
        step("install_python", [sys.executable, "-m", "pip", "install", "--no-cache-dir",
                                "--report", str(out / "INSTALL_REPORT.json"), "-r",
                                "apps/backend-compat/requirements-surface-build.txt"], 1500)
        step("pip_check", [sys.executable, "-m", "pip", "check"], 120)
        step("environment", [sys.executable, "-m", "pip", "freeze", "--all"], 120)
        step("install_root", [npm, "ci", "--no-audit", "--no-fund"], 600)
        step("install_ui", [npm, "--prefix", "apps/ui", "ci", "--no-audit", "--no-fund"], 600)
        step("download_profile", [sys.executable, "scripts/release/fetch_public_profile.py",
             "--base-url", BASE, "--manifest-sha256", BUNDLE, "--downloads", str(data / "downloads"),
             "--profile", str(data / "profile"), "--report", str(out / "PROFILE_DOWNLOAD.json")], 1500)
        sys.path.insert(0, str(source / "scripts/release"))
        from fetch_public_profile import download
        evidence = data / "evidence"
        evidence.mkdir()
        for name, (size, digest) in EVIDENCE.items():
            download(BASE + "/" + name, evidence / name, digest, size=size)
        write(out / "EVIDENCE_DOWNLOAD.json", EVIDENCE)
        step("launcher_preflight", [sys.executable, "apps/backend-compat/scripts/start_paper_backend.py",
             "--profile", str(data / "profile"), "--run-dir", str(data / "startup"), "--check-only"], 600)
        command = [sys.executable, "scripts/release/replay_public_campaign.py", "--evidence-dir",
                   str(evidence), "--profile", str(data / "profile"), "--output", str(out / "replay")]
        for case in CASES:
            command.extend(["--case", case])
        step("candidate_exact_replay", command, 3600)
        step("unchanged_release_gates", [sys.executable, "scripts/release/run_release_checks_v030.py",
             "--evidence-dir", str(evidence), "--profile", str(data / "profile"), "--replay-dir",
             str(out / "replay"), "--surface-python", sys.executable,
             "--output", str(out / "checks"), "--require-clean"], 3600)
        summary = json.loads((out / "checks/SUMMARY.json").read_text(encoding="utf-8"))
        require(summary.get("localReleaseValidationPassed") is True
                and summary.get("sourceAndInputsUnchanged") is True, "Final gate failure")
        report.update(status="PASS_FROZEN_SOFTWARE_AND_EXACT_REPLAY", checks_summary=summary)
    except Exception as exc:
        report.update(error=type(exc).__name__ + ": " + str(exc), traceback=traceback.format_exc())
    finally:
        for path in (data / "startup").glob("*.json"):
            target = out / "startup_records" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(path.read_bytes())
        report["files"] = {p.relative_to(out).as_posix(): sha(p)
                           for p in sorted(out.rglob("*")) if p.is_file()}
        write(out / "RESULTS.json", report)
        print(json.dumps({k: report[k] for k in ("status", "real_browser_3d_verified",
                                                "full_macos_ui_workflow_accepted")}), flush=True)
    return 0 if report["status"] == "PASS_FROZEN_SOFTWARE_AND_EXACT_REPLAY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
