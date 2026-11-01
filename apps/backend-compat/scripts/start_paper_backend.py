"""Launch the paper Python implementation with an explicitly bound local profile."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import traceback


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def verify_profile(profile):
    root = Path(profile).resolve()
    manifest_path = root / "paper_profile.json"
    contract = json.loads(manifest_path.read_text(encoding="utf-8"))
    if contract.get("schemaVersion") != "probeplanner.paper_profile.v1":
        raise ValueError("Unsupported paper profile schema")
    bindings = contract.get("files")
    required = {"artifact_manifest.json", "structures.json", "annotation.npy", "region_map_v2.json"}
    if not isinstance(bindings, dict) or not required.issubset(bindings):
        raise ValueError("Incomplete paper profile bindings")
    for relative, expected in bindings.items():
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or root not in path.parents:
            raise ValueError(f"Profile path escapes root: {relative}")
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Profile file missing or hash mismatch: {relative}")
    return contract, sha256(manifest_path)


def load_paper_backend(profile, run_dir, static_dir=None):
    profile, run_dir = Path(profile).resolve(), Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    save(run_dir / "startup.json", {"state": "verifying_profile", "executable": sys.executable})
    contract, contract_hash = verify_profile(profile)
    removed = []
    for key in list(os.environ):
        if key.startswith("PROBEPLANNER_") and key != "PROBEPLANNER_API_TOKEN":
            removed.append(key)
            del os.environ[key]
    import audit_registration_perturbation_sensitivity as atlas
    atlas.configure_backend_environment(profile, workers=1)
    os.environ.update({
        "PROBEPLANNER_MRI_CCF_REQUIRE_MANIFEST": "true",
        "PROBEPLANNER_MRI_CCF_MANIFEST_PATH": str(profile / "artifact_manifest.json"),
        "PROBEPLANNER_STATE_DIR": str(run_dir / "state"),
        "PROBEPLANNER_HOST": "127.0.0.1",
    })
    for field in ("SDF", "REGION_INDEX", "MESH"):
        os.environ[f"PROBEPLANNER_{field}_CACHE_DIR"] = str(run_dir / "cache" / field.lower())
    if static_dir is not None:
        os.environ["PROBEPLANNER_STATIC_DIR"] = str(Path(static_dir).resolve())
    server = atlas.import_backend_server()
    expected = Path(__file__).resolve().parents[1] / "src" / "optimizer_server.py"
    if Path(server.__file__).resolve() != expected:
        raise RuntimeError("Imported optimizer is outside this source tree")
    payload = server._mri_payload()
    if not payload.get("manifestValid") or payload.get("pathConsistency", {}).get("passed") is not True:
        save(run_dir / "profile_failure.json", payload)
        raise RuntimeError("Backend profile manifest or path consistency failed")
    save(run_dir / "startup.json", {"state": "loading_atlas", "profileContractSha256": contract_hash})
    server.load_atlas()
    if not server.atlas_loaded:
        raise RuntimeError(f"Atlas loading failed: {server.atlas_error}")
    structures = profile / "structures.json"
    records = atlas.read_structure_records(structures)
    targets = sorted({row["acronym"] for row in records if row.get("acronym")})
    ontology = atlas.apply_structure_tree_descendant_fallback(server, targets, structures)
    if not ontology.get("applied"):
        raise RuntimeError("Paper ontology initialization did not apply")
    save(run_dir / "ontology.json", ontology)
    save(run_dir / "planner_regions.json", server.PLANNER_REGIONS)
    runtime = {
        "backend": "paper_python", "python": sys.version.split()[0],
        "atlasMode": server.ACTIVE_ATLAS_MODE,
        "optimizerSha256": sha256(expected), "launcherSha256": sha256(__file__),
        "ontologyInitializerSha256": sha256(atlas.__file__),
        "profileContractSha256": contract_hash,
        "artifactManifestSha256": contract["files"]["artifact_manifest.json"],
        "ontologySha256": contract["files"]["structures.json"],
        "ontologyTargetCount": len(ontology["updatedTargets"]),
        "manifestEnforced": True, "pathConsistencyPassed": True,
        "workers": 1, "cacheInitiallyEmpty": True,
        "packages": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "Flask", "allensdk", "nibabel")},
        "discardedInheritedConfigurationKeys": sorted(removed),
        "scope": contract["scope"],
    }
    save(run_dir / "runtime.json", runtime)
    server.app.config["PAPER_RUNTIME_IDENTITY"] = runtime
    server.app.add_url_rule("/api/paper/runtime", "paper_runtime", lambda: server.jsonify(runtime))
    save(run_dir / "startup.json", {"state": "ready", "profileContractSha256": contract_hash})
    return server, runtime


def preflight(args):
    """Check cheap prerequisites without creating output or importing the atlas."""
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if args.run_dir.exists():
        raise ValueError("run-dir already exists; choose an empty new run location")
    if args.static_dir is not None and not (args.static_dir / "index.html").is_file():
        raise ValueError("static-dir must contain a built index.html; build apps/ui first")
    if not args.check_only:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("127.0.0.1", args.port))
        except OSError as error:
            raise ValueError(f"port {args.port} unavailable on 127.0.0.1; choose another port") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True, help="New directory; existing runs are never overwritten")
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--port", type=int, default=5039)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        preflight(args)
    except ValueError as error:
        parser.error(str(error))
    try:
        server, runtime = load_paper_backend(args.profile, args.run_dir, args.static_dir)
        if args.check_only:
            with server.app.test_request_context():
                save(args.run_dir / "registration_summary.json", server.api_mri_registration_summary().get_json())
                save(args.run_dir / "registration_qc.json", server._build_mri_registration_qc_payload())
            print(json.dumps(runtime), flush=True)
        else:
            print(f"Paper Python backend: http://127.0.0.1:{args.port}", flush=True)
            server.app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    except Exception:
        if args.run_dir.exists():
            save(args.run_dir / "STARTUP_FAILURE.json", {"error": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
