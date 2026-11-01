"""Packaging may change path metadata, but not the evaluated scientific inputs."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_public_profile as module


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def inputs(tmp_path):
    source, output, notices = tmp_path / 'original', tmp_path / 'public', tmp_path / 'notices'
    write(source / 'annotation.npy', b'unchanged-scientific-array')
    write(source / 'mri_surfaces/brain.obj', b'v 1 2 3\nf 1 2 3\n')
    write(source / 'stats.json', module.serialize(dict(path='D:/data/jacobian.npy', count=4, tre=None, verified=False)))
    write(source / 'provenance/old.json', module.serialize(dict(path='C:/Users/alice/source', metric=0.4)))
    files = {path.relative_to(source).as_posix(): module.sha(path) for path in source.rglob('*') if path.is_file()}
    artifact = dict(artifacts={name: dict(path=name, sha256=digest) for name, digest in files.items()},
                    valid=True, anatomicalTreAvailable=False)
    write(source / 'artifact_manifest.json', module.serialize(artifact))
    files['artifact_manifest.json'] = module.sha(source / 'artifact_manifest.json')
    write(source / 'paper_profile.json', module.serialize(dict(schemaVersion='probeplanner.paper_profile.v1',
          files=files, scope='Finite-pool software only')))
    for name in module.NOTICE_NAMES:
        write(notices / name, b'{}' if name.endswith('.json') else b'notice\n')
    return source, output, notices, module.sha(source / 'paper_profile.json')


def build(inputs):
    source, output, notices, expected = inputs
    return module.build_profile(source, output, notices, expected_profile_sha256=expected)


def test_copy_preserves_scientific_bytes_and_originals(inputs):
    source, output, notices, _ = inputs
    originals = {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    result = build(inputs)
    assert result['publicProfilePrepared'] is True and result['releaseAcceptance'] is False
    assert result['scientificFilesByteIdentical'] == 2
    assert (output / 'annotation.npy').read_bytes() == originals[Path('annotation.npy')]
    assert (output / 'mri_surfaces/brain.obj').read_bytes() == originals[Path('mri_surfaces/brain.obj')]
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()} == originals
    stats = json.loads((output / 'stats.json').read_text())
    assert stats == dict(path='<LOCAL_D>/data/jacobian.npy', count=4, tre=None, verified=False)
    assert not (output / 'provenance/old.json').exists()
    assert (output / 'provenance/path_projected/old.json').exists()


def test_every_deployed_file_and_rebound_artifact_is_bound(inputs):
    _, output, _, _ = inputs
    build(inputs)
    contract = json.loads((output / 'paper_profile.json').read_text())
    actual = {path.relative_to(output).as_posix(): module.sha(path) for path in output.rglob('*')
              if path.is_file() and path.name != 'paper_profile.json'}
    assert contract['files'] == actual
    artifact = json.loads((output / 'artifact_manifest.json').read_text())
    for row in artifact['artifacts'].values():
        assert row['sha256'] == module.sha(output / row['path'])
    assert artifact['anatomicalTreAvailable'] is False
    lineage = json.loads((output / 'PUBLIC_DERIVATION.json').read_text())
    assert lineage['scientificFileBytesChanged'] is False
    assert lineage['registrationEvidenceUpgraded'] is False


def test_output_cannot_replace_original(inputs):
    source, _, notices, expected = inputs
    with pytest.raises(ValueError, match='outside source'):
        module.build_profile(source, source, notices, expected_profile_sha256=expected)
    with pytest.raises(ValueError, match='outside source'):
        module.build_profile(source, source / 'child', notices, expected_profile_sha256=expected)


def test_existing_output_preserved(inputs):
    _, output, _, _ = inputs
    write(output / 'keep', b'original')
    with pytest.raises(ValueError, match='Output must be new'):
        build(inputs)
    assert (output / 'keep').read_bytes() == b'original'


def test_changed_scientific_file_fails_before_copy(inputs):
    source, output, _, _ = inputs
    (source / 'annotation.npy').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='Evaluated profile file changed'):
        build(inputs)
    assert not output.exists()


def test_changed_contract_fails_before_copy(inputs):
    source, output, _, _ = inputs
    (source / 'paper_profile.json').write_text('{}')
    with pytest.raises(ValueError, match='profile contract changed'):
        build(inputs)
    assert not output.exists()


def test_missing_notice_fails_before_copy(inputs):
    _, output, notices, _ = inputs
    (notices / module.NOTICE_NAMES[0]).unlink()
    with pytest.raises((ValueError, OSError)):
        build(inputs)
    assert not output.exists()


@pytest.mark.parametrize('path', ['../outside', '/absolute', 'D:/data', 'a\\b', '.git/config'])
def test_profile_path_escape_rejected(tmp_path, path):
    with pytest.raises(ValueError, match='Unsafe profile member'):
        module.checked_path(tmp_path, path)


def test_active_artifact_hash_cannot_diverge_from_contract(inputs):
    source, output, notices, _ = inputs
    artifact = json.loads((source / 'artifact_manifest.json').read_text())
    artifact['artifacts']['stats.json']['sha256'] = '0' * 64
    (source / 'artifact_manifest.json').write_bytes(module.serialize(artifact))
    contract = json.loads((source / 'paper_profile.json').read_text())
    contract['files']['artifact_manifest.json'] = module.sha(source / 'artifact_manifest.json')
    (source / 'paper_profile.json').write_bytes(module.serialize(contract))
    with pytest.raises(ValueError, match='Original artifact binding mismatch'):
        module.build_profile(source, output, notices, expected_profile_sha256=module.sha(source / 'paper_profile.json'))
    assert not output.exists()
