import importlib.metadata
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import start_paper_backend as launcher


@pytest.fixture
def loaded_profile(monkeypatch):
    app = SimpleNamespace(config={}, add_url_rule=lambda *args: None)
    server = SimpleNamespace(
        __file__=str(Path(launcher.__file__).resolve().parents[1] / "src/optimizer_server.py"),
        _mri_payload=lambda: {"manifestValid": True, "pathConsistency": {"passed": True}},
        load_atlas=lambda: None, atlas_loaded=True, PLANNER_REGIONS={},
        ACTIVE_ATLAS_MODE="test_fixture_only", app=app,
        risk_query_policy=SimpleNamespace(__file__=launcher.__file__),
        risk_query_execution=SimpleNamespace(configuration=lambda: {"requestedWorkers": 4}))
    atlas = SimpleNamespace(
        __file__=launcher.__file__, configure_backend_environment=lambda *args, **kwargs: None,
        import_backend_server=lambda: server, read_structure_records=lambda path: [{"acronym": "CA1"}],
        apply_structure_tree_descendant_fallback=lambda *args: {"applied": True, "updatedTargets": ["CA1"]})
    monkeypatch.setitem(sys.modules, "audit_registration_perturbation_sensitivity", atlas)
    contract = {"files": {"artifact_manifest.json": "fixture", "structures.json": "fixture"},
                "scope": "Unit fixture only; no anatomy or actual profile loading"}
    monkeypatch.setattr(launcher, "verify_profile", lambda path: (contract, "fixture"))
    return server


def test_profile_startup_without_optional_allensdk(tmp_path, monkeypatch, loaded_profile):
    actual_version = importlib.metadata.version

    def version(name):
        if name == "allensdk":
            raise importlib.metadata.PackageNotFoundError(name)
        return actual_version(name)

    monkeypatch.setattr(launcher.importlib.metadata, "version", version)
    _, runtime = launcher.load_paper_backend(tmp_path / "profile", tmp_path / "run", risk_query_workers=4)
    assert runtime["packages"]["allensdk"] is None
    assert runtime["packages"]["numpy"] == actual_version("numpy")
    assert loaded_profile.app.config["PAPER_RUNTIME_IDENTITY"] == runtime


def test_installed_package_versions_are_retained(monkeypatch):
    expected = {name: "fixture-" + name for name in ("numpy", "scipy", "Flask", "allensdk", "nibabel")}
    monkeypatch.setattr(launcher.importlib.metadata, "version", expected.__getitem__)
    assert launcher.runtime_package_versions() == expected


def test_absent_optional_package_is_explicit_null(monkeypatch):
    def version(name):
        if name == "allensdk":
            raise importlib.metadata.PackageNotFoundError(name)
        return "fixture-present"
    monkeypatch.setattr(launcher.importlib.metadata, "version", version)
    versions = launcher.runtime_package_versions()
    assert versions["allensdk"] is None
    assert {name for name, value in versions.items() if value is None} == {"allensdk"}


@pytest.mark.parametrize("missing", ["numpy", "scipy", "Flask", "nibabel"])
def test_missing_required_package_remains_fatal(monkeypatch, missing):
    def version(name):
        if name == missing:
            raise importlib.metadata.PackageNotFoundError(name)
        return "fixture-present"
    monkeypatch.setattr(launcher.importlib.metadata, "version", version)
    with pytest.raises(importlib.metadata.PackageNotFoundError, match=missing):
        launcher.runtime_package_versions()


def test_unexpected_metadata_error_remains_fatal(monkeypatch):
    def version(name):
        if name == "allensdk":
            raise ValueError("invalid metadata fixture")
        return "fixture-present"
    monkeypatch.setattr(launcher.importlib.metadata, "version", version)
    with pytest.raises(ValueError, match="invalid metadata fixture"):
        launcher.runtime_package_versions()
