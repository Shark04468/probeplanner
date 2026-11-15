"""A determinant file is not evidence for an unrelated registration."""
import csv
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend.registration_evidence import assess_registration_metrics
from test_optimizer_server import server


KEY = "jacobianAnomalyRatio"
ROLES = ("jacobian", "displacement_field", "fixed_image", "moving_image", "warped_mri")


def fixture(constant=False):
    hashes = {role: hashlib.sha256(role.encode()).hexdigest() for role in ROLES}
    record = {"registrationRunId": "synthetic-run-1", "metricValidation": {KEY: {
        "runId": "synthetic-run-1", "method": "displacement_field_jacobian_determinant",
        "value": 0.0, "jacobianValueType": "determinant_not_log",
        "transformDirection": "fixed_to_moving", "transformComponents": "full_composite",
        "derivativeCoordinates": "physical",
        "sampling": {"sampleCount": 64, "totalVoxels": 64, "sampleStride": 1},
        "artifacts": {role: {"key": role, "sha256": value} for role, value in hashes.items()},
    }}}
    summary = {"realJacobianEvidence": not constant, "anomalyRatio": 0.0,
        "jacobianValueType": "determinant_not_log", "sampleCount": 64,
        "totalVoxels": 64, "sampleStride": 1, "nonfiniteSampleCount": 0,
        "sampling": "strided_whole_volume", "anomalyBounds": [0.2, 5.0]}
    return record, hashes, summary


def assess(record, hashes, summary):
    return assess_registration_metrics({KEY: 0.0}, {KEY: "jacobian_determinant"},
                                       {KEY: record}, hashes, summary)


def test_nonconstant_hash_verified_volume_without_derivation_is_not_measurement():
    _, hashes, summary = fixture()
    result = assess({}, hashes, summary)
    assert result["measurementMetrics"][KEY] is None
    assert result["metricAssessment"][KEY]["usableAsMeasurement"] is False


@pytest.mark.parametrize("constant", [False, True])
def test_complete_declared_derivation_is_bound_not_anatomical_proof(constant):
    record, hashes, summary = fixture(constant)
    result = assess(record, hashes, summary)
    assert result["measurementMetrics"][KEY] == 0.0
    assert result["wholeVolumeFoldFreeEstablished"] is False
    assert result["measurementEvidenceComplete"] is False


@pytest.mark.parametrize("role", ROLES)
def test_each_derivation_artifact_is_required(role):
    record, hashes, summary = fixture()
    del record["metricValidation"][KEY]["artifacts"][role]
    assert assess(record, hashes, summary)["measurementMetrics"][KEY] is None


@pytest.mark.parametrize("field,value", [
    ("runId", "other-run"), ("method", "copied_from_neighbor_profile"),
    ("transformDirection", "moving_to_fixed"), ("transformComponents", "nonlinear_only"),
    ("derivativeCoordinates", "voxel"), ("jacobianValueType", "log_determinant"),
    ("value", 0.3),
])
def test_wrong_declared_convention_or_run_is_rejected(field, value):
    record, hashes, summary = fixture()
    record["metricValidation"][KEY][field] = value
    assert assess(record, hashes, summary)["measurementMetrics"][KEY] is None


@pytest.mark.parametrize("field,value", [
    ("sampleStride", 2), ("sampleCount", 63), ("totalVoxels", 128),
    ("nonfiniteSampleCount", 1),
])
def test_sampling_scope_and_invalid_values_cannot_be_hidden(field, value):
    record, hashes, summary = fixture()
    summary[field] = value
    assert assess(record, hashes, summary)["measurementMetrics"][KEY] is None


@pytest.mark.parametrize("role", ["jacobian", "warped_mri"])
def test_cannot_substitute_another_verified_file_for_active_artifact(role):
    record, hashes, summary = fixture()
    hashes["other_file"] = "f" * 64
    record["metricValidation"][KEY]["artifacts"][role] = {"key": "other_file", "sha256": hashes["other_file"]}
    assert assess(record, hashes, summary)["measurementMetrics"][KEY] is None


