"""Execute the frozen three-case public-profile replay on an already qualified Mac."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

from qualify_macos import SOURCE_COMMIT, junit_summary, require, run_step, sha, write

BASE_URL = "https://github.com/Shark04468/probeplanner/releases/download/v0.3.0-paper"
BUNDLE_SHA = "f3ab8b854a6f665ad484ea7ae95c41d6212fafb92099044949ec84cec171ad9a"
PROFILE_SHA = "3580f40eb42b475319ea62796ef8c7b8789b1d1110a9ca4638f8e2168b09c677"
EVIDENCE = {
    "PUBLIC_EVIDENCE_MANIFEST.json": (955886, "ff539dcc0781061acd4a8d15f0e2b3d1fc8d66d1eb2c603db2d5f142b3e614a2"),
    "PUBLIC_VALIDATION_EVIDENCE.zip": (43539684, "7c12730fe141dbf52bb55f2fe663d98f1414a7601185797fb0276e398a21890e"),
}
CASES = (
    "retrospective/search_unilateral_ca1",
    "confirmation/independent_request_01",
    "confirmation/reused_negative_control",
)


def validate_summary(summary):
    require(summary.get("reproductionPassed") is True, "Replay reproduction failed")
    require(summary.get("integrityPostflightPassed") is True, "Replay postflight failed")
    require((summary.get("completed"), summary.get("positive"), summary.get("negative")) == (3, 2, 1),
            "Wrong replay role/counts")
    rows = summary.get("cases", [])
    require(len(rows) == 3 and {r["case"] for r in rows} == set(CASES), "Replay cases differ")
    require(all(r.get("passed") is True for r in rows), "A replay case failed")
    require(summary.get("newUntouchedEvidence") is False, "Replay is not untouched evidence")
    require(summary.get("fullRequestedCohorts") is False, "Not a full-cohort rerun")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    source, qualification, out, data = (p.resolve() for p in (
        args.source, args.qualification, args.output, args.data))
    paths = (source, qualification, out, data)
    require(all(a != b and a not in b.parents and b not in a.parents
                for i, a in enumerate(paths) for b in paths[i + 1:]), "Use separate roots")
    out.mkdir(parents=True, exist_ok=False)
    report = dict(status="FAIL", source_commit=SOURCE_COMMIT, steps=[],
                  full_macos_workflow_accepted=False, anatomy_accepted=False,
                  new_untouched_evidence=False, manuscript_or_release_changed=False)
    try:
        qualified = json.loads((qualification / "RESULTS.json").read_text())
        require(qualified["status"] == "PASS_PREREQUISITES_ONLY", "Prerequisites not qualified")
        for name, expected in qualified["files"].items():
            require(sha(qualification / name) == expected, "Qualification member differs")
        junit_summary(qualification / "BACKEND_TESTS.xml")
        require(platform.system() == "Darwin", "Require actual macOS")
        require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source,
                    text=True).strip() == SOURCE_COMMIT, "Frozen source differs")
        write(out / "RUN_CONTRACT.json", dict(
            created_utc=datetime.now(timezone.utc).isoformat(),
            role="three_case_portability_replay_not_untouched_confirmation",
            os=platform.platform(), architecture=platform.machine(),
            source_commit=SOURCE_COMMIT, harness_commit=os.environ.get("GITHUB_SHA"),
            prerequisite_sha256=sha(qualification / "RESULTS.json"), driver_sha256=sha(__file__),
            helper_sha256=sha(Path(__file__).with_name("qualify_macos.py")),
            expected_profile_sha256=PROFILE_SHA, bundle_sha256=BUNDLE_SHA,
            case_ids=CASES, scope="Live profile startup and candidate/exact replay; browser/export still required."))
        data.mkdir(parents=True, exist_ok=False)
        report["steps"].append(run_step(out, source, "fetch_profile", [
            sys.executable, "scripts/release/fetch_public_profile.py", "--base-url", BASE_URL,
            "--manifest-sha256", BUNDLE_SHA, "--downloads", str(data / "downloads"),
            "--profile", str(data / "profile"), "--report", str(out / "PROFILE_DOWNLOAD.json")], 1500))
        sys.path.insert(0, str(source / "scripts/release"))
        from fetch_public_profile import download
        evidence = data / "evidence"
        evidence.mkdir()
        for name, (size, expected) in EVIDENCE.items():
            download(BASE_URL + "/" + name, evidence / name, expected, size=size)
        write(out / "EVIDENCE_DOWNLOAD.json", {name: dict(bytes=size, sha256=expected)
                                             for name, (size, expected) in EVIDENCE.items()})
        report["steps"].append(run_step(out, source, "cold_start", [sys.executable,
            "apps/backend-compat/scripts/start_paper_backend.py", "--profile", str(data / "profile"),
            "--run-dir", str(data / "startup"), "--check-only"], 600))
        runtime = json.loads((data / "startup/runtime.json").read_text())
        require(runtime["profileContractSha256"] == PROFILE_SHA, "Runtime profile mismatch")
        require(runtime["manifestEnforced"] is True and runtime["pathConsistencyPassed"] is True
                and runtime["cacheInitiallyEmpty"] is True, "Runtime did not enforce the contract")
        require(runtime["optimizerSha256"] == "5439589a385d161a457cc4899e89cdc999712798c8dd4d8440a1c4657144a0f9",
                "Runtime optimizer differs")
        command = [sys.executable, "scripts/release/replay_public_campaign.py", "--evidence-dir",
            str(evidence), "--profile", str(data / "profile"), "--output", str(out / "replay")]
        for case in CASES:
            command.extend(["--case", case])
        report["steps"].append(run_step(out, source, "candidate_exact_replay", command, 3600))
        summary = json.loads((out / "replay/SUMMARY.json").read_text())
        validate_summary(summary)
        report["replay_summary"] = summary
        require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=source,
                                           text=True).strip(), "Source changed")
        report["status"] = "PASS_STARTUP_AND_THREE_CASE_REPLAY_ONLY"
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        # Keep actual startup records, including failures, but not regenerable cache volumes.
        if (data / "startup").exists():
            for p in (data / "startup").glob("*.json"):
                target = out / "startup_records" / p.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(p.read_bytes())
        report["files"] = {p.relative_to(out).as_posix(): sha(p)
                           for p in sorted(out.rglob("*")) if p.is_file()}
        report["ephemeral_input_inventory"] = {
            p.relative_to(data).as_posix(): dict(bytes=p.stat().st_size, sha256=sha(p))
            for p in sorted(data.rglob("*")) if p.is_file() and "cache" not in p.parts
        } if data.exists() else {}
        write(out / "RESULTS.json", report)
        print(json.dumps({k: report[k] for k in ("status", "full_macos_workflow_accepted")}))
    return 0 if report["status"] == "PASS_STARTUP_AND_THREE_CASE_REPLAY_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
