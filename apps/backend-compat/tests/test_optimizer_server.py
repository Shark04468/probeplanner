import importlib
import sys
import json
import time
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture()
def server(tmp_path, monkeypatch):
    import optimizer_server

    mod = importlib.reload(optimizer_server)
    monkeypatch.setattr(mod, "atlas_loaded", True, raising=False)
    monkeypatch.setattr(mod, "atlas_error", None, raising=False)
    monkeypatch.setattr(mod, "AUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(mod, "API_TOKEN", "", raising=False)
    monkeypatch.setattr(mod, "ENGINE_VERSION", "test-engine", raising=False)

    audit_path = tmp_path / "audit.log"
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "AUDIT_LOG_PATH", audit_path, raising=False)
    monkeypatch.setattr(mod, "REPLAY_DIR", replay_dir, raising=False)
    mod.replay_index.clear()
    return mod


def _full_constraints(lambda_key="lambdaBoundary"):
    payload = {
        "maxAngle": "enforce",
        "minEntrySpacing": "enforce",
        "minClearance": "enforce",
        "brainBounds": "enforce",
        "continuousRiskDistance": "warn",
        "hemisphere": "warn",
    }
    payload[lambda_key] = "enforce"
    return payload


def _base_opt_payload(constraint_modes):
    return {
        "targetKeys": ["VISp"],
        "params": {"algorithm": "greedy", "maxAngle": 15},
        "probeParams": {"depthUm": 10000, "entrySpacingUm": 3500},
        "constraintModes": constraint_modes,
    }


def _fake_candidate():
    return {
        "id": 0,
        "ap_mm": 1.2,
        "ml_mm": -0.8,
        "theta_deg": 10.0,
        "phi_deg": 0.0,
        "depthUm": 7000,
        "skullTiltDeg": 0.0,
        "entryDvCorr_mm": 0.0,
        "entry_xyz": [1000.0, 1000.0, 1000.0],
        "tip_xyz": [1000.0, 8000.0, 1000.0],
        "dir": [0.0, 1.0, 0.0],
        "coveredRegions": ["VISp"],
        "overlapVoxels": {"VISp": 10},
        "nVoxelsTotal": 20,
        "source": "test",
        "trajectoryProfile": {},
    }


def test_task_registry_empty_id_returns_stable_placeholder():
    from backend.api import TASK_STATE_QUEUED, TaskRegistry

    task = TaskRegistry().get("")
    assert task["taskId"] == ""
    assert task["status"] == TASK_STATE_QUEUED
    assert task["stage"] == "unknown"
    assert task["finishedAt"] is None
    assert task["cancelled"] is False


