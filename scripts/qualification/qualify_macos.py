"""Run prerequisite tests on actual Mac hosts; not full UI/planning acceptance."""
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
import xml.etree.ElementTree as ET

SOURCE_COMMIT = "56868d57c23cbbb1207666f5e420bf47019591c6"
EXPECTED_TESTS = 112
LOCK = "apps/backend-compat/requirements-lock-py310.txt"
TEST_FILES = (
    "apps/backend-compat/tests/test_optimizer_server.py",
    "apps/backend-compat/tests/test_validation_scripts.py",
)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def junit_summary(path, expected=EXPECTED_TESTS):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    identities = [(c.get("classname"), c.get("name")) for c in cases]
    counts = {"executed": len(cases)}
    for tag in ("failure", "error", "skipped"):
        counts[tag] = sum(len(c.findall(tag)) for c in cases)
    require(len(set(identities)) == len(cases), "Duplicate JUnit test identity")
    require(all(name for _, name in identities), "Unnamed JUnit test")
    require(counts["executed"] == expected, "Unexpected executed test count")
    require(all(counts[k] == 0 for k in ("failure", "error", "skipped")),
            "JUnit contains a failure, error or skip")
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    require(bool(suites), "No JUnit suite")
    require(sum(int(s.get("tests", "-1")) for s in suites) == len(cases),
            "JUnit declared/actual test counts differ")
    for attr in ("failures", "errors", "skipped"):
        require(all(int(s.get(attr, "0")) == 0 for s in suites),
                "JUnit suite reports nonzero " + attr)
    return counts


def run_step(out, source, name, command, timeout):
    start = time.monotonic()
    record = {"name": name, "command": command, "timeout_seconds": timeout}
    print("START " + name, flush=True)
    with (out / (name + ".log")).open("x", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, cwd=source, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            record["returncode"] = result.returncode
        except subprocess.TimeoutExpired:
            record.update(returncode=None, timeout=True)
    record["seconds"] = time.monotonic() - start
    record["log_sha256"] = sha(out / (name + ".log"))
    write(out / (name + ".step.json"), record)
    print("DONE " + name + " returncode=" + str(record["returncode"]), flush=True)
    require(record["returncode"] == 0, "Failed step: " + name)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", choices=("x86_64", "arm64"), required=True)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.output.resolve()
    require(not out.exists(), "Output exists; preserve earlier attempts")
    require(source not in out.parents and out not in source.parents and source != out,
            "Separate the source and output")
    out.mkdir(parents=True)
    report = dict(status="FAIL", source_commit=SOURCE_COMMIT,
                  architecture_requested=args.architecture, steps=[],
                  full_macos_workflow_accepted=False, new_untouched_evidence=False,
                  manuscript_or_release_changed=False)
    try:
        observed_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=source, text=True).strip()
        contract = dict(created_utc=datetime.now(timezone.utc).isoformat(),
            role="actual_macos_prerequisite_qualification", source_commit=observed_commit,
            dirty_before=dirty, driver_sha256=sha(__file__), lock_sha256=sha(source / LOCK),
            test_sha256={p: sha(source / p) for p in TEST_FILES},
            expected_test_count=EXPECTED_TESTS, expected_python="3.10.20",
            os=platform.platform(), system=platform.system(), machine=platform.machine(),
            python=sys.version, mac_version=platform.mac_ver(),
            free_disk_bytes=shutil.disk_usage(out).free,
            github={k: os.environ.get(k) for k in (
                "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "RUNNER_ARCH",
                "RUNNER_OS", "ImageOS", "ImageVersion")},
            scope="No data replay, live browser, export or human-usability acceptance yet.")
        write(out / "RUN_CONTRACT.json", contract)
        require(observed_commit == SOURCE_COMMIT and not dirty, "Frozen source mismatch")
        require(platform.system() == "Darwin", "Must execute on an actual macOS host")
        require(platform.machine() == args.architecture, "Architecture mismatch")
        require(sys.version_info[:3] == (3, 10, 20), "Python version mismatch")
        steps = [
            ("source_identity", [sys.executable, "scripts/release/verify_source_v030.py",
                "--output", str(out / "SOURCE_IDENTITY.json")], 180),
            ("bootstrap", ["uv", "pip", "install", "--python", sys.executable,
                "pip==23.0.1", "setuptools==79.0.1", "wheel==0.48.0"], 180),
            ("install", [sys.executable, "-m", "pip", "install", "--no-cache-dir",
                "--report", str(out / "INSTALL_REPORT.json"), "-r", LOCK], 1500),
            ("pip_check", [sys.executable, "-m", "pip", "check"], 90),
            ("freeze", [sys.executable, "-m", "pip", "freeze", "--all"], 90),
            ("imports", [sys.executable, "-c",
                "import numpy, scipy, flask, allensdk, nibabel, ants, SimpleITK; "
                "print('Frozen runtime imports passed')"], 180),
            ("backend_tests", [sys.executable, "-m", "pytest", *TEST_FILES,
                "-q", "--junitxml=" + str(out / "BACKEND_TESTS.xml")], 600),
        ]
        for name, command, timeout in steps:
            report["steps"].append(run_step(out, source, name, command, timeout))
        report["tests"] = junit_summary(out / "BACKEND_TESTS.xml")
        report["dirty_after"] = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=source, text=True).strip()
        require(not report["dirty_after"], "Frozen source changed during qualification")
        report["status"] = "PASS_PREREQUISITES_ONLY"
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        report["files"] = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
        write(out / "RESULTS.json", report)
        print(json.dumps({k: report[k] for k in (
            "status", "full_macos_workflow_accepted", "source_commit")}), flush=True)
    return 0 if report["status"] == "PASS_PREREQUISITES_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())

