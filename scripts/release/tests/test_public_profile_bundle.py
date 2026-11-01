"""Transport checks must reject tampering before a profile is accepted."""
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_public_profile as fetcher
import prepare_public_profile as prepare
import public_profile_bundle as bundle


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    source, notices = tmp_path / 'original', tmp_path / 'notices'
    data = {'annotation.npy': b'array-bytes', 'layered_sdf.npz': b'sdf-bytes',
            'mri_surfaces/brain.obj': b'v 1 2 3\n', 'stats.json': b'{"count":3}',
            'structures.json': b'[]'}
    for name, content in data.items():
        prepare.write_bytes(source / name, content)
    bindings = {name: prepare.sha(source / name) for name in data}
    artifact = dict(artifacts={name: dict(path=name, sha256=value) for name, value in bindings.items()})
    prepare.write_bytes(source / 'artifact_manifest.json', prepare.serialize(artifact))
    bindings['artifact_manifest.json'] = prepare.sha(source / 'artifact_manifest.json')
    prepare.write_bytes(source / 'paper_profile.json', prepare.serialize(dict(schemaVersion='probeplanner.paper_profile.v1', files=bindings)))
    original_sha = prepare.sha(source / 'paper_profile.json')
    for name in prepare.NOTICE_NAMES:
        prepare.write_bytes(notices / name, b'{}')
    profile = tmp_path / 'profile'
    report = prepare.build_profile(source, profile, notices, expected_profile_sha256=original_sha)
    # Synthetic fixtures exercise the transport checks, not the real pinned dataset.
    monkeypatch.setattr(bundle, 'EVALUATED_PROFILE_SHA256', original_sha)
    output = tmp_path / 'assets'
    manifest = bundle.build(profile, output, report['publicProfileContractSha256'])
    return profile, output, manifest


def test_split_bundle_extracts_exact_profile(packaged, tmp_path):
    original, assets, manifest = packaged
    restored = tmp_path / 'restored'
    bundle.extract_verified(assets, restored, manifest)
    assert bundle.inspect_profile(restored, manifest['publicProfileContractSha256']) == manifest['files']
    for name in manifest['files']:
        assert (restored / name).read_bytes() == (original / name).read_bytes()


def test_asset_tamper_rejected_before_extraction(packaged, tmp_path):
    _, assets, manifest = packaged
    (assets / manifest['assets']['sdf']['filename']).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='Downloaded asset mismatch'):
        bundle.extract_verified(assets, tmp_path / 'restored', manifest)
    assert not (tmp_path / 'restored').exists()


def test_existing_profile_is_not_overwritten(packaged):
    original, assets, manifest = packaged
    with pytest.raises(ValueError, match='new and separate'):
        bundle.extract_verified(assets, original, manifest)


def test_extra_profile_file_rejected(packaged):
    original, _, manifest = packaged
    (original / 'unlisted').write_bytes(b'not-bound')
    with pytest.raises(ValueError, match='file set mismatch'):
        bundle.inspect_profile(original, manifest['publicProfileContractSha256'])


@pytest.mark.parametrize('mutation,match', [
    ('missing_role', 'roles'), ('wrong_version', 'release'), ('scientific_change', 'Scientific'),
    ('duplicate_member', 'duplicate'), ('unsafe_filename', 'filename'),
    ('negative_size', 'size'), ('boolean_size', 'size'), ('oversized', 'size'),
    ('invalid_hash', 'hash'), ('unsafe_member', 'Unsafe'), ('missing_profile', 'Unbound'),
])
def test_malformed_manifest_rejected(packaged, mutation, match):
    _, _, source = packaged
    manifest = deepcopy(source)
    if mutation == 'missing_role':
        del manifest['assets']['arrays']
    elif mutation == 'wrong_version':
        manifest['intendedReleaseTag'] = 'v0.2.1-paper'
    elif mutation == 'scientific_change':
        manifest['scientificFileBytesChanged'] = True
    elif mutation == 'duplicate_member':
        manifest['assets']['arrays']['members'] *= 2
    elif mutation == 'unsafe_filename':
        manifest['assets']['arrays']['filename'] = '../escape.zip'
    elif mutation == 'negative_size':
        manifest['files']['annotation.npy']['bytes'] = -1
    elif mutation == 'boolean_size':
        manifest['files']['annotation.npy']['bytes'] = True
    elif mutation == 'oversized':
        manifest['files']['annotation.npy']['bytes'] = bundle.MAX_ASSET_BYTES
    elif mutation == 'invalid_hash':
        manifest['files']['annotation.npy']['sha256'] = 'invalid'
    elif mutation == 'unsafe_member':
        manifest['files']['../bad'] = dict(bytes=1, sha256='a' * 64)
    elif mutation == 'missing_profile':
        del manifest['files']['paper_profile.json']
    with pytest.raises(ValueError, match=match):
        bundle.validate_bundle_manifest(manifest)


@pytest.mark.parametrize('url', ['http://example.com/data', 'file:///data', 'https://user:password@example.com/data'])
def test_unsafe_transport_url_rejected(url):
    with pytest.raises(ValueError):
        fetcher.check_url(url)


def test_loopback_download_then_verified_extraction(packaged, tmp_path):
    _, assets, manifest = packaged
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = fetcher.fetch(f'http://127.0.0.1:{server.server_port}',
            prepare.sha(assets / 'DATA_BUNDLE_MANIFEST.json'), tmp_path / 'downloads', tmp_path / 'received')
        assert result['downloadAndExtractionPassed'] is True
        assert result['transport'] == 'loopback'
        assert result['runtimeStartupVerified'] is False and result['releaseAcceptance'] is False
        assert result['filesVerified'] == len(manifest['files'])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_wrong_download_hash_leaves_no_extracted_profile(packaged, tmp_path):
    _, assets, _ = packaged
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ValueError, match='Download hash mismatch'):
            fetcher.fetch(f'http://127.0.0.1:{server.server_port}', '0' * 64,
                          tmp_path / 'downloads', tmp_path / 'received')
        assert not (tmp_path / 'received').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
