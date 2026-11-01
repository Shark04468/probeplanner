"""Copy a successful local WebGL build and bind its source and output bytes."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(project):
    hashes = {}
    for directory in ("Assets", "Packages", "ProjectSettings", "tools"):
        for path in sorted((project / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                hashes[path.relative_to(project).as_posix()] = sha256(path)
    return hashes


def verify_build_source(project, build_project):
    sources = source_hashes(project)
    if not sources or source_hashes(build_project) != sources:
        raise SystemExit("Build project does not match authoritative source; refusing publication")
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-log", type=Path, required=True)
    parser.add_argument("--build-project", type=Path, help="Optional separately staged build directory")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    build_project = args.build_project.resolve() if args.build_project else project
    destination = project.parent / "ui" / "public" / "unity"
    log = args.build_log.read_text(encoding="utf-8", errors="replace")
    if "[WebGLBatchBuild] Build succeeded." not in log or "return code 0" not in log:
        raise SystemExit("Build log does not establish successful completion")
    sources = verify_build_source(project, build_project)
    names = ["WebGL-Build.loader.js", "WebGL-Build.data.unityweb",
             "WebGL-Build.framework.js.unityweb", "WebGL-Build.wasm.unityweb"]
    outputs = {}
    for name in names:
        source = build_project / "WebGL-Build" / "Build" / name
        if not source.is_file() or source.stat().st_size == 0:
            raise SystemExit(f"Missing or empty build artifact: {name}")
        target = destination / "Build" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(source) != sha256(target):
            raise SystemExit(f"Copy mismatch: {name}")
        outputs[f"Build/{name}"] = sha256(target)
    record = {
        "schemaVersion": "probeplanner.unity_build.v1",
        "buildId": "paper-viewer-revision-20261101",
        "editor": (project / "ProjectSettings" / "ProjectVersion.txt").read_text().strip(),
        "buildLogSha256": sha256(args.build_log),
        "sourceHashes": sources,
        "stagedSourceVerified": True,
        "outputHashes": outputs,
        "wrapperSha256": sha256(destination / "index.html"),
        "scope": "Rebuilt software viewer; not anatomical registration validation",
    }
    out = destination / "BUILD_PROVENANCE.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"buildFiles": len(outputs), "sourceFiles": len(sources), "provenanceSha256": sha256(out)}))


if __name__ == "__main__":
    main()
