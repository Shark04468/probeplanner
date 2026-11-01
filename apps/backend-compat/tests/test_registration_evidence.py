import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend.registration_evidence import assess_registration_metrics, ROLES
from test_optimizer_server import server


def bound_record(key="treMm"):
    hashes = {role: str(i + 1) * 64 for i, role in enumerate(ROLES[key])}
    record = {
        "anatomicalTreAvailable": True,
        "metricValidation": {key: {
            "method": "held_out_anatomical_landmarks" if key == "treMm" else "transformed_moving_mask_comparison",
            "usedForRegistration": False,
            "value": 0.2,
            "artifacts": {role: {"key": role, "sha256": sha} for role, sha in hashes.items()},
        }},
    }
    return record, hashes


@pytest.mark.parametrize("record", [{}, {"anatomicalTreAvailable": True}, {"metricEvidence": {"treMm": "landmarks"}}])
def test_legacy_value_or_declaration_alone_is_not_anatomical_tre(record):
    result = assess_registration_metrics({"treMm": 0.2}, {}, {"treMm": record})
    assert result["anatomicalTreAvailable"] is False
    assert result["measurementMetrics"]["treMm"] is None


def test_bound_held_out_measurement_available_but_not_surgical_validation():
    record, hashes = bound_record()
    result = assess_registration_metrics({"treMm": 0.2}, {}, {"treMm": record}, hashes)
    assert result["anatomicalTreAvailable"] is True
    assert result["measurementMetrics"]["treMm"] == 0.2
    assert result["measurementEvidenceComplete"] is False
    assert "not_subject_surgical_validation" in result["measurementScope"]


@pytest.mark.parametrize("mutation", ["hash", "used_for_registration", "value", "missing_transform", "proxy"])
def test_inconsistent_landmark_evidence_is_rejected(mutation):
    record, hashes = bound_record()
    sources = {}
    declaration = record["metricValidation"]["treMm"]
    if mutation == "hash":
        hashes["moving_landmarks"] = "f" * 64
    elif mutation == "used_for_registration":
        declaration["usedForRegistration"] = True
    elif mutation == "value":
        declaration["value"] = 0.3
    elif mutation == "missing_transform":
        hashes.pop("forward_transform")
    else:
        sources["treMm"] = "registration_center_offset_proxy"
    result = assess_registration_metrics({"treMm": 0.2}, sources, {"treMm": record}, hashes)
    assert result["anatomicalTreAvailable"] is False


@pytest.mark.parametrize("key", ["dice", "hd95Mm"])
def test_mask_metrics_require_pretransform_and_transformed_masks(key):
    record, hashes = bound_record(key)
    assert assess_registration_metrics({key: 0.2}, {}, {key: record}, hashes)["measurementMetrics"][key] == 0.2
    hashes.pop("moving_mask")
    assert assess_registration_metrics({key: 0.2}, {}, {key: record}, hashes)["measurementMetrics"][key] is None


def test_heuristic_interval_is_not_a_confidence_interval():
    record, hashes = bound_record()
    record["metricCi95"] = {"treMm": [0.18, 0.22]}
    result = assess_registration_metrics({"treMm": 0.2}, {}, {"treMm": record}, hashes)
    assert result["measurementCi95"] == {}


def test_affine_flag_does_not_prove_positive_jacobian():
    result = assess_registration_metrics({"jacobianAnomalyRatio": 0.0},
        {"jacobianAnomalyRatio": "rigid_affine_no_deformable"}, {}, {"jacobian": "a" * 64},
        {"realJacobianEvidence": True, "anomalyRatio": 0.0})
    assert result["measurementMetrics"]["jacobianAnomalyRatio"] is None


def test_proxy_summary_and_export_are_not_validation(server, monkeypatch, tmp_path):
    path = tmp_path / "registration_summary.json"
    path.write_text(json.dumps({"registration": {"initialCenterOffset": [1, 2, 2], "rigidMetric": 0.2,
                                                "usedRigid": True, "usedDeformable": False}}), encoding="utf-8")
    monkeypatch.setattr(server, "MRI_CCF_REQUIRE_MANIFEST", False)
    monkeypatch.setattr(server, "MRI_CCF_ANNOTATION_PATH", str(tmp_path / "annotation.npy"))
    server._mri_runtime_update({"registrationSummaryPath": str(path)})
    client = server.app.test_client()
    summary = client.get("/api/mri/registration/summary").get_json()
    assert summary["metrics"]["treMm"] == 3.0
    assert summary["measurementMetrics"]["treMm"] is None
    assert not summary["anatomicalTreAvailable"]
    assert not summary["qualityGatePassed"]
    qc = client.get("/api/mri/registration/qc").get_json()
    assert not qc["qualityGate"]["passed"]
    assert qc["metrics"]["treMeanMm"] is None
    assert qc["metrics"]["treMedianMm"] is None
    assert qc["metrics"]["treMaxMm"] is None
    for row in qc["exportRows"]:
        if row["section"] == "metric":
            assert row["value"] is None and row["passed"] is None
    csv = client.get("/api/mri/registration/qc/export?format=csv").get_data(as_text=True)
    assert "evidenceStatus" in csv and "legacy_proxy" in csv


def test_missing_numeric_data_cannot_pass_qc(server, monkeypatch):
    monkeypatch.setattr(server, "api_mri_registration_summary", lambda: server.jsonify({"ok": True}))
    monkeypatch.setattr(server, "api_mri_overlay_status", lambda: server.jsonify({"ok": True}))
    with server.app.test_request_context():
        result = server._build_mri_registration_qc_payload()
    assert not result["qualityGate"]["passed"]
    assert not result["anatomicalTreAvailable"]


def test_manifest_hash_mismatch_clears_all_measurement_bindings(server, tmp_path):
    artifact = tmp_path / "value.txt"
    artifact.write_text("bound", encoding="ascii")
    manifest = tmp_path / "manifest.json"
    payload = {"schemaVersion": "probeplanner.mri_ccf.artifact_manifest.v1", "artifacts": {
        "jacobian": {"path": "value.txt", "required": True, "sha256": server._file_sha256(artifact)}}}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert server._load_mri_manifest_contract(manifest)["verifiedArtifactHashes"]
    artifact.write_text("changed", encoding="ascii")
    result = server._load_mri_manifest_contract(manifest)
    assert not result["manifestValid"] and not result["verifiedArtifactHashes"]


@pytest.mark.parametrize("status", ["assumed", "recorded", "unknown"])
def test_metadata_and_assumptions_are_not_passed_checks(server, status):
    check = server._qc_check(status, "metadata only")
    assert check["passed"] is None
    assert check["assessmentScope"] == "metadata_consistency_only"


def test_filename_and_declared_spacing_are_metadata_not_registration_verification(server, monkeypatch):
    summary = {"ok": True, "summary": {
        "fixedImage": "allen_ccf_template.nii.gz", "movingImage": "ambmc_mri.nii.gz",
        "spacingMm": [0.025, 0.025, 0.025],
    }}
    monkeypatch.setattr(server, "api_mri_registration_summary", lambda: server.jsonify(summary))
    monkeypatch.setattr(server, "api_mri_overlay_status", lambda: server.jsonify({"ok": True}))
    with server.app.test_request_context():
        result = server._build_mri_registration_qc_payload()
    assert result["checks"]["fixedMovingDirection"]["status"] == "recorded"
    assert result["checks"]["spacing"]["status"] == "recorded"
    assert result["checks"]["interpolationPolicy"]["status"] == "assumed"
    assert all(row["passed"] is None for row in result["checks"].values())
    assert not result["qualityGate"]["passed"]
