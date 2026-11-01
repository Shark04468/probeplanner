import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from start_paper_backend import verify_profile, sha256


def make_profile(root):
    root.mkdir()
    files = {}
    for name in ("artifact_manifest.json", "structures.json", "annotation.npy", "region_map_v2.json"):
        path = root / name
        path.write_text(name, encoding="ascii")
        files[name] = sha256(path)
    contract = {"schemaVersion": "probeplanner.paper_profile.v1", "files": files}
    (root / "paper_profile.json").write_text(json.dumps(contract), encoding="utf-8")
    return contract


def test_paper_profile_verifies_all_bindings(tmp_path):
    root = tmp_path / "data with spaces"
    contract = make_profile(root)
    assert verify_profile(root)[0] == contract


def test_paper_profile_rejects_changed_bytes(tmp_path):
    root = tmp_path / "data"
    make_profile(root)
    (root / "annotation.npy").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_profile(root)


def test_paper_profile_rejects_escape(tmp_path):
    root = tmp_path / "data"
    contract = make_profile(root)
    (tmp_path / "outside").write_bytes(b"outside")
    contract["files"]["../outside"] = sha256(tmp_path / "outside")
    (root / "paper_profile.json").write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        verify_profile(root)


def test_paper_profile_requires_ontology_binding(tmp_path):
    root = tmp_path / "data"
    contract = make_profile(root)
    contract["files"].pop("structures.json")
    (root / "paper_profile.json").write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete"):
        verify_profile(root)
