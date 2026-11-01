"""The source citation and final byte manifest cannot silently drift."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_package_v030 as package


@pytest.fixture
def metadata(tmp_path):
    for name in ('package.json', 'package-lock.json', 'apps/ui/package.json', 'apps/ui/package-lock.json',
                 'apps/backend-compat/package.json', 'CITATION.cff', '.zenodo.json', 'LICENSE'):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package.ROOT / name, target)
    return tmp_path


def change_json(root, filename, alter):
    path = root / filename
    data = json.loads(path.read_text())
    alter(data)
    path.write_text(json.dumps(data), encoding='utf-8')


def test_current_metadata_is_consistent(metadata):
    assert package.verify_metadata(metadata)['passed']


@pytest.mark.parametrize('filename', ['package.json', 'apps/ui/package.json', 'apps/backend-compat/package.json'])
def test_package_version_drift_fails(metadata, filename):
    change_json(metadata, filename, lambda d: d.update(version='0.2.1'))
    with pytest.raises(ValueError, match='Package version'):
        package.verify_metadata(metadata)


@pytest.mark.parametrize('filename', ['package-lock.json', 'apps/ui/package-lock.json'])
def test_lock_version_drift_fails(metadata, filename):
    change_json(metadata, filename, lambda d: d['packages'][''].update(version='0.2.1'))
    with pytest.raises(ValueError, match='Lock version'):
        package.verify_metadata(metadata)


def test_predecessor_is_not_older_v020(metadata):
    change_json(metadata, '.zenodo.json', lambda d: d['related_identifiers'][0].update(identifier='10.5281/zenodo.21307547'))
    with pytest.raises(ValueError, match='predecessor'):
        package.verify_metadata(metadata)


def test_citation_does_not_reuse_old_version_doi(metadata):
    with (metadata / 'CITATION.cff').open('a') as handle:
        handle.write('\ndoi: "10.5281/zenodo.21974187"\n')
    with pytest.raises(ValueError, match='anticipate'):
        package.verify_metadata(metadata)


def test_author_affiliation_change_fails(metadata):
    change_json(metadata, '.zenodo.json', lambda d: d['creators'][0].update(affiliation='Different institution'))
    with pytest.raises(ValueError, match='author/affiliation'):
        package.verify_metadata(metadata)


def test_source_inventory_excludes_only_manifest_self(tmp_path):
    (tmp_path / 'code.py').write_bytes(b'print(1)\r\n')
    manifest = tmp_path / package.MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{}')
    rows = package.source_inventory(tmp_path)
    assert list(rows) == ['code.py']
    assert rows['code.py']['bytes'] == 10


@pytest.fixture
def bound_package(tmp_path, monkeypatch):
    (tmp_path / 'code.py').write_bytes(b'print(1)\r\n')
    source = dict(sourceConsistencyPassed=True, evaluatedInventorySha256='a' * 64, optimizerSha256='b' * 64)
    monkeypatch.setattr(package, 'verify_source', lambda root: deepcopy(source))
    monkeypatch.setattr(package, 'verify_metadata', lambda root: dict(passed=True))
    assert package.build(tmp_path)['sourcePackagePassed']
    return tmp_path


def test_bound_source_verifies(bound_package):
    assert package.verify(bound_package)['sourcePackagePassed']


@pytest.mark.parametrize('mode', ['changed', 'missing', 'extra', 'newline'])
def test_any_source_byte_or_member_change_fails(bound_package, mode):
    path = bound_package / 'code.py'
    if mode == 'changed':
        path.write_bytes(b'print(2)\r\n')
    elif mode == 'missing':
        path.unlink()
    elif mode == 'extra':
        (bound_package / 'extra.txt').write_bytes(b'extra')
    else:
        path.write_bytes(b'print(1)\n')
    with pytest.raises(ValueError, match='file/hash/size'):
        package.verify(bound_package)


def test_existing_manifest_not_overwritten(bound_package):
    original = (bound_package / package.MANIFEST).read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        package.build(bound_package)
    assert (bound_package / package.MANIFEST).read_bytes() == original
