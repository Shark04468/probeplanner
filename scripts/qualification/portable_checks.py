"""Cross-platform orchestration; original tests and scientific checks are unmodified."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET


def packaging_counts(path, system):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    identities = [(c.get("classname"), c.get("name")) for c in cases]
    failures = [c for c in cases if c.find("failure") is not None or c.find("error") is not None]
    skipped = [c for c in cases if c.find("skipped") is not None]
    rows = [dict(classname=c.get("classname"), name=c.get("name"),
                 reason=c.find("skipped").get("message", "")) for c in skipped]
    expected = (("test_verify_source_v030", "test_symlink_not_followed", "Host does not permit unprivileged symlinks")
                if system == "win32" else
                ("test_verify_source_v030", "test_windows_junction_not_followed", "Windows reparse-point test"))
    allowed = (len(rows) == 1 and (rows[0]["classname"], rows[0]["name"]) == expected[:2]
               and rows[0]["reason"] == expected[2])
    suites = list(root.iter("testsuite"))
    suite_ok = bool(suites) and all(int(s.get("errors", "0")) == 0 and
                                  int(s.get("failures", "0")) == 0 for s in suites)
    suite_ok = suite_ok and sum(int(s.get("tests", "-1")) for s in suites) == len(cases)
    passed = (len(cases) == 203 and len(set(identities)) == 203 and not failures
              and suite_ok and (not rows or allowed))
    return dict(total=len(cases), passed=len(cases) - len(failures) - len(rows),
                failuresOrErrors=len(failures), skipped=len(rows), skippedTests=rows,
                notApplicableCount=len(rows) if allowed and system != "win32" else 0,
                countGatePassed=passed, originalTestsUnmodified=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "evidence-dir", "profile", "replay-dir", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    root, out = args.source.resolve(), args.output.resolve()
    assert not out.exists()
    out.mkdir(parents=True)
    sys.path.insert(0, str(root / "scripts/release"))
    import run_release_checks_v030 as original
    from prepare_public_profile import serialize, write_bytes
    result = dict(portableSoftwareVerificationPassed=False, sourceAndInputsUnchanged=False,
                  originalReleaseGateClaim=False, browser3dVerified=False)
    try:
        before = original.inventory(root)
        package = original.verify_package(root)
        git = original.verify_git(root, True, "v0.3.0-paper")
        environment = original.verify_environment(root)
        profile = original.inspect_profile(args.profile, original.PUBLIC_PROFILE_SHA256)
        replay = original.verify_replay(root, args.evidence_dir, args.profile, args.replay_dir)
        historical = root / "release-evidence/v0.2.1-paper"
        old_zip = historical / "ProbePlanner-v0.2.1-paper-immutable-evidence.zip"
        original.require(original.sha(old_zip) == original.OLD_ARCHIVE_SHA256 and
                         original.sha(historical / "IMMUTABLE_EVIDENCE_MANIFEST.json") == original.OLD_MANIFEST_SHA256,
                         "Historical evidence changed")
        write_bytes(out / "ENVIRONMENT.json", serialize(environment))
        write_bytes(out / "SOURCE_PACKAGE_CHECK.json", serialize(package))
        write_bytes(out / "PORTABLE_REPLAY_CHECK.json", serialize(replay))
        write_bytes(out / "RUN_CONTRACT.json", serialize(dict(git=git, sourceFiles=before,
            driverSha256=original.sha(__file__), originalReleaseDriverSha256=original.sha(Path(original.__file__)),
            originalTestsUnmodified=True, platform=sys.platform, packagingTotal=203,
            policy="Only the declared Windows-junction test is not applicable on non-Windows. The original Windows symlink-privilege policy remains. No skipped test is counted as passed.")))
        env = {k: v for k, v in os.environ.items() if not k.startswith("PROBEPLANNER_")}
        env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                   PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", CI="true")
        stages = {}
        stages["pip_check"] = original.execute(out, "pip_check", [sys.executable, "-m", "pip", "check"], root, env)
        for name, paths, count in [
            ("original_112", ["apps/backend-compat/tests/test_optimizer_server.py", "apps/backend-compat/tests/test_validation_scripts.py"], 112),
            ("backend_329", ["apps/backend-compat/tests"], 329),
            ("surface_builder_6", ["apps/backend-compat/scripts/tests"], 6),
            ("packaging", ["scripts/release/tests"], 203),
        ]:
            folder = out / name
            stage_env = dict(env, PROBEPLANNER_STATE_DIR=str(folder / "state"),
                             PROBEPLANNER_SDF_CACHE_DIR=str(folder / "sdf"),
                             PROBEPLANNER_REGION_INDEX_CACHE_DIR=str(folder / "region"))
            command = [sys.executable, "-X", "utf8", "-m", "pytest", *paths, "-q", "-ra",
                       "-p", "no:cacheprovider", "--junitxml=" + str(folder / "junit.xml"),
                       "--basetemp=" + str(folder / "temp")]
            if name != "packaging":
                stages[name] = original.execute(out, name, command, root, stage_env, count)
                continue
            folder.mkdir()
            write_bytes(folder / "INVOCATION.json", serialize(dict(command=command, expectedCount=count)))
            start = time.monotonic()
            with (folder / "run.log").open("xb") as log:
                completed = subprocess.run(command, cwd=root, env=stage_env, stdout=log, stderr=subprocess.STDOUT)
            counts = packaging_counts(folder / "junit.xml", sys.platform)
            record = dict(passed=completed.returncode == 0 and counts["countGatePassed"],
                          exitCode=completed.returncode, counts=counts, seconds=time.monotonic() - start)
            write_bytes(folder / "RESULT.json", serialize(record))
            stages[name] = record
            original.require(record["passed"], "Packaging check failed")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        original.require(npm, "npm unavailable")
        stages["frontend_152"] = original.execute(out, "frontend_152", [npm, "run", "test:frontend", "--",
            "--reporter=default", "--reporter=junit", "--outputFile=" + str(out / "frontend_152/junit.xml")], root / "apps/ui", env, 152)
        stages["ui_lint"] = original.execute(out, "ui_lint", [npm, "run", "lint"], root / "apps/ui", env)
        stages["ui_build"] = original.execute(out, "ui_build", [npm, "run", "build", "--", "--outDir", str(out / "ui_build/dist")], root / "apps/ui", env)
        stages["historical_exact"] = original.execute(out, "historical_exact", [sys.executable,
            "scripts/release/verify_exact_oracle_models.py", "--evidence-zip", str(old_zip),
            "--output", str(out / "historical_exact/verification.json")], root, env)
        stages["current_exact"] = original.execute(out, "current_exact", [sys.executable,
            "scripts/release/verify_public_evidence.py", "--evidence-dir", str(args.evidence_dir.resolve()),
            "--resolve-models", "--output", str(out / "current_exact/verification.json")], root, env)
        original.require(original.inventory(root) == before and original.verify_package(root) == package,
                         "Source changed")
        original.require(original.verify_git(root, True, "v0.3.0-paper") == git, "Git state changed")
        original.require(original.inspect_profile(args.profile, original.PUBLIC_PROFILE_SHA256) == profile,
                         "Profile changed")
        original.require(original.verify_replay(root, args.evidence_dir, args.profile, args.replay_dir) == replay,
                         "Replay changed")
        result.update(portableSoftwareVerificationPassed=True, sourceAndInputsUnchanged=True,
                      stages=stages, git=git, countsOverlap=True)
    except Exception as exc:
        result.update(error=type(exc).__name__ + ": " + str(exc), traceback=traceback.format_exc())
    write_bytes(out / "SUMMARY.json", serialize(result))
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result["portableSoftwareVerificationPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
