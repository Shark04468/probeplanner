import pytest

from publish_build import verify_build_source


def test_staged_build_requires_exact_source_identity(tmp_path):
    source, staged = tmp_path / "source", tmp_path / "staged"
    for root in (source, staged):
        (root / "Assets").mkdir(parents=True)
        (root / "Assets" / "contract.cs").write_text("unchanged source")
    assert verify_build_source(source, staged)
    (staged / "Assets" / "contract.cs").write_text("different source")
    with pytest.raises(SystemExit, match="does not match"):
        verify_build_source(source, staged)


def test_staged_build_rejects_extra_sources_and_empty_projects(tmp_path):
    source, staged = tmp_path / "source", tmp_path / "staged"
    with pytest.raises(SystemExit, match="does not match"):
        verify_build_source(source, staged)
    for root in (source, staged):
        (root / "Assets").mkdir(parents=True)
        (root / "Assets" / "contract.cs").write_text("unchanged source")
    (staged / "Assets" / "extra.cs").write_text("untracked code")
    with pytest.raises(SystemExit, match="does not match"):
        verify_build_source(source, staged)