def test_optimize_rejects_constraint_contract(server):
    client = server.app.test_client()
    bad_payload = _base_opt_payload({"maxAngle": "enforce"})
    resp = client.post("/api/optimize", json=bad_payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "constraint_contract"
    assert any("Missing constraint key" in x for x in body["details"])


def test_optimize_rejects_schema_validation(server):
    client = server.app.test_client()
    resp = client.post(
        "/api/optimize",
        json={
            "targetKeys": "VISp",
            "params": {},
            "probeParams": {},
            "constraintModes": _full_constraints(),
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "schema_validation_error"
    assert any("targetKeys must be a non-empty array" in d for d in body.get("details", []))


def test_api_invalid_json_returns_400(server):
    client = server.app.test_client()
    resp = client.post("/api/allen_mesh/batch", data="{", content_type="application/json")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_request_json"
    assert body["requestId"]
    assert body["engineVersion"] == "test-engine"


def test_api_internal_errors_are_redacted(server, monkeypatch):
    client = server.app.test_client()
    secret = "sensitive internal path C:/private/source.dat"

    def boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(server, "_fetch_mesh_obj_resolved", boom, raising=True)
    resp = client.get("/api/allen_mesh/123")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "internal_error"
    assert body["error"] == "Internal server error"
    assert body["requestId"]
    assert secret not in json.dumps(body)


def test_optional_json_endpoint_rejects_bad_json(server):
    client = server.app.test_client()
    resp = client.post("/api/version/freeze", data="{", content_type="application/json")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_request_json"


def test_json_body_must_be_object(server):
    client = server.app.test_client()
    resp = client.post("/api/allen_mesh/batch", json=["not", "an", "object"])

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_request_json"
    assert any("Top-level JSON value must be an object" in item for item in body.get("details", []))


def test_optional_json_endpoint_rejects_non_json_body(server):
    client = server.app.test_client()
    resp = client.post("/api/version/freeze", data="freeze=true", content_type="text/plain")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_request_json"
    assert body["error"] == "Request body must be JSON"


def test_mri_activate_invalid_bundle_is_redacted(server):
    client = server.app.test_client()
    secret = "C:/private/missing_annotation.npy"
    resp = client.post("/api/mri/activate", json={"annotationPath": secret})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "mri_bundle_invalid"
    assert body["error"] == "Invalid MRI hybrid artifact bundle"
    assert secret not in json.dumps(body)


def test_version_freeze_persist_error_is_redacted(server, monkeypatch):
    client = server.app.test_client()
    secret = "C:/private/version_freeze_state.json"

    def fail_persist():
        raise RuntimeError(secret)

    monkeypatch.setattr(server, "_persist_version_freeze_state", fail_persist, raising=True)
    resp = client.post("/api/version/freeze", json={"freeze": True})

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "version_freeze_persist_failed"
    assert body["error"] == "Failed to persist version freeze state"
    assert secret not in json.dumps(body)


def test_atlas_not_ready_error_is_redacted(server, monkeypatch):
    client = server.app.test_client()
    secret = "C:/private/atlas/cache/annotation_25.nrrd"
    monkeypatch.setattr(server, "atlas_loaded", False, raising=False)
    monkeypatch.setattr(server, "atlas_error", f"failed to read {secret}", raising=False)
    monkeypatch.setattr(server, "_ensure_atlas_loader", lambda force_retry=False: None, raising=True)
    server.atlas_load_state.update({"state": "error", "phase": "download", "message": secret, "progress": 0.25})

    resp = client.post("/api/optimize", json=_base_opt_payload(_full_constraints()))

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == "atlas_not_ready"
    assert body["error"] == "Atlas not loaded yet, please wait and retry."
    assert secret not in json.dumps(body)


def test_status_atlas_error_is_redacted(server, monkeypatch):
    client = server.app.test_client()
    secret = "C:/private/atlas/cache/annotation_25.nrrd"
    monkeypatch.setattr(server, "atlas_loaded", False, raising=False)
    monkeypatch.setattr(server, "atlas_error", f"failed to read {secret}", raising=False)
    monkeypatch.setattr(server, "_ensure_atlas_loader", lambda force_retry=False: None, raising=True)
    server.atlas_load_state.update({"state": "error", "phase": "download", "message": secret, "progress": 0.25})

    resp = client.get("/api/status?includeRegions=0")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["atlasError"] == "Atlas loading failed. Inspect backend logs using the response requestId."
    assert body["atlasLoad"]["message"] == body["atlasError"]
    assert secret not in json.dumps(body)


def test_mri_reconstruct_failed_task_is_redacted(server):
    client = server.app.test_client()
    secret = "C:/private/mri/annotation.npy"
    task = server._new_mri_task(kind="reconstruct")
    task_id = task["taskId"]
    task.update(
        {
            "state": server.TASK_STATE_FAILED,
            "status": server.TASK_STATE_FAILED,
            "message": f"missing {secret}",
            "error": f"missing {secret}",
            "repairHints": server._build_mri_repair_hints(f"missing {secret}", {"annotationPath": secret}),
        }
    )
    with server.mri_reconstruct_lock:
        server.mri_reconstruct_tasks[task_id] = task

    resp = client.get(f"/api/mri/reconstruct/status?taskId={task_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    payload = json.dumps(body)
    assert body["task"]["status"] == server.TASK_STATE_FAILED
    assert body["task"]["error"] == "MRI reconstruction failed. Inspect backend logs using the response requestId."
    assert "retryPayload" not in body["task"]["repairHints"]
    assert secret not in payload


def test_frontend_build_missing_error_is_redacted(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    missing_build = tmp_path / "private_build_dir"
    monkeypatch.setattr(server, "_frontend_build_dir", lambda: missing_build, raising=True)

    resp = client.get("/")

    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == "frontend_build_missing"
    assert body["error"] == "Frontend build not found"
    assert str(missing_build) not in json.dumps(body)


def test_task_status_internal_error_is_redacted(server):
    client = server.app.test_client()
    secret = "C:/private/replay/input.json"
    task_id = "task-internal-redaction"
    server.task_registry.finish(
        task_id,
        status=server.TASK_STATE_FAILED,
        stage="failed",
        message=f"failed reading {secret}",
        details=[f"failed reading {secret}"],
        result={
            "ok": False,
            "code": "internal_error",
            "error": f"failed reading {secret}",
            "details": [f"failed reading {secret}"],
        },
    )

    resp = client.get(f"/api/task/status/{task_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    payload = json.dumps(body)
    assert body["task"]["status"] == server.TASK_STATE_FAILED
    assert body["task"]["result"]["code"] == "internal_error"
    assert body["task"]["message"].startswith("Task failed internally.")
    assert secret not in payload


def test_benchmark_status_invalid_path_returns_400(server):
    client = server.app.test_client()
    resp = client.get("/api/benchmark/status?outDir=%00")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "path_invalid"
    assert body["error"] == "Invalid file system path"


def test_benchmark_file_invalid_outdir_returns_400(server):
    client = server.app.test_client()
    resp = client.get("/api/benchmark/file/result.csv?outDir=%00")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "path_invalid"
    assert body["error"] == "Invalid file system path"


def test_benchmark_file_send_error_is_redacted(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    monkeypatch.setattr(server, "BENCHMARK_ALLOWED_ROOTS", (tmp_path.resolve(),), raising=False)
    out_dir = tmp_path / "benchmark"
    out_dir.mkdir()
    (out_dir / "result.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(server, "benchmark_last_out_dir", out_dir, raising=False)
    secret = "C:/private/benchmark/result.csv"

    def fail_send(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(server, "send_file", fail_send, raising=True)
    resp = client.get("/api/benchmark/file/result.csv")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "internal_error"
    assert body["error"] == "Internal server error"
    assert secret not in json.dumps(body)


def test_auth_disabled_rejects_non_loopback_api_access(server):
    client = server.app.test_client()
    resp = client.get("/api/status", environ_overrides={"REMOTE_ADDR": "203.0.113.10"})

    assert resp.status_code == 403
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "auth_required"


def test_benchmark_init_rejects_path_outside_allowed_roots(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    monkeypatch.setattr(server, "BENCHMARK_ALLOWED_ROOTS", (allowed.resolve(),), raising=False)

    resp = client.post("/api/benchmark/init", json={"outDir": str(outside)})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "path_outside_workspace"


def test_benchmark_run_disabled_by_default(server):
    client = server.app.test_client()
    resp = client.post("/api/benchmark/run", json={})

    assert resp.status_code == 403
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "benchmark_run_disabled"


def test_frontend_source_maps_not_served_by_default(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    build_dir = tmp_path / "frontend_build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<main>Probe Planner</main>", encoding="utf-8")
    (build_dir / "main.js.map").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(server, "_frontend_build_dir", lambda: build_dir, raising=True)
    monkeypatch.setattr(server, "SERVE_SOURCE_MAPS", False, raising=False)

    resp = client.get("/main.js.map")

    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "source_map_disabled"


def test_allen_mesh_lod_invalid_lod_returns_400(server, monkeypatch):
    client = server.app.test_client()

    def fail_fetch(*args, **kwargs):
        raise AssertionError("mesh fetch should not run for invalid lod")

    monkeypatch.setattr(server, "_fetch_mesh_obj_resolved", fail_fetch, raising=True)
    resp = client.get("/api/allen_mesh_lod/123?lod=bad")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_query_param"
    assert "lod" in body["error"]


def test_mri_surface_mesh_invalid_lod_returns_400(server, monkeypatch):
    client = server.app.test_client()

    def fail_resolve(*args, **kwargs):
        raise AssertionError("mesh resolve should not run for invalid lod")

    monkeypatch.setattr(server, "_resolve_mri_surface_mesh", fail_resolve, raising=True)
    resp = client.get("/api/mri_surface_mesh_lod/skull?lod=9")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_query_param"
    assert "lod" in body["error"]


def test_mri_surface_mesh_unavailable_is_redacted(server, monkeypatch):
    client = server.app.test_client()
    secret = "C:/private/mri/surfaces/skull_lod1.obj"

    def fail_resolve(*args, **kwargs):
        raise FileNotFoundError(secret)

    monkeypatch.setattr(server, "_resolve_mri_surface_mesh", fail_resolve, raising=True)
    resp = client.get("/api/mri_surface_mesh_lod/skull?lod=1")

    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "mri_surface_mesh_unavailable"
    assert body["error"] == "MRI surface mesh unavailable"
    assert secret not in json.dumps(body)


def test_registration_qc_export_invalid_format_returns_400(server):
    client = server.app.test_client()
    resp = client.get("/api/mri/registration/qc/export?format=xlsx")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "invalid_query_param"
    assert "format" in json.dumps(body)


def test_registration_qc_overlay_invalid_query_returns_400(server):
    client = server.app.test_client()

    bad_axis = client.get("/api/mri/registration/qc/overlay_image?axis=diagonal")
    assert bad_axis.status_code == 400
    assert bad_axis.get_json()["code"] == "invalid_query_param"

    bad_mode = client.get("/api/mri/registration/qc/overlay_image?mode=fused")
    assert bad_mode.status_code == 400
    assert bad_mode.get_json()["code"] == "invalid_query_param"

    bad_index = client.get("/api/mri/registration/qc/overlay_image?index=mid")
    assert bad_index.status_code == 400
    assert bad_index.get_json()["code"] == "invalid_query_param"


def test_frontend_static_send_error_is_redacted(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    build_dir = tmp_path / "frontend_build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text("<main>Probe Planner</main>", encoding="utf-8")
    monkeypatch.setattr(server, "_frontend_build_dir", lambda: build_dir, raising=True)
    secret = "C:/private/frontend/index.html"

    def fail_send(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(server, "send_file", fail_send, raising=True)
    resp = client.get("/")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "internal_error"
    assert body["error"] == "Internal server error"
    assert secret not in json.dumps(body)


def test_optimize_rejects_non_mouse_species(server):
    client = server.app.test_client()
    payload = _base_opt_payload(_full_constraints())
    payload["clinicalContext"] = {"species": "human", "intendedUse": "automatic_decision"}
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "validation_error"
    assert "clinicalContext.species" in body["error"]


def test_optimize_rejects_intended_use_mismatch(server):
    client = server.app.test_client()
    payload = _base_opt_payload(_full_constraints())
    payload["clinicalContext"] = {"species": "mouse", "intendedUse": "cds"}
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "validation_error"
    assert "clinicalContext.intendedUse" in body["error"]


def test_validate_target_keys_allows_backend_auto_provision(server, monkeypatch):
    def fake_auto(keys):
        if "AUTO_NEW" in keys:
            server.PLANNER_REGIONS["AUTO_NEW"] = {
                "id": 999001,
                "ap": 1.0,
                "dv": 2.0,
                "ml": -1.0,
                "category": "dynamic",
            }
            return ["AUTO_NEW"]
        return []

    monkeypatch.setattr(server, "_auto_register_regions_if_needed", fake_auto, raising=True)
    out = server._validate_target_keys(["AUTO_NEW"])
    assert out == ["AUTO_NEW"]


def test_optimize_accepts_lambda_alias_and_supports_replay(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [json.loads(json.dumps(candidate))],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints(lambda_key="lambda"))
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["requestId"]
    assert body["engineVersion"] == "test-engine"
    assert isinstance(body.get("requestHash"), str) and len(body["requestHash"]) == 64
    assert isinstance(body.get("responseHash"), str) and len(body["responseHash"]) == 64
    assert body["selected"][0]["ap_mm_corrected"] == pytest.approx(candidate["ap_mm"])

    replay_resp = client.get(f"/api/replay/{body['requestId']}")
    assert replay_resp.status_code == 200
    replay_body = replay_resp.get_json()
    assert replay_body["record"]["request"]["targetKeys"] == ["VISp"]
    assert replay_body["record"]["requestHash"] == body["requestHash"]
    assert replay_body["record"]["responseHash"] == body["responseHash"]

    rerun_resp = client.post(f"/api/replay/{body['requestId']}/rerun")
    assert rerun_resp.status_code == 200
    rerun_body = rerun_resp.get_json()
    assert rerun_body["replayOf"] == body["requestId"]
    assert isinstance(rerun_body.get("requestHash"), str) and len(rerun_body["requestHash"]) == 64
    assert isinstance(rerun_body.get("responseHash"), str) and len(rerun_body["responseHash"]) == 64


def test_optimize_blocks_on_brain_bounds_enforce(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()
    candidate["theta_deg"] = 0.0
    candidate["depthUm"] = 9000
    candidate["dir"] = [0.0, 1.0, 0.0]

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == "candidate_enforced_filter_empty"
    assert body["enforcedConstraintFilter"]["reasons"]["brainBounds"] >= 1


def test_brain_mask_audit_counts_terminal_outside_gap(server, monkeypatch):
    annotation = np.zeros((1, 8, 1), dtype=np.uint32)
    annotation[0, 2:5, 0] = 1
    monkeypatch.setattr(server, "atlas_annotation", annotation, raising=True)

    summary = server._brain_mask_path_summary_from_voxels(
        [(0, idx, 0) for idx in range(8)],
        step_um=25.0,
    )

    assert summary["tipInside"] is False
    assert summary["firstInsideIndex"] == 2
    assert summary["lastInsideIndex"] == 4
    assert summary["maxOutsideGapUm"] == pytest.approx(75.0)
    assert summary["maxOutsideGapSegmentMm"] == pytest.approx([0.125, 0.175])


def test_tip_in_target_policy_requires_target_tip_and_zero_overshoot(server, monkeypatch):
    annotation = np.zeros((3, 3, 3), dtype=np.uint32)
    annotation[1, 1, 1] = 42
    monkeypatch.setattr(server, "atlas_annotation", annotation, raising=True)
    monkeypatch.setattr(server, "PLANNER_REGIONS", {"VISp": {"id": 42}}, raising=True)
    monkeypatch.setattr(server, "descendant_ids", {42: frozenset([42])}, raising=True)
    monkeypatch.setattr(server, "BREGMA_UM", np.array([25.0, -75.0, 25.0]), raising=True)

    candidate = {
        "depthUm": 100.0,
        "depthUm_corrected": 100.0,
        "ap_mm_corrected": 0.0,
        "ml_mm_corrected": 0.0,
        "theta_deg": 0.0,
        "phi_deg": 0.0,
        "skullTiltDeg": 0.0,
        "coveredRegions": ["VISp"],
    }
    summary = server._candidate_endpoint_target_summary(candidate, {"VISp": 42})
    assert summary["tipInRequestedTarget"] is True
    assert summary["tipTargetKeys"] == ["VISp"]
    assert summary["overshootAfterLastTargetUm"] == pytest.approx(0.0)

    ok, reason, _ = server._candidate_passes_single_enforced_constraints(
        candidate,
        {"targetEndpointPolicy": "tip_in_target", "minRiskDistanceMm": 0.0},
        {},
        {
            "maxAngle": "off",
            "lambdaBoundary": "off",
            "brainBounds": "off",
            "hemisphere": "off",
            "continuousRiskDistance": "off",
        },
    )
    assert ok is True
    assert reason is None


def test_unified_probe_audit_separates_entry_surfaces_from_internal_risk(server, monkeypatch):
    annotation = np.ones((1, 5, 1), dtype=np.uint32) * 42
    monkeypatch.setattr(server, "atlas_annotation", annotation, raising=True)
    monkeypatch.setattr(server, "PLANNER_REGIONS", {"VISp": {"id": 42}}, raising=True)
    monkeypatch.setattr(server, "descendant_ids", {42: frozenset([42])}, raising=True)
    monkeypatch.setattr(server, "BREGMA_UM", np.array([0.0, 0.0, 0.0]), raising=True)
    monkeypatch.setattr(
        server,
        "risk_model_summary",
        {
            "riskLayerCompleteness": {
                "vessel": {"usable": True, "status": "ready"},
                "ventricle": {"usable": False, "status": "configured_empty"},
                "sulcus": {"usable": False, "status": "missing"},
                "skull": {"usable": True, "status": "ready"},
                "brain_surface": {"usable": True, "status": "ready"},
                "drilling_surface": {"usable": False, "status": "missing"},
                "cortex_outside": {"usable": True, "status": "ready"},
            }
        },
        raising=True,
    )
    probe = {
        "depthUm": 100.0,
        "depthUm_corrected": 100.0,
        "ap_mm_corrected": 0.0,
        "ml_mm_corrected": 0.0,
        "theta_deg": 0.0,
        "phi_deg": 0.0,
        "skullTiltDeg": 0.0,
        "riskDiagnostics": {
            "classMinDistanceMm": {
                "vessel": 0.7,
                "skull": -0.2,
                "brain_surface": -0.1,
                "cortex_outside": -0.3,
            },
            "classThresholdMm": {"vessel": 0.6, "skull": 0.45},
        },
    }

    audit = server._build_unified_probe_audit(
        probe,
        ["VISp"],
        {"minRiskDistanceMm": 0.35},
    )

    assert audit["brainContainmentPassed"] is True
    assert audit["tipInTarget"] is True
    assert audit["distalOvershootMm"] == pytest.approx(0.0)
    assert audit["internalRiskViolationCount"] == 0
    assert audit["minimumInternalRiskMarginMm"] == pytest.approx(0.1)
    assert audit["entrySurfaceCrossingStatus"] == "allowed_entry_surface_semantics"
    assert audit["internalRiskAudit"]["claimStatus"] == "partial"
    assert audit["internalRiskAudit"]["unavailableClasses"] == [
        "no_go",
        "sulcus",
        "user_defined_exclusion",
        "user_exclusion",
        "ventricle",
    ]
    assert "skull" not in audit["internalRiskAudit"]["classes"]


def test_skull_is_not_used_as_internal_risk_margin(server):
    probe = {
        "minSafetyDistanceMm": -0.2,
        "riskDiagnostics": {
            "classMinDistanceMm": {"skull": -0.2, "vessel": 0.8},
            "classThresholdMm": {"skull": 0.45, "vessel": 0.6},
        },
    }

    threshold = server._effective_risk_threshold_payload_for_probe(probe, 0.35)

    assert threshold["riskClass"] == "vessel"
    assert threshold["marginMm"] == pytest.approx(0.2)


def test_depth_optimization_flag_is_normalized(server):
    payload = _base_opt_payload(_full_constraints())
    payload["params"]["targetEndpointPolicy"] = "tip_in_target"
    payload["params"]["depthOptimizationEnabled"] = False

    normalized = server._normalize_optimize_payload(payload)

    assert normalized["params"]["depthOptimizationEnabled"] is False


def test_stability_reference_contract_is_normalized(server):
    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "stabilityTieBreakerEnabled": True,
            "stabilityReferenceWeight": 0.08,
            "stabilityReferenceTrajectories": [
                {
                    "entryCcfUm": [1000.0, 1000.0, 1000.0],
                    "tipCcfUm": [1000.0, 8000.0, 1000.0],
                }
            ],
        }
    )

    normalized = server._normalize_optimize_payload(payload)

    assert normalized["params"]["stabilityTieBreakerEnabled"] is True
    assert normalized["params"]["stabilityReferenceWeight"] == pytest.approx(0.08)
    assert normalized["params"]["stabilityReferenceProbeCount"] == 1
    assert normalized["params"]["stabilityReferenceTrajectories"][0]["tipCcfUm"] == pytest.approx(
        [1000.0, 8000.0, 1000.0]
    )


def test_stability_tie_breaker_prefers_reference_proximity(server):
    near = _fake_candidate()
    far = {
        **_fake_candidate(),
        "entry_xyz": [3500.0, 1000.0, 1000.0],
        "tip_xyz": [3500.0, 8000.0, 1000.0],
    }
    params = {
        "maxAngle": 15.0,
        "objectiveCoverageWeight": 0.5,
        "objectiveSafetyWeight": 0.3,
        "objectiveAngleWeight": 0.2,
        "robustOptimization": False,
        "objectiveRiskPrimary": False,
        "stabilityTieBreakerEnabled": True,
        "stabilityReferenceWeight": 0.08,
        "stabilityEntryWeight": 1.0,
        "stabilityTipWeight": 1.0,
        "stabilityAngleWeight": 1.0,
        "stabilityReferenceTrajectories": [
            {
                "entryCcfUm": [1000.0, 1000.0, 1000.0],
                "tipCcfUm": [1000.0, 8000.0, 1000.0],
            }
        ],
    }

    near_score = server._compute_candidate_objectives(near, ["VISp"], params)
    far_score = server._compute_candidate_objectives(far, ["VISp"], params)

    assert near_score["scores"]["referenceStabilityCost"] == pytest.approx(0.0)
    assert far_score["scores"]["referenceStabilityCost"] == pytest.approx(5.0)
    assert near_score["explainScore"] > far_score["explainScore"]

    near["overlapVoxels"] = {"VISp": 10}
    far["overlapVoxels"] = {"VISp": 11}
    for candidate, score in ((near, near_score), (far, far_score)):
        candidate["objectiveScores"] = score["scores"]
        candidate["objectiveWeighted"] = score["weighted"]
        candidate["explainScore"] = score["explainScore"]
        candidate["paretoRank"] = 1
    selected = server.greedy_optimize(["VISp"], [far, near], 0.0, 0.0)["selected"]
    assert selected[0] is near


def test_trajectory_profile_uses_physical_depth_samples(server, monkeypatch):
    annotation = np.zeros((1, 1, 3), dtype=np.uint32)
    annotation[0, 0, 1] = 42
    monkeypatch.setattr(server, "atlas_annotation", annotation, raising=True)
    monkeypatch.setattr(server, "structure_map", {42: {"voxel_count": 1}}, raising=True)
    monkeypatch.setattr(server, "descendant_ids", {42: frozenset([42])}, raising=True)

    profile = server.compute_trajectory_profile(
        [(0, 0, 0), (0, 0, 1), (0, 0, 2)],
        [42],
        step_um=25.0,
        sample_depths_um=[0.0, 110.0, 275.0],
    )

    assert profile[42]["entry_depth_um"] == pytest.approx(110.0)
    assert profile[42]["exit_depth_um"] == pytest.approx(110.0)
    assert profile[42]["channel_span_um"] == pytest.approx(0.0)


def test_optimize_reports_hemisphere_warning(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()
    candidate["ml_mm"] = 1.2
    candidate["depthUm"] = 5000
    candidate["tip_xyz"] = [1000.0, 6000.0, 1000.0]
    candidate["coveredRegions"] = ["VISp"]

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["hemisphere"] = "warn"
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    hemisphere_issues = [
        issue for issue in body.get("safetyIssues", []) if issue.get("constraint") == "hemisphere"
    ]
    assert hemisphere_issues
    assert hemisphere_issues[0]["mode"] == "warn"


def test_optimize_runs_coverage_boost_when_enabled(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [],
            "uncovered": ["VISp"],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "coverage_boost_voxel",
        lambda *args, **kwargs: {"selected": [candidate], "uncovered": []},
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["params"]["coverageBoost"] = True
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["selected"]) == 1
    assert body["uncovered"] == []


def test_optimize_returns_explainable_scores(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "robustOptimization": True,
            "paretoEnabled": True,
            "objectiveCoverageWeight": 0.0,
            "objectiveSafetyWeight": 0.0,
            "objectiveAngleWeight": 0.0,
        }
    )
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rankingMode"] in {"pareto_robust", "pareto", "robust", "default"}
    assert set(body["objectiveWeights"].keys()) == {"coverage", "safety", "angle"}

    selected = body["selected"][0]
    assert "explainScore" in selected
    assert "paretoRank" in selected
    assert "robustScore" in selected
    assert "objectiveScores" in selected
    assert "objectiveWeighted" in selected
    assert "explain" in selected
    assert "preferencePreview" in body
    assert "balanced" in body["preferencePreview"]


def test_optimize_supports_drcc_fourth_method(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_risk_diagnostics",
        lambda *args, **kwargs: {
            "minDistanceMm": 0.62,
            "minSignedDistanceMm": 0.62,
            "closestDepthMm": 2.4,
            "nearestSourceClass": "vessel",
            "nearestSource": "risk_surface",
            "thresholdSegmentsMm": [],
            "dangerSegmentsTop3": [],
            "riskIntegralShortfallMm": 0.01,
            "riskTailShortfallMm": 0.0,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_uncertainty_metrics",
        lambda *args, **kwargs: {
            "coverageMean": 1.0,
            "coverageCi95": [1.0, 1.0],
            "passRate": 1.0,
            "minSafetyMarginMmCi95": [0.30, 0.80],
            "tipSigmaAxesMm": [0.1, 0.1, 0.1],
            "tipSigmaMm": 0.173,
            "entrySigmaMm": 0.1,
            "angleSigmaDeg": 1.0,
            "depthSigmaMm": 0.1,
            "coneHalfAngleDeg": 1.75,
            "minSafetyDistanceMmMean": 0.62,
            "minSafetyDistanceMmCi95": [0.51, 0.76],
            "safetyPassProbability": 0.93,
            "safetyPassProbabilityCi95": [0.70, 0.99],
            "safetyThresholdMm": 0.35,
            "riskDistanceSigmaMm": 0.05,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "algorithm": "drcc",
            "objectivePreset": "safety_first",
            "drccEnabled": True,
            "drccRiskEpsilon": 0.08,
            "drccWassersteinRadius": 0.03,
            "drccEvalTopK": 64,
            "drccStrict": True,
            "drccCvarAlpha": 0.9,
            "sdfRiskIntegralWeight": 1.0,
            "sdfRiskCvarWeight": 0.7,
            "selectiveConformalEnabled": True,
            "selectiveConformalAlpha": 0.1,
            "selectiveReviewProbThreshold": 0.8,
            "selectiveRejectProbThreshold": 0.35,
            "minRiskDistanceMm": 0.35,
        }
    )

    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["algorithm"] == "drcc"
    assert body["drccEnabled"] is True
    assert body["rankingMode"] == "drcc_pareto_robust"
    assert isinstance(body.get("drccSummary"), dict)
    assert body["drccSummary"]["solver"] in {"exact_pool", "hybrid", "heuristic"}
    assert isinstance(body["drccSummary"].get("exactPool"), dict)
    assert isinstance(body.get("preferenceProfiles"), dict)
    assert "balanced" in body["preferenceProfiles"]
    assert "safety_first" in body["preferenceProfiles"]
    assert isinstance(body.get("selectiveConformalSummary"), dict)
    assert isinstance(body.get("stratifiedCalibration"), dict)

    if body.get("selected"):
        selected = body["selected"][0]
        assert isinstance(selected.get("drccMetrics"), dict)
        assert isinstance(selected.get("selectiveConformal"), dict)
        assert "decision" in selected["selectiveConformal"]
        assert selected["primaryEndpoints"]["safetyPassProbabilityGuaranteed"] >= 0.0
    else:
        assert isinstance(body["drccSummary"]["exactPool"]["status"], str)


def test_optimize_reports_continuous_min_safety_distance(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_min_risk_distance_mm",
        lambda *args, **kwargs: 0.42,
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_uncertainty_metrics",
        lambda *args, **kwargs: {
            "coverageMean": 1.0,
            "coverageCi95": [1.0, 1.0],
            "passRate": 1.0,
            "minSafetyMarginMmCi95": [0.3, 0.8],
            "tipSigmaAxesMm": [0.1, 0.1, 0.1],
            "tipSigmaMm": 0.173,
            "entrySigmaMm": 0.1,
            "angleSigmaDeg": 1.0,
            "depthSigmaMm": 0.1,
            "coneHalfAngleDeg": 1.75,
            "minSafetyDistanceMmMean": 0.42,
            "minSafetyDistanceMmCi95": [0.31, 0.57],
            "safetyPassProbability": 0.875,
            "safetyPassProbabilityCi95": [0.65, 0.97],
            "safetyThresholdMm": 0.35,
            "riskDistanceSigmaMm": 0.05,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["continuousRiskDistance"] = "enforce"
    payload["params"]["minRiskDistanceMm"] = 0.35
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    selected = body["selected"][0]
    assert selected["minSafetyDistanceMm"] == pytest.approx(0.42)
    assert selected["minSafetyDistanceCi95"] == [0.31, 0.57]
    assert selected["safetyDistanceConfidence"]["probability"] == pytest.approx(0.875)
    assert selected["safetyDistanceConfidence"]["thresholdMm"] == pytest.approx(0.35)
    assert body["robustParams"]["minRiskDistanceMm"] == pytest.approx(0.35)
    assert body["uncertaintySummary"]["meanSafetyPassProbability"] == pytest.approx(0.875)


def test_optimize_returns_primary_endpoint_summary(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_risk_diagnostics",
        lambda *args, **kwargs: {
            "minDistanceMm": 0.41,
            "closestDepthMm": 2.1,
            "thresholdSegmentsMm": [],
            "nearestSourceClass": "ventricle",
            "nearestSource": "atlas_keywords:ventricle",
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_uncertainty_metrics",
        lambda *args, **kwargs: {
            "coverageMean": 1.0,
            "coverageCi95": [1.0, 1.0],
            "passRate": 1.0,
            "minSafetyMarginMmCi95": [0.31, 0.71],
            "tipSigmaAxesMm": [0.1, 0.1, 0.1],
            "tipSigmaMm": 0.17,
            "entrySigmaMm": 0.1,
            "angleSigmaDeg": 1.0,
            "depthSigmaMm": 0.1,
            "coneHalfAngleDeg": 1.7,
            "minSafetyDistanceMmMean": 0.41,
            "minSafetyDistanceMmCi95": [0.31, 0.56],
            "safetyPassProbability": 0.92,
            "safetyPassProbabilityCi95": [0.64, 0.99],
            "safetyThresholdMm": 0.35,
            "riskDistanceSigmaMm": 0.08,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["clinicalContext"] = {
        "species": "mouse",
        "intendedUse": "automatic_decision",
        "studyArm": "silent",
        "hospitalId": "H1",
        "deviceModel": "NP2.0",
        "expertAcceptanceRate": 0.8,
    }
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["clinicalContract"]["species"] == "mouse"
    assert body["clinicalContract"]["intendedUse"] == "automatic_decision"
    assert "primaryEndpointSummary" in body
    assert body["primaryEndpointSummary"]["planningTimeMs"] >= 0
    assert body["primaryEndpointSummary"]["expertAcceptanceRate"] == pytest.approx(0.8)
    probe = body["selected"][0]
    assert "primaryEndpoints" in probe
    assert probe["primaryEndpoints"]["continuousMinSafetyDistanceMm"] == pytest.approx(0.41)
    assert probe["primaryEndpoints"]["safetyPassProbability"] == pytest.approx(0.92)


def test_optimize_emits_statistical_contract_fields(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_uncertainty_metrics",
        lambda *args, **kwargs: {
            "coverageMean": 1.0,
            "coverageCi95": [1.0, 1.0],
            "passRate": 1.0,
            "minSafetyMarginMmCi95": [0.32, 0.74],
            "tipSigmaAxesMm": [0.1, 0.1, 0.1],
            "tipSigmaMm": 0.17,
            "entrySigmaMm": 0.1,
            "angleSigmaDeg": 1.0,
            "depthSigmaMm": 0.1,
            "coneHalfAngleDeg": 1.7,
            "minSafetyDistanceMmMean": 0.43,
            "minSafetyDistanceMmCi95": [0.33, 0.57],
            "safetyPassProbability": 0.91,
            "safetyPassProbabilityCi95": [0.66, 0.99],
            "safetyThresholdMm": 0.35,
            "riskDistanceSigmaMm": 0.08,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["clinicalContext"] = {
        "species": "mouse",
        "intendedUse": "automatic_decision",
        "batchId": "B-01",
    }
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    probe = body["selected"][0]
    stats = probe.get("statisticalSafety")
    assert isinstance(stats, dict)
    assert isinstance(stats.get("minDistanceMm"), (int, float))
    assert isinstance(stats.get("minDistanceCi95"), list) and len(stats["minDistanceCi95"]) == 2
    assert isinstance(stats.get("pSafetyGtThreshold"), (int, float))
    assert isinstance(stats.get("pSafetyGtThresholdCi95"), list) and len(stats["pSafetyGtThresholdCi95"]) == 2
    assert stats["thresholdMm"] == pytest.approx(0.35)
    assert probe["minDistanceMm"] == pytest.approx(stats["minDistanceMm"])
    assert probe["pSafetyGtThreshold"] == pytest.approx(stats["pSafetyGtThreshold"])
    assert isinstance(body.get("statisticalContract"), dict)
    coverage = body["statisticalContract"]["fieldCoverage"]
    assert coverage["minDistanceMm"]["count"] == 1
    assert coverage["minDistanceCi95"]["count"] == 1
    assert coverage["pSafetyGtThreshold"]["count"] == 1
    assert coverage["pSafetyGtThresholdCi95"]["count"] == 1
    strat = body.get("stratifiedCalibration") or {}
    dims = [row.get("dimension") for row in (strat.get("dimensions") or [])]
    assert "brain_region" in dims
    assert "hemisphere" in dims
    assert "batch" in dims


def test_optimize_honors_explicit_continuous_risk_for_keyword_only_model(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_min_risk_distance_mm",
        lambda *args, **kwargs: 0.0,
        raising=True,
    )
    monkeypatch.setattr(server, "ALLOW_KEYWORD_ONLY_RISK_ENFORCE", False, raising=False)
    server.risk_model_summary.clear()
    server.risk_model_summary.update(
        {
            "enabled": True,
            "source": ["atlas_keywords"],
            "voxelCount": 1000,
            "surfacePointCount": 300,
        }
    )

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["continuousRiskDistance"] = "enforce"
    payload["params"]["minRiskDistanceMm"] = 0.35
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 422
    body = resp.get_json()
    issues = [i for i in body.get("safetyIssues", []) if i.get("constraint") == "continuousRiskDistance"]
    assert issues
    assert issues[0]["mode"] == "enforce"
    assert issues[0]["modeAdjusted"] is False
    assert "failureCard" in issues[0]
    assert "segmentDepthMm" in issues[0]["failureCard"]
    assert issues[0]["failureCard"]["minDistanceMm"] == pytest.approx(0.0)
    assert isinstance(issues[0]["failureCard"].get("counterfactualPassIf"), list)
    assert issues[0]["failureCard"]["counterfactualPassIf"]
    assert body["riskConstraintPolicy"]["effectiveMode"] == "enforce"
    assert body["riskConstraintPolicy"]["reason"] == "strict_override"
    assert body["failureCards"]


def test_optimize_uses_class_specific_risk_threshold(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_risk_diagnostics",
        lambda *args, **kwargs: {
            "minDistanceMm": 0.20,
            "minSignedDistanceMm": 0.20,
            "closestDepthMm": 1.2,
            "nearestSourceClass": "vessel",
            "nearestSource": "vessel_mask",
            "classThresholdMm": {"default": 0.35, "vessel": 0.10},
            "dangerSegmentsTop3": [],
            "thresholdSegmentsMm": [],
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["params"]["minRiskDistanceMm"] = 0.35
    payload["params"]["riskThresholdsMm"] = {"vessel": 0.10}
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    continuous_issues = [
        i for i in body.get("safetyIssues", []) if i.get("constraint") == "continuousRiskDistance"
    ]
    assert continuous_issues == []


def test_optimize_blocks_continuous_risk_with_keyword_strict_override(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "_compute_probe_min_risk_distance_mm",
        lambda *args, **kwargs: 0.0,
        raising=True,
    )
    monkeypatch.setattr(server, "ALLOW_KEYWORD_ONLY_RISK_ENFORCE", True, raising=False)
    server.risk_model_summary.clear()
    server.risk_model_summary.update(
        {
            "enabled": True,
            "source": ["atlas_keywords"],
            "voxelCount": 1000,
            "surfacePointCount": 300,
        }
    )

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["continuousRiskDistance"] = "enforce"
    payload["params"]["minRiskDistanceMm"] = 0.35
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["code"] == "safety_contract_block"
    assert any("continuousRiskDistance" in d for d in body.get("details", []))


def test_failure_card_reports_nearest_feasible_adjustment(server, monkeypatch):
    client = server.app.test_client()
    bad = _fake_candidate()
    bad["id"] = 0
    bad["ap_mm"] = 1.2
    bad["ml_mm"] = -0.8
    bad["coveredRegions"] = ["VISp"]

    good = _fake_candidate()
    good["id"] = 1
    good["ap_mm"] = 1.0
    good["ml_mm"] = -0.5
    good["theta_deg"] = 9.0
    good["coveredRegions"] = ["VISp"]

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [bad, good],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    def _fake_risk_diag(probe, *args, **kwargs):
        if probe.get("id") == 1:
            return {
                "minDistanceMm": 0.72,
                "minSignedDistanceMm": 0.72,
                "closestDepthMm": 2.0,
                "nearestSourceClass": "vessel",
                "nearestSource": "risk_surface",
                "thresholdSegmentsMm": [],
                "dangerSegmentsTop3": [],
            }
        return {
            "minDistanceMm": 0.08,
            "minSignedDistanceMm": 0.08,
            "closestDepthMm": 1.8,
            "nearestSourceClass": "vessel",
            "nearestSource": "risk_surface",
            "thresholdSegmentsMm": [{"startDepthMm": 1.7, "endDepthMm": 1.9, "sampleCount": 3}],
            "dangerSegmentsTop3": [{"startDepthMm": 1.7, "endDepthMm": 1.9, "sampleCount": 3}],
        }

    monkeypatch.setattr(server, "_compute_probe_risk_diagnostics", _fake_risk_diag, raising=True)

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["continuousRiskDistance"] = "warn"
    payload["params"]["minRiskDistanceMm"] = 0.35
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    issues = [i for i in body.get("safetyIssues", []) if i.get("constraint") == "continuousRiskDistance"]
    assert issues
    card = issues[0].get("failureCard") or {}
    alt = card.get("alternativeCandidate") or {}
    nearest = alt.get("nearestFeasible") or {}
    assert nearest.get("candidateId") == 1
    assert isinstance(nearest.get("minAdjustment"), dict)
    cf = card.get("counterfactualPassIf") or []
    assert any(item.get("parameter") == "nearestFeasibleCandidate" for item in cf if isinstance(item, dict))


def test_task_cancel_and_status_endpoints(server):
    client = server.app.test_client()
    cancel_resp = client.post("/api/task/cancel", json={"taskId": "task-demo-1"})
    assert cancel_resp.status_code == 200
    cancel_body = cancel_resp.get_json()
    assert cancel_body["ok"] is True
    assert cancel_body["cancelled"] is True
    assert cancel_body["taskId"] == "task-demo-1"

    status_resp = client.get("/api/task/status/task-demo-1")
    assert status_resp.status_code == 200
    status_body = status_resp.get_json()
    assert status_body["ok"] is True
    assert status_body["taskId"] == "task-demo-1"
    assert status_body["cancelled"] is True


def test_auth_blocks_without_token(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "AUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(server, "API_TOKEN", "secret-token", raising=False)

    unauth_resp = client.get("/api/status")
    assert unauth_resp.status_code == 401

    auth_resp = client.get(
        "/api/status",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert auth_resp.status_code == 200


def test_register_landmarks_outputs_uncertainty(server):
    client = server.app.test_client()
    resp = client.post(
        "/api/register_landmarks",
        json={
            "landmarks": {
                "bregma": [0.0, 0.0, 0.0],
                "lambda": [-4.2, 0.0, 0.0],
                "mlRef": [0.0, 0.0, 2.1],
            }
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "factors" in body
    assert "uncertainty" in body
    assert "apScaleCi95" in body["uncertainty"]


def test_status_and_version_include_atlas_signature(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setitem(server.atlas_signature, "hash", "atlas-hash-test")
    monkeypatch.setitem(server.atlas_signature, "shape", [528, 320, 456])

    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    status_body = status_resp.get_json()
    assert status_body["atlasHash"] == "atlas-hash-test"
    assert status_body["atlasSignature"]["hash"] == "atlas-hash-test"
    assert "riskConstraintPolicy" in status_body
    assert status_body["clinicalContract"]["species"] == "mouse"

    version_resp = client.get("/api/version")
    assert version_resp.status_code == 200
    version_body = version_resp.get_json()
    assert version_body["atlasHash"] == "atlas-hash-test"
    assert version_body["atlasSignature"]["hash"] == "atlas-hash-test"
    assert "riskConstraintPolicy" in version_body
    assert version_body["clinicalContract"]["intendedUse"] == "automatic_decision"


def test_status_exposes_extended_supported_regions(server):
    client = server.app.test_client()
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    regions = body.get("regions", {})
    assert "ILA" in regions
    assert "GPi" in regions
    assert "SNr" in regions


def test_replay_record_contains_hashes_and_atlas_hash(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setitem(server.atlas_signature, "hash", "atlas-hash-replay")
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True

    replay_resp = client.get(f"/api/replay/{body['requestId']}")
    assert replay_resp.status_code == 200
    replay_record = replay_resp.get_json()["record"]
    assert replay_record["atlasHash"] == "atlas-hash-replay"
    assert isinstance(replay_record.get("requestHash"), str) and len(replay_record["requestHash"]) == 64
    assert isinstance(replay_record.get("responseHash"), str) and len(replay_record["responseHash"]) == 64
    assert isinstance(replay_record["response"].get("deterministicResponseHash"), str)
    assert len(replay_record["response"]["deterministicResponseHash"]) == 64


def test_version_freeze_blocks_atlas_hash_mismatch(server, monkeypatch):
    client = server.app.test_client()
    server.version_freeze_state.update(
        {
            "frozen": True,
            "engineVersion": "test-engine",
            "atlasHash": "atlas-frozen",
            "frozenAt": "2026-03-22T00:00:00Z",
            "frozenBy": "pytest",
            "note": "freeze mismatch test",
        }
    )
    monkeypatch.setitem(server.atlas_signature, "hash", "atlas-running")

    payload = _base_opt_payload(_full_constraints())
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "version_freeze_mismatch"
    assert any("atlasHash" in d for d in body.get("details", []))


def test_golden_replay_same_input_same_solution_signature(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()
    candidate["coveredRegions"] = ["VISp", "CA1"]
    candidate["overlapVoxels"] = {"VISp": 20, "CA1": 10}
    candidate["nVoxelsTotal"] = 40

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [json.loads(json.dumps(candidate))],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["targetKeys"] = ["VISp", "CA1"]
    payload["params"].update(
        {
            "robustOptimization": True,
            "robustSamples": 8,
            "entrySigmaMm": 0.1,
            "angleSigmaDeg": 1.0,
            "depthSigmaMm": 0.1,
            "paretoEnabled": True,
            "paretoTopK": 6,
            "objectiveCoverageWeight": 0.5,
            "objectiveSafetyWeight": 0.3,
            "objectiveAngleWeight": 0.2,
        }
    )

    first_resp = client.post("/api/optimize", json=payload)
    assert first_resp.status_code == 200
    first = first_resp.get_json()

    rerun_resp = client.post(f"/api/replay/{first['requestId']}/rerun")
    assert rerun_resp.status_code == 200
    rerun = rerun_resp.get_json()

    def signature(body):
        selected = body.get("selected", [])
        packed = []
        for p in selected:
            packed.append(
                (
                    round(float(p.get("ap_mm", 0.0)), 6),
                    round(float(p.get("ml_mm", 0.0)), 6),
                    round(float(p.get("theta_deg", 0.0)), 6),
                    round(float(p.get("phi_deg", 0.0)), 6),
                    tuple(sorted(p.get("coveredRegions", []))),
                    round(float(p.get("coverageFraction", 0.0)), 6),
                    round(float(p.get("robustScore", 0.0)), 6),
                    int(p.get("paretoRank", 0)),
                )
            )
        return tuple(packed), tuple(body.get("uncovered", []))

    assert signature(first) == signature(rerun)
    assert first.get("deterministicResponseHash") == rerun.get("deterministicResponseHash")


def test_optimize_streaming_auto_fallback_when_oversubscribed(server, monkeypatch):
    client = server.app.test_client()
    candidate = _fake_candidate()

    monkeypatch.setattr(
        server,
        "generate_candidates_voxel",
        lambda *args, **kwargs: [candidate],
        raising=True,
    )
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "streamingPipelineEnabled": True,
            "streamingAllowOversubscribe": False,
            "computeWorkers": 64,
            "candidateGenWorkers": 64,
        }
    )
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    cp = body.get("computeProfile", {})
    assert cp.get("streamingPipelineRequested") is True
    assert cp.get("streamingPipelineApplied") is False
    assert cp.get("streamingPipelineReason") == "auto_disabled_oversubscribed_workers"


def test_atlas_mode_contract_exposes_flat_and_nested_qiu_flags(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "QIU2018_ANNOTATION_PATH", r"D:\qiu2018\annotation.npy", raising=False)
    monkeypatch.setattr(server, "QIU2018_REGION_MAP_PATH", r"D:\qiu2018\region_map.json", raising=False)
    monkeypatch.setattr(server, "QIU2018_STATS_PATH", r"D:\qiu2018\stats.json", raising=False)
    monkeypatch.setattr(server, "QIU2018_JACOBIAN_PATH", r"D:\qiu2018\jacobian.npy", raising=False)

    resp = client.get("/api/atlas/mode")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["activeAtlasMode"] in {"allen_sdk", "qiu2018"}
    assert isinstance(body.get("supportedAtlasModes"), list)
    assert isinstance(body.get("qiu2018"), dict)
    assert body["qiu2018"]["annotationConfigured"] is True
    assert body["qiu2018"]["regionMapConfigured"] is True
    assert body["qiu2018"]["statsConfigured"] is True
    assert body["qiu2018"]["jacobianConfigured"] is True
    # Flat aliases used by frontend fallback readers.
    assert body["annotationConfigured"] is True
    assert body["regionMapConfigured"] is True
    assert body["statsConfigured"] is True
    assert body["jacobianConfigured"] is True


def test_atlas_mode_post_switch_triggers_loader_refresh(server, monkeypatch):
    client = server.app.test_client()
    called = {"ensure": 0}

    monkeypatch.setattr(server, "_atlas_loader_alive", lambda: False, raising=True)

    def _fake_apply(mode):
        server.ACTIVE_ATLAS_MODE = mode
        return True

    def _fake_ensure(force_retry=False):
        if force_retry:
            called["ensure"] += 1

    monkeypatch.setattr(server, "_apply_active_atlas_mode", _fake_apply, raising=True)
    monkeypatch.setattr(server, "_ensure_atlas_loader", _fake_ensure, raising=True)

    resp = client.post("/api/atlas/mode", json={"mode": "qiu2018"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["activeAtlasMode"] == "qiu2018"
    assert body["changed"] is True
    assert called["ensure"] == 1


def test_status_contract_includes_progress_aliases_and_region_counters(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "ACTIVE_ATLAS_MODE", "qiu2018", raising=False)
    monkeypatch.setattr(server, "QIU2018_ANNOTATION_PATH", r"D:\qiu2018\annotation.npy", raising=False)
    monkeypatch.setattr(server, "QIU2018_REGION_MAP_PATH", r"D:\qiu2018\region_map.json", raising=False)
    monkeypatch.setattr(server, "QIU2018_STATS_PATH", r"D:\qiu2018\stats.json", raising=False)
    monkeypatch.setattr(server, "QIU2018_JACOBIAN_PATH", r"D:\qiu2018\jacobian.npy", raising=False)
    monkeypatch.setitem(server.atlas_load_state, "state", "loading")
    monkeypatch.setitem(server.atlas_load_state, "phase", "indexing")
    monkeypatch.setitem(server.atlas_load_state, "progress", 0.37)

    resp = client.get("/api/status?regionScope=all")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["activeAtlasMode"] == "qiu2018"
    assert isinstance(body.get("atlasLoad"), dict)
    assert body["atlasLoad"]["phase"] == "indexing"
    assert body["atlasLoadPct"] == pytest.approx(37.0)
    assert body["annotationConfigured"] is True
    assert body["regionMapConfigured"] is True
    assert body["statsConfigured"] is True
    assert body["jacobianConfigured"] is True
    assert body["qiu2018"]["annotationConfigured"] is True
    assert isinstance(body.get("regionCount"), int)
    assert isinstance(body.get("catalogRegionCount"), int)
    assert isinstance(body.get("presentRegionCount"), int)
    assert isinstance(body.get("plannerRegionCount"), int)
    assert isinstance(body.get("indexedRegionCount"), int)
    assert body["regionScope"] == "all"
    assert isinstance(body.get("workflowSummary"), dict)
    assert body["workflowSummary"]["status"] in server.WORKFLOW_STATUS_VALUES
    assert isinstance(body["workflowSummary"].get("progress"), float)


def test_status_notices_include_reason_impact_recovery_triplet(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "atlas_loaded", False, raising=False)
    monkeypatch.setattr(server, "atlas_error", "", raising=False)
    monkeypatch.setitem(server.atlas_load_state, "state", "loading")
    monkeypatch.setitem(server.atlas_load_state, "phase", "risk_model")
    monkeypatch.setitem(server.atlas_load_state, "message", "Initializing risk model")
    monkeypatch.setitem(server.atlas_load_state, "progress", 0.82)
    with server.risk_model_lock:
        server.risk_model_summary.clear()
        server.risk_model_summary.update(
            {
                "enabled": False,
                "state": "unavailable",
                "source": [],
                "distanceModel": "none",
            }
        )

    resp = client.get("/api/status?includeRegions=0")
    assert resp.status_code == 200
    body = resp.get_json()
    notices = body.get("degradationNotices") or []
    assert isinstance(notices, list)
    assert len(notices) >= 1
    hit = notices[0]
    assert isinstance(hit.get("reasonEn"), str) and hit["reasonEn"]
    assert isinstance(hit.get("impactEn"), str) and hit["impactEn"]
    assert isinstance(hit.get("recoveryEn"), str) and hit["recoveryEn"]
    assert isinstance(hit.get("reasonCn"), str) and hit["reasonCn"]
    assert isinstance(hit.get("impactCn"), str) and hit["impactCn"]
    assert isinstance(hit.get("recoveryCn"), str) and hit["recoveryCn"]


def test_status_exposes_mri_layer_status(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "ACTIVE_ATLAS_MODE", "mri_ccf_hybrid", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", r"D:\mri\annotation.npy", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_REGION_MAP_PATH", r"D:\mri\region_map.json", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_SKULL_MASK_PATH", r"D:\mri\skull_mask.npy", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_OUTER_TISSUE_MASK_PATH", r"D:\mri\outer_mask.npy", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_LAYERED_SDF_PATH", r"D:\mri\layered_sdf.npz", raising=False)
    monkeypatch.setattr(server, "MRI_CCF_USE_APPEARANCE", True, raising=False)
    with server.risk_model_lock:
        server.risk_model_summary.clear()
        server.risk_model_summary.update(
            {
                "enabled": True,
                "source": ["vessel", "skull", "cortex_outside"],
                "structuredMaskEnabled": True,
                "sdfEnabled": True,
                "distanceModel": "trajectory_sdf_edt",
            }
        )

    resp = client.get("/api/status?includeRegions=0")
    assert resp.status_code == 200
    body = resp.get_json()
    layer = body.get("mriLayerStatus") or {}
    assert layer.get("hybridActive") is True
    assert layer.get("appearanceActive") is True
    assert layer.get("riskLayerStructuredMaskEnabled") is True
    assert layer.get("riskLayerSdfEnabled") is True
    assert layer.get("riskLayerDistanceModel") == "trajectory_sdf_edt"


def test_atlas_mode_contract_exposes_mri_hybrid_flags(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    out = tmp_path / "mri_mode_contract"
    out.mkdir(parents=True, exist_ok=True)
    small_shape = (8, 6, 4)
    monkeypatch.setattr(server, "ATLAS_SHAPE", small_shape, raising=False)
    annotation = np.ones(small_shape, dtype=np.uint32)
    annotation_path = out / "annotation.npy"
    region_map_path = out / "region_map.json"
    stats_path = out / "stats.json"
    jacobian_path = out / "jacobian.npy"
    skull_path = out / "skull_mask.npy"
    outer_path = out / "outer_tissue_mask.npy"
    sdf_path = out / "layered_sdf.npz"
    reg_summary_path = out / "registration_summary.json"

    np.save(annotation_path, annotation)
    np.save(jacobian_path, np.ones_like(annotation, dtype=np.float32))
    np.save(skull_path, np.zeros_like(annotation, dtype=np.uint8))
    np.save(outer_path, np.zeros_like(annotation, dtype=np.uint8))
    np.savez_compressed(sdf_path, signed_sdf_mm=np.ones_like(annotation, dtype=np.float32))
    region_map_path.write_text(json.dumps({"VISp": 385}), encoding="utf-8")
    stats_path.write_text(json.dumps({"dice": 0.9}), encoding="utf-8")
    reg_summary_path.write_text(json.dumps({"dice": 0.91}), encoding="utf-8")

    monkeypatch.setattr(server, "MRI_CCF_REQUIRE_MANIFEST", False, raising=False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", str(annotation_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_REGION_MAP_PATH", str(region_map_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_STATS_PATH", str(stats_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_JACOBIAN_PATH", str(jacobian_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_SKULL_MASK_PATH", str(skull_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_OUTER_TISSUE_MASK_PATH", str(outer_path), raising=False)
    monkeypatch.setattr(server, "MRI_CCF_LAYERED_SDF_PATH", str(sdf_path), raising=False)

    resp = client.get("/api/atlas/mode")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "mri_ccf_hybrid" in body.get("supportedAtlasModes", [])
    assert isinstance(body.get("mriCcfHybrid"), dict)
    assert body["mriCcfHybrid"]["annotationConfigured"] is True
    assert body["mriCcfHybrid"]["regionMapConfigured"] is True
    assert body["mriCcfHybrid"]["statsConfigured"] is True
    assert body["mriCcfHybrid"]["jacobianConfigured"] is True
    assert body["mriCcfHybrid"]["skullMaskConfigured"] is True
    assert body["mriCcfHybrid"]["outerTissueMaskConfigured"] is True
    assert body["mriCcfHybrid"]["layeredSdfConfigured"] is True
    assert isinstance(body["mriCcfHybrid"]["useMriAppearance"], bool)
    assert isinstance(body.get("mriLayerStatus"), dict)


def test_mri_appearance_toggle_contract(server):
    client = server.app.test_client()

    bad = client.post("/api/mri/appearance", json={})
    assert bad.status_code == 400
    bad_body = bad.get_json()
    assert bad_body["code"] == "mri_appearance_missing"

    set_on = client.post("/api/mri/appearance", json={"useMriAppearance": True})
    assert set_on.status_code == 200
    set_on_body = set_on.get_json()
    assert set_on_body["ok"] is True
    assert set_on_body["useMriAppearance"] is True
    assert set_on_body["mriCcfHybrid"]["useMriAppearance"] is True
    assert isinstance(set_on_body.get("mriLayerStatus"), dict)

    set_off = client.post("/api/mri/appearance", json={"useMriAppearance": False})
    assert set_off.status_code == 200
    set_off_body = set_off.get_json()
    assert set_off_body["ok"] is True
    assert set_off_body["useMriAppearance"] is False

    get_resp = client.get("/api/mri/appearance")
    assert get_resp.status_code == 200
    get_body = get_resp.get_json()
    assert get_body["ok"] is True
    assert get_body["useMriAppearance"] is False


def test_mri_activate_validates_bundle_and_switches_mode(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    small_shape = (8, 6, 4)
    monkeypatch.setattr(server, "ATLAS_SHAPE", small_shape, raising=False)
    annotation = np.ones(small_shape, dtype=np.uint32)
    skull = np.zeros_like(annotation, dtype=np.uint8)
    outer = np.zeros_like(annotation, dtype=np.uint8)
    sdf = np.ones_like(annotation, dtype=np.float32)

    out = tmp_path / "mri"
    out.mkdir(parents=True, exist_ok=True)
    annotation_path = out / "annotation.npy"
    region_map_path = out / "region_map.json"
    region_map_v2_path = out / "region_map_v2.json"
    stats_path = out / "stats.json"
    registration_summary_path = out / "registration_summary.json"
    warped_mri_path = out / "warped_mri_in_ccf.nii.gz"
    jacobian_path = out / "jacobian.npy"
    skull_path = out / "skull_mask.npy"
    outer_path = out / "outer_tissue_mask.npy"
    sdf_path = out / "layered_sdf.npz"
    manifest_path = out / "artifact_manifest.json"

    np.save(annotation_path, annotation)
    np.save(jacobian_path, np.ones_like(annotation, dtype=np.float32))
    np.save(skull_path, skull)
    np.save(outer_path, outer)
    np.savez_compressed(sdf_path, signed_sdf_mm=sdf)
    region_map_path.write_text(json.dumps({"VISp": 385}), encoding="utf-8")
    region_map_v2_path.write_text(
        json.dumps(
            {
                "schemaVersion": "region_map_v2",
                "entries": [{"id": 385, "canonicalAcronym": "VISp", "aliases": ["VISp"]}],
                "aliases": {"visp": "VISp"},
            }
        ),
        encoding="utf-8",
    )
    stats_path.write_text(json.dumps({"dice": 0.9}), encoding="utf-8")
    registration_summary_path.write_text(json.dumps({"dice": 0.91}), encoding="utf-8")
    warped_mri_path.write_bytes(b"pytest warped mri placeholder")
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "probeplanner.mri_ccf.artifact_manifest.v1",
                "profileId": "pytest-mri",
                "atlasShape": list(small_shape),
                "artifacts": {
                    "annotation": {"path": str(annotation_path)},
                    "region_map": {"path": str(region_map_path)},
                    "region_map_v2": {"path": str(region_map_v2_path)},
                    "stats": {"path": str(stats_path)},
                    "registration_summary": {"path": str(registration_summary_path)},
                    "warped_mri": {"path": str(warped_mri_path)},
                    "jacobian": {"path": str(jacobian_path)},
                    "skull_mask": {"path": str(skull_path)},
                    "outer_tissue_mask": {"path": str(outer_path)},
                    "layered_sdf": {"path": str(sdf_path)},
                },
            }
        ),
        encoding="utf-8",
    )

    called = {"ensure": 0}

    def _fake_apply(mode):
        server.ACTIVE_ATLAS_MODE = mode
        return True

    def _fake_ensure(force_retry=False):
        if force_retry:
            called["ensure"] += 1

    monkeypatch.setattr(server, "_apply_active_atlas_mode", _fake_apply, raising=True)
    monkeypatch.setattr(server, "_ensure_atlas_loader", _fake_ensure, raising=True)

    bad = client.post("/api/mri/activate", json={"annotationPath": "D:/missing/annotation.npy"})
    assert bad.status_code == 400
    bad_body = bad.get_json()
    assert bad_body["code"] == "mri_bundle_invalid"

    good = client.post(
        "/api/mri/activate",
        json={
            "manifestPath": str(manifest_path),
            "datasetProfile": "custom",
            "switchMode": True,
        },
    )
    assert good.status_code == 200
    body = good.get_json()
    assert body["ok"] is True
    assert body["activeAtlasMode"] == "mri_ccf_hybrid"
    assert body["bundle"]["annotationPath"] == str(annotation_path.resolve())
    assert body["bundle"]["datasetProfile"] == "custom"
    assert called["ensure"] == 1


def test_mri_registration_summary_endpoint_contract(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    out = tmp_path / "mri_reg_summary"
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "registration_summary.json"
    summary_payload = {"dice": 0.92, "hd95Mm": 0.21, "treMm": 0.11}
    summary_path.write_text(json.dumps(summary_payload), encoding="utf-8")

    monkeypatch.setattr(server, "MRI_CCF_REQUIRE_MANIFEST", False, raising=False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", str(out / "annotation.npy"), raising=False)
    server._mri_runtime_update({"registrationSummaryPath": str(summary_path)})

    resp = client.get("/api/mri/registration/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["path"] == str(summary_path.resolve())
    assert isinstance(body.get("metrics"), dict)
    assert body["metrics"]["dice"] == pytest.approx(0.92)


def test_mri_registration_qc_fails_closed_when_metrics_violate_thresholds(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    out = tmp_path / "mri_reg_qc_fail"
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "registration_summary.json"
    summary_payload = {
        "dice": 0.70,
        "hd95Mm": 0.90,
        "treMm": 1.60,
        "ccfTemplate": "allen_ccf_template.nii.gz",
        "ambmcImage": "ambmc_mri.nii.gz",
        "spacingMm": [0.025, 0.025, 0.025],
        "interpolationPolicy": {"label": "NearestNeighbor", "intensity": "Linear"},
    }
    summary_path.write_text(json.dumps(summary_payload), encoding="utf-8")

    monkeypatch.setattr(server, "MRI_CCF_REQUIRE_MANIFEST", False, raising=False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", str(out / "annotation.npy"), raising=False)
    server._mri_runtime_update({"registrationSummaryPath": str(summary_path)})

    resp = client.get("/api/mri/registration/qc")
    assert resp.status_code == 200
    body = resp.get_json()
    quality_gate = body.get("qualityGate") or {}
    reasons = quality_gate.get("blockingReasons") or []
    assert quality_gate.get("passed") is False
    assert any("Dice" in reason for reason in reasons)
    assert any("HD95" in reason for reason in reasons)
    assert any("TRE" in reason for reason in reasons)


def test_mri_registration_summary_endpoint_proxy_metrics_contract(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    out = tmp_path / "mri_reg_proxy_summary"
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "registration_summary.json"
    summary_payload = {
        "registration": {
            "initialCenterOffset": [1.0, 2.0, 2.0],
            "usedRigid": True,
            "rigidMetric": 0.2,
            "usedDeformable": False,
        }
    }
    summary_path.write_text(json.dumps(summary_payload), encoding="utf-8")

    monkeypatch.setattr(server, "MRI_CCF_REQUIRE_MANIFEST", False, raising=False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", str(out / "annotation.npy"), raising=False)
    server._mri_runtime_update({"registrationSummaryPath": str(summary_path)})

    resp = client.get("/api/mri/registration/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    metrics = body.get("metrics") or {}
    evidence = body.get("metricEvidence") or {}
    assert metrics.get("treMm") == pytest.approx(3.0)
    assert metrics.get("dice") == pytest.approx(0.8)
    assert metrics.get("hd95Mm") == pytest.approx(1.8)
    assert metrics.get("jacobianAnomalyRatio") == pytest.approx(0.0)
    assert evidence.get("treMm") == "registration_center_offset_proxy"
    assert evidence.get("dice") == "registration_metric_proxy"


def test_reconstruction_quality_state_proxy_fill_contract(server):
    registration_summary = {
        "registration": {
            "initialCenterOffset": [1.0, 2.0, 2.0],
            "usedRigid": True,
            "rigidMetric": 0.2,
            "usedDeformable": False,
        }
    }
    server._update_reconstruction_quality_state(
        annotation=None,
        stats_override={},
        registration_summary_override=registration_summary,
        profile_source="pytest_proxy_fill",
    )
    quality = server.reconstruction_quality_state
    metrics = quality.get("metrics") or {}
    metric_ci = quality.get("metricCi95") or {}
    evidence = quality.get("metricEvidence") or {}
    assert metrics.get("treMm") == pytest.approx(3.0)
    assert metrics.get("dice") == pytest.approx(0.8)
    assert metrics.get("hd95Mm") == pytest.approx(1.8)
    assert metrics.get("jacobianAnomalyRatio") == pytest.approx(0.0)
    assert isinstance(metric_ci.get("treMm"), list) and len(metric_ci.get("treMm")) == 2
    assert isinstance(metric_ci.get("dice"), list) and len(metric_ci.get("dice")) == 2
    assert evidence.get("treMm") == "registration_center_offset_proxy"


def test_continuous_risk_policy_uses_source_tag(server, monkeypatch):
    monkeypatch.setattr(server, "ALLOW_KEYWORD_ONLY_RISK_ENFORCE", False, raising=False)
    with server.risk_model_lock:
        server.risk_model_summary.clear()
        server.risk_model_summary.update(
            {
                "enabled": True,
                "source": ["atlas_keywords:vessel"],
                "sourceTag": "atlas_keywords_only",
                "sdfBuildState": "ready",
            }
        )
    policy = server._continuous_risk_policy({})
    assert policy.get("riskSourceTag") == "atlas_keywords_only"
    assert policy.get("reason") == "default_enforce"
    assert policy.get("requestedMode") == "enforce"
    assert policy.get("effectiveMode") == "enforce"
    assert policy.get("sourceLimited") is False


def test_continuous_risk_policy_requires_explicit_opt_out(server, monkeypatch):
    monkeypatch.setattr(server, "ALLOW_KEYWORD_ONLY_RISK_ENFORCE", False, raising=False)
    with server.risk_model_lock:
        server.risk_model_summary.clear()
        server.risk_model_summary.update({"enabled": False, "source": [], "sourceTag": None})
    policy = server._continuous_risk_policy({})
    assert policy.get("requestedMode") == "enforce"
    assert policy.get("effectiveMode") == "enforce"
    assert policy.get("reason") == "risk_model_disabled_default_enforce"

    policy = server._continuous_risk_policy({"continuousRiskDistance": "warn"})
    assert policy.get("requestedMode") == "warn"
    assert policy.get("effectiveMode") == "warn"
    assert policy.get("reason") == "risk_model_disabled_explicit_mode"


def test_mri_activate_accepts_active_profile_contract(server, monkeypatch, tmp_path):
    client = server.app.test_client()
    small_shape = (8, 6, 4)
    monkeypatch.setattr(server, "ATLAS_SHAPE", small_shape, raising=False)
    annotation = np.ones(small_shape, dtype=np.uint32)
    out = tmp_path / "mri_active_profile"
    out.mkdir(parents=True, exist_ok=True)

    annotation_path = out / "annotation.npy"
    region_map_path = out / "region_map.json"
    stats_path = out / "stats.json"
    reg_summary_path = out / "registration_summary.json"
    warped_mri_path = out / "warped_mri_in_ccf.nii.gz"
    skull_path = out / "skull_mask.npy"
    outer_path = out / "outer_tissue_mask.npy"
    sdf_path = out / "layered_sdf.npz"
    manifest_path = out / "artifact_manifest.json"
    active_profile_path = out / "active_profile.json"

    np.save(annotation_path, annotation)
    np.save(skull_path, np.zeros_like(annotation, dtype=np.uint8))
    np.save(outer_path, np.zeros_like(annotation, dtype=np.uint8))
    np.savez_compressed(sdf_path, signed_sdf_mm=np.ones_like(annotation, dtype=np.float32))
    region_map_path.write_text(json.dumps({"VISp": 385}), encoding="utf-8")
    stats_path.write_text(json.dumps({"dice": 0.9}), encoding="utf-8")
    reg_summary_path.write_text(json.dumps({"dice": 0.91}), encoding="utf-8")
    warped_mri_path.write_bytes(b"pytest warped mri placeholder")
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "probeplanner.mri_ccf.artifact_manifest.v1",
                "profileId": "pytest-profile",
                "atlasShape": list(small_shape),
                "artifacts": {
                    "annotation": {"path": str(annotation_path)},
                    "region_map": {"path": str(region_map_path)},
                        "stats": {"path": str(stats_path)},
                        "registration_summary": {"path": str(reg_summary_path)},
                        "warped_mri": {"path": str(warped_mri_path)},
                        "skull_mask": {"path": str(skull_path)},
                    "outer_tissue_mask": {"path": str(outer_path)},
                    "layered_sdf": {"path": str(sdf_path)},
                },
            }
        ),
        encoding="utf-8",
    )
    active_profile_path.write_text(
        json.dumps(
            {
                "schemaVersion": "probeplanner.mri_ccf.active_profile.v1",
                "manifestPath": str(manifest_path),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "_apply_active_atlas_mode", lambda mode: True, raising=True)
    monkeypatch.setattr(server, "_ensure_atlas_loader", lambda force_retry=False: None, raising=True)
    resp = client.post(
        "/api/mri/activate",
        json={"activeProfilePath": str(active_profile_path), "switchMode": True},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["bundle"]["manifestPath"] == str(manifest_path.resolve())
    assert body["bundle"]["activeProfilePath"] == str(active_profile_path.resolve())


def test_mri_reconstruct_start_and_status_contract(server, monkeypatch):
    client = server.app.test_client()

    monkeypatch.setattr(
        server,
        "_validate_mri_artifact_bundle",
        lambda bundle: (
            {
                "annotationPath": "D:/mri/annotation.npy",
                "regionMapPath": "D:/mri/region_map.json",
                "statsPath": "D:/mri/stats.json",
                "jacobianPath": None,
                "skullMaskPath": None,
                "outerTissueMaskPath": None,
                "layeredSdfPath": None,
                "datasetProfile": "custom",
            },
            {"annotationShape": list(server.ATLAS_SHAPE), "regionMapEntries": 1},
        ),
        raising=True,
    )
    monkeypatch.setattr(server, "_ensure_atlas_loader", lambda force_retry=False: None, raising=True)
    monkeypatch.setattr(server, "_apply_active_atlas_mode", lambda mode: True, raising=True)

    start = client.post(
        "/api/mri/reconstruct/start",
        json={
            "annotationPath": "D:/mri/annotation.npy",
            "datasetProfile": "custom",
            "activateAfterBuild": True,
            "switchMode": True,
        },
    )
    assert start.status_code == 200
    start_body = start.get_json()
    assert start_body["ok"] is True
    task_id = start_body["taskId"]
    assert isinstance(task_id, str) and task_id.startswith("mri_reconstruct_")

    state = None
    detail = None
    for _ in range(40):
        status_resp = client.get(f"/api/mri/reconstruct/status?taskId={task_id}")
        assert status_resp.status_code == 200
        status_body = status_resp.get_json()
        task = status_body["task"]
        state = str(task.get("state"))
        detail = task
        if state in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert state == "completed", detail
    assert isinstance(detail.get("result"), dict)
    assert detail["result"]["bundle"]["annotationPath"] == "D:/mri/annotation.npy"


def test_reconstruction_quality_endpoint_returns_gate_and_profile(server):
    client = server.app.test_client()
    resp = client.get("/api/reconstruction/quality")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert isinstance(body.get("quality"), dict)
    assert "gateEnabled" in body["quality"]
    assert isinstance(body.get("dynamicUncertaintyProfile"), dict)
    assert isinstance(body.get("qualityGateFailureCard"), dict)


def test_native_optimize_normalizes_payload_before_engine(server, monkeypatch):
    client = server.app.test_client()
    captured = {}

    monkeypatch.setattr(server, "_native_engine_service_enabled", lambda: True, raising=True)
    monkeypatch.setattr(
        server,
        "reconstruction_quality_state",
        {"gateEnabled": True, "passed": True, "blockingReasons": []},
        raising=False,
    )

    def fake_engine(body, task_id):
        captured["body"] = json.loads(json.dumps(body))
        candidate = _fake_candidate()
        return server._normalize_native_engine_payload({
            "ok": True,
            "selected": [candidate],
            "uncovered": [],
            "candidateCount": 1,
            "candidateUniverseCount": 1,
            "timeMs": 3,
        }, body)

    monkeypatch.setattr(server, "_run_native_engine_service_pipeline", fake_engine, raising=True)

    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "riskThresholdTemplate": "conservative",
            "minRiskDistanceMm": 0.42,
        }
    )
    payload["correctionFactors"] = {
        "ap": 2.0,
        "ml": 1.0,
        "dv": 1.0,
        "apShift": 0.5,
        "mlShift": 0.0,
        "dvShift": 0.0,
    }

    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert captured["body"]["params"]["riskThresholdTemplate"] == "conservative"
    assert captured["body"]["params"]["riskThresholdsMm"]["vessel"] == pytest.approx(0.80, abs=1e-6)
    assert captured["body"]["correctionApplied"] is True
    assert body["correctionApplied"] is True
    assert body["selected"][0]["ap_mm_corrected"] == pytest.approx(2.9, abs=1e-6)
    replay_resp = client.get(f"/api/replay/{body['requestId']}")
    assert replay_resp.status_code == 200
    replay = replay_resp.get_json()["record"]
    assert replay["request"]["params"]["riskThresholdTemplate"] == "conservative"
    assert replay["request"]["correctionApplied"] is True


def test_strict_endpoint_request_bypasses_native_engine_service(server, monkeypatch):
    client = server.app.test_client()
    calls = {"python": 0, "native": 0}

    monkeypatch.setattr(server, "_native_engine_service_enabled", lambda: True, raising=True)
    monkeypatch.setattr(server, "atlas_loaded", True, raising=False)
    monkeypatch.setattr(
        server,
        "reconstruction_quality_state",
        {"gateEnabled": True, "passed": True, "blockingReasons": []},
        raising=False,
    )

    def fake_python(body, task_id):
        calls["python"] += 1
        return {
            "ok": True,
            "selected": [],
            "uncovered": list(body.get("targetKeys") or []),
            "algorithm": "greedy",
            "candidateCount": 0,
            "candidateUniverseCount": 0,
            "timeMs": 1,
        }

    def fake_native(body, task_id):
        calls["native"] += 1
        raise AssertionError("native engine must not receive strict endpoint requests")

    monkeypatch.setattr(server, "_run_optimize_pipeline", fake_python, raising=True)
    monkeypatch.setattr(server, "_run_native_engine_service_pipeline", fake_native, raising=True)

    payload = _base_opt_payload(_full_constraints())
    payload["params"].update({
        "targetEndpointPolicy": "tip_in_target",
        "depthOptimizationEnabled": True,
        "stabilityTieBreakerEnabled": False,
    })
    resp = client.post("/api/optimize", json=payload)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["plannerExecutionBackend"] == "python_strict_contract"
    assert body["nativeEngineBypassReason"]
    assert calls == {"python": 1, "native": 0}


def test_native_optimize_enforces_reconstruction_quality_gate(server, monkeypatch):
    client = server.app.test_client()
    monkeypatch.setattr(server, "_native_engine_service_enabled", lambda: True, raising=True)
    monkeypatch.setattr(
        server,
        "reconstruction_quality_state",
        {
            "gateEnabled": True,
            "passed": False,
            "blockingReasons": ["dice below threshold"],
        },
        raising=False,
    )

    def fail_if_called(body, task_id):
        raise AssertionError("native engine should not be called when MRI QC fails")

    monkeypatch.setattr(server, "_run_native_engine_service_pipeline", fail_if_called, raising=True)
    resp = client.post("/api/optimize", json=_base_opt_payload(_full_constraints()))
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "reconstruction_quality_gate_block"
    assert "reconstructionQuality" in body


def test_benchmark_init_and_status_support_custom_workspace_paths(server, tmp_path):
    client = server.app.test_client()
    server.BENCHMARK_ALLOWED_ROOTS = (tmp_path.resolve(),)
    replay_dir = tmp_path / "replay_custom"
    out_dir = tmp_path / "benchmark_custom"
    replay_dir.mkdir(parents=True, exist_ok=True)

    init_resp = client.post(
        "/api/benchmark/init",
        json={
            "replayDir": str(replay_dir),
            "outDir": str(out_dir),
            "overwriteTemplates": True,
        },
    )
    assert init_resp.status_code == 200
    init_body = init_resp.get_json()
    assert init_body["ok"] is True
    assert Path(init_body["replayDir"]) == replay_dir
    assert Path(init_body["outDir"]) == out_dir
    assert Path(init_body["templateDir"]).exists()
    assert Path(init_body["paramTemplate"]).exists()
    assert Path(init_body["expertCsv"]).exists()
    assert Path(init_body["manualOverrideCsv"]).exists()
    assert Path(init_body["trialCsv"]).exists()
    assert Path(init_body["residualCsv"]).exists()

    status_resp = client.get(
        f"/api/benchmark/status?replayDir={replay_dir}&outDir={out_dir}"
    )
    assert status_resp.status_code == 200
    status_body = status_resp.get_json()
    assert status_body["ok"] is True
    assert Path(status_body["replayDir"]) == replay_dir
    assert Path(status_body["outDir"]) == out_dir
    assert status_body["initialized"] is True
    assert isinstance(status_body.get("templatePaths"), dict)
    assert Path(status_body["templatePaths"]["paramTemplate"]).exists()


def test_auto_avoid_prefilter_keeps_feasible_and_tracks_near(server, monkeypatch):
    cand_a = _fake_candidate()
    cand_a["id"] = 0
    cand_b = _fake_candidate()
    cand_b["id"] = 1
    cand_c = _fake_candidate()
    cand_c["id"] = 2

    def _fake_risk_diag(probe, *args, **kwargs):
        pid = int(probe.get("id", -1))
        if pid == 0:
            d = 0.46
        elif pid == 1:
            d = 0.31
        else:
            d = 0.12
        return {
            "minDistanceMm": d,
            "minSignedDistanceMm": d,
            "nearestSourceClass": "ventricle",
            "nearestSource": "risk_surface",
            "closestDepthMm": 1.0,
            "closestVoxel": [0, 0, 0],
            "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
            "classMinDistanceMm": {"ventricle": d},
            "thresholdSegmentsMm": [],
            "dangerSegmentsTop3": [],
            "riskIntegralShortfallMm": 0.0,
            "riskTailShortfallMm": 0.0,
        }

    monkeypatch.setattr(server, "_compute_probe_risk_diagnostics", _fake_risk_diag, raising=True)
    params = {
        "minRiskDistanceMm": 0.35,
        "riskThresholdsMm": {"ventricle": 0.35},
        "autoAvoidEnabled": True,
        "autoAvoidCoarseStepUm": 50.0,
        "autoAvoidSafetyBufferMm": 0.05,
        "autoAvoidNearMarginMm": 0.08,
    }
    kept, near, summary = server._prefilter_candidates_for_auto_avoid(
        [cand_a, cand_b, cand_c],
        params,
        {"continuousRiskDistance": "enforce"},
    )
    assert summary["keptMode"] == "feasible_only"
    assert summary["feasibleCount"] == 1
    assert summary["nearCount"] == 1
    assert summary["rejectedCount"] == 1
    assert [c.get("id") for c in kept] == [0]
    assert [c.get("id") for c in near] == [1]
    assert cand_c["riskPrefilter"]["bucket"] == "rejected"


def test_optimize_auto_repair_replaces_risky_probe(server, monkeypatch):
    client = server.app.test_client()
    bad = _fake_candidate()
    bad["id"] = 0
    bad["ap_mm"] = 1.0
    bad["coveredRegions"] = ["VISp"]
    good = _fake_candidate()
    good["id"] = 1
    good["ap_mm"] = 1.4
    good["coveredRegions"] = ["VISp"]

    monkeypatch.setattr(server, "generate_candidates_voxel", lambda *args, **kwargs: [bad, good], raising=True)
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    def _fake_risk_diag(probe, *args, **kwargs):
        pid = int(probe.get("id", -1))
        # Keep both candidates in "near" bucket for prefilter (no feasible-only drop),
        # then verify auto-repair upgrades risky -> strictly feasible.
        dist = 0.30 if pid == 0 else 0.36
        return {
            "minDistanceMm": dist,
            "minSignedDistanceMm": dist,
            "nearestSourceClass": "ventricle",
            "nearestSource": "risk_surface",
            "closestDepthMm": 1.0,
            "closestVoxel": [0, 0, 0],
            "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
            "classMinDistanceMm": {"ventricle": dist},
            "thresholdSegmentsMm": [{"startDepthMm": 0.9, "endDepthMm": 1.2, "sampleCount": 4}],
            "dangerSegmentsTop3": [{"startDepthMm": 0.9, "endDepthMm": 1.2, "sampleCount": 4}],
            "riskIntegralShortfallMm": 0.0,
            "riskTailShortfallMm": 0.0,
        }

    monkeypatch.setattr(server, "_compute_probe_risk_diagnostics", _fake_risk_diag, raising=True)

    payload = _base_opt_payload(_full_constraints())
    payload["constraintModes"]["continuousRiskDistance"] = "enforce"
    payload["params"].update(
        {
            "autoAvoidEnabled": True,
            "autoRepairEnabled": True,
            "strictFallbackDecompose": True,
            "autoAvoidSafetyBufferMm": 0.05,
            "autoAvoidNearMarginMm": 0.08,
            "minRiskDistanceMm": 0.35,
            "riskThresholdsMm": {"ventricle": 0.35},
        }
    )
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["selected"][0]["id"] == 1
    assert body["autoRepairSummary"]["enabled"] is True
    assert body["autoRepairSummary"]["applied"] is False
    assert body["autoRepairSummary"]["skipReason"] == "no_enforced_constraint_violation"


def test_optimize_strict_fallback_returns_feasible_subsolution(server, monkeypatch):
    client = server.app.test_client()
    risky = _fake_candidate()
    risky["id"] = 0
    risky["coveredRegions"] = ["VISp", "CA1"]
    safe = _fake_candidate()
    safe["id"] = 1
    safe["ap_mm"] = 1.6
    safe["coveredRegions"] = ["VISp"]

    monkeypatch.setattr(server, "generate_candidates_voxel", lambda *args, **kwargs: [risky, safe], raising=True)
    monkeypatch.setattr(
        server,
        "greedy_optimize",
        lambda target_keys, candidates, min_clearance, min_entry: {
            "selected": [candidates[0]],
            "uncovered": [],
            "milpStatus": None,
            "milpNodes": None,
        },
        raising=True,
    )

    def _fake_risk_diag(probe, *args, **kwargs):
        pid = int(probe.get("id", -1))
        # Keep both candidates as near-feasible for prefilter; enforce check still
        # rejects risky candidate and accepts safe candidate.
        dist = 0.30 if pid == 0 else 0.36
        return {
            "minDistanceMm": dist,
            "minSignedDistanceMm": dist,
            "nearestSourceClass": "ventricle",
            "nearestSource": "risk_surface",
            "closestDepthMm": 1.0,
            "closestVoxel": [0, 0, 0],
            "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
            "classMinDistanceMm": {"ventricle": dist},
            "thresholdSegmentsMm": [{"startDepthMm": 0.9, "endDepthMm": 1.2, "sampleCount": 4}],
            "dangerSegmentsTop3": [{"startDepthMm": 0.9, "endDepthMm": 1.2, "sampleCount": 4}],
            "riskIntegralShortfallMm": 0.0,
            "riskTailShortfallMm": 0.0,
        }

    monkeypatch.setattr(server, "_compute_probe_risk_diagnostics", _fake_risk_diag, raising=True)

    payload = _base_opt_payload(_full_constraints())
    payload["targetKeys"] = ["VISp", "CA1"]
    payload["constraintModes"]["continuousRiskDistance"] = "enforce"
    payload["params"].update(
        {
            "autoAvoidEnabled": True,
            "autoRepairEnabled": False,
            "strictFallbackDecompose": True,
            "autoAvoidSafetyBufferMm": 0.05,
            "autoAvoidNearMarginMm": 0.08,
            "minRiskDistanceMm": 0.35,
            "riskThresholdsMm": {"ventricle": 0.35},
        }
    )
    resp = client.post("/api/optimize", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["selected"][0]["id"] == 1
    assert "CA1" in body["uncovered"]
    assert body["strictFallbackSummary"]["applied"] is False
    assert body["strictFallbackSummary"]["infeasibleTargets"] == []


def test_objective_risk_primary_prefers_safer_candidate(server):
    risky = _fake_candidate()
    safe = _fake_candidate()
    risky["id"] = 10
    safe["id"] = 11
    risky["riskDiagnostics"] = {
        "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
        "classMinDistanceMm": {"ventricle": 0.20},
        "minDistanceMm": 0.20,
        "minSignedDistanceMm": 0.20,
        "nearestSourceClass": "ventricle",
        "riskIntegralShortfallMm": 0.22,
        "riskTailShortfallMm": 0.08,
    }
    safe["riskDiagnostics"] = {
        "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
        "classMinDistanceMm": {"ventricle": 0.55},
        "minDistanceMm": 0.55,
        "minSignedDistanceMm": 0.55,
        "nearestSourceClass": "ventricle",
        "riskIntegralShortfallMm": 0.0,
        "riskTailShortfallMm": 0.0,
    }
    params = {
        "maxAngle": 15,
        "objectiveCoverageWeight": 0.5,
        "objectiveSafetyWeight": 0.3,
        "objectiveAngleWeight": 0.2,
        "objectiveRiskPrimary": True,
        "objectiveRiskPrimaryBoost": 0.25,
        "riskSoftplusSigmaMm": 0.04,
        "sdfRiskPathWeight": 1.0,
        "sdfRiskMarginWeight": 1.0,
        "sdfRiskCvarWeight": 0.7,
        "robustOptimization": False,
    }
    obj_risky = server._compute_candidate_objectives(risky, ["VISp"], params)
    obj_safe = server._compute_candidate_objectives(safe, ["VISp"], params)
    assert obj_safe["explainScore"] > obj_risky["explainScore"]
    assert obj_safe["scores"]["continuousRisk"] > obj_risky["scores"]["continuousRisk"]


def test_score_candidates_parallel_returns_compute_metadata(server):
    server.candidate_objective_cache.clear()
    candidates = []
    for i in range(48):
        c = _fake_candidate()
        c["id"] = i + 100
        c["ap_mm"] = 0.6 + i * 0.01
        c["riskDiagnostics"] = {
            "classThresholdMm": {"default": 0.35, "ventricle": 0.35},
            "classMinDistanceMm": {"ventricle": 0.40 + (i % 4) * 0.03},
            "minDistanceMm": 0.40 + (i % 4) * 0.03,
            "minSignedDistanceMm": 0.40 + (i % 4) * 0.03,
            "nearestSourceClass": "ventricle",
            "riskIntegralShortfallMm": 0.0,
            "riskTailShortfallMm": 0.0,
        }
        candidates.append(c)
    params = {
        "maxAngle": 15,
        "objectiveCoverageWeight": 0.5,
        "objectiveSafetyWeight": 0.3,
        "objectiveAngleWeight": 0.2,
        "objectiveRiskPrimary": True,
        "objectiveRiskPrimaryBoost": 0.2,
        "robustOptimization": False,
        "computeWorkers": 4,
        "computeChunkSize": 32,
    }
    stats = server._score_candidates(candidates, ["VISp"], params)
    assert stats["evaluated"] == len(candidates)
    assert "workersUsed" in stats
    assert "parallelMode" in stats
    assert stats["workersUsed"] >= 1


def test_region_index_cache_uses_json_not_pickle(server, tmp_path, monkeypatch):
    cache_dir = tmp_path / "region_index_cache"
    monkeypatch.setattr(server, "REGION_INDEX_CACHE_DIR", cache_dir, raising=False)
    monkeypatch.setattr(server, "REGION_INDEX_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(server, "REGION_INDEX_CACHE_MIN_REGIONS", 1, raising=False)

    cache_path = server._save_region_index_cache(
        "allen_sdk",
        "abc123",
        {"CA1": {"id": 1}},
        {1: {"id": 1, "acronym": "CA1"}},
        {1: [1, 2, 3]},
    )
    assert cache_path.endswith("_region_index.json")
    assert not list(cache_dir.glob("*.pkl"))
    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    assert raw["plannerSeed"]["CA1"]["id"] == 1

    loaded = server._load_region_index_cache("allen_sdk", "abc123")
    assert loaded["cacheHit"] is True
    assert loaded["plannerSeed"]["CA1"]["id"] == 1
    assert loaded["structureMap"]["1"]["acronym"] == "CA1"


def test_calibration_profile_overrides_defaults_when_not_explicit(server, tmp_path):
    calib_path = tmp_path / "planner_calibration.json"
    calib_payload = {
        "objectiveWeights": {"coverage": 0.3, "safety": 0.55, "angle": 0.15},
        "riskThresholdsMm": {"default": 0.31, "ventricle": 0.37},
        "uncertaintyCalibration": {
            "entrySigmaMm": 0.11,
            "angleSigmaDeg": 1.7,
            "depthSigmaMm": 0.16,
            "riskDistanceSigmaMm": 0.05,
            "riskMarginTreMm": 0.22,
        },
        "meta": {"sampleCount": 42},
    }
    calib_path.write_text(json.dumps(calib_payload, ensure_ascii=False), encoding="utf-8")

    payload = _base_opt_payload(_full_constraints())
    payload["params"]["calibrationProfilePath"] = str(calib_path)
    normalized = server._normalize_optimize_payload(payload)
    params = normalized["params"]

    assert params["objectiveCoverageWeight"] == pytest.approx(0.3, abs=1e-6)
    assert params["objectiveSafetyWeight"] == pytest.approx(0.55, abs=1e-6)
    assert params["objectiveAngleWeight"] == pytest.approx(0.15, abs=1e-6)
    assert params["entrySigmaMm"] == pytest.approx(0.11, abs=1e-6)
    assert params["angleSigmaDeg"] == pytest.approx(1.7, abs=1e-6)
    assert params["riskMarginTreMm"] == pytest.approx(0.22, abs=1e-6)
    assert params["baseMinRiskDistanceMm"] == pytest.approx(0.31, abs=1e-6)
    assert params["minRiskDistanceMm"] == pytest.approx(0.53, abs=1e-6)
    assert params["riskThresholdsMmBase"]["ventricle"] == pytest.approx(0.37, abs=1e-6)
    assert params["riskThresholdsMm"]["ventricle"] == pytest.approx(0.59, abs=1e-6)
    assert params["riskThresholdsMm"]["brain_surface"] == pytest.approx(0.05, abs=1e-6)


def test_risk_margin_tre_inflates_only_regular_risk_thresholds(server):
    payload = _base_opt_payload(_full_constraints())
    payload["params"].update(
        {
            "minRiskDistanceMm": 0.35,
            "riskThresholdsMm": {
                "default": 0.35,
                "ventricle": 0.40,
                "skull": 0.45,
                "brain_surface": 0.0,
                "drilling_surface": 0.0,
            },
            "uncertaintyCalibration": {"landmarkTreMm": 0.18},
        }
    )

    normalized = server._normalize_optimize_payload(payload)
    params = normalized["params"]

    assert params["riskMarginTreMm"] == pytest.approx(0.18, abs=1e-6)
    assert params["baseMinRiskDistanceMm"] == pytest.approx(0.35, abs=1e-6)
    assert params["minRiskDistanceMm"] == pytest.approx(0.53, abs=1e-6)
    assert params["riskThresholdsMmBase"]["ventricle"] == pytest.approx(0.40, abs=1e-6)
    assert params["riskThresholdsMm"]["default"] == pytest.approx(0.53, abs=1e-6)
    assert params["riskThresholdsMm"]["ventricle"] == pytest.approx(0.58, abs=1e-6)
    assert params["riskThresholdsMm"]["skull"] == pytest.approx(0.63, abs=1e-6)
    assert params["riskThresholdsMm"]["brain_surface"] == pytest.approx(0.05, abs=1e-6)
    assert params["riskThresholdsMm"]["drilling_surface"] == pytest.approx(0.05, abs=1e-6)


def test_risk_thresholds_floor_boundary_surface_classes(server):
    payload = _base_opt_payload(_full_constraints())
    payload["params"]["riskThresholdsMm"] = {
        "default": 0.0,
        "vessel": 0.0,
        "ventricle": 0.0,
        "sulcus": 0.0,
        "cortex_outside": 0.0,
        "brain_surface": 0.0,
        "drilling_surface": 0.0,
        "skull": 0.0,
    }

    normalized = server._normalize_optimize_payload(payload)
    thresholds = normalized["params"]["riskThresholdsMm"]

    assert thresholds["default"] == pytest.approx(0.01, abs=1e-6)
    assert thresholds["vessel"] == pytest.approx(0.01, abs=1e-6)
    assert thresholds["ventricle"] == pytest.approx(0.01, abs=1e-6)
    assert thresholds["sulcus"] == pytest.approx(0.01, abs=1e-6)
    assert thresholds["skull"] == pytest.approx(0.01, abs=1e-6)
    assert thresholds["cortex_outside"] == pytest.approx(0.05, abs=1e-6)
    assert thresholds["brain_surface"] == pytest.approx(0.05, abs=1e-6)
    assert thresholds["drilling_surface"] == pytest.approx(0.05, abs=1e-6)