@pytest.mark.parametrize("invalid_key", [["jacobian"], {"key": "jacobian"}])
def test_malformed_artifact_key_is_unverified_not_an_api_exception(invalid_key):
    record, hashes, summary = fixture()
    record["metricValidation"][KEY]["artifacts"]["jacobian"]["key"] = invalid_key
    assert assess(record, hashes, summary)["measurementMetrics"][KEY] is None


def test_api_qc_and_csv_do_not_promote_cross_profile_jacobian(server, monkeypatch, tmp_path):
    path = tmp_path / "jacobian.npy"
    np.save(path, np.linspace(0.8, 1.2, 64, dtype=np.float32).reshape(4, 4, 4))
    stats = {"metrics": {KEY: 0.0}, "mergeNote": "Copied from another profile"}
    payload = {"registrationSummaryConfigured": True, "statsConfigured": True,
        "registrationSummaryPath": str(tmp_path / "registration_summary.json"),
        "statsPath": str(tmp_path / "stats.json"), "jacobianPath": str(path),
        "pathConsistency": {"passed": True},
        "verifiedArtifactHashes": {"stats": "a" * 64, "registration_summary": "b" * 64,
                                   "jacobian": server._file_sha256(path)}}
    monkeypatch.setattr(server, "_mri_payload", lambda: payload)
    monkeypatch.setattr(server, "_load_mri_hybrid_stats", lambda: stats)
    monkeypatch.setattr(server, "_load_mri_hybrid_registration_summary", lambda: {})
    monkeypatch.setattr(server, "reconstruction_quality_state", {"passed": True})
    monkeypatch.setattr(server, "api_mri_overlay_status", lambda: server.jsonify({"ok": True}))
    client = server.app.test_client()
    summary = client.get("/api/mri/registration/summary").get_json()
    assert summary["metrics"][KEY] == 0.0
    assert summary["measurementMetrics"][KEY] is None
    assert not summary["qualityGatePassed"]
    qc = client.get("/api/mri/registration/qc").get_json()
    assert qc["legacyDiagnostics"][KEY] == 0.0
    assert qc["metrics"][KEY] is None
    assert qc["metrics"]["jacobianStd"] is None
    row = next(row for row in qc["exportRows"] if row["name"] == "Jacobian anomaly ratio")
    assert row["value"] is None and row["passed"] is None
    raw = client.get("/api/mri/registration/qc/export?format=csv").get_data(as_text=True)
    row = next(row for row in csv.DictReader(io.StringIO(raw)) if row["name"] == "Jacobian anomaly ratio")
    assert row["value"] == "" and row["passed"] == ""


def test_constant_array_is_described_without_asserting_it_is_fake(server, tmp_path):
    path = tmp_path / "identity_determinant.npy"
    np.save(path, np.ones((4, 4, 4), dtype=np.float32))
    summary = server._compute_jacobian_summary_from_file(path, max_samples=16)
    assert summary["sampleStride"] == 4
    assert summary["sampleCount"] == 16 and summary["totalVoxels"] == 64
    assert summary["nonfiniteSampleCount"] == 0
    assert summary["evidence"] == "sampled_constant_volume"
    assert "placeholder" not in summary["warning"].lower()


@pytest.mark.parametrize("language", ["En", "Cn"])
def test_constant_diagnostic_notice_does_not_assert_fake_warp(server, monkeypatch, language):
    monkeypatch.setattr(server, "ACTIVE_ATLAS_MODE", "mri_ccf_hybrid")
    monkeypatch.setattr(server, "reconstruction_quality_state", {
        "passed": True, "jacobianSummary": {"realJacobianEvidence": False, "std": 0.0}})
    notices = server._build_degradation_notices(
        atlas_loaded_snapshot=True, atlas_load_snapshot={}, atlas_error_snapshot=None,
        mri_payload={"annotationConfigured": True})
    notice = next(row for row in notices if row["code"] == "mri_jacobian_placeholder")
    text = " ".join(str(value) for key, value in notice.items() if key.endswith(language)).lower()
    assert "placeholder" not in text and "占位" not in text
    assert "ants/syn" not in text
