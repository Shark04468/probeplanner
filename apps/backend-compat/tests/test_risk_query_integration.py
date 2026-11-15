import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial import cKDTree

from test_optimizer_server import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import start_paper_backend as launcher
from backend import risk_query


def test_query_wrapper_matches_single_worker_and_numpy_fallback(server, monkeypatch):
    monkeypatch.setattr(risk_query, "available_cpus", lambda: 8)
    monkeypatch.setattr(server, "risk_query_execution", risk_query.ExactRiskQueryPolicy(4, runtime_budget=8))
    points = np.array([[.2, .3, .4], [.9, 1., .5]])
    reference = np.array([[0., 0., 0.], [1., 1., 1.]])
    tree = cKDTree(reference)
    fast = server._query_min_distance_um(points, tree_obj=tree)
    slow = server._query_min_distance_um(points, reference_points_um=reference)
    assert fast[:3] == slow[:3]
    np.testing.assert_array_equal(fast[3], slow[3])
    assert server._query_min_distance_um([]) == (None, None, None, None)


def test_scalar_tree_result_keeps_original_wrapper_convention(server, monkeypatch):
    monkeypatch.setattr(risk_query, "available_cpus", lambda: 8)
    monkeypatch.setattr(server, "risk_query_execution", risk_query.ExactRiskQueryPolicy(4, runtime_budget=8))
    tree = cKDTree(np.array([[0., 0., 0.], [1., 1., 1.]]))
    result = server._query_min_distance_um(np.array([1., 1., 1.]), tree_obj=tree)
    assert result[:3] == (0., 0, 1)
    np.testing.assert_array_equal(result[3], [0.])


@pytest.mark.parametrize("value", [0, 9, True, 4.0, "4"])
def test_invalid_direct_launcher_option_has_no_output_side_effect(tmp_path, value):
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="risk_query_workers"):
        launcher.load_paper_backend(tmp_path / "profile", output, risk_query_workers=value)
    assert not output.exists()


@pytest.mark.parametrize("value", [None, "4"])
def test_cli_forwards_explicit_option_or_default_before_loading(tmp_path, monkeypatch, value):
    seen = []
    def loader(*args, **kwargs):
        seen.append(kwargs)
        raise RuntimeError("loader boundary")
    monkeypatch.setattr(launcher, "load_paper_backend", loader)
    args = ["start_paper_backend.py", "--profile", str(tmp_path / "profile"),
            "--run-dir", str(tmp_path / "run"), "--check-only"]
    if value is not None:
        args += ["--risk-query-workers", value]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(RuntimeError, match="loader boundary"):
        launcher.main()
    assert seen == [{"risk_query_workers": 1 if value is None else 4}]


def test_launcher_configuration_overrides_inherited_value_and_binds_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBEPLANNER_RISK_QUERY_WORKERS", "7")
    monkeypatch.setattr(risk_query, "available_cpus", lambda: 8)
    app = SimpleNamespace(config={}, add_url_rule=lambda *args: None)
    fake = SimpleNamespace(
        __file__=str(Path(launcher.__file__).resolve().parents[1] / "src/optimizer_server.py"),
        _mri_payload=lambda: {"manifestValid": True, "pathConsistency": {"passed": True}},
        load_atlas=lambda: None, atlas_loaded=True, PLANNER_REGIONS={},
        ACTIVE_ATLAS_MODE="test_only", app=app, risk_query_policy=risk_query)
    def import_server():
        fake.risk_query_execution = risk_query.ExactRiskQueryPolicy(
            launcher.os.environ["PROBEPLANNER_RISK_QUERY_WORKERS"], runtime_budget=8)
        return fake
    atlas = SimpleNamespace(
        __file__=launcher.__file__, configure_backend_environment=lambda *args, **kwargs: None,
        import_backend_server=import_server, read_structure_records=lambda path: [{"acronym": "CA1"}],
        apply_structure_tree_descendant_fallback=lambda *args: {"applied": True, "updatedTargets": ["CA1"]})
    monkeypatch.setitem(sys.modules, "audit_registration_perturbation_sensitivity", atlas)
    contract = {"files": {"artifact_manifest.json": "fixture", "structures.json": "fixture"}, "scope": "test fixture only"}
    monkeypatch.setattr(launcher, "verify_profile", lambda path: (contract, "fixture"))
    monkeypatch.setattr(launcher.importlib.metadata, "version", lambda name: "test-only")
    _, runtime = launcher.load_paper_backend(tmp_path / "profile", tmp_path / "run", risk_query_workers=4)
    assert runtime["riskQueryPolicy"]["requestedWorkers"] == 4
    assert runtime["riskQueryPolicy"]["effectiveWorkers"] == 4
    assert runtime["riskQueryPolicySha256"] == launcher.sha256(risk_query.__file__)
    assert "PROBEPLANNER_RISK_QUERY_WORKERS" in runtime["discardedInheritedConfigurationKeys"]
    assert runtime == app.config["PAPER_RUNTIME_IDENTITY"]
